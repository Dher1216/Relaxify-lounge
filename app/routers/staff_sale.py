import os
import datetime
from decimal import Decimal
from ..template_env import templates
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
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


@router.get("/sale")
def sale_form(request: Request, db: Session = Depends(get_db), saved_ref: str = None):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    from ..accounting import current_chair_rates
    rate_map = current_chair_rates(db, datetime.date.today())
    settings = _get_settings(db)

    return templates.TemplateResponse("staff_sale.html", {
        "request": request, "user": user, "rate_map": rate_map, "settings": settings,
        "today": datetime.date.today().isoformat(), "error": None, "saved_ref": saved_ref,
    })


@router.post("/sale")
def submit_sale(
    request: Request, db: Session = Depends(get_db),
    transaction_date: str = Form(...), chair_type: str = Form(...), duration_minutes: int = Form(...),
    eye_massager: str = Form("0"), discount_type: str = Form("NONE"),
    pwd_senior_id: str = Form(""), promo_percent: str = Form(""),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    def render_error(msg):
        from ..accounting import current_chair_rates
        rate_map = current_chair_rates(db, datetime.date.today())
        return templates.TemplateResponse("staff_sale.html", {
            "request": request, "user": user, "rate_map": rate_map, "settings": _get_settings(db),
            "today": transaction_date, "error": msg, "saved_ref": None,
        })

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

    try:
        ref = next_reference_number(db, "RECEIPT", t_date)
        txn = Transaction(
            reference_number=ref, transaction_date=t_date, transaction_type="RECEIPT",
            remarks=" ".join(remarks_bits), status="ACTIVE", created_by=user.username,
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
            action=f"Recorded sale via Staff screen: {ref} - {' '.join(remarks_bits)} - Net ₱{net_cash:,.2f}",
            module="TRANSACTIONS", reference=ref,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        detail = f" (Technical detail for admin: {type(e).__name__}: {e})" if user.role == "ADMIN" else ""
        return render_error(f"Unable to save this sale.{detail}")

    return RedirectResponse(f"/staff/sale?saved_ref={ref}", status_code=303)
