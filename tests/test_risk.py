from fin.analytics.risk import Holding, build, runway, stress, volatility


def test_runway_math():
    r = runway(liquid=3000, monthly_need=2300, paycheck_monthly=1300, tuition_monthly=700,
               bills_next_6m=4000)
    assert r["monthly_costs"] == 3000 and r["months_no_income"] == 1.0
    assert r["months_without_sponsor"] == 1.8            # 3000 / (3000 - 1300)
    assert r["cash_after_planned_bills"] == -1000
    assert runway(1000, 1000, 2000)["months_without_sponsor"] is None   # paychecks cover it


def test_stress_uses_beta_and_tech_tilt():
    h = [Holding("FSKAX", "us_stock", 7000), Holding("FTIHX", "intl_stock", 2000),
         Holding("NVDA", "single_stock", 1000, beta=2.4)]
    s = {x["scenario"]: x for x in stress(h)}
    crash = s["Financial crisis (like 2008)"]
    # 7000*-50% + 2000*-55% + 1000*max(-50*2.4, -90) = -3500 -1100 -900
    assert crash["loss"] == -5500 and crash["loss_pct"] == -55.0
    assert crash["biggest_hits"][0]["ticker"] == "FSKAX"
    q = stress([Holding("QQQM", "us_stock", 1000)])
    assert {x["scenario"]: x["loss"] for x in q}["Bear market (like 2022)"] == -325.0
    assert {x["scenario"]: x["loss"] for x in q}["Tech/AI bust (like 2000-02)"] == -750.0
    assert {x["scenario"]: x["loss"] for x in q}["Financial crisis (like 2008)"] == -550.0


def test_volatility_and_flags():
    h = [Holding("FSKAX", "us_stock", 8000), Holding("NVDA", "single_stock", 2000, beta=2.4)]
    v = volatility(h)
    assert v["annual_volatility_pct"] == round(0.8 * 16 + 0.2 * 40, 1)
    rep = build(h, runway(500, 2300, 1300, 700, 4000))
    text = " ".join(rep.flags)
    assert "doesn't cover bills" in text and "Less than 1 month" in text
    assert "2008-style crash" in text and "above your 10% limit" in text
    assert volatility([]) == {}


def test_summary_risk_empty_db_and_whatif(engine, monkeypatch):
    from fin.web import summary
    from fin_mcp import tools

    monkeypatch.setattr(summary, "_beta_lookup", lambda tickers: {})
    r = summary.risk(engine)
    assert r["total"] == 0 and r["stress"][0]["loss"] == 0
    r = summary.risk(engine, new_money=20000, keep_cash=7500, pick="NVDA")
    assert r["total"] == 20000 and r["runway"]["liquid"] == 7500
    assert any(h["ticker"] == "NVDA" and h["hypothetical"] for h in r["holdings"])
    out = tools.risk(engine, new_money=1000)
    assert out["data"]["total"] == 1000
