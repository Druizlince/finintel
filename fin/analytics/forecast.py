"""Month-by-month outlook: monthly surplus in, planned expenses out (pure functions)."""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from fin.analytics.cashflow import ZERO, _add_months, money


@dataclass(frozen=True)
class Planned:
    name: str
    amount: Decimal  # positive = you pay
    due: dt.date
    category: str | None = None


@dataclass
class OutlookMonth:
    key: str
    surplus: Decimal
    planned_out: Decimal
    items: list[str]
    low_point: Decimal        # balance if this month's payments hit before any income arrives
    end_balance: Decimal
    committed_later: Decimal  # planned expenses still due after this month
    free: Decimal             # end balance minus later commitments minus emergency fund


@dataclass
class Outlook:
    months: list[OutlookMonth]
    warnings: list[str] = field(default_factory=list)
    invest_from: OutlookMonth | None = None


def upcoming(planned: list[Planned], today: dt.date) -> list[Planned]:
    return sorted((p for p in planned if p.due >= today), key=lambda p: p.due)


def build_outlook(
    start_balance: Decimal, monthly_surplus: Decimal, planned: list[Planned],
    ef_target: Decimal, today: dt.date, months: int = 6,
) -> Outlook:
    future = upcoming(planned, today)
    rows: list[OutlookMonth] = []
    warnings: list[str] = []
    bal = start_balance
    y, m = today.year, today.month
    for i in range(months):
        dim = calendar.monthrange(y, m)[1]
        month_end = dt.date(y, m, dim)
        # This month: only the remaining days earn surplus.
        frac = Decimal(dim - today.day) / dim if i == 0 else Decimal(1)
        surplus = monthly_surplus * frac
        items = [p for p in future if (p.due.year, p.due.month) == (y, m)]
        out = sum((p.amount for p in items), ZERO)
        low_point = bal - out
        bal = bal + surplus - out
        later = sum((p.amount for p in future if p.due > month_end), ZERO)
        free = bal - later - ef_target
        key = f"{y:04d}-{m:02d}"
        if low_point < 0 and items:
            warnings.append(
                f"{key}: {', '.join(p.name for p in items)} ({money(out):,}) could exceed your cash "
                f"if it is due before that month's income arrives (short by about "
                f"{money(-low_point):,}). Keep that much in checking or schedule it after payday."
            )
        if bal < 0:
            warnings.append(f"{key}: projected balance goes negative ({money(bal):,}).")
        rows.append(OutlookMonth(key, money(surplus), money(out), [p.name for p in items],
                                 money(low_point), money(bal), money(later), money(free)))
        y, m = _add_months(y, m, 1)
    invest_from = next((r for r in rows if r.free > 0), None)
    return Outlook(rows, warnings, invest_from)
