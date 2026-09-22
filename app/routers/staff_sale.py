import os
import datetime
from decimal import Decimal
from ..template_env import templates
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user
from ..models import Account, ChairRate, Setting, Transaction, TransactionLine, AuditLog
from ..accounting import next_reference_number, to_decimal

router = APIRouter(prefix="/staff")

CHAIR_CODES = {"Deluxe": "4101", "King": "4102"}
ADDON_CODE = "4103"
CASH_CODE = "1000"
DISCOUNT_CODES = {"PWD_SENIOR": "4201", "PROMO": "4202"}


def _get_account(db: Session, code: str):
    return db.query(Account).filter(Account.code == code, Account.is_active == True).first()  # noqa: E712


def _get_settings(db: Session) -> dict:
    defaults = {"eye_massager_short_price": "20", "eye_massager_short_threshold": "15", "eye_massager_free_threshold": "30"}
    rows = {s.key: s.value for s in db.query(Setting).all()}
    defaults.update(rows)
    return defaults


def _get_rate(db: Session, chair_type: str, duration: int, as_of: datetime.date):
    return (
        db.query(ChairRate)
        .filter(ChairRate.chair_type == chair_type, ChairRate.duration_minutes == duration, ChairRate.is_active == True)  # noqa: E712
        .filter(ChairRate.effective_from <= as_of)
        .order_by(ChairRate.effective_from.desc())
        .first()
    )


def _todays_sales(db: Session, username: str):
    today = datetime.date.today()
    return (
        db.query(Transaction)
        .filter(
            Transaction.transaction_type == "RECEIPT",
            Transaction.created_by == username,
            Transaction.transaction_date == today,
            Transaction.status == "ACTIVE",
        )
        .order_by(Transaction.created_at.desc())
        .all()
    )


def _todays_sales_by_chair(db: Session, username: str):
    """Splits today's sales into Deluxe and King lists, based on which revenue
    account was actually credited on each transaction - not by parsing the remarks
    text, so this stays correct even if the remarks wording ever changes."""
    sales = _todays_sales(db, username)
    deluxe_acc = _get_account(db, CHAIR_CODES["Deluxe"])
    king_acc = _get_account(db, CHAIR_CODES["King"])
    deluxe_sales, king_sales = [], []
    for t in sales:
        credited_ids = {l.account_id for l in t.lines if l.credit > 0}
        if deluxe_acc and deluxe_acc.id in credited_ids:
            deluxe_sales.append(t)
        elif king_acc and king_acc.id in credited_ids:
            king_sales.append(t)
    return deluxe_sales, king_sales


@router.get("/sale")
def sale_form(request: Request, db: Session = Depends(get_db), saved_ref: str = None):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    from ..accounting import current_chair_rates
    rate_map = current_chair_rates(db, datetime.date.today())
    settings = _get_settings(db)
    deluxe_sales, king_sales = _todays_sales_by_chair(db, user.username)

    return templates.TemplateResponse("staff_sale.html", {
        "request": request, "user": user, "rate_map": rate_map, "settings": settings,
        "today": datetime.date.today().isoformat(), "error": None, "saved_ref": saved_ref,
        "deluxe_sales": deluxe_sales, "king_sales": king_sales,
        "todays_sales_count": len(deluxe_sales) + len(king_sales),
    })


@router.post("/sale")
def submit_sale(
    request: Request, db: Session = Depends(get_db),
    transaction_date: str = Form(...), chair_type: str = Form(...), duration_minutes: int = Form(...),
    eye_massager: str = Form("0"), discount_type: str = Form("NONE"),
    pwd_senior_id: str = Form(""), promo_percent: str = Form(""),
    client_token: str = Form(""), occurred_at: str = Form(""),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    def render_error(msg):
        from ..accounting import current_chair_rates
        rate_map = current_chair_rates(db, datetime.date.today())
        deluxe_sales, king_sales = _todays_sales_by_chair(db, user.username)
        return templates.TemplateResponse("staff_sale.html", {
            "request": request, "user": user, "rate_map": rate_map, "settings": _get_settings(db),
            "today": transaction_date, "error": msg, "saved_ref": None,
            "deluxe_sales": deluxe_sales, "king_sales": king_sales,
            "todays_sales_count": len(deluxe_sales) + len(king_sales),
        })

    # --- Idempotency check: if this exact submission already succeeded before
    # (a retry after a network hiccup, a double-click, a re-sent offline queue
    # item), don't create a second transaction - just show the same success
    # result as if it had just been submitted. This is the core fix for staff
    # not knowing whether a sale posted and re-submitting "just in case."
    if client_token:
        existing = db.query(Transaction).filter(Transaction.client_token == client_token).first()
        if existing:
            return RedirectResponse(f"/staff/sale?saved_ref={existing.reference_number}", status_code=303)

    try:
        t_date = datetime.date.fromisoformat(transaction_date)
    except ValueError:
        return render_error("Please select a valid date.")
    if t_date > datetime.date.today():
        return render_error("Date cannot be in the future.")
    if chair_type not in CHAIR_CODES:
        return render_error("Please select a valid chair.")

    rate = _get_rate(db, chair_type, duration_minutes, t_date)
    if not rate:
        return render_error(f"No rate is configured for {chair_type} at {duration_minutes} minutes on this date. Ask an admin to set one under Rates & Settings.")
    base_price = Decimal(str(rate.price))

    settings = _get_settings(db)
    addon_amount = Decimal("0.00")
    if eye_massager == "1":
        short_thresh = int(settings["eye_massager_short_threshold"])
        free_thresh = int(settings["eye_massager_free_threshold"])
        if duration_minutes <= short_thresh:
            addon_amount = to_decimal(settings["eye_massager_short_price"])
        elif duration_minutes >= free_thresh:
            addon_amount = Decimal("0.00")
        else:
            addon_amount = Decimal("0.00")  # falls between thresholds - default to free rather than overcharge

    gross = base_price + addon_amount
    discount_amount = Decimal("0.00")
    discount_code = None
    remarks_bits = [f"{chair_type} {duration_minutes}min"]
    if eye_massager == "1":
        remarks_bits.append("+ Eye Massager")

    if discount_type == "PWD_SENIOR":
        if not pwd_senior_id.strip():
            return render_error("PWD/Senior ID number is required to apply this discount.")
        discount_amount = (gross * Decimal("0.20")).quantize(Decimal("0.01"))
        discount_code = DISCOUNT_CODES["PWD_SENIOR"]
        remarks_bits.append(f"- PWD/Senior Discount 20% (ID: {pwd_senior_id.strip()})")
    elif discount_type == "PROMO":
        try:
            pct = Decimal(promo_percent or "0")
        except Exception:
            return render_error("Please enter a valid promo discount percentage.")
        if pct < 0 or pct > 100:
            return render_error("Promo discount percentage must be between 0 and 100.")
        if pct > 0:
            discount_amount = (gross * pct / Decimal("100")).quantize(Decimal("0.01"))
            discount_code = DISCOUNT_CODES["PROMO"]
            remarks_bits.append(f"- Promotional Discount {pct}%")

    net_cash = gross - discount_amount
    if net_cash < 0:
        return render_error("Discount cannot exceed the total price.")

    cash_acc = _get_account(db, CASH_CODE)
    chair_acc = _get_account(db, CHAIR_CODES[chair_type])
    addon_acc = _get_account(db, ADDON_CODE) if addon_amount > 0 else None
    discount_acc = _get_account(db, discount_code) if discount_code else None

    if not cash_acc or not chair_acc or (addon_amount > 0 and not addon_acc) or (discount_code and not discount_acc):
        return render_error("A required account is missing or inactive. Please contact an admin.")

    # occurred_at: when the sale actually happened at the counter (important for
    # offline-recorded sales, where this can be well before the sync actually
    # reaches the server). Falls back to "now" for a normal online submission.
    occurred_dt = None
    if occurred_at:
        try:
            occurred_dt = datetime.datetime.fromisoformat(occurred_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            occurred_dt = None
    if not occurred_dt:
        occurred_dt = datetime.datetime.utcnow()

    try:
        ref = next_reference_number(db, "RECEIPT", t_date)
        txn = Transaction(
            reference_number=ref, transaction_date=t_date, transaction_type="RECEIPT",
            remarks=" ".join(remarks_bits), status="ACTIVE", created_by=user.username,
            client_token=client_token or None, occurred_at=occurred_dt,
        )
        db.add(txn)
        db.flush()

        db.add(TransactionLine(transaction_id=txn.id, account_id=cash_acc.id, debit=net_cash, credit=Decimal("0.00")))
        if discount_amount > 0:
            db.add(TransactionLine(transaction_id=txn.id, account_id=discount_acc.id, debit=discount_amount, credit=Decimal("0.00")))
        db.add(TransactionLine(transaction_id=txn.id, account_id=chair_acc.id, debit=Decimal("0.00"), credit=base_price))
        if addon_amount > 0:
            db.add(TransactionLine(transaction_id=txn.id, account_id=addon_acc.id, debit=Decimal("0.00"), credit=addon_amount))

        db.add(AuditLog(
            username=user.username,
            action=f"Recorded sale via Staff screen: {ref} - {' '.join(remarks_bits)} - Net ₱{net_cash:,.2f}"
                   + (" [synced from offline entry]" if occurred_dt and (datetime.datetime.utcnow() - occurred_dt).total_seconds() > 120 else ""),
            module="TRANSACTIONS", reference=ref,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        detail = f" (Technical detail for admin: {type(e).__name__}: {e})" if user.role == "ADMIN" else ""
        return render_error(f"Unable to save this sale.{detail}")

    return RedirectResponse(f"/staff/sale?saved_ref={ref}", status_code=303)


@router.post("/sale/api")
async def submit_sale_api(request: Request, db: Session = Depends(get_db)):
    """
    JSON endpoint used by the offline sync queue (see the service worker / sync
    script). Accepts the same fields as the normal form submission, but returns
    JSON instead of a redirect, since this is called from background JavaScript
    rather than a real page navigation. Shares all the same validation and
    idempotency logic by delegating to submit_sale().
    """
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "error": "Not logged in."}, status_code=401)

    body = await request.json()
    result = submit_sale(
        request=request, db=db,
        transaction_date=body.get("transaction_date", ""),
        chair_type=body.get("chair_type", ""),
        duration_minutes=int(body.get("duration_minutes", 0) or 0),
        eye_massager=body.get("eye_massager", "0"),
        discount_type=body.get("discount_type", "NONE"),
        pwd_senior_id=body.get("pwd_senior_id", ""),
        promo_percent=body.get("promo_percent", ""),
        client_token=body.get("client_token", ""),
        occurred_at=body.get("occurred_at", ""),
    )
    # submit_sale() returns a RedirectResponse on success or a TemplateResponse on error.
    # Translate both into a plain JSON result for the JS sync code to interpret.
    if isinstance(result, RedirectResponse):
        ref = result.headers["location"].split("saved_ref=")[-1]
        return JSONResponse({"ok": True, "reference_number": ref})
    else:
        return JSONResponse({"ok": False, "error": "This sale could not be saved. It will remain queued and retry automatically."}, status_code=400)
