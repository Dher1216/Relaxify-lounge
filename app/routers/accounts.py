import os
from ..template_env import templates
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user, can
from ..models import Account, TransactionLine, AuditLog

router = APIRouter(prefix="/accounts")

ACCOUNT_TYPES = ["Asset", "Liability", "Equity", "Revenue", "COGS", "Expense"]


@router.get("")
def list_accounts(request: Request, db: Session = Depends(get_db), q: str = None):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    query = db.query(Account).order_by(Account.code)
    if q:
        query = query.filter((Account.code.ilike(f"%{q}%")) | (Account.name.ilike(f"%{q}%")))
    accounts = query.all()
    return templates.TemplateResponse("accounts.html", {
        "request": request, "user": user, "accounts": accounts, "q": q or "",
        "account_types": ACCOUNT_TYPES, "can_edit": can(user, "accounts"), "error": None,
    })


@router.post("/add")
def add_account(
    request: Request, db: Session = Depends(get_db),
    code: str = Form(...), name: str = Form(...), account_type: str = Form(...),
    category: str = Form(""), normal_balance: str = Form(...), notes: str = Form(""),
):
    user = get_current_user(request, db)
    if not user or not can(user, "accounts"):
        return RedirectResponse("/accounts", status_code=303)

    code = code.strip()
    if not code or not name.strip():
        return _render_error(request, db, user, "Account code and name are required.")
    if db.query(Account).filter(Account.code == code).first():
        return _render_error(request, db, user, f"Account code {code} already exists.")

    db.add(Account(
        code=code, name=name.strip(), account_type=account_type,
        category=category.strip() or None, normal_balance=normal_balance,
        notes=notes.strip() or None, is_active=True,
    ))
    db.add(AuditLog(username=user.username, action=f"Added account {code} - {name}", module="ACCOUNTS", reference=code))
    db.commit()
    return RedirectResponse("/accounts", status_code=303)


@router.post("/{account_id}/edit")
def edit_account(
    account_id: int, request: Request, db: Session = Depends(get_db),
    code: str = Form(...), name: str = Form(...), account_type: str = Form(...),
    category: str = Form(""), normal_balance: str = Form(...), notes: str = Form(""),
):
    user = get_current_user(request, db)
    if not user or not can(user, "accounts"):
        return RedirectResponse("/accounts", status_code=303)
    acc = db.query(Account).filter(Account.id == account_id).first()
    if not acc:
        return RedirectResponse("/accounts", status_code=303)

    code = code.strip()
    if not code or not name.strip():
        return _render_error(request, db, user, "Account code and name are required.")

    duplicate = db.query(Account).filter(Account.code == code, Account.id != account_id).first()
    if duplicate:
        return _render_error(request, db, user, f"Account code {code} is already used by another account.")

    old = f"{acc.code} - {acc.name} ({acc.account_type})"
    acc.code = code
    acc.name = name.strip()
    acc.account_type = account_type
    acc.category = category.strip() or None
    acc.normal_balance = normal_balance
    acc.notes = notes.strip() or None
    db.add(AuditLog(
        username=user.username,
        action=f"Edited account: '{old}' -> '{acc.code} - {acc.name} ({acc.account_type})'",
        module="ACCOUNTS", reference=acc.code,
    ))
    db.commit()
    return RedirectResponse("/accounts", status_code=303)


@router.post("/{account_id}/toggle")
def toggle_account(account_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not can(user, "accounts"):
        return RedirectResponse("/accounts", status_code=303)
    acc = db.query(Account).filter(Account.id == account_id).first()
    if acc:
        has_txns = db.query(TransactionLine).filter(TransactionLine.account_id == acc.id).first() is not None
        acc.is_active = not acc.is_active
        db.add(AuditLog(
            username=user.username,
            action=f"{'Activated' if acc.is_active else 'Deactivated'} account {acc.code} - {acc.name}"
                   + (" (has existing transactions, not deleted)" if has_txns else ""),
            module="ACCOUNTS", reference=acc.code,
        ))
        db.commit()
    return RedirectResponse("/accounts", status_code=303)


def _render_error(request, db, user, msg):
    accounts = db.query(Account).order_by(Account.code).all()
    return templates.TemplateResponse("accounts.html", {
        "request": request, "user": user, "accounts": accounts, "q": "",
        "account_types": ACCOUNT_TYPES, "can_edit": can(user, "accounts"), "error": msg,
    })
