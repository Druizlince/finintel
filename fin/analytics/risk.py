"""Phase 4d - risk: cash runway, stress tests, estimated volatility, risk flags.

Pure functions on plain numbers (easy to test). Scenario shocks and volatilities are
ESTIMATES based on history (labeled as such); they are not predictions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Typical yearly volatility per bucket (long-run estimates, %).
VOL = {"us_stock": 16.0, "intl_stock": 17.0, "single_stock": 40.0, "bond": 6.0,
       "real_estate": 20.0, "crypto": 65.0, "other": 20.0}
EXPECTED_RETURN = 7.0   # long-run nominal estimate for a stock-heavy mix (%)

# Scenario shocks (% change). single_stock uses beta x market shock, capped.
SCENARIOS = [
    {"key": "correction", "name": "Normal correction",
     "about": "Happens every 1-2 years on average; usually recovers within months.",
     "shock": {"us_stock": -10, "intl_stock": -10, "bond": 1, "real_estate": -12, "crypto": -30,
               "other": -10}, "market": -10, "recovery": "a few months (typical)"},
    {"key": "bear", "name": "Bear market (like 2022)",
     "about": "S&P 500 fell about 25% in 2022; tech fell more.",
     "shock": {"us_stock": -25, "intl_stock": -25, "bond": -10, "real_estate": -30,
               "crypto": -65, "other": -25}, "market": -25,
     "recovery": "about 2 years for the S&P 500 (Jan 2022 high regained Jan 2024)"},
    {"key": "crash", "name": "Financial crisis (like 2008)",
     "about": "S&P 500 fell about 55% from Oct 2007 to Mar 2009.",
     "shock": {"us_stock": -50, "intl_stock": -55, "bond": 5, "real_estate": -65,
               "crypto": -75, "other": -50}, "market": -50,
     "recovery": "about 4-5 years including dividends"},
    {"key": "tech", "name": "Tech/AI bust (like 2000-02)",
     "about": "Nasdaq fell about 78%; the broad market about 49%.",
     "shock": {"us_stock": -45, "intl_stock": -45, "bond": 8, "real_estate": -10,
               "crypto": -75, "other": -35}, "market": -45,
     "recovery": "S&P 500 about 7 years; Nasdaq about 15 years"},
]
TECH_HEAVY = {"QQQ", "QQQM", "VGT", "XLK", "SMH", "SOXX", "VUG", "SCHG"}
DEFAULT_BETA = 1.5
SINGLE_CAP = -90.0


@dataclass
class Holding:
    ticker: str
    bucket: str
    value: float
    beta: float | None = None


@dataclass
class RiskReport:
    runway: dict = field(default_factory=dict)
    stress: list[dict] = field(default_factory=list)
    volatility: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    total: float = 0.0


def runway(liquid: float, monthly_need: float, paycheck_monthly: float,
           tuition_monthly: float = 0.0, bills_next_6m: float = 0.0) -> dict:
    """How long cash lasts. 'Without sponsor' = only paychecks keep coming."""
    cost = monthly_need + tuition_monthly
    gap = max(cost - paycheck_monthly, 0.0)
    after_bills = liquid - bills_next_6m
    out = {
        "liquid": round(liquid, 2), "monthly_costs": round(cost, 2),
        "paycheck_monthly": round(paycheck_monthly, 2),
        "months_no_income": round(liquid / cost, 1) if cost > 0 else None,
        "months_without_sponsor": (round(liquid / gap, 1) if gap > 0 else None),
        "bills_next_6m": round(bills_next_6m, 2),
        "cash_after_planned_bills": round(after_bills, 2),
    }
    return out


def _shock(h: Holding, sc: dict) -> float:
    if h.bucket == "single_stock":
        beta = h.beta if h.beta is not None else DEFAULT_BETA
        return max(sc["market"] * beta, SINGLE_CAP)
    s = sc["shock"].get(h.bucket, sc["shock"].get("other", sc["market"]))
    if h.bucket == "us_stock" and h.ticker.upper() in TECH_HEAVY:
        # Tech-heavy funds fall harder: ~1.3x in corrections/bear markets (2022: Nasdaq -33%
        # vs S&P -25%), ~1.1x in a broad crash (2008), and far more in a tech bust (2000-02).
        s = {"tech": -75.0, "crash": s * 1.1}.get(sc["key"], s * 1.3)
    return float(s)


def stress(holdings: list[Holding]) -> list[dict]:
    total = sum(h.value for h in holdings)
    out = []
    for sc in SCENARIOS:
        loss = sum(h.value * _shock(h, sc) / 100 for h in holdings)
        worst = sorted(((h.ticker, h.value * _shock(h, sc) / 100) for h in holdings),
                       key=lambda x: x[1])[:3]
        out.append({"scenario": sc["name"], "about": sc["about"], "recovery": sc["recovery"],
                    "loss": round(loss, 2), "loss_pct": round(loss / total * 100, 1) if total else 0.0,
                    "value_after": round(total + loss, 2),
                    "biggest_hits": [{"ticker": t, "loss": round(v, 2)} for t, v in worst if v < 0]})
    return out


def volatility(holdings: list[Holding]) -> dict:
    total = sum(h.value for h in holdings)
    if total <= 0:
        return {}
    # Weighted volatility = conservative upper bound (assumes everything moves together).
    vol = 0.0
    for h in holdings:
        v = VOL.get(h.bucket, 20.0)
        if h.bucket == "single_stock" and h.beta:
            v = max(v, 16.0 * h.beta)
        vol += h.value / total * v
    bad_year = EXPECTED_RETURN - 1.65 * vol           # ~1-in-20 bad year (normal approx.)
    return {"annual_volatility_pct": round(vol, 1),
            "typical_year_range_pct": [round(EXPECTED_RETURN - vol, 1), round(EXPECTED_RETURN + vol, 1)],
            "one_in_20_bad_year_pct": round(bad_year, 1),
            "one_in_20_bad_year_dollars": round(total * bad_year / 100, 2),
            "note": "Estimates from long-run averages; real losses can be larger."}


def build(holdings: list[Holding], run: dict, months_to_need_money: float | None = None) -> RiskReport:
    r = RiskReport(runway=run, stress=stress(holdings), volatility=volatility(holdings),
                   total=round(sum(h.value for h in holdings), 2))
    m = run.get("months_without_sponsor")
    if run.get("cash_after_planned_bills", 0) < 0:
        r.flags.append(f"Cash ({run['liquid']:,.0f}) doesn't cover bills due in the next 6 months "
                       f"({run['bills_next_6m']:,.0f}) - don't invest money you'll need for these.")
    if run.get("months_no_income") is not None and run["months_no_income"] < 1:
        r.flags.append("Less than 1 month of costs in cash - one surprise bill could force a "
                       "card balance or selling investments.")
    if m is not None and m < 3:
        r.flags.append(f"If sponsor support paused, cash would last about {m} months - "
                       "keep a buffer before investing more.")
    if r.total and r.stress:
        crash = next(s for s in r.stress if "2008" in s["scenario"])
        r.flags.append(f"In a 2008-style crash this portfolio could drop about "
                       f"{-crash['loss']:,.0f} ({crash['loss_pct']}%). Only invest money you "
                       "won't need for 5+ years.")
    singles = sum(h.value for h in holdings if h.bucket == "single_stock")
    if r.total and singles / r.total > 0.10:
        r.flags.append(f"Individual stocks are {singles / r.total * 100:.0f}% - above your 10% limit.")
    return r


def safe_beta(x: object) -> float | None:
    try:
        b = float(x)  # type: ignore[arg-type]
        return b if math.isfinite(b) and 0 < b < 5 else None
    except (TypeError, ValueError):
        return None
