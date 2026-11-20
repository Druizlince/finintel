"""Money other people owe you for charges you paid (e.g. a relative's purchases on your card).

Stored as JSON in the settings table (key "receivables"), so no schema change is needed.
An open receivable tied to a card is NOT counted as your debt: card_owed and net cash
exclude it until you mark it settled.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from fin.db.models import Setting

KEY = "receivables"
ZERO = Decimal("0")


@dataclass
class Receivable:
    id: int
    who: str
    amount: Decimal
    card: str | None = None  # institution name fragment, e.g. "chase"
    note: str = ""
    created: str = ""


def load_receivables(s: Session) -> list[Receivable]:
    row = s.get(Setting, KEY)
    if row is None or not row.value:
        return []
    items = []
    for d in json.loads(row.value):
        d["amount"] = Decimal(str(d["amount"]))
        items.append(Receivable(**d))
    return items


def _save(s: Session, items: list[Receivable]) -> None:
    data = [{**asdict(r), "amount": str(r.amount)} for r in items]
    row = s.get(Setting, KEY)
    if row is None:
        s.add(Setting(key=KEY, value=json.dumps(data)))
    else:
        row.value = json.dumps(data)


def add_receivable(s: Session, who: str, amount: Decimal, card: str | None = None,
                   note: str = "", today: dt.date | None = None) -> Receivable:
    if amount <= 0:
        raise ValueError("amount must be positive")
    items = load_receivables(s)
    r = Receivable(id=max((i.id for i in items), default=0) + 1, who=who.strip(), amount=amount,
                   card=(card or "").strip().lower() or None, note=note.strip(),
                   created=(today or dt.date.today()).isoformat())
    items.append(r)
    _save(s, items)
    return r


def settle_receivable(s: Session, rid: int, amount: Decimal | None = None) -> Receivable | None:
    """Remove (or reduce, for a partial repayment) a receivable. Returns what remains/was removed."""
    items = load_receivables(s)
    for r in items:
        if r.id == rid:
            if amount is not None and Decimal("0") < amount < r.amount:
                r.amount -= amount
                _save(s, items)
                return r
            items.remove(r)
            _save(s, items)
            return r
    return None


def owed_by_others(items: list[Receivable], institution_name: str) -> Decimal:
    """Open receivables tied to this card's institution (matched by name fragment)."""
    name = institution_name.lower()
    return sum((r.amount for r in items if r.card and r.card in name), ZERO)
