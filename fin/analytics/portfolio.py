"""Phase 4b - portfolio analytics: allocation vs targets, concentration, gains, new-money plan.

Pure functions over Position objects (easy to test) plus load_positions() for the database.
Cash (money market, uninvested cash) is shown separately and is NOT part of the allocation:
in the user's plan cash in the brokerage is a bill reserve (tuition), not an investment choice.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")

BUCKETS = {
    "us_stock": "US stock funds",
    "intl_stock": "International stock funds",
    "single_stock": "Individual stocks",
    "bond": "Bonds / Treasuries",
    "real_estate": "Real estate (REITs)",
    "crypto": "Crypto",
    "other": "Other / unclassified",
}

# Long-term targets from the plan (% of invested money, cash excluded). Editable via settings.
DEFAULT_TARGETS = {"us_stock": Decimal("70"), "intl_stock": Decimal("20"),
                   "single_stock": Decimal("10")}
SINGLE_STOCK_LIMIT = Decimal("10")   # any one company above this % of invested = warning
FUND_LIMIT = Decimal("80")           # one fund above this % = low diversification note

# Default fund to buy for each bucket when there is no holding there yet (Fidelity zero-min).
DEFAULT_BUY = {"us_stock": "FSKAX", "intl_stock": "FTIHX", "bond": "FXNAX",
               "real_estate": "FREL"}

KNOWN = {
    # US broad / large-cap / growth
    **dict.fromkeys(["VTI", "FSKAX", "FZROX", "ITOT", "SCHB", "SWTSX", "VOO", "FXAIX", "SPY",
                     "IVV", "SPLG", "QQQ", "QQQM", "VUG", "SCHG", "VGT", "SCHD", "VIG",
                     "FNILX", "VTSAX", "VFIAX", "RSP", "IWM", "VB", "VO"], "us_stock"),
    # International
    **dict.fromkeys(["VXUS", "FTIHX", "FZILX", "IXUS", "VEA", "VWO", "IEMG", "SWISX", "EFA",
                     "VEU", "SPDW", "FSPSX", "VTIAX", "FTEC_INTL"], "intl_stock"),
    # Bonds / Treasuries
    **dict.fromkeys(["BND", "AGG", "FXNAX", "BNDX", "SGOV", "BIL", "VGIT", "SCHZ", "TLT",
                     "IEF", "SHY", "VBTLX", "FUAMX"], "bond"),
    # Real estate
    **dict.fromkeys(["VNQ", "FREL", "SCHH", "XLRE", "VNQI"], "real_estate"),
    # Crypto funds
    **dict.fromkeys(["FBTC", "IBIT", "BITB", "ETHA", "FETH", "BTC", "ETH"], "crypto"),
}
CASH_TICKERS = {"SPAXX", "FDRXX", "FZFXX", "SPRXX", "FCASH", "CORE", "VMFXX", "SWVXX",
                "CASH", "USD"}


@dataclass
class Position:
    ticker: str | None
    name: str
    sec_type: str            # stock | etf | mutual_fund | bond | cash | crypto | option | other
    market_value: Decimal
    quantity: Decimal = ZERO
    price: Decimal | None = None
    cost_basis: Decimal | None = None
    account: str = ""


def bucket_of(p: Position) -> str:
    """Return a bucket key, or 'cash'."""
    t = (p.ticker or "").upper()
    if t in CASH_TICKERS or p.sec_type == "cash" or "money market" in p.name.lower():
        return "cash"
    if t in KNOWN:
        return KNOWN[t]
    if p.sec_type == "stock":
        return "single_stock"
    if p.sec_type in ("bond",):
        return "bond"
    if p.sec_type == "crypto":
        return "crypto"
    name = p.name.lower()
    if "international" in name or "ex-us" in name or "ex us" in name or "world ex" in name:
        return "intl_stock"
    if "total stock" in name or "s&p 500" in name or "500 index" in name or "nasdaq" in name:
        return "us_stock"
    if "bond" in name or "treasury" in name:
        return "bond"
    if "real estate" in name or "reit" in name:
        return "real_estate"
    return "other"


def pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole <= 0:
        return ZERO
    return (part / whole * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


@dataclass
class Analysis:
    total: Decimal
    invested: Decimal
    cash: Decimal
    by_bucket: dict[str, Decimal]
    allocation: list[dict] = field(default_factory=list)   # bucket rows with % and drift
    top: list[dict] = field(default_factory=list)          # largest positions
    gains: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    buy_plan: list[dict] = field(default_factory=list)
    new_money: Decimal = ZERO


def analyze(positions: list[Position], extra_cash: Decimal = ZERO,
            targets: dict[str, Decimal] | None = None,
            new_money: Decimal = ZERO) -> Analysis:
    targets = {k: Decimal(str(v)) for k, v in (targets or DEFAULT_TARGETS).items()}
    by_bucket: dict[str, Decimal] = {}
    cash = extra_cash
    invested_pos = []
    for p in positions:
        b = bucket_of(p)
        if b == "cash":
            cash += p.market_value
            continue
        by_bucket[b] = by_bucket.get(b, ZERO) + p.market_value
        invested_pos.append((p, b))
    invested = sum(by_bucket.values(), ZERO)
    a = Analysis(total=invested + cash, invested=invested, cash=cash, by_bucket=by_bucket,
                 new_money=new_money)

    # Allocation vs target
    for key in list(BUCKETS):
        cur = by_bucket.get(key, ZERO)
        tgt = targets.get(key)
        if cur == 0 and tgt is None:
            continue
        cur_pct = pct(cur, invested)
        row = {"bucket": key, "label": BUCKETS[key], "value": cur, "pct": cur_pct,
               "target_pct": tgt, "drift": (cur_pct - tgt) if tgt is not None else None}
        a.allocation.append(row)
        if invested > 0 and tgt is not None and abs(cur_pct - tgt) >= 5:
            word = "above" if cur_pct > tgt else "below"
            a.warnings.append(f"{BUCKETS[key]} are {cur_pct}% of investments, {word} the "
                              f"{tgt}% target.")
        elif invested > 0 and tgt is None and cur > 0:
            a.warnings.append(f"{BUCKETS[key]} ({cur_pct}%) aren't in your targets.")

    # Concentration (by ticker, summed across accounts)
    by_ticker: dict[str, tuple[Decimal, str, str]] = {}
    for p, b in invested_pos:
        k = (p.ticker or p.name).upper()
        v, _, _ = by_ticker.get(k, (ZERO, b, p.name))
        by_ticker[k] = (v + p.market_value, b, p.name)
    for k, (v, b, name) in sorted(by_ticker.items(), key=lambda x: -x[1][0])[:10]:
        share = pct(v, invested)
        a.top.append({"ticker": k, "name": name, "bucket": b, "value": v, "pct": share})
        if b == "single_stock" and share > SINGLE_STOCK_LIMIT:
            a.warnings.append(f"{k} is {share}% of your investments - above the "
                              f"{SINGLE_STOCK_LIMIT}% single-company limit.")
        if b != "single_stock" and share > FUND_LIMIT and len(by_ticker) > 1:
            a.warnings.append(f"{k} is {share}% of investments; fine for a broad index fund, "
                              "but check it's diversified.")
    singles = by_bucket.get("single_stock", ZERO)
    if invested > 0 and "single_stock" not in targets and pct(singles, invested) > SINGLE_STOCK_LIMIT:
        a.warnings.append(f"Individual stocks total {pct(singles, invested)}% "
                          f"(suggested max {SINGLE_STOCK_LIMIT}%).")

    # Gains (only where cost basis is known)
    known = [(p, p.cost_basis) for p, _ in invested_pos if p.cost_basis is not None]
    if known:
        mv = sum((p.market_value for p, _ in known), ZERO)
        cb = sum((c for _, c in known), ZERO)
        a.gains = {"market_value": mv, "cost_basis": cb, "gain": mv - cb,
                   "gain_pct": pct(mv - cb, cb) if cb > 0 else None,
                   "positions_without_cost_basis": len(invested_pos) - len(known),
                   "by_position": [{"ticker": p.ticker, "gain": p.market_value - c,
                                    "gain_pct": pct(p.market_value - c, c) if c > 0 else None}
                                   for p, c in known]}

    # New-money plan: buy-only, fill the buckets furthest below target first.
    if new_money > 0:
        a.buy_plan = buy_plan(by_bucket, new_money, targets, invested_pos)
    return a


def buy_plan(by_bucket: dict[str, Decimal], money: Decimal, targets: dict[str, Decimal],
             invested_pos: list[tuple[Position, str]] | None = None) -> list[dict]:
    """Split new money so the portfolio moves toward the target mix without selling."""
    tsum = sum(targets.values(), ZERO)
    if tsum <= 0:
        return []
    total_after = sum(by_bucket.values(), ZERO) + money
    gaps = {}
    for k, t in targets.items():
        want = total_after * t / tsum
        gaps[k] = max(want - by_bucket.get(k, ZERO), ZERO)
    gap_total = sum(gaps.values(), ZERO)
    if gap_total <= 0:
        return []
    held = {}
    for p, b in invested_pos or []:
        if p.ticker and (b not in held or p.market_value > held[b][1]):
            held[b] = (p.ticker, p.market_value)
    plan = []
    for k, g in sorted(gaps.items(), key=lambda x: -x[1]):
        amt = (money * g / gap_total).quantize(CENT, ROUND_HALF_UP)
        if amt <= 0:
            continue
        if k == "single_stock":
            suggestion = held.get(k, ("your researched pick(s)",))[0]
            note = "only after a 12-point memo; split into 2 buys"
        else:
            suggestion = held.get(k, (DEFAULT_BUY.get(k, ""),))[0]
            note = ""
        plan.append({"bucket": k, "label": BUCKETS[k], "amount": amt, "suggest": suggestion,
                     "note": note})
    # Fix rounding so the plan adds up exactly.
    diff = money - sum((r["amount"] for r in plan), ZERO)
    if plan and diff:
        plan[0]["amount"] += diff
    return plan


def history(points: list[tuple[dt.datetime, Decimal]]) -> dict:
    """Total portfolio value over time (one value per snapshot date)."""
    by_day: dict[dt.date, Decimal] = {}
    for when, v in points:
        by_day[when.date()] = by_day.get(when.date(), ZERO) + v
    series = sorted(by_day.items())
    if not series:
        return {"series": [], "change": None, "change_pct": None}
    first, last = series[0][1], series[-1][1]
    return {"series": [{"date": d.isoformat(), "value": v} for d, v in series],
            "change": last - first, "change_pct": pct(last - first, first) if first > 0 else None,
            "note": "Value change includes money you added or withdrew (not a pure return)."}


# ---------------- database side ----------------

TARGETS_KEY = "portfolio_targets"


def load_targets(s) -> dict[str, Decimal]:  # noqa: ANN001
    from fin.db.models import Setting

    row = s.get(Setting, TARGETS_KEY)
    if row is None or not row.value:
        return dict(DEFAULT_TARGETS)
    return {k: Decimal(str(v)) for k, v in json.loads(row.value).items()}


def save_targets(s, targets: dict[str, Decimal]) -> None:  # noqa: ANN001
    from fin.db.models import Setting

    bad = [k for k in targets if k not in BUCKETS]
    if bad:
        raise ValueError(f"unknown bucket(s): {', '.join(bad)}")
    if sum(targets.values(), ZERO) != 100:
        raise ValueError("targets must add up to 100")
    data = json.dumps({k: str(v) for k, v in targets.items()})
    row = s.get(Setting, TARGETS_KEY)
    if row is None:
        s.add(Setting(key=TARGETS_KEY, value=data))
    else:
        row.value = data


def load_positions(s) -> tuple[list[Position], Decimal, list[tuple[dt.datetime, Decimal]], str | None]:  # noqa: ANN001,E501
    """Latest brokerage positions, uninvested cash, value history, latest as-of."""
    from sqlalchemy import func, select

    from fin.db.models import Account, BalanceSnapshot, HoldingSnapshot, Institution, Security

    positions: list[Position] = []
    extra_cash = ZERO
    hist: list[tuple[dt.datetime, Decimal]] = []
    as_of = None
    accounts = s.scalars(select(Account).join(Institution).where(
        Account.type.in_(["brokerage", "ira", "roth_ira", "401k", "403b", "457b", "hsa"]),
        Account.is_active.is_(True))).all()
    for a in accounts:
        label = f"{a.institution.name} {a.name} ...{a.mask or ''}"
        latest = s.scalar(select(func.max(HoldingSnapshot.as_of))
                          .where(HoldingSnapshot.account_id == a.id))
        held_value = ZERO        # non-cash positions
        cash_positions = ZERO    # money-market / cash listed as positions
        if latest is not None:
            as_of = max(filter(None, [as_of, latest]))
            for h, sec in s.execute(select(HoldingSnapshot, Security).join(Security).where(
                    HoldingSnapshot.account_id == a.id, HoldingSnapshot.as_of == latest)):
                mv = Decimal(h.market_value if h.market_value is not None
                             else (h.quantity or 0) * (h.price or 0))
                pos = Position(
                    ticker=sec.ticker, name=sec.name, sec_type=sec.type, market_value=mv,
                    quantity=Decimal(h.quantity or 0), price=h.price, account=label,
                    cost_basis=Decimal(h.cost_basis) if h.cost_basis is not None else None)
                if bucket_of(pos) == "cash":
                    cash_positions += mv
                else:
                    held_value += mv
                    positions.append(pos)
            for when, v in s.execute(select(HoldingSnapshot.as_of, func.sum(
                    HoldingSnapshot.market_value)).where(HoldingSnapshot.account_id == a.id)
                    .group_by(HoldingSnapshot.as_of)):
                if v is not None:
                    hist.append((when, Decimal(v)))
        bal = s.scalar(select(BalanceSnapshot).where(BalanceSnapshot.account_id == a.id)
                       .order_by(BalanceSnapshot.as_of.desc()).limit(1))
        if bal is not None and bal.current is not None:
            # Cash = total account value minus invested positions. Robust whether the broker
            # lists the money market as a position, as cash, or both (no double counting).
            extra_cash += max(Decimal(bal.current) - held_value, ZERO)
            as_of = max(filter(None, [as_of, bal.as_of]))
        else:
            extra_cash += cash_positions + (Decimal(bal.available) if bal is not None
                                            and bal.available is not None else ZERO)
    return positions, extra_cash, hist, (as_of.isoformat() if as_of else None)
