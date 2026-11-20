"""Pure functions that turn Plaid's JSON into our schema. No network, fully unit-tested."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

SUBTYPE_MAP = {
    "checking": "checking",
    "savings": "savings",
    "money market": "savings",
    "cd": "savings",
    "hsa": "hsa",
    "credit card": "credit",
    "paypal": "credit",
    "ira": "ira",
    "roth": "roth_ira",
    "401k": "401k",
    "403b": "403b",
    "457b": "457b",
    "brokerage": "brokerage",
}
TYPE_MAP = {"depository": "checking", "credit": "credit", "investment": "brokerage", "loan": "loan"}


def _s(value: Any) -> str | None:
    value = getattr(value, "value", value)  # plaid enums carry .value
    return None if value is None else str(value)


def map_account_type(type_: Any, subtype: Any) -> str:
    sub = (_s(subtype) or "").lower()
    if sub in SUBTYPE_MAP:
        return SUBTYPE_MAP[sub]
    return TYPE_MAP.get((_s(type_) or "").lower(), "other")


def to_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def normalize_amount(plaid_amount: Any) -> Decimal:
    """Plaid: positive = money leaving the account. Ours: negative = money out."""
    return -Decimal(str(plaid_amount))


def to_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def map_account(a: dict[str, Any]) -> dict[str, Any]:
    balances = a.get("balances") or {}
    mask = _s(a.get("mask"))
    return {
        "provider_account_id": a["account_id"],
        "name": a.get("name") or "Account",
        "official_name": a.get("official_name"),
        "type": map_account_type(a.get("type"), a.get("subtype")),
        "subtype": _s(a.get("subtype")),
        "mask": mask[-4:] if mask else None,
        "currency": balances.get("iso_currency_code") or "USD",
        "balances": {
            "current": to_decimal(balances.get("current")),
            "available": to_decimal(balances.get("available")),
            "credit_limit": to_decimal(balances.get("limit")),
        },
    }


def map_transaction(t: dict[str, Any]) -> dict[str, Any]:
    pfc = t.get("personal_finance_category") or {}
    return {
        "provider_txn_id": t["transaction_id"],
        "provider_account_id": t["account_id"],
        "date": to_date(t["date"]),
        "authorized_date": to_date(t.get("authorized_date")),
        "amount": normalize_amount(t["amount"]),
        "merchant": t.get("merchant_name") or t.get("name"),
        "description": t.get("name"),
        "category": pfc.get("primary"),
        "category_detail": pfc.get("detailed"),
        "pending": bool(t.get("pending")),
    }
