"""Current-balance helpers for accounts that aren't live (e.g. Apple Card from statement PDFs)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin.db.models import Account, BalanceSnapshot, Institution, Transaction


def statement_card_balance(session: Session, account: Account, snap: BalanceSnapshot) -> tuple[Decimal, bool]:
    """For statement-imported cards: last statement balance minus payments to that card from
    your linked bank accounts since the statement date. Returns (balance, is_estimate).
    New purchases since the statement are not visible until the next statement is imported."""
    if account.institution.provider != "csv" or account.type != "credit":
        return Decimal(snap.current), False
    name = account.institution.name.lower()  # e.g. "apple card"
    paid = Decimal(0)
    rows = session.execute(
        select(Transaction.amount, Transaction.merchant, Transaction.description)
        .join(Account, Transaction.account_id == Account.id)
        .join(Institution, Account.institution_id == Institution.id)
        .where(Account.type == "checking", Transaction.date > snap.as_of.date(),
               Transaction.amount < 0, Institution.provider != "csv")
    ).all()
    for amount, merchant, desc in rows:
        if name in f"{merchant or ''} {desc or ''}".lower():
            paid += -Decimal(amount)
    return max(Decimal(0), Decimal(snap.current) - paid), True
