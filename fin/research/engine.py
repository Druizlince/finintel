"""Entry points used by the CLI, the web app and the Claude connector."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fin.research.gather import clean_ticker, gather_macro, gather_stock
from fin.research.http import Fetcher
from fin.research.memo import build_memo
from fin.research.providers import Finnhub, Fmp, Fred


def clients(fetcher: Fetcher | None = None) -> tuple[Any, Any, Any]:
    from fin import secrets
    from fin.config import get_settings

    f = fetcher or Fetcher(get_settings().data_dir / "cache" / "research")
    fred_k, fh_k, fmp_k = (secrets.get_secret(n) for n in
                           ("fred_api_key", "finnhub_api_key", "fmp_api_key"))
    return (Fred(fred_k, f) if fred_k else None, Finnhub(fh_k, f) if fh_k else None,
            Fmp(fmp_k, f) if fmp_k else None)


def macro(refresh: bool = False, providers: tuple | None = None) -> dict:
    fred, _, _ = providers or clients()
    data, gaps = gather_macro(fred, refresh=refresh)
    return {"series": data, "gaps": gaps}


def _fit(ticker: str, engine: Any, planned: float) -> dict:
    try:
        from fin.analytics.portfolio import bucket_of, load_positions
        from fin.db.session import session_scope

        with session_scope(engine) as s:
            positions, _cash, _h, _ = load_positions(s)
    except Exception:  # noqa: BLE001 - fit is a nice-to-have; never block the memo
        return {"planned": planned}
    invested = sum((p.market_value for p in positions if bucket_of(p) != "cash"), Decimal(0))
    owned = sum((p.market_value for p in positions if (p.ticker or "").upper() == ticker),
                Decimal(0))
    return {"invested": float(invested), "owned_value": float(owned), "planned": planned}


def research(ticker: str, refresh: bool = False, planned: float = 0.0, engine: Any = None,
             providers: tuple | None = None) -> dict:
    t = clean_ticker(ticker)
    fred, finnhub, fmp = providers or clients()
    mac, mgaps = gather_macro(fred, refresh=refresh)
    data, gaps = gather_stock(t, finnhub, fmp, refresh=refresh)
    return build_memo(data, mac, mgaps + gaps, _fit(t, engine, planned))
