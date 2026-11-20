"""Read-only public-data clients: FRED (macro), Finnhub (quote/metrics/news/ratings),
FMP (financial statements). Each method returns plain dicts/lists; missing data -> None."""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import quote

from fin.research.http import Fetcher

MIN, HOUR, DAY = 60, 3600, 86400


class Fred:
    BASE = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, key: str, fetch: Fetcher) -> None:
        self.key, self.fetch = key, fetch

    def series(self, series_id: str, limit: int = 15, refresh: bool = False) -> list[tuple[str, float]]:
        url = (f"{self.BASE}?series_id={quote(series_id)}&api_key={self.key}&file_type=json"
               f"&sort_order=desc&limit={limit}")
        obs = self.fetch.get_json(url, ttl=DAY, refresh=refresh).get("observations", [])
        out = []
        for o in obs:
            try:
                out.append((o["date"], float(o["value"])))
            except (KeyError, ValueError):
                continue   # FRED uses "." for missing values
        return out   # newest first


class Finnhub:
    BASE = "https://finnhub.io/api/v1"

    def __init__(self, key: str, fetch: Fetcher) -> None:
        self.key, self.fetch = key, fetch

    def _get(self, path: str, ttl: int, refresh: bool, **params: str) -> Any:
        q = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        return self.fetch.get_json(f"{self.BASE}{path}?{q}&token={self.key}", ttl=ttl,
                                   refresh=refresh)

    def quote(self, symbol: str, refresh: bool = False) -> dict:
        return self._get("/quote", 15 * MIN, refresh, symbol=symbol) or {}

    def profile(self, symbol: str, refresh: bool = False) -> dict:
        return self._get("/stock/profile2", DAY, refresh, symbol=symbol) or {}

    def metrics(self, symbol: str, refresh: bool = False) -> dict:
        return (self._get("/stock/metric", DAY, refresh, symbol=symbol, metric="all") or {}
                ).get("metric", {}) or {}

    def recommendations(self, symbol: str, refresh: bool = False) -> list[dict]:
        r = self._get("/stock/recommendation", DAY, refresh, symbol=symbol)
        return r if isinstance(r, list) else []

    def news(self, symbol: str, days: int = 10, refresh: bool = False) -> list[dict]:
        today = dt.date.today()
        r = self._get("/company-news", 2 * HOUR, refresh, symbol=symbol,
                      **{"from": (today - dt.timedelta(days=days)).isoformat(),
                         "to": today.isoformat()})
        return r if isinstance(r, list) else []


class Fmp:
    BASE = "https://financialmodelingprep.com/stable"

    def __init__(self, key: str, fetch: Fetcher) -> None:
        self.key, self.fetch = key, fetch

    def _get(self, path: str, symbol: str, limit: int, refresh: bool) -> list[dict]:
        url = f"{self.BASE}/{path}?symbol={quote(symbol)}&limit={limit}&apikey={self.key}"
        r = self.fetch.get_json(url, ttl=7 * DAY, refresh=refresh)
        return r if isinstance(r, list) else []

    def income(self, symbol: str, refresh: bool = False) -> list[dict]:
        return self._get("income-statement", symbol, 5, refresh)

    def balance(self, symbol: str, refresh: bool = False) -> list[dict]:
        return self._get("balance-sheet-statement", symbol, 1, refresh)

    def cashflow(self, symbol: str, refresh: bool = False) -> list[dict]:
        return self._get("cash-flow-statement", symbol, 1, refresh)
