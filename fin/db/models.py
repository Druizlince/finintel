"""Database schema.

Conventions
- Money is Decimal (Numeric), never float.
- Transaction amounts: NEGATIVE = money out, POSITIVE = money in (normalized from each provider).
- Every fact records where it came from (`source`) and how trustworthy it is (`quality`):
    verified_account - reported by the institution itself
    delayed_market   - market data, possibly 15+ minutes or end-of-day delayed
    computed         - calculated by this system from other stored data
    estimate         - a projection or third-party estimate
    rebuilt          - reconstructed (e.g. cost basis rebuilt from activity history)
- Only the last 4 digits of account numbers are ever stored.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(18, 4)
QTY = Numeric(24, 8)

QUALITY_VALUES = ("verified_account", "delayed_market", "computed", "estimate", "rebuilt")
ACCOUNT_TYPES = (
    "checking", "savings", "credit", "brokerage", "ira", "roth_ira",
    "401k", "403b", "457b", "hsa", "loan", "other",
)


def _in(col: str, values: tuple[str, ...], name: str, nullable: bool = False) -> CheckConstraint:
    vals = ", ".join(f"'{v}'" for v in values)
    expr = f"{col} IN ({vals})"
    if nullable:
        expr = f"{col} IS NULL OR {expr}"
    return CheckConstraint(expr, name=name)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = (
        _in("provider", ("plaid", "snaptrade", "csv", "manual"), "ck_institutions_provider"),
        _in("status", ("active", "needs_relink", "disconnected"), "ck_institutions_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(20))
    # Provider's public institution id (e.g. Plaid "ins_3"); used to stop duplicate links.
    provider_institution_id: Mapped[str | None] = mapped_column(String(50))
    # Encrypted (fin.crypto) provider reference, e.g. a Plaid item_id. Tokens live in the keyring.
    provider_ref_enc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    accounts: Mapped[list[Account]] = relationship(back_populates="institution")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("institution_id", "provider_account_id", name="uq_accounts_provider_id"),
        _in("type", ACCOUNT_TYPES, "ck_accounts_type"),
        CheckConstraint("mask IS NULL OR length(mask) <= 4", name="ck_accounts_mask_len"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"))
    provider_account_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    official_name: Mapped[str | None] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(20))
    subtype: Mapped[str | None] = mapped_column(String(50))
    mask: Mapped[str | None] = mapped_column(String(4))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Whether this account counts toward the emergency fund (e.g. savings yes, brokerage no).
    is_emergency_fund: Mapped[bool] = mapped_column(Boolean, default=False)

    institution: Mapped[Institution] = relationship(back_populates="accounts")


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"
    __table_args__ = (
        UniqueConstraint("account_id", "as_of", "source", name="uq_balance_snapshot"),
        _in("quality", QUALITY_VALUES, "ck_balance_quality"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    current: Mapped[Decimal] = mapped_column(MONEY)
    available: Mapped[Decimal | None] = mapped_column(MONEY)
    credit_limit: Mapped[Decimal | None] = mapped_column(MONEY)
    source: Mapped[str] = mapped_column(String(30))
    quality: Mapped[str] = mapped_column(String(20), default="verified_account")


class RecurringStream(Base):
    __tablename__ = "recurring_streams"
    __table_args__ = (
        _in("kind", ("subscription", "bill", "income", "transfer", "other"), "ck_recurring_kind"),
        _in(
            "cadence",
            ("weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual", "irregular"),
            "ck_recurring_cadence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    merchant: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    cadence: Mapped[str] = mapped_column(String(20))
    avg_amount: Mapped[Decimal] = mapped_column(MONEY)
    last_date: Mapped[dt.date | None] = mapped_column(Date)
    next_expected: Mapped[dt.date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(30), default="computed")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "provider_txn_id", name="uq_transactions_provider_id"),
        Index("ix_transactions_account_date", "account_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    provider_txn_id: Mapped[str] = mapped_column(String(200))
    date: Mapped[dt.date] = mapped_column(Date)
    authorized_date: Mapped[dt.date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)  # negative = out, positive = in
    merchant: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    category_detail: Mapped[str | None] = mapped_column(String(150))
    pending: Mapped[bool] = mapped_column(Boolean, default=False)
    recurring_stream_id: Mapped[int | None] = mapped_column(ForeignKey("recurring_streams.id"))
    source: Mapped[str] = mapped_column(String(30))
    imported_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Security(Base):
    __tablename__ = "securities"
    __table_args__ = (
        UniqueConstraint("ticker", "type", name="uq_securities_ticker_type"),
        _in(
            "type",
            ("stock", "etf", "mutual_fund", "bond", "cash", "crypto", "option", "other"),
            "ck_securities_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(20))
    cusip: Mapped[str | None] = mapped_column(String(9))
    isin: Mapped[str | None] = mapped_column(String(12))
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(150))
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class HoldingSnapshot(Base):
    __tablename__ = "holdings_snapshots"
    __table_args__ = (
        UniqueConstraint("account_id", "security_id", "as_of", name="uq_holding_snapshot"),
        _in("quality", QUALITY_VALUES, "ck_holding_quality"),
        _in("cost_basis_quality", QUALITY_VALUES, "ck_holding_cb_quality", nullable=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    price: Mapped[Decimal | None] = mapped_column(MONEY)
    price_as_of: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    market_value: Mapped[Decimal | None] = mapped_column(MONEY)
    cost_basis: Mapped[Decimal | None] = mapped_column(MONEY)
    cost_basis_quality: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(30))
    quality: Mapped[str] = mapped_column(String(20), default="verified_account")


class InvestmentActivity(Base):
    __tablename__ = "investment_activities"
    __table_args__ = (
        UniqueConstraint("account_id", "provider_activity_id", name="uq_activity_provider_id"),
        _in(
            "type",
            ("buy", "sell", "dividend", "interest", "fee", "transfer_in", "transfer_out",
             "split", "reinvest", "other"),
            "ck_activity_type",
        ),
        Index("ix_activity_account_date", "account_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"))
    provider_activity_id: Mapped[str] = mapped_column(String(200))
    date: Mapped[dt.date] = mapped_column(Date)
    type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal | None] = mapped_column(QTY)
    price: Mapped[Decimal | None] = mapped_column(MONEY)
    amount: Mapped[Decimal] = mapped_column(MONEY)  # cash effect: negative = out of account cash
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30))


class PriceDaily(Base):
    __tablename__ = "prices_daily"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(MONEY)
    high: Mapped[Decimal | None] = mapped_column(MONEY)
    low: Mapped[Decimal | None] = mapped_column(MONEY)
    close: Mapped[Decimal] = mapped_column(MONEY)
    adj_close: Mapped[Decimal | None] = mapped_column(MONEY)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(30))


class Fundamental(Base):
    __tablename__ = "fundamentals"
    __table_args__ = (
        UniqueConstraint(
            "security_id", "period_end", "period_type", "metric", "source", name="uq_fundamental"
        ),
        _in("period_type", ("FY", "Q", "TTM"), "ck_fundamental_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), index=True)
    period_end: Mapped[dt.date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(3))
    metric: Mapped[str] = mapped_column(String(100))
    value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
    unit: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(30))
    filed_at: Mapped[dt.date | None] = mapped_column(Date)


class MacroObservation(Base):
    __tablename__ = "macro_observations"

    series_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    source: Mapped[str] = mapped_column(String(30), default="fred")


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    target_amount: Mapped[Decimal] = mapped_column(MONEY)
    target_date: Mapped[dt.date | None] = mapped_column(Date)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    priority: Mapped[int] = mapped_column(Integer, default=3)
    notes: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    """User planning settings, e.g. emergency_fund_target_months, risk_tolerance."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (_in("status", ("running", "ok", "error"), "ck_sync_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="running")
    records: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)  # always stored redacted


class SyncCursor(Base):
    """Where incremental sync left off (e.g. Plaid /transactions/sync cursor) per institution."""

    __tablename__ = "sync_cursors"

    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    cursor: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class CategoryRule(Base):
    """User rules that relabel transactions, e.g. Zelle payments to a landlord -> rent.
    A rule matches when every word of `pattern` appears in the merchant or description."""

    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    label: Mapped[str | None] = mapped_column(String(200))
    # Optional amount range (absolute value, inclusive), e.g. exactly 1000 = rent.
    min_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    max_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    # "out" = only money you send/spend, "in" = only money you receive, NULL = both.
    direction: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PlannedExpense(Base):
    """Known upcoming one-off or installment expenses (tuition, repairs, insurance renewal)."""

    __tablename__ = "planned_expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(MONEY)  # positive = money you will pay
    due_date: Mapped[dt.date] = mapped_column(Date)
    category: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
