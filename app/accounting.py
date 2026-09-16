import datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session
from sqlalchemy import func

from .models import Transaction, TransactionLine, Account, Counter, ChairRate

PREFIXES = {"RECEIPT": "RCPT", "DISBURSEMENT": "DISB"}


def get_descendant_ids(db: Session, account: Account) -> list[int]:
    """Returns [account.id] plus the ids of its direct children (2-level hierarchy)."""
    child_ids = [a.id for a in db.query(Account).filter(Account.parent_id == account.id).all()]
    return [account.id] + child_ids


def is_leaf_account(db: Session, account: Account) -> bool:
    """True if this account has no active children - i.e. transactions can post to it directly."""
    return db.query(Account).filter(Account.parent_id == account.id, Account.is_active == True).first() is None  # noqa: E712


def postable_accounts(db: Session) -> list[Account]:
    """Active accounts that transactions are allowed to post to directly - excludes any
    account that currently has active children, since those exist purely as rollup labels."""
    all_active = db.query(Account).filter(Account.is_active == True).order_by(Account.code).all()  # noqa: E712
    parent_ids = {a.parent_id for a in all_active if a.parent_id}
    return [a for a in all_active if a.id not in parent_ids]


def next_reference_number(db: Session, transaction_type: str, as_of: datetime.date) -> str:
    prefix = PREFIXES[transaction_type]
    key = f"{prefix}-{as_of.year}"
    counter = db.query(Counter).filter(Counter.key == key).with_for_update().first()
    if not counter:
        counter = Counter(key=key, next_value=1)
        db.add(counter)
        db.flush()
    number = counter.next_value
    counter.next_value += 1
    return f"{prefix}-{as_of.year}-{number:06d}"


def to_decimal(value) -> Decimal:
    try:
        d = Decimal(str(value).replace(",", "").strip() or "0")
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a valid amount.")
    return d.quantize(Decimal("0.01"))


def validate_lines(line_inputs: list[dict]) -> tuple[Decimal, Decimal, list[str]]:
    """
    line_inputs: [{"account_id": int, "debit": str/num, "credit": str/num}, ...]
    Returns (total_debit, total_credit, errors)
    """
    errors = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    real_lines = 0

    for i, line in enumerate(line_inputs, start=1):
        if not line.get("account_id"):
            continue
        try:
            debit = to_decimal(line.get("debit") or 0)
            credit = to_decimal(line.get("credit") or 0)
        except ValueError as e:
            errors.append(f"Line {i}: {e}")
            continue

        if debit < 0 or credit < 0:
            errors.append(f"Line {i}: negative amounts are not allowed.")
            continue
        if debit > 0 and credit > 0:
            errors.append(f"Line {i}: a line cannot have both a debit and a credit amount.")
            continue
        if debit == 0 and credit == 0:
            continue

        real_lines += 1
        total_debit += debit
        total_credit += credit

    if real_lines < 2:
        errors.append("At least two accounting lines with an amount are required.")

    if total_debit != total_credit:
        errors.append(
            f"Transaction cannot be saved because total debits (₱{total_debit:,.2f}) "
            f"and total credits (₱{total_credit:,.2f}) are not equal."
        )

    return total_debit, total_credit, errors


def account_net_balance(db: Session, account: Account, as_of: datetime.date = None, from_date: datetime.date = None):
    """
    Signed balance in the account's *normal* direction, from ACTIVE transactions only.
    """
    q = (
        db.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .filter(TransactionLine.account_id == account.id)
        .filter(Transaction.status == "ACTIVE")
    )
    if from_date:
        q = q.filter(Transaction.transaction_date >= from_date)
    if as_of:
        q = q.filter(Transaction.transaction_date <= as_of)

    total_debit, total_credit = q.first()
    total_debit = Decimal(total_debit)
    total_credit = Decimal(total_credit)

    if account.normal_balance == "Debit":
        return total_debit - total_credit
    else:
        return total_credit - total_debit


def trial_balance(db: Session, as_of: datetime.date):
    accounts = db.query(Account).filter(Account.is_active == True).order_by(Account.code).all()  # noqa: E712
    rows = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    for acc in accounts:
        balance = account_net_balance(db, acc, as_of=as_of)
        if balance == 0:
            continue
        if acc.normal_balance == "Debit":
            debit_col = balance if balance >= 0 else Decimal("0.00")
            credit_col = -balance if balance < 0 else Decimal("0.00")
        else:
            credit_col = balance if balance >= 0 else Decimal("0.00")
            debit_col = -balance if balance < 0 else Decimal("0.00")
        rows.append({
            "code": acc.code, "name": acc.name,
            "debit": debit_col, "credit": credit_col,
        })
        total_debit += debit_col
        total_credit += credit_col
    return rows, total_debit, total_credit


def income_statement(db: Session, from_date: datetime.date, to_date: datetime.date):
    accounts = db.query(Account).filter(
        Account.is_active == True,  # noqa: E712
        Account.account_type.in_(["Revenue", "COGS", "Expense"]),
    ).order_by(Account.code).all()

    revenue, cogs, expenses = [], [], []
    total_revenue = total_cogs = total_expenses = Decimal("0.00")

    for acc in accounts:
        balance = account_net_balance(db, acc, as_of=to_date, from_date=from_date)
        if balance == 0:
            continue
        if acc.account_type == "Revenue":
            # Contra-revenue accounts (e.g. Sales Discounts) have a Debit normal balance -
            # their balance() is positive in ITS OWN direction, but that represents an amount
            # to SUBTRACT from revenue, not add. Flip the sign so the total comes out right.
            contribution = balance if acc.normal_balance == "Credit" else -balance
            row = {"code": acc.code, "name": acc.name, "amount": contribution}
            revenue.append(row); total_revenue += contribution
        elif acc.account_type == "COGS":
            row = {"code": acc.code, "name": acc.name, "amount": balance}
            cogs.append(row); total_cogs += balance
        else:
            row = {"code": acc.code, "name": acc.name, "amount": balance}
            expenses.append(row); total_expenses += balance

    gross_profit = total_revenue - total_cogs
    net_income = gross_profit - total_expenses
    return {
        "revenue": revenue, "total_revenue": total_revenue,
        "cogs": cogs, "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        "expenses": expenses, "total_expenses": total_expenses,
        "net_income": net_income,
    }


def balance_sheet(db: Session, as_of: datetime.date):
    accounts = db.query(Account).filter(
        Account.is_active == True,  # noqa: E712
        Account.account_type.in_(["Asset", "Liability", "Equity"]),
    ).order_by(Account.code).all()

    assets, liabilities, equity = [], [], []
    total_assets = total_liabilities = total_equity = Decimal("0.00")

    for acc in accounts:
        balance = account_net_balance(db, acc, as_of=as_of)
        if balance == 0:
            continue
        row = {"code": acc.code, "name": acc.name, "amount": balance}
        if acc.account_type == "Asset":
            assets.append(row); total_assets += balance
        elif acc.account_type == "Liability":
            liabilities.append(row); total_liabilities += balance
        else:
            equity.append(row); total_equity += balance

    # This system doesn't perform period-end closing entries, so Revenue/COGS/Expense
    # accounts carry live balances rather than rolling into Retained Earnings automatically.
    # To keep the Balance Sheet accurate at any as-of date, we fold cumulative net income
    # (since inception, up to as_of) into equity as "Current Earnings" - the standard
    # approach any accounting system uses before a formal closing entry is posted.
    inception_income = income_statement(db, datetime.date(1900, 1, 1), as_of)
    net_income_to_date = inception_income["net_income"]
    if net_income_to_date != 0:
        equity.append({"code": "", "name": "Current Earnings (uncredited to Retained Earnings)", "amount": net_income_to_date})
    total_equity += net_income_to_date

    return {
        "assets": assets, "total_assets": total_assets,
        "liabilities": liabilities, "total_liabilities": total_liabilities,
        "equity": equity, "total_equity": total_equity,
        "total_liabilities_and_equity": total_liabilities + total_equity,
        "is_balanced": total_assets == (total_liabilities + total_equity),
    }


def statement_of_changes_in_equity(db: Session, from_date: datetime.date, to_date: datetime.date):
    equity_accounts = db.query(Account).filter(
        Account.is_active == True, Account.account_type == "Equity"  # noqa: E712
    ).order_by(Account.code).all()

    beginning = Decimal("0.00")
    additions = Decimal("0.00")
    withdrawals = Decimal("0.00")
    day_before = from_date - datetime.timedelta(days=1)

    for acc in equity_accounts:
        beginning += account_net_balance(db, acc, as_of=day_before)
        period_balance = account_net_balance(db, acc, as_of=to_date, from_date=from_date)
        if acc.normal_balance == "Credit":
            additions += period_balance if period_balance > 0 else Decimal("0.00")
            withdrawals += -period_balance if period_balance < 0 else Decimal("0.00")
        else:
            withdrawals += period_balance if period_balance > 0 else Decimal("0.00")

    # Fold in net income earned before this period (not yet closed to Retained Earnings)
    # so the beginning balance matches what the Balance Sheet showed as of day_before.
    beginning += income_statement(db, datetime.date(1900, 1, 1), day_before)["net_income"]

    inc = income_statement(db, from_date, to_date)
    net_income = inc["net_income"]
    ending = beginning + additions - withdrawals + net_income

    return {
        "beginning": beginning, "additions": additions,
        "net_income": net_income, "withdrawals": withdrawals,
        "ending": ending,
    }


def cash_flow_statement(db: Session, from_date: datetime.date, to_date: datetime.date):
    """
    Simplified direct method: classifies each cash-affecting line by the
    OTHER (contra) account's type on that same transaction.
    Cash accounts = accounts flagged as Asset/Current Assets containing 'Cash'.
    """
    cash_accounts = db.query(Account).filter(
        Account.is_active == True,  # noqa: E712
        Account.account_type == "Asset",
        Account.name.ilike("%cash%"),
    ).all()
    cash_ids = {a.id for a in cash_accounts}

    day_before = from_date - datetime.timedelta(days=1)
    beginning_cash = sum((account_net_balance(db, a, as_of=day_before) for a in cash_accounts), Decimal("0.00"))
    ending_cash = sum((account_net_balance(db, a, as_of=to_date) for a in cash_accounts), Decimal("0.00"))

    operating = investing = financing = Decimal("0.00")

    txns = (
        db.query(Transaction)
        .filter(Transaction.status == "ACTIVE")
        .filter(Transaction.transaction_date >= from_date, Transaction.transaction_date <= to_date)
        .all()
    )
    for txn in txns:
        cash_lines = [l for l in txn.lines if l.account_id in cash_ids]
        if not cash_lines:
            continue
        cash_movement = sum((l.debit - l.credit) for l in cash_lines)
        other_lines = [l for l in txn.lines if l.account_id not in cash_ids]
        if not other_lines:
            continue
        # classify by the dominant contra account type
        contra_types = [l.account.account_type for l in other_lines]
        if any(t in ("Asset",) for t in contra_types) and any(
            "fixed" in (l.account.category or "").lower() for l in other_lines
        ):
            investing += cash_movement
        elif any(t == "Liability" for t in contra_types) and any(
            "long-term" in (l.account.category or "").lower() for l in other_lines
        ):
            financing += cash_movement
        elif any(t == "Equity" for t in contra_types):
            financing += cash_movement
        else:
            operating += cash_movement

    net_change = operating + investing + financing
    return {
        "operating": operating, "investing": investing, "financing": financing,
        "net_change": net_change,
        "beginning_cash": beginning_cash, "ending_cash": ending_cash,
        "reconciles": (beginning_cash + net_change) == ending_cash,
    }


def account_ledger(db: Session, account: Account, from_date: datetime.date, to_date: datetime.date):
    """
    General-ledger-style report for exactly one account. If `account` has children,
    combines all of them into one ledger (each line still shows which specific
    child account it came from) - otherwise it's just that one account's history.
    """
    children = db.query(Account).filter(Account.parent_id == account.id).all()
    accounts_in_scope = [account] + children
    ids = [a.id for a in accounts_in_scope]
    normal = account.normal_balance  # children are expected to share the parent's normal balance

    day_before = from_date - datetime.timedelta(days=1)
    beginning = Decimal("0.00")
    for a in accounts_in_scope:
        beginning += account_net_balance(db, a, as_of=day_before)

    lines = (
        db.query(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .filter(TransactionLine.account_id.in_(ids))
        .filter(Transaction.status == "ACTIVE")
        .filter(Transaction.transaction_date >= from_date, Transaction.transaction_date <= to_date)
        .order_by(Transaction.transaction_date, Transaction.id)
        .all()
    )

    running = beginning
    rows = []
    for line in lines:
        signed = (line.debit - line.credit) if normal == "Debit" else (line.credit - line.debit)
        running += signed
        rows.append({
            "date": line.transaction.transaction_date,
            "reference_number": line.transaction.reference_number,
            "transaction_id": line.transaction.id,
            "account_code": line.account.code,
            "account_name": line.account.name,
            "remarks": line.transaction.remarks,
            "debit": line.debit,
            "credit": line.credit,
            "running_balance": running,
        })

    return {
        "beginning": beginning,
        "rows": rows,
        "ending": running,
        "children": children,
    }


def current_chair_rates(db: Session, as_of: datetime.date) -> dict:
    """Returns {chair_type: {duration_minutes: price}} using whichever rate is actually
    in effect as of the given date - never a future-dated rate that hasn't kicked in yet."""
    all_rates = db.query(ChairRate).filter(ChairRate.is_active == True, ChairRate.effective_from <= as_of).all()  # noqa: E712
    result = {}
    latest_effective = {}
    for r in all_rates:
        key = (r.chair_type, r.duration_minutes)
        if key not in latest_effective or r.effective_from > latest_effective[key]:
            latest_effective[key] = r.effective_from
            result.setdefault(r.chair_type, {})[r.duration_minutes] = float(r.price)
    return result


def current_chair_rates(db: Session, as_of: datetime.date) -> dict:
    """Returns {chair_type: {duration_minutes: price}} using whichever rate is actually
    in effect as of the given date - never a future-dated rate that hasn't kicked in yet."""
    all_rates = db.query(ChairRate).filter(ChairRate.is_active == True, ChairRate.effective_from <= as_of).all()  # noqa: E712
    result = {}
    latest_effective = {}
    for r in all_rates:
        key = (r.chair_type, r.duration_minutes)
        if key not in latest_effective or r.effective_from > latest_effective[key]:
            latest_effective[key] = r.effective_from
            result.setdefault(r.chair_type, {})[r.duration_minutes] = float(r.price)
    return result
