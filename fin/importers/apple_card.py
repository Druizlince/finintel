"""Import Apple Card monthly statement PDFs (Apple has no API; export from card.apple.com).

Statement amounts: charges positive, returns and payments negative.
Ours: negative = money out (purchase), positive = money in (payment/return).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

SECTIONS = {"Payments": "payment", "Transactions": "purchase",
            "Apple Card Monthly Installments": "purchase", "Installments": "purchase"}
END_SECTIONS = {"Daily Cash", "Interest Charged", "Interest Charges", "Legal"}
LINE_RE = re.compile(
    r"^\s*(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(?:-?\d+%\s+\$[\d,]+\.\d{2}\s+)?(-?\$[\d,]+\.\d{2})\s*$"
)
# "^\s*Total Balance" (start of line) so "Previous Total Balance" is not matched.
BALANCE_RE = re.compile(
    r"^\s*Total Balance\s+\$([\d,]+\.\d{2})\s*\n\s*as of (\w{3} \d{1,2}, \d{4})", re.MULTILINE
)
PERIOD_RE = re.compile(r"(\w{3} \d{1,2}) — (\w{3} \d{1,2}), (\d{4})")

CATEGORY_KEYWORDS = [
    (("FUEL", "SHELL", "EXXON", "MOBIL", "SUNOCO", "GAS ", "AUTO WASH", "CAR WASH", "PARKMOBILE",
      "EZPASS", "E-ZPASS", "UBER", "LYFT", "PMUSA"), "TRANSPORTATION"),
    (("MUNI", "MUNIC", "COURT"), "GOVERNMENT_AND_NON_PROFIT"),
    (("APPLE.COM/BILL", "ANTHROPIC", "CLAUDE", "AMAZON PRIME", "NETFLIX", "SPOTIFY", "HULU",
      "SUSCRIPCION", "SUBSCRIPTION", "YOUTUBE", "DISNEY"), "SUBSCRIPTIONS"),
    (("RESTAURANT", "CAFE", "PIZZA", "STARBUCKS", "DUNKIN", "BAGEL", "MCDONALD", "CHIPOTLE",
      "SHOPRITE", "STOP & SHOP", "WHOLE FOODS", "TRADER JOE", "COSTCO", "TST*", "WENDY",
      "PANERA", "EINSTEIN"), "FOOD_AND_DRINK"),
]
FRIENDLY = [("APPLE.COM/BILL", "Apple (App Store/iCloud)"), ("ANTHROPIC", "Claude (Anthropic)"),
            ("AMAZON PRIME", "Amazon Prime"), ("SUSCRIPCION MADRIDISTA", "Real Madrid membership"),
            ("PMUSA", "ParkMobile"), ("AMAZON MKTPL", "Amazon"), ("TST*", None),
            (" MUNI", "Municipal court")]


@dataclass(frozen=True)
class Line:
    date: dt.date
    description: str
    amount: Decimal  # our sign convention
    kind: str        # purchase | return | payment
    txn_id: str
    merchant: str
    category: str


@dataclass(frozen=True)
class Statement:
    period_end: dt.date | None
    balance: Decimal | None
    lines: list[Line]


def categorize(desc: str, kind: str) -> str:
    if kind == "payment":
        return "TRANSFER_IN"
    up = desc.upper()
    for keys, cat in CATEGORY_KEYWORDS:
        if any(k in up for k in keys):
            return cat
    return "GENERAL_MERCHANDISE"


def merchant_name(desc: str) -> str:
    up = desc.upper()
    for key, name in FRIENDLY:
        if key in up and name:
            return name
    clean = desc.replace("(RETURN)", "").replace("TST*", "").strip()
    clean = re.split(r"\d{3,}", clean)[0]            # cut at street numbers / zip codes
    return " ".join(clean.split()[:4]).title()


def parse_statement(text: str) -> Statement:
    section = None
    raw: list[tuple[dt.date, str, Decimal, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in SECTIONS:
            section = SECTIONS[stripped]
            continue
        if stripped in END_SECTIONS:
            section = None
            continue
        if section is None or stripped.startswith("Total"):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        date = dt.datetime.strptime(m.group(1), "%m/%d/%Y").date()
        desc = " ".join(m.group(2).split())
        stmt_amount = Decimal(m.group(3).replace("$", "").replace(",", ""))
        kind = "payment" if section == "payment" else ("return" if stmt_amount < 0 else "purchase")
        raw.append((date, desc, -stmt_amount, kind))

    seen: Counter = Counter()
    lines = []
    for date, desc, amount, kind in raw:
        key = (date, desc, amount)
        seen[key] += 1  # same merchant+amount twice on one day stays two transactions
        digest = hashlib.sha256(f"{date}|{desc}|{amount}|{seen[key]}".encode()).hexdigest()[:24]
        lines.append(Line(date, desc, amount, kind, f"apple-{digest}",
                          "Apple Card payment" if kind == "payment" else merchant_name(desc),
                          categorize(desc, kind)))

    balance = period_end = None
    b = BALANCE_RE.search(text)
    if b:
        balance = Decimal(b.group(1).replace(",", ""))
        period_end = dt.datetime.strptime(b.group(2), "%b %d, %Y").date()
    return Statement(period_end, balance, lines)


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(p.extract_text(extraction_mode="layout") for p in PdfReader(str(path)).pages)


def import_statements(paths: list[Path], engine=None) -> list[dict]:  # noqa: ANN001
    """Import statement PDFs. Safe to re-run: already-imported lines are skipped."""
    from sqlalchemy import select

    from fin.db.models import Account, BalanceSnapshot, Institution, Transaction
    from fin.db.session import session_scope

    results = []
    with session_scope(engine) as s:
        inst = s.scalar(select(Institution).where(Institution.provider == "csv",
                                                  Institution.provider_institution_id == "apple-card"))
        if inst is None:
            inst = Institution(name="Apple Card", provider="csv", provider_institution_id="apple-card")
            s.add(inst)
            s.flush()
        acct = s.scalar(select(Account).where(Account.institution_id == inst.id,
                                              Account.provider_account_id == "apple-card"))
        if acct is None:
            acct = Account(institution_id=inst.id, provider_account_id="apple-card",
                           name="Apple Card", type="credit")
            s.add(acct)
            s.flush()

        for path in paths:
            st = parse_statement(pdf_text(path))
            added = 0
            for ln in st.lines:
                exists = s.scalar(select(Transaction.id).where(
                    Transaction.account_id == acct.id, Transaction.provider_txn_id == ln.txn_id))
                if exists:
                    continue
                s.add(Transaction(account_id=acct.id, provider_txn_id=ln.txn_id, date=ln.date,
                                  amount=ln.amount, merchant=ln.merchant, description=ln.description,
                                  category=ln.category, pending=False, source="apple_pdf"))
                added += 1
            if st.balance is not None and st.period_end is not None:
                as_of = dt.datetime.combine(st.period_end, dt.time(23, 59), tzinfo=dt.timezone.utc)
                have = s.scalar(select(BalanceSnapshot.id).where(
                    BalanceSnapshot.account_id == acct.id, BalanceSnapshot.as_of == as_of,
                    BalanceSnapshot.source == "apple_pdf"))
                if not have:
                    s.add(BalanceSnapshot(account_id=acct.id, as_of=as_of, current=st.balance,
                                          source="apple_pdf", quality="verified_account"))
            s.flush()
            results.append({"file": path.name, "lines": len(st.lines), "added": added,
                            "balance": st.balance, "as_of": st.period_end})
        inst.last_synced_at = dt.datetime.now(dt.timezone.utc)
    return results
