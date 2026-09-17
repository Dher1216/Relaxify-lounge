import os
from ..template_env import templates
import datetime
from decimal import Decimal
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..auth import get_current_user
from ..models import Transaction, TransactionLine, Account

router = APIRouter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    today = datetime.date.today()
    month_start = today.replace(day=1)

    todays_txns = db.query(Transaction).filter(
        Transaction.transaction_date == today, Transaction.status == "ACTIVE"
    ).all()
    todays_receipts = [t for t in todays_txns if t.transaction_type == "RECEIPT"]
    todays_disbursements = [t for t in todays_txns if t.transaction_type == "DISBURSEMENT"]

    def cash_movement_for(txns):
        """Net cash movement (debit minus credit) across the cash lines of these
        transactions. Using the NET, not summing debit+credit separately, matters
        specifically for any transaction that touches two cash accounts at once
        (e.g. a transfer between Cash on Hand and Cash in Bank) - summing both
        sides separately would double-count a movement that's actually zero-sum
        from the business's perspective."""
        if not txns:
            return Decimal("0.00")
        txn_ids = [t.id for t in txns]
        net = (
            db.query(func.coalesce(func.sum(TransactionLine.debit - TransactionLine.credit), 0))
            .join(Account, Account.id == TransactionLine.account_id)
            .filter(TransactionLine.transaction_id.in_(txn_ids))
            .filter(Account.name.ilike("%cash%"))
            .scalar()
        )
        return abs(Decimal(net))

    todays_receipts_cash = cash_movement_for(todays_receipts)
    todays_disbursements_cash = cash_movement_for(todays_disbursements)

    def sum_type_for_period(account_type, start, end):
        total = Decimal("0.00")
        accounts = db.query(Account).filter(Account.account_type == account_type, Account.is_active == True).all()  # noqa: E712
        for acc in accounts:
            q = (
                db.query(
                    func.coalesce(func.sum(TransactionLine.debit), 0),
                    func.coalesce(func.sum(TransactionLine.credit), 0),
                )
                .join(Transaction, Transaction.id == TransactionLine.transaction_id)
                .filter(TransactionLine.account_id == acc.id)
                .filter(Transaction.status == "ACTIVE")
                .filter(Transaction.transaction_date >= start, Transaction.transaction_date <= end)
            )
            d, c = q.first()
            d, c = Decimal(d), Decimal(c)
            total += (c - d) if acc.normal_balance == "Credit" else (d - c)
        return total

    month_revenue = sum_type_for_period("Revenue", month_start, today)
    month_expenses = sum_type_for_period("Expense", month_start, today) + sum_type_for_period("COGS", month_start, today)
    month_net_income = month_revenue - month_expenses

    cash_accounts = db.query(Account).filter(Account.is_active == True, Account.name.ilike("%cash%")).all()  # noqa: E712
    cash_balance = Decimal("0.00")
    for acc in cash_accounts:
        q = (
            db.query(
                func.coalesce(func.sum(TransactionLine.debit), 0),
                func.coalesce(func.sum(TransactionLine.credit), 0),
            )
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .filter(TransactionLine.account_id == acc.id)
            .filter(Transaction.status == "ACTIVE")
        )
        d, c = q.first()
        cash_balance += Decimal(d) - Decimal(c)

    recent = (
        db.query(Transaction)
        .order_by(Transaction.created_at.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "today": today,
        "todays_receipts": todays_receipts, "todays_disbursements": todays_disbursements,
        "todays_receipts_cash": todays_receipts_cash, "todays_disbursements_cash": todays_disbursements_cash,
        "month_revenue": month_revenue, "month_expenses": month_expenses,
        "month_net_income": month_net_income, "cash_balance": cash_balance,
        "recent": recent,
    })
