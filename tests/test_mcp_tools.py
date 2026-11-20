import datetime as dt
from decimal import Decimal as D

import pytest

from fin.db.models import (Account, BalanceSnapshot, Institution, PlannedExpense, Transaction)
from fin.db.session import session_scope
from fin_mcp import tools


@pytest.fixture
def seeded(engine):
    with session_scope(engine) as s:
        inst = Institution(name="TD Bank", provider="plaid",
                           last_synced_at=dt.datetime(2026, 11, 17, tzinfo=dt.timezone.utc))
        chk = Account(institution=inst, provider_account_id="c", name="Checking", type="checking", mask="5445")
        s.add_all([inst, chk])
        s.flush()
        s.add(BalanceSnapshot(account_id=chk.id, as_of=dt.datetime(2026, 11, 17, tzinfo=dt.timezone.utc),
                              current=D("1500"), available=D("1400"), source="plaid"))
        s.add(Transaction(account_id=chk.id, provider_txn_id="t1", date=dt.date(2026, 10, 5),
                          amount=D("-12.99"), merchant="Hulu", category="ENTERTAINMENT", source="plaid"))
        s.add(PlannedExpense(name="Body shop", amount=D("1500"), due_date=dt.date.today() + dt.timedelta(days=10)))
    return engine


def test_every_tool_returns_data_sources_warnings(seeded):
    for fn in (tools.data_status, tools.overview, tools.cashflow, tools.plan, tools.upcoming,
               tools.holdings, tools.investment_activity):
        out = fn(seeded)
        assert set(out) == {"data", "sources", "warnings"}, fn.__name__
        assert out["sources"][0]["name"] == "TD Bank"


def test_search_and_limits(seeded):
    out = tools.search_transactions(seeded, query="hulu")
    assert out["data"][0]["amount"] == -12.99
    assert tools.search_transactions(seeded, query="hulu", month="2026-09")["data"] == []
    assert len(tools.search_transactions(seeded, limit=10_000)["data"]) <= 100


def test_upcoming_includes_planned(seeded):
    names = [i["name"] for i in tools.upcoming(seeded, days=30)["data"]]
    assert "Body shop" in names


def test_plan_is_labeled_not_advice(seeded):
    assert any("not financial advice" in w for w in tools.plan(seeded)["warnings"])
