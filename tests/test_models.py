import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from fin.db.models import Account, BalanceSnapshot, Institution, Transaction
from fin.db.session import session_scope


def _seed(session):
    inst = Institution(name="TD Bank", provider="plaid")
    acct = Account(
        institution=inst, provider_account_id="acc-1", name="Complete Checking",
        type="checking", mask="5445",
    )
    session.add_all([inst, acct])
    session.flush()
    return acct


def test_insert_and_read_back(engine):
    with session_scope(engine) as s:
        acct = _seed(s)
        s.add(Transaction(
            account_id=acct.id, provider_txn_id="t1", date=dt.date(2026, 11, 1),
            amount=Decimal("-12.99"), merchant="Spotify", source="plaid",
        ))
    with session_scope(engine) as s:
        txn = s.scalars(select(Transaction)).one()
        assert txn.amount == Decimal("-12.9900")
        assert isinstance(txn.amount, Decimal)


def test_duplicate_transaction_rejected(engine):
    with pytest.raises(IntegrityError):
        with session_scope(engine) as s:
            acct = _seed(s)
            for _ in range(2):
                s.add(Transaction(
                    account_id=acct.id, provider_txn_id="dup", date=dt.date(2026, 11, 1),
                    amount=Decimal("-5"), source="plaid",
                ))
            s.flush()


def test_invalid_quality_rejected(engine):
    with pytest.raises(IntegrityError):
        with session_scope(engine) as s:
            acct = _seed(s)
            s.add(BalanceSnapshot(
                account_id=acct.id, as_of=dt.datetime.now(dt.timezone.utc),
                current=Decimal("100"), source="plaid", quality="made_up",
            ))
            s.flush()


def test_foreign_keys_enforced(engine):
    with pytest.raises(IntegrityError):
        with session_scope(engine) as s:
            s.add(Transaction(
                account_id=999, provider_txn_id="x", date=dt.date(2026, 11, 1),
                amount=Decimal("1"), source="plaid",
            ))
            s.flush()


def test_mask_longer_than_four_rejected(engine):
    with pytest.raises(IntegrityError):
        with session_scope(engine) as s:
            inst = Institution(name="Chase", provider="plaid")
            s.add(Account(
                institution=inst, provider_account_id="a", name="Sapphire",
                type="credit", mask="123456789",
            ))
            s.flush()


def test_add_missing_columns_upgrades_old_table():
    from sqlalchemy import inspect, text

    from fin.db.session import init_db, make_engine

    eng = make_engine("sqlite:///:memory:")
    with eng.begin() as c:  # an "old" institutions table without the newer column
        c.execute(text(
            "CREATE TABLE institutions (id INTEGER PRIMARY KEY, name VARCHAR(200), "
            "provider VARCHAR(20), provider_ref_enc TEXT, status VARCHAR(20), "
            "last_synced_at DATETIME, created_at DATETIME)"
        ))
    init_db(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("institutions")}
    assert "provider_institution_id" in cols
