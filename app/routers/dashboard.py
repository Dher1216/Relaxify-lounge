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
        """Net cash movement (debit minus credit) summed PER TRANSACTION, then added
        together. This must be computed per-transaction, not as one combined sum
        across all transactions: if it were combined first, one transaction with an
        unusual net-negative cash line (e.g. a compound entry that happens to credit
        a cash account) could silently cancel out part of a completely different
        transaction's cash inflow before the final total is taken - understating the
        real total. Computing and taking the absolute value per transaction first
        prevents that cross-transaction cancellation entirely."""
        if not txns:
            return Decimal("0.00")
        total = Decimal("0.00")
        for t in txns:
            net = (
                db.query(func.coalesce(func.sum(TransactionLine.debit - TransactionLine.credit), 0))
                .join(Account, Account.id == TransactionLine.account_id)
                .filter(TransactionLine.transaction_id == t.id)
                .filter(Account.is_cash_account == True)  # noqa: E712
                .scalar()
            )
            total += abs(Decimal(net))
        return total

    todays_receipts_cash = cash_movement_for(todays_receipts)
    todays_disbursements_cash = cash_movement_for(todays_disbursements)

    # "Today's Sales" is intentionally NOT the same thing as "Today's Receipts" - it's
    # detected automatically from the accounting entries themselves (any line crediting
    # Service Revenue or one of its sub-accounts), rather than relying on someone
    # correctly picking a "sales" label at data-entry time. This means something like a
    # loan proceeds receipt never gets miscounted as a sale, regardless of what
    # transaction type it was entered under.
    service_revenue = db.query(Account).filter(Account.code == "4100").first()
    todays_sales_amount = Decimal("0.00")
    todays_sales_txn_ids = set()
    if service_revenue:
        revenue_account_ids = [service_revenue.id] + [
            a.id for a in db.query(Account).filter(Account.parent_id == service_revenue.id).all()
        ]
        sales_lines = (
            db.query(TransactionLine)
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .filter(TransactionLine.account_id.in_(revenue_account_ids))
            .filter(Transaction.status == "ACTIVE")
            .filter(Transaction.transaction_date == today)
            .all()
        )
        for line in sales_lines:
            todays_sales_amount += (line.credit - line.debit)
            todays_sales_txn_ids.add(line.transaction_id)
    todays_sales_count = len(todays_sales_txn_ids)

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

    cash_accounts = db.query(Account).filter(Account.is_active == True, Account.is_cash_account == True).order_by(Account.code).all()  # noqa: E712
    cash_breakdown = []  # [{name, balance}, ...] - one entry per actual cash/bank/e-wallet account
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
        acc_balance = Decimal(d) - Decimal(c)
        cash_balance += acc_balance
        cash_breakdown.append({"name": acc.name, "balance": acc_balance})

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
        "todays_sales_amount": todays_sales_amount, "todays_sales_count": todays_sales_count,
        "month_revenue": month_revenue, "month_expenses": month_expenses,
        "month_net_income": month_net_income, "cash_balance": cash_balance, "cash_breakdown": cash_breakdown,
        "recent": recent,
    })
