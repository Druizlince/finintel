from decimal import Decimal

from sqlalchemy import select

from fin import secrets
from fin.db.models import Account, BalanceSnapshot, Institution, SyncRun, Transaction
from fin.db.session import session_scope
from fin.sync import link_plaid_item, run_plaid_sync


from fakes import FakePlaid, txn as _txn  # noqa: E402


def _link(engine, fake):
    with session_scope(engine) as s:
        link_plaid_item(s, fake, "public-sandbox-x")


def test_link_stores_token_in_keyring_not_db(engine):
    fake = FakePlaid()
    _link(engine, fake)
    assert secrets.get_secret("plaid_token:item-123") == "access-sandbox-fake-token"
    with session_scope(engine) as s:
        inst = s.scalars(select(Institution)).one()
        assert "item-123" not in (inst.provider_ref_enc or "")  # encrypted


def test_full_sync_pages_through_and_stores_everything(engine):
    fake = FakePlaid()
    _link(engine, fake)
    results = run_plaid_sync(engine, fake)

    assert results[0]["status"] == "ok"
    assert results[0]["transactions_upserted"] == 3
    assert fake.cursors_seen == ["", "c1"]
    with session_scope(engine) as s:
        assert len(s.scalars(select(Account)).all()) == 2
        sav = s.scalar(select(Account).where(Account.type == "savings"))
        assert sav.is_emergency_fund is True
        amounts = sorted(t.amount for t in s.scalars(select(Transaction)))
        assert amounts == [Decimal("-20"), Decimal("-10"), Decimal("500")]
        assert len(s.scalars(select(BalanceSnapshot)).all()) == 2
        assert s.scalars(select(SyncRun)).one().status == "ok"


def test_second_sync_is_incremental_and_idempotent(engine):
    fake = FakePlaid()
    _link(engine, fake)
    run_plaid_sync(engine, fake)

    fake.pages["c2"] = {
        "added": [_txn("t4", 5)],
        "modified": [_txn("t1", 11)],                 # amount corrected
        "removed": [{"transaction_id": "t2"}],        # e.g. pending that dropped
        "next_cursor": "c3", "has_more": False,
    }
    res = run_plaid_sync(engine, fake)[0]
    assert fake.cursors_seen[-1] == "c2"              # resumed where it left off
    assert res["transactions_removed"] == 1
    with session_scope(engine) as s:
        txns = {t.provider_txn_id: t.amount for t in s.scalars(select(Transaction))}
        assert txns == {"t1": Decimal("-11"), "t3": Decimal("500"), "t4": Decimal("-5")}
        assert len(s.scalars(select(Account)).all()) == 2   # no duplicate accounts


def test_failure_is_recorded_and_relink_flagged(engine):
    fake = FakePlaid()
    _link(engine, fake)

    class LoginRequired(Exception):
        code = "ITEM_LOGIN_REQUIRED"

    def boom(*_a):
        raise LoginRequired("Plaid error ITEM_LOGIN_REQUIRED: access-sandbox-abc-123 expired")

    fake.get_accounts = boom
    res = run_plaid_sync(engine, fake)[0]
    assert res["status"] == "error"
    assert "abc-123" not in res["error"]              # token redacted
    with session_scope(engine) as s:
        assert s.scalars(select(Institution)).one().status == "needs_relink"
        assert s.scalars(select(SyncRun)).one().status == "error"


def test_not_ready_cursor_is_not_saved(engine):
    """Regression: a cursor from a NOT_READY response must not be saved, or the
    transactions that arrive afterward are silently skipped."""
    fake = FakePlaid()
    real_pages = fake.pages
    fake.pages = {"": {"added": [], "modified": [], "removed": [], "next_cursor": "too-early",
                       "has_more": False, "transactions_update_status": "NOT_READY"}}
    _link(engine, fake)
    first = run_plaid_sync(engine, fake)[0]
    assert first["update_status"] == "NOT_READY"

    fake.pages = real_pages                     # data is ready now
    second = run_plaid_sync(engine, fake)[0]
    assert fake.cursors_seen[-2:] == ["", "c1"]  # started from scratch, not "too-early"
    assert second["transactions_upserted"] == 3


def test_reset_cursor_resyncs_without_duplicates(engine):
    from fin.sync import reset_plaid_cursors

    fake = FakePlaid()
    _link(engine, fake)
    run_plaid_sync(engine, fake)
    assert reset_plaid_cursors(engine) == 1
    run_plaid_sync(engine, fake)
    with session_scope(engine) as s:
        assert len(s.scalars(select(Transaction)).all()) == 3
