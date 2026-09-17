import datetime
from sqlalchemy import (
    Column, Integer, String, Numeric, Date, DateTime, ForeignKey, Boolean, Text
)
from sqlalchemy.orm import relationship, backref
from .database import Base


def now_utc():
    return datetime.datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="STAFF")  # ADMIN, ACCOUNTANT, STAFF, VIEWER
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_utc)


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(150), nullable=False)
    account_type = Column(String(30), nullable=False)   # Asset, Liability, Equity, Revenue, COGS, Expense
    category = Column(String(60), nullable=True)         # e.g. Current Assets, Operating Expenses
    normal_balance = Column(String(6), nullable=False)    # Debit / Credit
    notes = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    parent_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)

    children = relationship("Account", backref=backref("parent", remote_side=[id]))


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    reference_number = Column(String(30), unique=True, nullable=False, index=True)
    transaction_date = Column(Date, nullable=False)
    transaction_type = Column(String(20), nullable=False)  # RECEIPT, DISBURSEMENT, TRANSFER
    remarks = Column(Text, nullable=True)
    status = Column(String(10), nullable=False, default="ACTIVE")  # ACTIVE / VOIDED
    void_reason = Column(String(255), nullable=True)
    created_by = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=now_utc)
    updated_by = Column(String(50), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    client_token = Column(String(64), unique=True, nullable=True, index=True)
    occurred_at = Column(DateTime, nullable=True)

    lines = relationship("TransactionLine", back_populates="transaction", cascade="all, delete-orphan")


class TransactionLine(Base):
    __tablename__ = "transaction_lines"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    debit = Column(Numeric(18, 2), nullable=False, default=0)
    credit = Column(Numeric(18, 2), nullable=False, default=0)

    transaction = relationship("Transaction", back_populates="lines")
    account = relationship("Account")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=now_utc)
    username = Column(String(50), nullable=False)
    action = Column(Text, nullable=False)
    module = Column(String(50), nullable=False)
    reference = Column(String(50), nullable=True)


class FinancialNote(Base):
    __tablename__ = "financial_notes"

    id = Column(Integer, primary_key=True)
    title = Column(String(150), nullable=False)
    content = Column(Text, nullable=False)
    updated_by = Column(String(50), nullable=True)
    updated_at = Column(DateTime, default=now_utc)


class ChairRate(Base):
    """Price list for each chair type at each duration option. Admin-editable, takes
    effect by date so raising prices later never rewrites historical transactions."""
    __tablename__ = "chair_rates"

    id = Column(Integer, primary_key=True)
    chair_type = Column(String(30), nullable=False)   # "Deluxe" / "King"
    duration_minutes = Column(Integer, nullable=False)
    price = Column(Numeric(18, 2), nullable=False)
    effective_from = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class Setting(Base):
    """Small generic key-value store for simple admin-editable numbers (e.g. the
    Eye Massager add-on price) that don't warrant their own table."""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(60), unique=True, nullable=False)
    value = Column(String(255), nullable=False)


class Counter(Base):
    """Tracks the next number for reference-number sequences, per type per year."""
    __tablename__ = "counters"

    id = Column(Integer, primary_key=True)
    key = Column(String(30), unique=True, nullable=False)  # e.g. "RCPT-2026"
    next_value = Column(Integer, nullable=False, default=1)
