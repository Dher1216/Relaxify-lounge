import os
import datetime
from decimal import Decimal, InvalidOperation
from ..template_env import templates
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..auth import get_current_user, can
from ..models import SockEntry, Transaction, AuditLog

router = APIRouter()


def _parse_quantity(raw: str):
    """Pairs are tracked to the nearest half-pair (a single damaged sock = 0.5),
    never finer than that - a sock inventory in quarters wouldn't mean anything.
    Returns (Decimal, error_message). error_message is None if valid."""
    try:
        q = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None, "Please enter a valid number."
    q = q.quantize(Decimal("0.1"))
    if (q * 2) % 1 != 0:
        return None, "Quantity must be in whole or half pairs (e.g. 1, 1.5, 2)."
    return q, None


# ---------------- Admin: stock management ----------------

@router.get("/inventory/socks")
def socks_home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can(user, "sock_manage"):
        return RedirectResponse("/", status_code=303)

    current_stock = _current_stock(db)
    recent = (
        db.query(SockEntry)
        .filter(SockEntry.is_cancelled == False)  # noqa: E712
        .order_by(SockEntry.occurred_at.desc())
        .limit(30)
        .all()
    )
    return templates.TemplateResponse("socks.html", {
        "request": request, "user": user, "current_stock": current_stock, "recent": recent, "error": None,
    })


def _current_stock(db: Session) -> int:
    """Running stock level right now: every RECEIVE adds, every ADJUST and every
    staff-recorded sale (any status - a voided sale still used a physical pair)
    subtracts. COUNT entries are checkpoints for reconciliation, not part of this
    running total, since the whole point of counting is to check this number
    against reality, not to let a count silently redefine it.

    Sales only count from the moment this feature was first actually used (the
    earliest sock_entries row of any kind) - not retroactively for the business's
    entire sales history before anyone ever logged a single pair. Otherwise the
    very first time this page is opened, it charges every historical sale against
    a stock of zero and shows a confusing, meaningless negative number."""
    tracking_start = (
        db.query(func.min(SockEntry.occurred_at))
        .filter(SockEntry.is_cancelled == False)  # noqa: E712
        .scalar()
    )
    received = db.query(SockEntry).filter(SockEntry.entry_type == "RECEIVE", SockEntry.is_cancelled == False).all()  # noqa: E712
    adjusted = db.query(SockEntry).filter(SockEntry.entry_type == "ADJUST", SockEntry.is_cancelled == False).all()  # noqa: E712
    sales_query = db.query(Transaction).filter(Transaction.chair_type.isnot(None))
    if tracking_start:
        sales_query = sales_query.filter(Transaction.occurred_at >= tracking_start)
    else:
        # No sock entries logged at all yet - nothing to charge against, since
        # tracking hasn't started.
        sales_query = sales_query.filter(Transaction.id.is_(None))
    sales_count = sales_query.count()
    return sum(r.quantity for r in received) - sum(a.quantity for a in adjusted) - sales_count


@router.post("/inventory/socks/receive")
def receive_stock(
    request: Request, db: Session = Depends(get_db),
    quantity: str = Form(...), occurred_at: str = Form(""), notes: str = Form(""),
):
    user = get_current_user(request, db)
    if not user or not can(user, "sock_manage"):
        return RedirectResponse("/inventory/socks", status_code=303)
    quantity, err = _parse_quantity(quantity)
    if err:
        return _render_error(request, db, user, err)
    if quantity <= 0:
        return _render_error(request, db, user, "Quantity received must be a positive number.")

    when = _parse_occurred_at(occurred_at)
    db.add(SockEntry(entry_type="RECEIVE", quantity=quantity, reason=notes.strip() or None, occurred_at=when, entered_by=user.username))
    db.add(AuditLog(username=user.username, action=f"Received {quantity} pairs of foot socks" + (f" ({notes.strip()})" if notes.strip() else ""), module="INVENTORY"))
    db.commit()
    return RedirectResponse("/inventory/socks", status_code=303)


@router.post("/inventory/socks/adjust")
def adjust_stock(
    request: Request, db: Session = Depends(get_db),
    quantity: str = Form(...), reason: str = Form(...), occurred_at: str = Form(""),
):
    user = get_current_user(request, db)
    if not user or not can(user, "sock_manage"):
        return RedirectResponse("/inventory/socks", status_code=303)
    quantity, err = _parse_quantity(quantity)
    if err:
        return _render_error(request, db, user, err)
    if quantity <= 0:
        return _render_error(request, db, user, "Adjustment quantity must be a positive number.")
    if not reason.strip():
        return _render_error(request, db, user, "A reason is required for every adjustment.")

    when = _parse_occurred_at(occurred_at)
    db.add(SockEntry(entry_type="ADJUST", quantity=quantity, reason=reason.strip(), occurred_at=when, entered_by=user.username))
    db.add(AuditLog(username=user.username, action=f"Adjusted out {quantity} pairs of foot socks - reason: {reason.strip()}", module="INVENTORY"))
    db.commit()
    return RedirectResponse("/inventory/socks", status_code=303)


@router.post("/inventory/socks/{entry_id}/cancel")
def cancel_entry(entry_id: int, request: Request, db: Session = Depends(get_db), cancel_reason: str = Form("")):
    user = get_current_user(request, db)
    if not user or not can(user, "sock_manage"):
        return RedirectResponse("/inventory/socks", status_code=303)
    entry = db.query(SockEntry).filter(SockEntry.id == entry_id).first()
    if entry and not entry.is_cancelled:
        entry.is_cancelled = True
        entry.cancelled_by = user.username
        entry.cancel_reason = cancel_reason.strip() or None
        db.add(AuditLog(username=user.username, action=f"Cancelled sock entry #{entry.id} ({entry.entry_type}, qty {entry.quantity})", module="INVENTORY"))
        db.commit()
    return RedirectResponse("/inventory/socks", status_code=303)


# ---------------- Staff/anyone: blind shift-end count ----------------

@router.get("/staff/sock-count")
def sock_count_form(request: Request, db: Session = Depends(get_db), saved: int = 0):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    # Deliberately does NOT show current stock or expected count - a blind count
    # only means something if the person entering it can't just copy the expected number.
    return templates.TemplateResponse("sock_count.html", {
        "request": request, "user": user, "error": None, "saved": saved,
    })


@router.post("/staff/sock-count")
def submit_sock_count(
    request: Request, db: Session = Depends(get_db),
    quantity: str = Form(...), outgoing_staff: str = Form(...), client_token: str = Form(""),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    if client_token:
        existing = db.query(SockEntry).filter(SockEntry.client_token == client_token).first()
        if existing:
            return RedirectResponse("/staff/sock-count?saved=1", status_code=303)

    quantity, err = _parse_quantity(quantity)
    if err:
        return templates.TemplateResponse("sock_count.html", {
            "request": request, "user": user, "error": err, "saved": 0,
        })
    if quantity < 0:
        return templates.TemplateResponse("sock_count.html", {
            "request": request, "user": user, "error": "Count can't be negative.", "saved": 0,
        })
    if not outgoing_staff.strip():
        return templates.TemplateResponse("sock_count.html", {
            "request": request, "user": user, "error": "Please enter who was working this shift.", "saved": 0,
        })

    db.add(SockEntry(
        entry_type="COUNT", quantity=quantity, occurred_at=datetime.datetime.utcnow(),
        entered_by=user.username, outgoing_staff=outgoing_staff.strip(), client_token=client_token or None,
    ))
    db.add(AuditLog(username=user.username, action=f"Logged shift-end sock count: {quantity} pairs (outgoing: {outgoing_staff.strip()})", module="INVENTORY"))
    db.commit()
    return RedirectResponse("/staff/sock-count?saved=1", status_code=303)


# ---------------- Reconciliation report ----------------

@router.get("/inventory/socks/reconciliation")
def reconciliation_report(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can(user, "sock_manage"):
        return RedirectResponse("/", status_code=303)

    shifts = _build_shift_reconciliation(db)
    return templates.TemplateResponse("sock_reconciliation.html", {
        "request": request, "user": user, "shifts": shifts,
    })


def _build_shift_reconciliation(db: Session):
    all_entries = (
        db.query(SockEntry)
        .filter(SockEntry.is_cancelled == False)  # noqa: E712
        .order_by(SockEntry.occurred_at)
        .all()
    )
    counts = [e for e in all_entries if e.entry_type == "COUNT"]
    if not counts:
        return []

    shifts = []
    window_start = datetime.datetime(1970, 1, 1)
    opening = None

    for count in counts:
        window_end = count.occurred_at
        received = sum(e.quantity for e in all_entries if e.entry_type == "RECEIVE" and window_start < e.occurred_at <= window_end)
        adjusted = sum(e.quantity for e in all_entries if e.entry_type == "ADJUST" and window_start < e.occurred_at <= window_end)

        shift_sales = (
            db.query(Transaction)
            .filter(Transaction.chair_type.isnot(None))
            .filter(Transaction.occurred_at > window_start, Transaction.occurred_at <= window_end)
            .all()
        )
        total_sales = len(shift_sales)
        voided_sales = sum(1 for t in shift_sales if t.status == "VOIDED")

        if opening is None:
            # First-ever count: nothing to reconcile against yet, just establishes the baseline.
            shifts.append({
                "occurred_at": window_end, "outgoing_staff": count.outgoing_staff,
                "opening": None, "received": received, "adjusted": adjusted,
                "total_sales": total_sales, "voided_sales": voided_sales,
                "expected": None, "actual": count.quantity, "variance": None,
                "entered_by": count.entered_by,
            })
        else:
            expected = opening + received - adjusted - total_sales
            variance = count.quantity - expected
            shifts.append({
                "occurred_at": window_end, "outgoing_staff": count.outgoing_staff,
                "opening": opening, "received": received, "adjusted": adjusted,
                "total_sales": total_sales, "voided_sales": voided_sales,
                "expected": expected, "actual": count.quantity, "variance": variance,
                "entered_by": count.entered_by,
            })

        opening = count.quantity
        window_start = window_end

    shifts.reverse()  # most recent first
    return shifts


def _parse_occurred_at(value: str) -> datetime.datetime:
    if value:
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.datetime.utcnow()


def _render_error(request, db, user, msg):
    current_stock = _current_stock(db)
    recent = db.query(SockEntry).filter(SockEntry.is_cancelled == False).order_by(SockEntry.occurred_at.desc()).limit(30).all()  # noqa: E712
    return templates.TemplateResponse("socks.html", {
        "request": request, "user": user, "current_stock": current_stock, "recent": recent, "error": msg,
    })
