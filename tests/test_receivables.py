import datetime as dt
from decimal import Decimal

from fin.analytics.receivables import (
    add_receivable, load_receivables, owed_by_others, settle_receivable,
)
from fin.db.models import Account, BalanceSnapshot, Institution
from fin.db.session import session_scope
from fin.web.summary import overview


def _card(s, name="Chase", owed="1891.24"):
    inst = Institution(name=name, provider="plaid", status="active")
    s.add(inst)
    s.flush()
    acc = Account(institution_id=inst.id, provider_account_id="c1", name="CREDIT CARD",
                  type="credit", is_active=True)
    s.add(acc)
    s.flush()
    s.add(BalanceSnapshot(account_id=acc.id, as_of=dt.datetime(2026, 11, 17),
                          current=Decimal(owed), source="plaid"))


def test_add_list_settle(engine):
    with session_scope(engine) as s:
        r = add_receivable(s, "Roommate", Decimal("510"), card="Chase", note="her charges")
        assert r.id == 1 and r.card == "chase"
    with session_scope(engine) as s:
        items = load_receivables(s)
        assert [i.amount for i in items] == [Decimal("510")]
        assert owed_by_others(items, "Chase") == Decimal("510")
        assert owed_by_others(items, "American Express") == 0
        left = settle_receivable(s, 1, Decimal("200"))
        assert left.amount == Decimal("310")
    with session_scope(engine) as s:
        assert settle_receivable(s, 1) is not None
        assert load_receivables(s) == []


def test_overview_excludes_what_others_owe(engine):
    with session_scope(engine) as s:
        _card(s)
        add_receivable(s, "Roommate", Decimal("510"), card="chase")
    o = overview(engine)
    assert o["card_owed"] == 1381.24
    assert o["owed_to_you_on_cards"] == 510.0
    assert o["accounts"][0]["owed_by_others"] == 510.0


def test_mcp_owed_tool(engine):
    from fin_mcp import tools

    out = tools.owed_to_me(engine, action="add", who="Roommate", amount=510, card="chase")
    assert out["data"]["open"][0]["amount"] == 510.0
    out = tools.owed_to_me(engine, action="settle", item_id=1, amount=10)
    assert out["data"]["open"][0]["amount"] == 500.0
    out = tools.owed_to_me(engine, action="settle", item_id=1)
    assert out["data"]["open"] == []
