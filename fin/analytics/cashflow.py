"""Cash-flow analytics. Pure functions over plain transaction records - no database, no network.

Sign convention (same as the database): amount < 0 = money out, amount > 0 = money in.
Categories are Plaid's personal_finance_category (primary / detailed).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")
TRANSFER_CATS = {"TRANSFER_IN", "TRANSFER_OUT"}
UNLINKED_CARD = "CARD_PAYMENTS_UNLINKED"


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Txn:
    id: int
    account_id: int
    account_type: str  # checking / savings / credit / ...
    date: dt.date
    amount: Decimal
    merchant: str | None = None
    category: str | None = None
    detail: str | None = None
    pending: bool = False


# --- classification ----------------------------------------------------------------------

def _is_card_payment(t: Txn) -> bool:
    return (t.detail or "").startswith("LOAN_PAYMENTS_CREDIT_CARD")


def _transferish(t: Txn) -> bool:
    return t.category in TRANSFER_CATS or t.category == "LOAN_PAYMENTS" or _is_card_payment(t)


def find_internal_transfers(txns: list[Txn], window_days: int = 5) -> set[int]:
    """Pair money leaving one of YOUR accounts with the same amount arriving in another of
    your accounts within a few days (e.g. TD -> Chase card payment). Both sides are excluded
    from income and spending, so nothing is counted twice."""
    outs = sorted((t for t in txns if not t.pending and t.amount < 0), key=lambda t: t.date)
    ins = [t for t in txns if not t.pending and t.amount > 0]
    used: set[int] = set()
    internal: set[int] = set()
    for o in outs:
        best: tuple[int, Txn] | None = None
        for i in ins:
            if i.id in used or i.account_id == o.account_id or i.amount != -o.amount:
                continue
            if not (_transferish(o) or _transferish(i)):
                continue
            gap = abs((i.date - o.date).days)
            if gap <= window_days and (best is None or gap < best[0]):
                best = (gap, i)
        if best:
            used.add(best[1].id)
            internal.update({o.id, best[1].id})
    return internal


def classify(t: Txn, internal: set[int]) -> str:
    """income | spending | external_in | external_out | internal | pending"""
    if t.pending:
        return "pending"
    if t.id in internal:
        return "internal"
    if t.category == "INCOME" and t.amount > 0:
        return "income"
    if t.account_type == "credit" and t.amount > 0 and _transferish(t):
        return "external_in"  # card paid from an account we don't track
    if _is_card_payment(t) and t.amount < 0:
        return "spending"  # paying a card we don't track (e.g. Apple Card) = its purchases
    if t.category in TRANSFER_CATS:
        return "external_in" if t.amount > 0 else "external_out"
    return "spending"  # purchases (negative) and refunds (positive, reduce spending)


def spend_category(t: Txn) -> str:
    return UNLINKED_CARD if _is_card_payment(t) else (t.category or "UNCATEGORIZED")


# --- monthly totals --------------------------------------------------------------------------

@dataclass
class Month:
    key: str
    income: Decimal = ZERO
    spending: Decimal = ZERO
    external_in: Decimal = ZERO
    external_out: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        return self.income + self.external_in - self.spending - self.external_out


def monthly(txns: list[Txn], internal: set[int]) -> dict[str, Month]:
    months: dict[str, Month] = {}
    for t in txns:
        kind = classify(t, internal)
        if kind in ("internal", "pending"):
            continue
        m = months.setdefault(f"{t.date:%Y-%m}", Month(f"{t.date:%Y-%m}"))
        if kind == "income":
            m.income += t.amount
        elif kind == "spending":
            m.spending -= t.amount
        elif kind == "external_in":
            m.external_in += t.amount
        elif kind == "external_out":
            m.external_out -= t.amount
    return months


def average(months: dict[str, Month], keys: list[str]) -> Month:
    n = Decimal(len(keys)) or Decimal(1)
    avg = Month("average")
    for k in keys:
        m = months.get(k, Month(k))
        avg.income += m.income
        avg.spending += m.spending
        avg.external_in += m.external_in
        avg.external_out += m.external_out
    for f in ("income", "spending", "external_in", "external_out"):
        setattr(avg, f, money(getattr(avg, f) / n))
    return avg


def spending_by_category(txns: list[Txn], internal: set[int], keys: list[str]) -> dict[str, Decimal]:
    keyset, n = set(keys), Decimal(len(keys) or 1)
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for t in txns:
        if f"{t.date:%Y-%m}" in keyset and classify(t, internal) == "spending":
            totals[spend_category(t)] -= t.amount
    return dict(sorted(((k, money(v / n)) for k, v in totals.items()), key=lambda kv: -kv[1]))


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def complete_months(txns: list[Txn], today: dt.date, max_months: int) -> list[str]:
    """Most recent complete calendar months for which EVERY account already has data."""
    firsts: dict[int, dt.date] = {}
    for t in txns:
        if t.account_id not in firsts or t.date < firsts[t.account_id]:
            firsts[t.account_id] = t.date
    if not firsts:
        return []
    start = max(firsts.values())
    y, m = (start.year, start.month) if start.day == 1 else _add_months(start.year, start.month, 1)
    end_y, end_m = _add_months(today.year, today.month, -1)
    keys = []
    while (y, m) <= (end_y, end_m):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = _add_months(y, m, 1)
    return keys[-max_months:]


# --- recurring charges -----------------------------------------------------------------------

CADENCES = [  # name, min gap, max gap (days), per-month factor
    ("weekly", 5, 9, Decimal(52) / 12),
    ("every 2 weeks", 12, 17, Decimal(26) / 12),
    ("monthly", 25, 35, Decimal(1)),
    ("quarterly", 80, 100, Decimal(1) / 3),
    ("yearly", 340, 390, Decimal(1) / 12),
]


@dataclass
class Recurring:
    merchant: str
    direction: str  # "out" or "in"
    category: str | None
    cadence: str
    typical_amount: Decimal
    per_month: Decimal
    count: int
    last: dt.date
    next_expected: dt.date
    active: bool


def normalize_merchant(name: str | None) -> str:
    s = re.sub(r"[^a-z ]", " ", (name or "").lower())
    words = [w for w in s.split() if len(w) > 1]
    return " ".join(words[:3]) or (name or "unknown").lower()


def detect_recurring(txns: list[Txn], internal: set[int], today: dt.date) -> list[Recurring]:
    groups: dict[tuple[str, str], list[Txn]] = defaultdict(list)
    for t in txns:
        kind = classify(t, internal)
        if kind in ("internal", "pending") or t.amount == 0:
            continue
        groups[("out" if t.amount < 0 else "in", normalize_merchant(t.merchant))].append(t)

    found: list[Recurring] = []
    for (direction, merchant), items in groups.items():
        items.sort(key=lambda t: t.date)
        dates = sorted({t.date for t in items})
        if len(dates) < 2:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        gap = statistics.median(gaps)
        cadence = next((c for c in CADENCES if c[1] <= gap <= c[2]), None)
        if cadence is None or (len(dates) < 3 and cadence[0] != "yearly"):
            continue
        if cadence[0] == "every 2 weeks" and any(g != 14 for g in gaps):
            # Uneven 13-17 day gaps (15th + month-end) = paid twice a month, not every 2 weeks.
            cadence = ("twice a month", 12, 17, Decimal(2))
        amounts = [abs(t.amount) for t in items]
        med = Decimal(statistics.median(amounts))
        if any(abs(a - med) > max(med * Decimal("0.25"), Decimal(5)) for a in amounts):
            continue
        last = dates[-1]
        if cadence[0] == "monthly":
            ny, nm = _add_months(last.year, last.month, 1)
            day = min(last.day, calendar.monthrange(ny, nm)[1])
            next_expected = dt.date(ny, nm, day)
        else:
            next_expected = last + dt.timedelta(days=round(gap))
        found.append(Recurring(
            merchant=merchant, direction=direction, category=spend_category(items[-1]),
            cadence=cadence[0], typical_amount=money(med), per_month=money(med * cadence[3]),
            count=len(items), last=last, next_expected=next_expected,
            active=(today - last).days <= gap * 1.5,
        ))
    return sorted(found, key=lambda r: (r.direction, -r.per_month))


# --- the plan ---------------------------------------------------------------------------------

@dataclass
class Plan:
    monthly_income: Decimal
    monthly_need: Decimal
    monthly_transfers_in: Decimal
    counted_transfers_in: bool
    monthly_surplus: Decimal
    cash: Decimal
    card_owed: Decimal
    net_cash: Decimal
    lump: Decimal
    reserve: Decimal
    ef_months: int
    ef_target: Decimal
    deficit_buffer_months: int
    deficit_buffer: Decimal
    pool: Decimal
    investable_now: Decimal
    investable_monthly: Decimal
    runway_months: Decimal | None
    months_to_fund: Decimal | None = None
    notes: list[str] = field(default_factory=list)


def build_plan(
    avg: Month, cash: Decimal, card_owed: Decimal, *, ef_months: int = 6,
    lump: Decimal = ZERO, reserve: Decimal = ZERO, deficit_buffer_months: int = 12,
    include_transfers_in: bool = False, ef_exclude: Decimal = ZERO,
) -> Plan:
    """ef_exclude: monthly amount left out of the emergency-fund base (e.g. tuition, which is
    planned separately with `reserve`)."""
    need = avg.spending + avg.external_out
    surplus = avg.income - need + (avg.external_in if include_transfers_in else ZERO)
    deficit = -surplus if surplus < 0 else ZERO
    ef_target = max(ZERO, need - ef_exclude) * ef_months
    deficit_buffer = deficit * deficit_buffer_months
    net_cash = cash - card_owed
    pool = net_cash + lump
    held = ef_target + deficit_buffer + reserve
    investable_now = max(ZERO, pool - held)
    notes = []
    if deficit:
        notes.append(
            f"You spend more than you earn by about {money(deficit):,} per month. Until that "
            "changes, money set aside shrinks every month, so investing only makes sense with "
            "money beyond the emergency fund AND a buffer for that shortfall."
        )
    if not include_transfers_in and avg.external_in:
        notes.append(
            "Money transferred in from outside your accounts (e.g. family, Zelle) is NOT counted "
            "as income here because it isn't guaranteed. Use --include-transfers-in to count it."
        )
    investable_monthly = max(ZERO, surplus)
    months_to_fund = None
    if pool < held:
        notes.append(f"You are {money(held - pool):,} short of fully funding the safety targets.")
        if surplus > 0:
            # Surplus goes to the safety targets first; investing starts once they're funded.
            months_to_fund = money((held - pool) / surplus)
            investable_monthly = ZERO
            notes.append(
                f"Put the monthly surplus toward the safety targets first: at this pace they are "
                f"funded in about {months_to_fund} months, then about {money(surplus):,}/month "
                "becomes investable."
            )
    return Plan(
        monthly_income=avg.income, monthly_need=money(need),
        monthly_transfers_in=avg.external_in, counted_transfers_in=include_transfers_in,
        monthly_surplus=money(surplus), cash=money(cash), card_owed=money(card_owed),
        net_cash=money(net_cash), lump=money(lump), reserve=money(reserve), ef_months=ef_months,
        ef_target=money(ef_target), deficit_buffer_months=deficit_buffer_months,
        deficit_buffer=money(deficit_buffer), pool=money(pool),
        investable_now=money(investable_now), investable_monthly=money(investable_monthly),
        months_to_fund=months_to_fund,
        runway_months=(money((pool - reserve) / deficit) if deficit else None), notes=notes,
    )
