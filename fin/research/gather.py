"""Collect everything the memo needs. Each provider can fail independently; failures become
'gaps' in the memo instead of crashing it."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from fin.research.http import FetchError

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

MACRO_SERIES = {
    "DGS10": "10-year Treasury yield (%)",
    "FEDFUNDS": "Fed funds rate (%)",
    "UNRATE": "Unemployment rate (%)",
    "CPIAUCSL": "Inflation, CPI year over year (%)",
}


def clean_ticker(t: str) -> str:
    t = (t or "").strip().upper()
    if not TICKER_RE.match(t):
        raise ValueError("ticker must look like NVDA, TSM or BRK.B")
    return t


def gather_macro(fred: Any, refresh: bool = False) -> tuple[dict, list[str]]:
    out, gaps = {}, []
    if fred is None:
        return out, ["Macro data: no FRED key set (fin secret set fred_api_key)."]
    for sid, label in MACRO_SERIES.items():
        try:
            obs = fred.series(sid, limit=16 if sid == "CPIAUCSL" else 3, refresh=refresh)
        except FetchError as exc:
            gaps.append(f"FRED {sid}: {exc}")
            continue
        if not obs:
            continue
        if sid == "CPIAUCSL":
            # Match the same month a year earlier by date (FRED can skip months, e.g. a
            # government shutdown), instead of assuming exactly 12 rows back.
            by_date = dict(obs)
            pair = None
            for d, v in obs:                       # newest first
                ago = by_date.get(f"{int(d[:4]) - 1}{d[4:]}")
                if ago:
                    pair = (d, v, ago)
                    break
            if pair:
                d, v, ago = pair
                out[sid] = {"label": label, "date": d, "value": round((v / ago - 1) * 100, 2)}
                if d != obs[0][0]:
                    gaps.append(f"CPI for {obs[0][0][:7]} has no year-earlier value (FRED gap); "
                                f"showing {d[:7]} instead")
            else:
                gaps.append("FRED CPIAUCSL: no value from a year earlier to compare")
        else:
            out[sid] = {"label": label, "date": obs[0][0], "value": obs[0][1]}
    return out, gaps


def gather_stock(ticker: str, finnhub: Any, fmp: Any, refresh: bool = False) -> tuple[dict, list[str]]:
    d: dict[str, Any] = {"ticker": ticker, "fetched": dt.datetime.now().isoformat(timespec="seconds")}
    gaps: list[str] = []
    if finnhub is None:
        gaps.append("Prices, metrics, news, analyst ratings: no Finnhub key "
                    "(fin secret set finnhub_api_key).")
    else:
        for name, fn in (("quote", finnhub.quote), ("profile", finnhub.profile),
                         ("metrics", finnhub.metrics), ("recommendations", finnhub.recommendations),
                         ("news", finnhub.news)):
            try:
                d[name] = fn(ticker, refresh=refresh)
            except FetchError as exc:
                gaps.append(f"Finnhub {name}: {exc}")
    if fmp is None:
        gaps.append("Financial statements: no FMP key (fin secret set fmp_api_key).")
    else:
        for name, fn in (("income", fmp.income), ("balance", fmp.balance),
                         ("cashflow", fmp.cashflow)):
            try:
                d[name] = fn(ticker, refresh=refresh)
            except FetchError as exc:
                gaps.append(f"FMP {name}: {exc}")
    return d, gaps
