"""Apply user category rules (pure function)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal

from fin.analytics.cashflow import Txn


@dataclass(frozen=True)
class Rule:
    pattern: str
    category: str
    label: str | None = None
    min_amount: Decimal | None = None  # absolute value, inclusive
    max_amount: Decimal | None = None
    direction: str | None = None  # "out", "in" or None (both)


def _words(text: str | None) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def apply_rules(txn: Txn, description: str | None, rules: list[Rule]) -> Txn:
    """First matching rule wins. A match turns a 'transfer' into ordinary spending/income
    of the rule's category, and groups it under the rule's label."""
    text = _words(f"{txn.merchant or ''} {description or ''}")
    for r in rules:
        need = _words(r.pattern)
        if r.direction == "out" and txn.amount > 0 or r.direction == "in" and txn.amount < 0:
            continue
        size = abs(txn.amount)
        if r.min_amount is not None and size < r.min_amount:
            continue
        if r.max_amount is not None and size > r.max_amount:
            continue
        if need and need <= text:
            return replace(txn, category=r.category.upper(), detail=None,
                           merchant=r.label or txn.merchant)
    return txn
