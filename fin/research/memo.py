"""The 12-point investment memo. Pure function: data in, structured memo out.

Every point lists FACTS (numbers from the data providers, with dates), and an ASSESSMENT
that is clearly an OPINION based on simple, stated rules of thumb. A score per point:
green / yellow / red / gray (no data). This is a checklist to support a decision, not advice.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

SINGLE_STOCK_LIMIT_PCT = 10.0


def _f(x: Any) -> float | None:
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def _money(x: float | None) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"${x / div:,.1f}{suf}"
    return f"${x:,.2f}"


def _p(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{d}f}%"


def _point(n: int, title: str, facts: list[str], assessment: str, score: str) -> dict:
    return {"n": n, "title": title, "facts": [f for f in facts if f], "assessment": assessment,
            "score": score}


def build_memo(data: dict, macro: dict, gaps: list[str], fit: dict | None = None) -> dict:
    t = data.get("ticker", "?")
    q = data.get("quote") or {}
    prof = data.get("profile") or {}
    m = data.get("metrics") or {}
    recs = data.get("recommendations") or []
    news = data.get("news") or []
    inc = data.get("income") or []
    bal = (data.get("balance") or [{}])[0] if data.get("balance") else {}
    cf = (data.get("cashflow") or [{}])[0] if data.get("cashflow") else {}
    fit = fit or {}
    price = _f(q.get("c"))
    points = []

    # 1 Business
    mcap = _f(prof.get("marketCapitalization"))
    facts = [f"{prof.get('name', t)} - {prof.get('finnhubIndustry', 'industry n/a')}, "
             f"{prof.get('country', '')} ({prof.get('exchange', '')})".strip(),
             f"Market value: {_money(mcap * 1e6) if mcap else 'n/a'}",
             f"Price: {_money(price)} ({_p(_f(q.get('dp')), 2)} today)" if price else ""]
    points.append(_point(1, "Business", facts,
                         "Describe the moat in your own words: why do customers keep paying, and "
                         "what could replace it? (needs your judgment)",
                         "gray" if not prof else "info"))

    # 2 Growth
    rg = _f(m.get("revenueGrowthTTMYoy"))
    eg = _f(m.get("epsGrowthTTMYoy"))
    cagr = None
    revs = [(_f(r.get("revenue")), r.get("fiscalYear") or str(r.get("date", ""))[:4]) for r in inc]
    revs = [r for r in revs if r[0]]
    if len(revs) >= 2 and revs[-1][0] > 0:
        years = len(revs) - 1
        cagr = ((revs[0][0] / revs[-1][0]) ** (1 / years) - 1) * 100
    facts = [f"Revenue growth (last 12 months vs year before): {_p(rg)}",
             f"EPS growth (last 12 months): {_p(eg)}",
             f"Revenue {revs[-1][1]}->{revs[0][1]}: {_money(revs[-1][0])} -> {_money(revs[0][0])} "
             f"({_p(cagr)} per year)" if cagr is not None else ""]
    g = rg if rg is not None else cagr
    score = "gray" if g is None else "green" if g > 15 else "yellow" if g >= 0 else "red"
    points.append(_point(2, "Growth", facts, {
        "green": "Growing fast (>15%/yr).", "yellow": "Growing slowly (0-15%/yr).",
        "red": "Shrinking - needs a turnaround story.", "gray": "No growth data."}[score], score))

    # 3 Profitability
    gm, nm, om = (_f(m.get("grossMarginTTM")), _f(m.get("netProfitMarginTTM")),
                  _f(m.get("operatingMarginTTM")))
    roe = _f(m.get("roeTTM"))
    facts = [f"Gross margin {_p(gm)}, operating margin {_p(om)}, net margin {_p(nm)}",
             f"Return on equity {_p(roe)}" if roe is not None else ""]
    score = "gray" if nm is None else "green" if nm > 15 else "yellow" if nm >= 5 else "red"
    points.append(_point(3, "Profitability", facts, {
        "green": "Highly profitable (net margin >15%).", "yellow": "Thin-to-moderate margins.",
        "red": "Barely profitable or losing money.", "gray": "No margin data."}[score], score))

    # 4 Balance sheet
    de = _f(m.get("totalDebt/totalEquityQuarterly"))
    cr = _f(m.get("currentRatioQuarterly"))
    cash = _f(bal.get("cashAndCashEquivalents"))
    debt = _f(bal.get("totalDebt"))
    facts = [f"Debt / equity {de:.2f}" if de is not None else "Debt / equity n/a",
             f"Current ratio {cr:.2f}" if cr is not None else "",
             f"Cash {_money(cash)} vs debt {_money(debt)}" if cash is not None and debt is not None else ""]
    if de is None:
        score = "gray"
    else:
        score = "green" if de < 0.5 else "yellow" if de <= 1.5 else "red"
        if cr is not None and cr < 1 and score == "green":
            score = "yellow"
    points.append(_point(4, "Balance sheet", facts, {
        "green": "Low debt - can survive a bad year.", "yellow": "Moderate debt - watch it.",
        "red": "Heavy debt - risky if business slows.", "gray": "No balance-sheet data."}[score],
        score))

    # 5 Cash flow
    fcf = _f(cf.get("freeCashFlow"))
    rev0 = revs[0][0] if revs else None
    fcfm = (fcf / rev0 * 100) if fcf is not None and rev0 else None
    facts = [f"Free cash flow (last fiscal year): {_money(fcf)}" if fcf is not None else
             "Free cash flow n/a", f"FCF margin {_p(fcfm)}" if fcfm is not None else ""]
    score = ("gray" if fcf is None else "red" if fcf <= 0 else
             "green" if (fcfm or 0) > 10 else "yellow")
    points.append(_point(5, "Cash flow", facts, {
        "green": "Turns sales into real cash.", "yellow": "Positive but modest cash generation.",
        "red": "Burning cash.", "gray": "No cash-flow data."}[score], score))

    # 6 Valuation
    pe, ps = _f(m.get("peTTM")), _f(m.get("psTTM"))
    ten = (macro.get("DGS10") or {}).get("value")
    ey = (100 / pe) if pe and pe > 0 else None
    facts = [f"P/E {pe:.1f}" if pe is not None else "P/E n/a",
             f"Price / sales {ps:.1f}" if ps is not None else "",
             f"Earnings yield {_p(ey, 2)} vs 10-year Treasury {_p(ten, 2)}" if ey and ten else ""]
    if pe is None:
        score = "gray"
    elif pe <= 0:
        score = "red"
    else:
        score = "green" if pe < 20 else "yellow" if pe <= 40 else "red"
    note = {"green": "Priced modestly for its earnings.", "yellow": "Fully priced - needs growth "
            "to justify it.", "red": "Expensive or unprofitable - high expectations baked in.",
            "gray": "No valuation data."}[score]
    if ey and ten and ey < ten:
        note += " Its earnings yield is below what risk-free Treasuries pay."
    if pe and pe > 0 and pe < 20 and g is not None and g > 30:
        note += " Low P/E with very fast growth - the market doubts the growth will last."
    points.append(_point(6, "Valuation", facts, note, score))

    # 7 Price action
    hi, lo = _f(m.get("52WeekHigh")), _f(m.get("52WeekLow"))
    r52, r13 = _f(m.get("52WeekPriceReturnDaily")), _f(m.get("13WeekPriceReturnDaily"))
    beta = _f(m.get("beta"))
    off = ((price / hi - 1) * 100) if price and hi else None
    facts = [f"52-week range {_money(lo)} - {_money(hi)}; now {_p(off)} from the high"
             if off is not None else "", f"Return: 13 weeks {_p(r13)}, 52 weeks {_p(r52)}",
             f"Beta {beta:.2f} (1.0 = moves like the market)" if beta is not None else ""]
    if off is None and beta is None:
        score = "gray"
    else:
        score = "yellow" if (off is not None and off < -30) or (beta or 0) > 1.6 else "green"
    points.append(_point(7, "Price action", facts,
                         "Deep drop from the high and/or very volatile: size small and buy in "
                         "steps." if score == "yellow" else
                         "Normal range." if score == "green" else "No price data.", score))

    # 8 Analysts
    latest = recs[0] if recs else {}
    tot = sum(int(latest.get(k, 0) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
    buys = int(latest.get("strongBuy", 0) or 0) + int(latest.get("buy", 0) or 0)
    share = buys / tot * 100 if tot else None
    facts = [f"{latest.get('period', '')}: strong buy {latest.get('strongBuy', 0)}, buy "
             f"{latest.get('buy', 0)}, hold {latest.get('hold', 0)}, sell {latest.get('sell', 0)}, "
             f"strong sell {latest.get('strongSell', 0)}" if tot else "No analyst data."]
    score = "gray" if share is None else "green" if share >= 70 else "yellow" if share >= 40 else "red"
    points.append(_point(8, "Analyst view", facts,
                         (f"{share:.0f}% of analysts say buy. Analysts are often too optimistic; "
                          "treat as a sentiment check.") if share is not None else
                         "No analyst data.", score))

    # 9 News / catalysts
    items = []
    for n in sorted(news, key=lambda x: -(x.get("datetime") or 0))[:6]:
        when = dt.datetime.fromtimestamp(n["datetime"]).date().isoformat() if n.get("datetime") else ""
        items.append(f"{when} {n.get('source', '')}: {str(n.get('headline', ''))[:140]}")
    points.append(_point(9, "Recent news", items or ["No recent headlines."],
                         "Read the 2-3 most important stories yourself before deciding.",
                         "info" if items else "gray"))

    # 10 Macro
    facts = [f"{v['label']}: {v['value']} ({v['date']})" for v in macro.values()]
    infl = (macro.get("CPIAUCSL") or {}).get("value")
    score = "gray" if not macro else (
        "yellow" if (ten or 0) > 4.5 or (infl or 0) > 3.5 else "green")
    points.append(_point(10, "Macro backdrop", facts or ["No macro data."], {
        "yellow": "High rates or inflation: pressure on stock valuations, safe money pays well.",
        "green": "Supportive backdrop.", "gray": "No macro data."}[score], score))

    # 11 Risks (automatic flags + things only you can judge)
    flags = [f"{p['title']}: {p['assessment']}" for p in points if p["score"] == "red"]
    flags.append("Check yourself: leadership, regulation/legal, competition, customer concentration.")
    points.append(_point(11, "Key risks", flags,
                         "Write the one thing that would make you sell (your 'thesis breaker').",
                         "red" if len(flags) > 2 else "yellow" if len(flags) > 1 else "info"))

    # 12 Fit with the plan
    facts = []
    owned = fit.get("owned_value") or 0.0
    invested = fit.get("invested") or 0.0
    base = invested + (fit.get("planned") or 0.0)
    if owned:
        facts.append(f"You own {_money(owned)} ({owned / invested * 100:.1f}% of investments).")
    else:
        facts.append("You don't own it directly yet.")
    if base:
        facts.append(f"Max position at {SINGLE_STOCK_LIMIT_PCT:.0f}%: about "
                     f"{_money(base * SINGLE_STOCK_LIMIT_PCT / 100)}"
                     + (f" (on {_money(base)} invested + planned)" if fit.get("planned") else ""))
    dy = _f(m.get("dividendYieldIndicatedAnnual"))
    if dy:
        facts.append(f"Dividend yield {_p(dy, 2)} - for nonresident aliens, 30% of US dividends is usually "
                     "withheld unless a tax treaty lowers it.")
    facts.append("Broad funds (VTI/QQQM) may already hold it - your real exposure is higher.")
    over = invested and owned / invested * 100 > SINGLE_STOCK_LIMIT_PCT
    points.append(_point(12, "Fit with your plan", facts,
                         "Over your 10% single-company limit - don't add more." if over else
                         "Keep it a satellite: max 10%, bought in 2 steps, held 1+ year.",
                         "red" if over else "info"))

    counts = {s: sum(1 for p in points if p["score"] == s) for s in ("green", "yellow", "red", "gray")}
    return {"ticker": t, "name": prof.get("name") or t, "price": price,
            "as_of": data.get("fetched"), "points": points, "scorecard": counts, "gaps": gaps,
            "disclaimer": "Facts come from FRED, Finnhub and FMP (may be delayed). Assessments are "
                          "simple rules of thumb (opinion), not financial advice."}


def render(memo: dict) -> str:
    icon = {"green": "[+]", "yellow": "[~]", "red": "[!]", "gray": "[ ]", "info": "[i]"}
    lines = [f"12-POINT MEMO: {memo['name']} ({memo['ticker']})  price {_money(memo['price'])}"
             f"  as of {memo['as_of']}",
             "Scorecard: " + ", ".join(f"{v} {k}" for k, v in memo["scorecard"].items() if v), ""]
    for p in memo["points"]:
        lines.append(f"{icon.get(p['score'], '')} {p['n']:>2}. {p['title']}")
        for f in p["facts"]:
            lines.append(f"       FACT  {f}")
        lines.append(f"       VIEW  {p['assessment']}")
    if memo["gaps"]:
        lines += ["", "Data gaps:"] + [f"  - {g}" for g in memo["gaps"]]
    lines += ["", memo["disclaimer"]]
    return "\n".join(lines)
