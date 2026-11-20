import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from fin.db.models import (Account, BalanceSnapshot, HoldingSnapshot, Institution,
                           InvestmentActivity, Security)
from fin.db.session import session_scope
from fin.providers.snaptrade_mapping import (map_account, map_account_type, map_activity,
                                             map_position, plain)
from fin.sync_snaptrade import run_snaptrade_sync


def _sym(ticker, code="et", desc=None):
    return {"symbol": ticker, "description": desc or ticker, "type": {"code": code},
            "currency": {"code": "USD"}}


ACCOUNT = {
    "id": "acc-fid-1", "brokerage_authorization": "auth-1", "institution_name": "Fidelity",
    "name": "Individual", "number": "Z12345678", "raw_type": "Individual",
    "balance": {"total": {"amount": 1500.0, "currency": "USD"}},
}
POSITIONS = [
    {"symbol": {"symbol": _sym("VTI", "et", "Vanguard Total Stock Market ETF")},
     "units": 2, "price": 300.0, "average_purchase_price": 250.0},
    {"symbol": {"symbol": _sym("QQQ", "et")}, "units": 1, "price": 500.0,
     "average_purchase_price": None},
]
ACTIVITIES = [
    {"id": "a1", "type": "BUY", "trade_date": "2026-09-01T00:00:00Z", "units": 2, "price": 250,
     "amount": -500, "symbol": _sym("VTI")},
    {"id": "a2", "type": "DIVIDEND", "trade_date": "2026-10-01", "amount": 3.21,
     "symbol": _sym("VTI")},
    {"id": "a3", "type": "CONTRIBUTION", "trade_date": "2026-08-15", "amount": 1000,
     "symbol": None},
]


class FakeSnap:
    def __init__(self):
        self.activity_calls = []

    def list_accounts(self):
        return [ACCOUNT]

    def get_balances(self, account_id):
        return [{"currency": {"code": "USD"}, "cash": 200.0}]

    def get_positions(self, account_id):
        return POSITIONS

    def get_activities(self, account_id, start, end):
        self.activity_calls.append(start)
        return ACTIVITIES


def test_account_type_mapping():
    assert map_account_type("ROTH IRA") == "roth_ira"
    assert map_account_type("Traditional IRA") == "ira"
    assert map_account_type("Rollover IRA") == "ira"
    assert map_account_type("Individual") == "brokerage"
    assert map_account_type(None) == "brokerage"


def test_map_account_and_position():
    m = map_account(ACCOUNT)
    assert m["mask"] == "5678" and m["type"] == "brokerage"
    assert m["total_value"] == Decimal("1500.0")
    p = map_position(POSITIONS[0])
    assert p["security"]["ticker"] == "VTI" and p["security"]["type"] == "etf"
    assert p["market_value"] == Decimal("600.0") and p["cost_basis"] == Decimal("500.0")
    assert map_position(POSITIONS[1])["cost_basis"] is None


def test_map_activity_types_and_dates():
    a = map_activity(ACTIVITIES[0])
    assert a["type"] == "buy" and a["date"] == dt.date(2026, 9, 1)
    assert map_activity(ACTIVITIES[2])["type"] == "transfer_in"
    assert map_activity({"id": "x"}) is None  # incomplete -> skipped, no crash


def test_plain_handles_sdk_none_wrapper():
    class NoneClass:  # mimics the SDK's null wrapper
        pass
    assert plain({"a": NoneClass(), "b": [1, {"c": 2}]}) == {"a": None, "b": [1, {"c": 2}]}


def test_full_sync_and_incremental(engine):
    fake = FakeSnap()
    res = run_snaptrade_sync(engine, fake)
    assert res == [{"account": "Fidelity Individual", "positions": 2, "activities_added": 3}]
    with session_scope(engine) as s:
        inst = s.scalars(select(Institution)).one()
        assert inst.provider == "snaptrade" and inst.name == "Fidelity"
        acct = s.scalars(select(Account)).one()
        assert acct.mask == "5678"
        bal = s.scalars(select(BalanceSnapshot)).one()
        assert bal.current == Decimal("1500.0") and bal.available == Decimal("200.0")
        assert len(s.scalars(select(HoldingSnapshot)).all()) == 2
        assert {x.ticker for x in s.scalars(select(Security))} == {"VTI", "QQQ"}

    res2 = run_snaptrade_sync(engine, fake)
    assert res2[0]["activities_added"] == 0                     # no duplicates
    assert fake.activity_calls[0] == dt.date(2015, 1, 1)       # first: full history
    assert fake.activity_calls[1] > dt.date(2026, 1, 1)        # then: incremental window
    with session_scope(engine) as s:
        assert len(s.scalars(select(InvestmentActivity)).all()) == 3
        assert len(s.scalars(select(Security)).all()) == 2      # securities reused


def test_sync_error_is_recorded_and_redacted(engine):
    class Broken(FakeSnap):
        def list_accounts(self):
            raise RuntimeError("401 Unauthorized consumer_key=ABCDEF123")
    res = run_snaptrade_sync(engine, Broken())
    assert "ABCDEF123" not in res[-1]["error"]
