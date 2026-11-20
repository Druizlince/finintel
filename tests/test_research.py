import json

import pytest

from fin.research.gather import clean_ticker, gather_macro, gather_stock
from fin.research.http import FetchError, Fetcher, _strip
from fin.research.memo import build_memo, render

QUOTE = {"c": 150.96, "dp": -2.89}
PROFILE = {"name": "NVIDIA Corp", "finnhubIndustry": "Semiconductors", "country": "US",
           "exchange": "NASDAQ", "marketCapitalization": 3_650_000}
METRICS = {"revenueGrowthTTMYoy": 105.0, "epsGrowthTTMYoy": 130.0, "grossMarginTTM": 74.9,
           "operatingMarginTTM": 62.0, "netProfitMarginTTM": 56.0, "roeTTM": 90.0,
           "totalDebt/totalEquityQuarterly": 0.1, "currentRatioQuarterly": 4.0,
           "peTTM": 16.0, "psTTM": 9.0, "52WeekHigh": 243.37, "52WeekLow": 140.0,
           "52WeekPriceReturnDaily": -17.0, "13WeekPriceReturnDaily": -30.0, "beta": 1.7,
           "dividendYieldIndicatedAnnual": 0.03}
RECS = [{"period": "2026-11-01", "strongBuy": 30, "buy": 25, "hold": 5, "sell": 1, "strongSell": 0}]
NEWS = [{"datetime": 1795000000, "source": "CNBC", "headline": "Nvidia beats, guides higher"}]
INCOME = [{"fiscalYear": "2026", "revenue": 300e9}, {"fiscalYear": "2025", "revenue": 130e9},
          {"fiscalYear": "2024", "revenue": 61e9}]
CASHFLOW = [{"freeCashFlow": 150e9}]
BALANCE = [{"cashAndCashEquivalents": 50e9, "totalDebt": 10e9}]


class FakeFinnhub:
    def quote(self, t, refresh=False): return QUOTE
    def profile(self, t, refresh=False): return PROFILE
    def metrics(self, t, refresh=False): return METRICS
    def recommendations(self, t, refresh=False): return RECS
    def news(self, t, refresh=False): return NEWS


class FakeFmp:
    def income(self, t, refresh=False): return INCOME
    def balance(self, t, refresh=False): return BALANCE
    def cashflow(self, t, refresh=False): raise FetchError("HTTP 402 (free plan)")


class FakeFred:
    def series(self, sid, limit=3, refresh=False):
        if sid == "CPIAUCSL":   # a skipped month (like Oct 2025) must not break the math
            return [("2026-10-01", 330.0)] + [(f"2026-0{m}-01", 325.0) for m in range(9, 0, -1)] \
                + [("2025-12-01", 321.0), ("2025-11-01", 320.5), ("2025-10-01", 320.0)]
        return [("2026-11-18", {"DGS10": 5.07, "FEDFUNDS": 4.5, "UNRATE": 4.4}[sid])]


def test_clean_ticker():
    assert clean_ticker(" nvda ") == "NVDA" and clean_ticker("brk.b") == "BRK.B"
    with pytest.raises(ValueError):
        clean_ticker("rm -rf /")


def test_macro_and_cpi_yoy():
    m, gaps = gather_macro(FakeFred())
    assert m["DGS10"]["value"] == 5.07 and m["CPIAUCSL"]["value"] == 3.12 and gaps == []


def test_cpi_falls_back_when_year_ago_month_missing():
    class GapFred(FakeFred):
        def series(self, sid, limit=3, refresh=False):
            if sid == "CPIAUCSL":   # Oct 2025 missing, like the real 2025 shutdown gap
                return [("2026-10-01", 337.476), ("2026-09-01", 336.298),
                        ("2025-11-01", 325.063), ("2025-09-01", 324.245)]
            return super().series(sid, limit, refresh)

    m, gaps = gather_macro(GapFred())
    assert m["CPIAUCSL"] == {"label": m["CPIAUCSL"]["label"], "date": "2026-09-01", "value": 3.72}
    assert any("showing 2026-09" in g for g in gaps)
    assert gather_macro(None)[1]  # missing key is a gap, not a crash


def test_memo_full_flow():
    data, gaps = gather_stock("NVDA", FakeFinnhub(), FakeFmp())
    assert any("cashflow" in g for g in gaps)          # one provider failing is tolerated
    macro, _ = gather_macro(FakeFred())
    memo = build_memo(data, macro, gaps, {"invested": 20000, "owned_value": 0, "planned": 0})
    sc = {p["title"]: p["score"] for p in memo["points"]}
    assert len(memo["points"]) == 12
    assert sc["Growth"] == "green" and sc["Valuation"] == "green" and sc["Price action"] == "yellow"
    assert sc["Cash flow"] == "gray" and sc["Macro backdrop"] == "yellow"
    val = next(p for p in memo["points"] if p["title"] == "Valuation")
    assert "doubts the growth" in val["assessment"]
    fit = next(p for p in memo["points"] if p["n"] == 12)
    assert any("$2,000" in f or "2.0K" in f or "$2.0" in f for f in fit["facts"])
    text = render(memo)
    assert "12-POINT MEMO" in text and "FACT" in text and "VIEW" in text


def test_memo_without_any_keys():
    data, gaps = gather_stock("NVDA", None, None)
    memo = build_memo(data, {}, gaps)
    assert memo["scorecard"]["gray"] >= 8 and len(memo["gaps"]) == 2


def test_fit_over_limit():
    data, gaps = gather_stock("NVDA", FakeFinnhub(), FakeFmp())
    memo = build_memo(data, {}, gaps, {"invested": 10000, "owned_value": 2500})
    assert memo["points"][11]["score"] == "red"


def test_cache_and_key_stripping(tmp_path, monkeypatch):
    calls = []

    class R:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(req, timeout=0):
        calls.append(req.full_url)
        return R(json.dumps({"ok": 1}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    f = Fetcher(tmp_path)
    url = "https://x.test/a?symbol=NVDA&token=SECRET123"
    assert f.get_json(url) == {"ok": 1} and f.get_json(url) == {"ok": 1}
    assert len(calls) == 1                                  # second call served from cache
    assert "SECRET123" not in _strip(url)
    assert all("SECRET123" not in p.read_text() for p in tmp_path.iterdir())


def test_mcp_research_without_keys(engine, monkeypatch):
    from fin_mcp import tools

    monkeypatch.setattr("fin.research.engine.clients", lambda fetcher=None: (None, None, None))
    out = tools.research(engine, ticker="NVDA")
    assert out["data"]["ticker"] == "NVDA" and any("Finnhub" in w for w in out["warnings"])
    assert tools.research(engine, ticker="bad ticker!")["data"] is None
