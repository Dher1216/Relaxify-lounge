import os
from ..template_env import templates
import datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user, can
from ..models import Transaction, TransactionLine, Account, AuditLog
from ..accounting import next_reference_number, validate_lines, to_decimal

router = APIRouter(prefix="/transactions")

TYPE_LABELS = {"RECEIPT": "Receipt", "DISBURSEMENT": "Disbursement"}


@router.get("/new/{ttype}")
def new_form(ttype: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ttype = ttype.upper()
    if ttype not in TYPE_LABELS:
        return RedirectResponse("/", status_code=303)

    accounts = db.query(Account).filter(Account.is_active == True).order_by(Account.code).all()  # noqa: E712
    return templates.TemplateResponse("transaction_form.html", {
        "request": request, "user": user, "ttype": ttype, "edit_mode": False,
        "action_url": f"/transactions/new/{ttype}",
        "ttype_label": TYPE_LABELS[ttype], "accounts": accounts,
        "today": datetime.date.today().isoformat(), "max_date": datetime.date.today().isoformat(), "error": None, "form": None,
    })


@router.post("/new/{ttype}")
async def create_transaction(
    ttype: str,
    request: Request,
    db: Session = Depends(get_db),
    transaction_date: str = Form(...),
    remarks: str = Form(""),
    confirm_no_remarks: str = Form("0"),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ttype = ttype.upper()

    form_data = await request.form()
    account_ids = form_data.getlist("account_id[]")
    debits = form_data.getlist("debit[]")
    credits = form_data.getlist("credit[]")

    accounts = db.query(Account).filter(Account.is_active == True).order_by(Account.code).all()  # noqa: E712

    def render_error(msg):
        return templates.TemplateResponse("transaction_form.html", {
            "request": request, "user": user, "ttype": ttype, "edit_mode": False,
            "action_url": f"/transactions/new/{ttype}",
            "ttype_label": TYPE_LABELS[ttype], "accounts": accounts,
            "today": transaction_date, "max_date": datetime.date.today().isoformat(), "error": msg,
            "form": {"transaction_date": transaction_date, "remarks": remarks, "account_ids": account_ids, "debits": debits, "credits": credits},
        })

    try:
        t_date = datetime.date.fromisoformat(transaction_date)
    except ValueError:
        return render_error("Please select a valid transaction date.")

    if t_date > datetime.date.today():
        return render_error("Transaction date cannot be in the future.")

    line_inputs = [
        {"account_id": aid or None, "debit": d, "credit": c}
        for aid, d, c in zip(account_ids, debits, credits)
    ]
    total_debit, total_credit, errors = validate_lines(line_inputs)
    if errors:
        return render_error(" ".join(errors))

    if not remarks.strip() and confirm_no_remarks != "1":
        return templates.TemplateResponse("transaction_form.html", {
            "request": request, "user": user, "ttype": ttype, "edit_mode": False,
            "action_url": f"/transactions/new/{ttype}",
            "ttype_label": TYPE_LABELS[ttype], "accounts": accounts,
            "today": transaction_date, "max_date": datetime.date.today().isoformat(), "error": None, "confirm_remarks": True,
            "form": {"transaction_date": transaction_date, "remarks": remarks, "account_ids": account_ids, "debits": debits, "credits": credits},
        })

    # Save atomically
    try:
        ref = next_reference_number(db, ttype, t_date)
        txn = Transaction(
            reference_number=ref, transaction_date=t_date, transaction_type=ttype,
            remarks=remarks.strip() or None, status="ACTIVE", created_by=user.username,
        )
        db.add(txn)
        db.flush()

        for line in line_inputs:
            if not line["account_id"]:
                continue
            debit = to_decimal(line["debit"] or 0)
            credit = to_decimal(line["credit"] or 0)
            if debit == 0 and credit == 0:
                continue
            db.add(TransactionLine(
                transaction_id=txn.id, account_id=int(line["account_id"]),
                debit=debit, credit=credit,
            ))

        db.add(AuditLog(
            username=user.username,
            action=f"Added {TYPE_LABELS[ttype].lower()} {ref} (₱{total_debit:,.2f})"
                   + ("" if remarks.strip() else " [no remarks provided]"),
            module="TRANSACTIONS", reference=ref,
        ))
        db.commit()
    except Exception:
        db.rollback()
        return render_error("Unable to save transaction. No changes were recorded.")

    return RedirectResponse(f"/transactions/{txn.id}?saved=1", status_code=303)


@router.get("")
def list_transactions(
    request: Request, db: Session = Depends(get_db),
    date_from: str = None, date_to: str = None, ttype: str = None,
    status: str = None, q: str = None, page: int = 1,
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    query = db.query(Transaction)
    if date_from:
        query = query.filter(Transaction.transaction_date >= datetime.date.fromisoformat(date_from))
    if date_to:
        query = query.filter(Transaction.transaction_date <= datetime.date.fromisoformat(date_to))
    if ttype:
        query = query.filter(Transaction.transaction_type == ttype)
    if status:
        query = query.filter(Transaction.status == status)
    if q:
        query = query.filter(Transaction.reference_number.ilike(f"%{q}%"))

    page_size = 25
    total = query.count()
    txns = (
        query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )

    return templates.TemplateResponse("transactions_list.html", {
        "request": request, "user": user, "txns": txns, "total": total,
        "page": page, "page_size": page_size,
        "filters": {"date_from": date_from, "date_to": date_to, "ttype": ttype, "status": status, "q": q},
    })


@router.get("/{txn_id}")
def view_transaction(txn_id: int, request: Request, db: Session = Depends(get_db), saved: int = 0):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        return RedirectResponse("/transactions", status_code=303)
    total_debit = sum((l.debit for l in txn.lines), start=txn.lines[0].debit * 0) if txn.lines else 0
    total_credit = sum((l.credit for l in txn.lines), start=txn.lines[0].credit * 0) if txn.lines else 0
    return templates.TemplateResponse("transaction_detail.html", {
        "request": request, "user": user, "txn": txn,
        "total_debit": total_debit, "total_credit": total_credit, "saved": saved,
        "can_edit": can(user, "edit_posted_transactions"),
    })


@router.get("/{txn_id}/edit")
def edit_form(txn_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        return RedirectResponse("/transactions", status_code=303)
    if not can(user, "edit_posted_transactions"):
        return RedirectResponse(f"/transactions/{txn_id}", status_code=303)
    if txn.status != "ACTIVE":
        return RedirectResponse(f"/transactions/{txn_id}", status_code=303)

    accounts = db.query(Account).filter(Account.is_active == True).order_by(Account.code).all()  # noqa: E712
    form = {
        "transaction_date": txn.transaction_date.isoformat(),
        "remarks": txn.remarks or "",
        "account_ids": [str(l.account_id) for l in txn.lines],
        "debits": [str(l.debit) if l.debit else "" for l in txn.lines],
        "credits": [str(l.credit) if l.credit else "" for l in txn.lines],
    }
    return templates.TemplateResponse("transaction_form.html", {
        "request": request, "user": user, "ttype": txn.transaction_type, "edit_mode": True,
        "action_url": f"/transactions/{txn_id}/edit", "txn_id": txn_id,
        "reference_number": txn.reference_number,
        "ttype_label": TYPE_LABELS[txn.transaction_type], "accounts": accounts,
        "today": txn.transaction_date.isoformat(), "max_date": datetime.date.today().isoformat(),
        "error": None, "form": form,
    })


@router.post("/{txn_id}/edit")
async def edit_transaction(
    txn_id: int,
    request: Request,
    db: Session = Depends(get_db),
    transaction_date: str = Form(...),
    remarks: str = Form(""),
    confirm_no_remarks: str = Form("0"),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        return RedirectResponse("/transactions", status_code=303)
    if not can(user, "edit_posted_transactions") or txn.status != "ACTIVE":
        return RedirectResponse(f"/transactions/{txn_id}", status_code=303)

    form_data = await request.form()
    account_ids = form_data.getlist("account_id[]")
    debits = form_data.getlist("debit[]")
    credits = form_data.getlist("credit[]")

    accounts = db.query(Account).filter(Account.is_active == True).order_by(Account.code).all()  # noqa: E712

    def render_error(msg, confirm_remarks=False):
        return templates.TemplateResponse("transaction_form.html", {
            "request": request, "user": user, "ttype": txn.transaction_type, "edit_mode": True,
            "action_url": f"/transactions/{txn_id}/edit", "txn_id": txn_id,
            "reference_number": txn.reference_number,
            "ttype_label": TYPE_LABELS[txn.transaction_type], "accounts": accounts,
            "today": transaction_date, "max_date": datetime.date.today().isoformat(),
            "error": None if confirm_remarks else msg, "confirm_remarks": confirm_remarks,
            "form": {"transaction_date": transaction_date, "remarks": remarks, "account_ids": account_ids, "debits": debits, "credits": credits},
        })

    try:
        t_date = datetime.date.fromisoformat(transaction_date)
    except ValueError:
        return render_error("Please select a valid transaction date.")
    if t_date > datetime.date.today():
        return render_error("Transaction date cannot be in the future.")

    line_inputs = [
        {"account_id": aid or None, "debit": d, "credit": c}
        for aid, d, c in zip(account_ids, debits, credits)
    ]
    total_debit, total_credit, errors = validate_lines(line_inputs)
    if errors:
        return render_error(" ".join(errors))

    if not remarks.strip() and confirm_no_remarks != "1":
        return render_error(None, confirm_remarks=True)

    # Capture "before" snapshot for the audit trail before we touch anything
    before_lines = [f"{l.account.code} Dr {l.debit} / Cr {l.credit}" for l in txn.lines]
    before_summary = f"date={txn.transaction_date}, remarks='{txn.remarks or ''}', lines=[{'; '.join(before_lines)}]"

    try:
        txn.transaction_date = t_date
        txn.remarks = remarks.strip() or None
        txn.updated_by = user.username
        txn.updated_at = datetime.datetime.utcnow()

        # Replace all lines atomically. We go through the ORM relationship (txn.lines)
        # rather than a raw bulk-delete query, so SQLAlchemy's session stays in sync with
        # what's actually in the database - a raw bulk delete can desync the session and
        # fail on stricter backends like Postgres even though it may appear to work on SQLite.
        txn.lines.clear()
        db.flush()
        for line in line_inputs:
            if not line["account_id"]:
                continue
            debit = to_decimal(line["debit"] or 0)
            credit = to_decimal(line["credit"] or 0)
            if debit == 0 and credit == 0:
                continue
            txn.lines.append(TransactionLine(
                account_id=int(line["account_id"]), debit=debit, credit=credit,
            ))
        db.flush()

        after_lines = [
            f"{next(a.code for a in accounts if a.id == int(l['account_id']))} Dr {l['debit'] or 0} / Cr {l['credit'] or 0}"
            for l in line_inputs if l["account_id"]
        ]
        after_summary = f"date={t_date}, remarks='{remarks.strip()}', lines=[{'; '.join(after_lines)}]"

        db.add(AuditLog(
            username=user.username,
            action=f"Edited transaction {txn.reference_number}. BEFORE: {before_summary} | AFTER: {after_summary}",
            module="TRANSACTIONS", reference=txn.reference_number,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        # Log the real cause to the audit trail (ADMIN-visible) so failures are diagnosable,
        # while still showing everyday users a clean, non-technical message.
        try:
            db.add(AuditLog(
                username=user.username,
                action=f"FAILED to edit transaction {txn.reference_number}: {type(e).__name__}: {e}",
                module="SYSTEM_ERROR", reference=txn.reference_number,
            ))
            db.commit()
        except Exception:
            db.rollback()
        detail = f" (Technical detail for admin: {type(e).__name__}: {e})" if user.role == "ADMIN" else ""
        return render_error(f"Unable to save changes. No changes were recorded.{detail}")

    return RedirectResponse(f"/transactions/{txn_id}?saved=1", status_code=303)


@router.post("/{txn_id}/void")
def void_transaction(txn_id: int, request: Request, db: Session = Depends(get_db), reason: str = Form("")):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can(user, "transactions"):
        return RedirectResponse(f"/transactions/{txn_id}", status_code=303)

    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if txn and txn.status == "ACTIVE":
        txn.status = "VOIDED"
        txn.void_reason = reason or None
        txn.updated_by = user.username
        txn.updated_at = datetime.datetime.utcnow()
        db.add(AuditLog(
            username=user.username,
            action=f"Voided transaction {txn.reference_number}" + (f" (reason: {reason})" if reason else ""),
            module="TRANSACTIONS", reference=txn.reference_number,
        ))
        db.commit()
    return RedirectResponse(f"/transactions/{txn_id}", status_code=303)
