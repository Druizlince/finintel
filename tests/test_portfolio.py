import datetime as dt
from decimal import Decimal as D

from fin.analytics.portfolio import (
    Position, analyze, bucket_of, buy_plan, history, load_targets, save_targets,
)
from fin.db.session import session_scope


def P(t, typ, v, name=None, cb=None):  # noqa: N802
    return Position(ticker=t, name=name or t, sec_type=typ, market_value=D(v),
                    cost_basis=D(cb) if cb is not None else None)


def test_buckets():
    assert bucket_of(P("FSKAX", "mutual_fund", 1)) == "us_stock"
    assert bucket_of(P("VXUS", "etf", 1)) == "intl_stock"
    assert bucket_of(P("NVDA", "stock", 1)) == "single_stock"
    assert bucket_of(P("SPAXX", "mutual_fund", 1)) == "cash"
    assert bucket_of(P("XYZ", "mutual_fund", 1, name="Some International Index")) == "intl_stock"
    assert bucket_of(P("ABC", "etf", 1, name="Mystery fund")) == "other"


def test_allocation_concentration_and_cash_excluded():
    a = analyze([P("FSKAX", "mutual_fund", 7000, cb=6000), P("FTIHX", "mutual_fund", 1500),
                 P("NVDA", "stock", 1500, cb=1000), P("SPAXX", "mutual_fund", 5000)],
                extra_cash=D("100"))
    assert a.invested == D("10000") and a.cash == D("5100")
    alloc = {r["bucket"]: r for r in a.allocation}
    assert alloc["us_stock"]["pct"] == D("70.0") and alloc["us_stock"]["drift"] == 0
    assert alloc["intl_stock"]["drift"] == D("-5.0")
    assert any("NVDA is 15.0%" in w for w in a.warnings)
    assert len([w for w in a.warnings if "Individual stocks" in w]) == 1
    assert a.gains["gain"] == D("1500") and a.gains["positions_without_cost_basis"] == 1


def test_empty_portfolio_has_no_crash():
    a = analyze([], extra_cash=D("4.50"), new_money=D("1000"))
    assert a.invested == 0 and a.cash == D("4.50")
    assert sum(r["amount"] for r in a.buy_plan) == D("1000")
    plan = {r["bucket"]: r["amount"] for r in a.buy_plan}
    assert plan == {"us_stock": D("700.00"), "intl_stock": D("200.00"), "single_stock": D("100.00")}


def test_buy_plan_fills_gaps_first_and_adds_up():
    held = {"us_stock": D("9000"), "intl_stock": D("0"), "single_stock": D("1000")}
    plan = buy_plan(held, D("2000"), {"us_stock": D(70), "intl_stock": D(20), "single_stock": D(10)})
    amounts = {r["bucket"]: r["amount"] for r in plan}
    assert sum(amounts.values()) == D("2000")
    assert "us_stock" not in amounts             # already above its 70% share
    assert amounts["intl_stock"] > amounts["single_stock"]


def test_history():
    h = history([(dt.datetime(2027, 1, 1), D("1000")), (dt.datetime(2027, 2, 1), D("1100"))])
    assert h["change"] == D("100") and h["change_pct"] == D("10.0")
    assert history([])["series"] == []


def test_targets_roundtrip(engine):
    with session_scope(engine) as s:
        assert load_targets(s)["us_stock"] == D("70")
        save_targets(s, {"us_stock": D(60), "intl_stock": D(25), "single_stock": D(15)})
    with session_scope(engine) as s:
        assert load_targets(s)["intl_stock"] == D("25")


def test_summary_and_mcp_tool_without_positions(engine):
    from fin.web.summary import portfolio
    from fin_mcp import tools

    p = portfolio(engine, new_money=500)
    assert p["positions"] == [] and len(p["buy_plan"]) == 3
    out = tools.portfolio(engine)
    assert any("No invested positions" in w for w in out["warnings"])
