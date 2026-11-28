"""Pure functions that turn SnapTrade JSON into our schema. No network, fully unit-tested.

SnapTrade responses are deeply nested and some fields vary by brokerage, so every lookup is
defensive: a missing field becomes None instead of a crash.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any


def plain(value: Any) -> Any:
    """Convert SDK response objects (frozendicts, schema wrappers) into plain Python."""
    name = type(value).__name__
    if name == "NoneClass":
        return None
    if name == "BoolClass":
        return bool(value)
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def dig(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, Mapping):
            return None
        obj = obj.get(key)
    return obj


def dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def to_date(value: Any) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def map_account_type(raw: str | None) -> str:
    r = (raw or "").lower()
    if "roth" in r:
        return "roth_ira"
    if "ira" in r or "rollover" in r:
        return "ira"
    if "401" in r:
        return "401k"
    if "403" in r:
        return "403b"
    if "457" in r:
        return "457b"
    if "hsa" in r or "health savings" in r:
        return "hsa"
    return "brokerage"


def map_account(a: Mapping[str, Any]) -> dict[str, Any]:
    raw_type = a.get("raw_type") or dig(a, "meta", "type") or a.get("name")
    number = str(a.get("number") or "")
    auth = a.get("brokerage_authorization")
    auth_id = auth.get("id") if isinstance(auth, Mapping) else auth
    return {
        "provider_account_id": str(a["id"]),
        "authorization_id": str(auth_id) if auth_id else None,
        "institution_name": a.get("institution_name") or "Brokerage",
        "name": a.get("name") or "Brokerage account",
        "type": map_account_type(raw_type),
        "subtype": str(raw_type) if raw_type else None,
        "mask": number[-4:] if number else None,
        "currency": dig(a, "balance", "total", "currency") or "USD",
        "total_value": dec(dig(a, "balance", "total", "amount")),
    }


SECURITY_TYPES = {
    "cs": "stock", "ad": "stock", "ps": "stock", "et": "etf", "etf": "etf", "etn": "etf",
    "oef": "mutual_fund", "mf": "mutual_fund", "mmf": "mutual_fund", "bnd": "bond",
    "crypto": "crypto", "opt": "option",
}


def map_security(sym: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """`sym` is SnapTrade's UniversalSymbol (position.symbol.symbol or activity.symbol)."""
    if not isinstance(sym, Mapping):
        return None
    ticker = sym.get("symbol") or sym.get("raw_symbol")
    if not ticker:
        return None
    code = str(dig(sym, "type", "code") or "").lower()
    currency = sym.get("currency")
    return {
        "ticker": str(ticker).upper(),
        "name": sym.get("description") or str(ticker),
        "type": SECURITY_TYPES.get(code, "other"),
        "currency": (currency.get("code") if isinstance(currency, Mapping) else currency) or "USD",
    }


INSTRUMENT_KINDS = {
    "equity": "stock", "stock": "stock", "common_stock": "stock", "adr": "stock",
    "etf": "etf", "etn": "etf", "mutual_fund": "mutual_fund", "fund": "mutual_fund",
    "money_market": "mutual_fund", "bond": "bond", "fixed_income": "bond",
    "crypto": "crypto", "cryptocurrency": "crypto", "option": "option",
}


def _instrument_security(inst: Mapping[str, Any]) -> dict[str, Any] | None:
    """Newer positions format: position.instrument = {kind, symbol, raw_symbol, description, currency}."""
    ticker = inst.get("symbol") or inst.get("raw_symbol")
    if not ticker:
        return None
    currency = inst.get("currency")
    return {
        "ticker": str(ticker).upper(),
        "name": inst.get("description") or str(ticker),
        "type": INSTRUMENT_KINDS.get(str(inst.get("kind") or "").lower(), "other"),
        "currency": (currency.get("code") if isinstance(currency, Mapping) else currency) or "USD",
    }


def _total_cost(raw: Decimal | None, units: Decimal, price: Decimal | None) -> Decimal | None:
    """The newer format's `cost_basis` may be a total or a per-share average; pick the one that
    is closer to the position's market value."""
    if raw is None:
        return None
    if price is None or units == 0 or price == 0:
        return raw
    value = units * price
    as_total = abs(raw - value) / value
    as_avg = abs(raw * units - value) / value
    return raw if as_total <= as_avg else raw * units


def map_position(p: Mapping[str, Any]) -> dict[str, Any] | None:
    inst = p.get("instrument")
    if isinstance(inst, Mapping):
        # Newer SnapTrade positions format. Cash sweep (e.g. SPAXX) is skipped: it is already
        # counted in the account's cash balance, so keeping it would double count.
        if p.get("cash_equivalent") is True:
            return None
        sec = _instrument_security(inst)
        units = dec(p.get("units"))
        if sec is None or units is None:
            return None
        price = dec(p.get("price"))
        return {
            "security": sec,
            "quantity": units,
            "price": price,
            "market_value": (units * price) if price is not None else None,
            "cost_basis": _total_cost(dec(p.get("cost_basis")), units, price),
        }

    sec = map_security(dig(p, "symbol", "symbol"))
    units = dec(p.get("units"))
    if units is None:
        units = dec(p.get("fractional_units"))
    if sec is None or units is None:
        return None
    price = dec(p.get("price"))
    avg = dec(p.get("average_purchase_price"))
    return {
        "security": sec,
        "quantity": units,
        "price": price,
        "market_value": (units * price) if price is not None else None,
        "cost_basis": (units * avg) if avg is not None else None,
    }


ACTIVITY_TYPES = {
    "BUY": "buy", "SELL": "sell", "DIVIDEND": "dividend", "STOCK_DIVIDEND": "dividend",
    "REI": "reinvest", "INTEREST": "interest", "FEE": "fee", "CONTRIBUTION": "transfer_in",
    "DEPOSIT": "transfer_in", "TRANSFER_IN": "transfer_in", "WITHDRAWAL": "transfer_out",
    "TRANSFER_OUT": "transfer_out", "SPLIT": "split",
}


def map_activity(a: Mapping[str, Any]) -> dict[str, Any] | None:
    date = to_date(a.get("trade_date") or a.get("settlement_date"))
    amount = dec(a.get("amount"))
    if not a.get("id") or date is None or amount is None:
        return None
    return {
        "provider_activity_id": str(a["id"]),
        "date": date,
        "type": ACTIVITY_TYPES.get(str(a.get("type") or "").upper(), "other"),
        "quantity": dec(a.get("units")),
        "price": dec(a.get("price")),
        "amount": amount,
        "description": a.get("description"),
        "security": map_security(a.get("symbol")),
    }
