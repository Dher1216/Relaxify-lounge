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


def _parent_options(db: Session, exclude_id: int = None):
    """Accounts eligible to be picked as a parent: active, and not themselves a child
    (keeps the hierarchy to 2 levels), and not the account being edited itself."""
    q = db.query(Account).filter(Account.is_active == True, Account.parent_id.is_(None))  # noqa: E712
    if exclude_id:
        q = q.filter(Account.id != exclude_id)
    return q.order_by(Account.code).all()


@router.get("")
def list_accounts(request: Request, db: Session = Depends(get_db), q: str = None):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    query = db.query(Account).order_by(Account.code)
    if q:
        query = query.filter((Account.code.ilike(f"%{q}%")) | (Account.name.ilike(f"%{q}%")))
    all_accounts = query.all()

    # Order for display: top-level accounts, each immediately followed by its own children
    top_level = [a for a in all_accounts if not a.parent_id]
    by_parent = {}
    for a in all_accounts:
        if a.parent_id:
            by_parent.setdefault(a.parent_id, []).append(a)
    ordered = []
    for a in top_level:
        ordered.append((a, 0))
        for child in sorted(by_parent.get(a.id, []), key=lambda x: x.code):
            ordered.append((child, 1))
    # Any children whose parent got filtered out by search - show them anyway, unindented
    shown_ids = {a.id for a, _ in ordered}
    for a in all_accounts:
        if a.id not in shown_ids:
            ordered.append((a, 0))

    return templates.TemplateResponse("accounts.html", {
        "request": request, "user": user, "ordered_accounts": ordered, "q": q or "",
        "account_types": ACCOUNT_TYPES, "can_edit": can(user, "accounts"), "error": None,
        "parent_options": _parent_options(db),
    })


@router.post("/add")
def add_account(
    request: Request, db: Session = Depends(get_db),
    code: str = Form(...), name: str = Form(...), account_type: str = Form(...),
    category: str = Form(""), normal_balance: str = Form(...), notes: str = Form(""),
    parent_id: str = Form(""),
):
    user = get_current_user(request, db)
    if not user or not can(user, "accounts"):
        return RedirectResponse("/accounts", status_code=303)

    code = code.strip()
    if not code or not name.strip():
        return _render_error(request, db, user, "Account code and name are required.")
    if db.query(Account).filter(Account.code == code).first():
        return _render_error(request, db, user, f"Account code {code} already exists.")

    parent = None
    if parent_id:
        parent = db.query(Account).filter(Account.id == int(parent_id)).first()
        if not parent:
            return _render_error(request, db, user, "Selected parent account was not found.")
        if parent.parent_id:
            return _render_error(request, db, user, "That account is already a sub-account and can't have its own sub-accounts (max 2 levels).")

    db.add(Account(
        code=code, name=name.strip(), account_type=account_type,
        category=category.strip() or None, normal_balance=normal_balance,
        notes=notes.strip() or None, is_active=True,
        parent_id=parent.id if parent else None,
    ))
    label = f"Added account {code} - {name}" + (f" under parent {parent.code} - {parent.name}" if parent else "")
    db.add(AuditLog(username=user.username, action=label, module="ACCOUNTS", reference=code))
    db.commit()
    return RedirectResponse("/accounts", status_code=303)


@router.post("/{account_id}/edit")
def edit_account(
    account_id: int, request: Request, db: Session = Depends(get_db),
    code: str = Form(...), name: str = Form(...), account_type: str = Form(...),
    category: str = Form(""), normal_balance: str = Form(...), notes: str = Form(""),
    parent_id: str = Form(""),
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

    new_parent = None
    if parent_id:
        if int(parent_id) == account_id:
            return _render_error(request, db, user, "An account can't be its own parent.")
        new_parent = db.query(Account).filter(Account.id == int(parent_id)).first()
        if not new_parent:
            return _render_error(request, db, user, "Selected parent account was not found.")
        if new_parent.parent_id:
            return _render_error(request, db, user, "That account is already a sub-account and can't have its own sub-accounts (max 2 levels).")
    # An account that itself already has children can't also become someone's child
    has_children = db.query(Account).filter(Account.parent_id == account_id).first() is not None
    if new_parent and has_children:
        return _render_error(request, db, user, "This account already has sub-accounts of its own, so it can't be made a sub-account (max 2 levels).")

    old = f"{acc.code} - {acc.name} ({acc.account_type})"
    acc.code = code
    acc.name = name.strip()
    acc.account_type = account_type
    acc.category = category.strip() or None
    acc.normal_balance = normal_balance
    acc.notes = notes.strip() or None
    acc.parent_id = new_parent.id if new_parent else None
    db.add(AuditLog(
        username=user.username,
        action=f"Edited account: '{old}' -> '{acc.code} - {acc.name} ({acc.account_type})'"
               + (f", parent set to {new_parent.code}" if new_parent else ""),
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
        if acc.is_active:
            active_children = db.query(Account).filter(Account.parent_id == acc.id, Account.is_active == True).first()  # noqa: E712
            if active_children:
                return _render_error(request, db, user, f"Can't deactivate {acc.code} - it still has active sub-accounts. Deactivate those first.")
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
    all_accounts = db.query(Account).order_by(Account.code).all()
    top_level = [a for a in all_accounts if not a.parent_id]
    by_parent = {}
    for a in all_accounts:
        if a.parent_id:
            by_parent.setdefault(a.parent_id, []).append(a)
    ordered = []
    for a in top_level:
        ordered.append((a, 0))
        for child in sorted(by_parent.get(a.id, []), key=lambda x: x.code):
            ordered.append((child, 1))
    return templates.TemplateResponse("accounts.html", {
        "request": request, "user": user, "ordered_accounts": ordered, "q": "",
        "account_types": ACCOUNT_TYPES, "can_edit": can(user, "accounts"), "error": msg,
        "parent_options": _parent_options(db),
    })
