"""Local dashboard: http://127.0.0.1:<port>. Read-only toward banks/brokers.

Security
- Listens on 127.0.0.1 only; Host header must be 127.0.0.1/localhost (blocks DNS rebinding).
- Every change (sync, add/delete rules or planned expenses) needs the per-launch token the page
  receives, plus a same-origin Origin header (blocks other websites from triggering actions).
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import secrets as pysecrets
import threading
import webbrowser
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.engine import Engine

from fin.analytics.cashflow import _add_months
from fin.db.models import CategoryRule, PlannedExpense
from fin.db.session import init_db, session_scope
from fin.logsetup import redact
from fin.web import summary

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class RuleIn(BaseModel):
    pattern: str = Field(min_length=2, max_length=200)
    category: str = Field(pattern=r"^[A-Za-z_]{2,100}$")
    label: str | None = Field(default=None, max_length=200)
    min_amount: Decimal | None = Field(default=None, ge=0)
    max_amount: Decimal | None = Field(default=None, ge=0)
    direction: Literal["in", "out"] | None = None


class PlannedIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    amount: Decimal = Field(gt=0, le=1_000_000)
    due_date: dt.date
    repeat: int = Field(default=1, ge=1, le=36)
    category: str | None = Field(default=None, pattern=r"^[A-Za-z_]{2,100}$")


def create_app(*, port: int, token: str, engine: Engine | None = None,
               extra_hosts: list[str] | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    for h in extra_hosts or []:  # e.g. private Tailscale HTTPS name (via `tailscale serve`)
        h = h.strip().lower()
        if h:
            hosts.add(h)
            origins.add(f"https://{h}")

    @app.middleware("http")
    async def guard(request: Request, call_next):  # noqa: ANN001, ANN202
        if (request.headers.get("host") or "").lower() not in hosts:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        if request.method not in ("GET", "HEAD") and (
            request.headers.get("origin") not in origins
            or not hmac.compare_digest(request.headers.get("x-fin-token", "").encode(), token.encode())
        ):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)

    @app.get("/manifest.webmanifest")
    def manifest() -> JSONResponse:
        return JSONResponse({"name": "finintel", "short_name": "finintel", "start_url": "/",
                             "display": "standalone", "background_color": "#1d2433",
                             "theme_color": "#1d2433",
                             "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]},
                            media_type="application/manifest+json")

    @app.get("/icon.svg")
    def icon():  # noqa: ANN202
        from fastapi.responses import Response

        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">'
               '<rect width="180" height="180" rx="38" fill="#1d2433"/>'
               '<path d="M40 128 L76 88 L102 108 L140 56" stroke="#4cc38a" stroke-width="14" '
               'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
        return Response(svg, media_type="image/svg+xml")

    @app.get("/api/overview")
    def api_overview() -> dict[str, Any]:
        return summary.overview(engine)

    @app.get("/api/cashflow")
    def api_cashflow(
        months: int = Query(3, ge=1, le=24), ef_months: int = Query(6, ge=1, le=12),
        start: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        outlook: int = Query(6, ge=1, le=24),
    ) -> dict[str, Any]:
        return summary.cashflow(engine, months=months, ef_months=ef_months, start=start,
                                outlook_months=outlook)

    @app.get("/api/portfolio")
    def api_portfolio(new_money: float = 0) -> dict[str, Any]:
        if new_money < 0 or new_money > 10_000_000:
            raise HTTPException(400, "new_money out of range")
        return summary.portfolio(engine, new_money=new_money)

    @app.get("/api/research/{ticker}")
    def api_research(ticker: str, planned: float = 0, refresh: bool = False) -> dict[str, Any]:
        from fin.research.engine import research

        try:
            return research(ticker, refresh=refresh, planned=planned, engine=engine)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/risk")
    def api_risk(new_money: float = 0, keep_cash: float = 0, pick: str | None = None) -> dict[str, Any]:
        if not (0 <= new_money <= 10_000_000 and 0 <= keep_cash <= 10_000_000):
            raise HTTPException(400, "amount out of range")
        if pick:
            from fin.research.gather import clean_ticker

            try:
                pick = clean_ticker(pick)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        return summary.risk(engine, new_money=new_money, keep_cash=keep_cash, pick=pick)

    @app.post("/api/sync")
    def api_sync() -> dict[str, Any]:
        from fin import secrets
        from fin.sync import run_plaid_sync

        out: dict[str, Any] = {}
        try:
            out["plaid"] = run_plaid_sync(engine)
        except Exception as exc:  # noqa: BLE001
            out["plaid"] = [{"status": "error", "error": redact(str(exc))}]
        if secrets.get_secret("snaptrade_client_id") and secrets.get_secret("snaptrade_consumer_key"):
            from fin.sync_snaptrade import run_snaptrade_sync

            try:
                out["snaptrade"] = run_snaptrade_sync(engine)
            except Exception as exc:  # noqa: BLE001
                out["snaptrade"] = [{"error": redact(str(exc))}]
        return out

    @app.post("/api/import/apple-card")
    def api_import_apple() -> dict[str, Any]:
        """Import every Apple Card statement PDF in the project's imports folder (safe to repeat)."""
        from fin.config import PROJECT_ROOT
        from fin.importers.apple_card import import_statements

        paths = sorted(p for p in (PROJECT_ROOT / "imports").glob("*.pdf")
                       if "apple card" in p.name.lower())
        if not paths:
            return {"files": [], "message": "No Apple Card PDFs in the imports folder."}
        try:
            res = import_statements(paths, engine)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, redact(str(exc))) from None
        return {"files": [{"file": r["file"], "added": r["added"], "lines": r["lines"]} for r in res]}

    # --- rules --------------------------------------------------------------------------
    @app.get("/api/rules")
    def list_rules() -> list[dict[str, Any]]:
        with session_scope(engine) as s:
            return [{"id": r.id, "pattern": r.pattern, "category": r.category, "label": r.label,
                     "min_amount": summary.num(r.min_amount), "max_amount": summary.num(r.max_amount),
                     "direction": r.direction}
                    for r in s.scalars(select(CategoryRule).order_by(CategoryRule.id))]

    @app.post("/api/rules")
    def add_rule(body: RuleIn) -> dict[str, Any]:
        with session_scope(engine) as s:
            r = CategoryRule(pattern=body.pattern.strip(), category=body.category.upper(),
                             label=body.label or None, min_amount=body.min_amount,
                             max_amount=body.max_amount, direction=body.direction)
            s.add(r)
            s.flush()
            return {"id": r.id}

    @app.delete("/api/rules/{rule_id}")
    def delete_rule(rule_id: int) -> dict[str, bool]:
        with session_scope(engine) as s:
            r = s.get(CategoryRule, rule_id)
            if r is None:
                raise HTTPException(404, "No such rule")
            s.delete(r)
        return {"ok": True}

    # --- planned expenses --------------------------------------------------------------
    @app.get("/api/planned")
    def list_planned() -> list[dict[str, Any]]:
        today = dt.date.today()
        with session_scope(engine) as s:
            return [{"id": p.id, "name": p.name, "amount": summary.num(p.amount),
                     "due_date": p.due_date.isoformat(), "category": p.category,
                     "past": p.due_date < today}
                    for p in s.scalars(select(PlannedExpense).order_by(PlannedExpense.due_date))]

    @app.post("/api/planned")
    def add_planned(body: PlannedIn) -> dict[str, Any]:
        import calendar

        ids = []
        with session_scope(engine) as s:
            for i in range(body.repeat):
                y, m = _add_months(body.due_date.year, body.due_date.month, i)
                due = dt.date(y, m, min(body.due_date.day, calendar.monthrange(y, m)[1]))
                name = body.name if body.repeat == 1 else f"{body.name} ({i + 1}/{body.repeat})"
                p = PlannedExpense(name=name, amount=body.amount, due_date=due,
                                   category=body.category.upper() if body.category else None)
                s.add(p)
                s.flush()
                ids.append(p.id)
        return {"ids": ids}

    @app.delete("/api/planned/{planned_id}")
    def delete_planned(planned_id: int) -> dict[str, bool]:
        with session_scope(engine) as s:
            p = s.get(PlannedExpense, planned_id)
            if p is None:
                raise HTTPException(404, "No such planned expense")
            s.delete(p)
        return {"ok": True}

    return app


def _port_free(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _is_finintel(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            return b"<title>finintel</title>" in r.read(4096)
    except Exception:  # noqa: BLE001
        return False


def serve(port: int = 8800, open_browser: bool = True) -> None:
    import uvicorn

    if not _port_free(port):
        if _is_finintel(port):
            url = f"http://127.0.0.1:{port}/"
            print(f"The dashboard is already running at {url} - opening it.")
            if open_browser:
                webbrowser.open(url)
            return
        for candidate in range(port + 1, port + 20):
            if _port_free(candidate):
                print(f"Port {port} is used by another program; using {candidate} instead.")
                port = candidate
                break
        else:
            raise SystemExit(f"No free port between {port} and {port + 19}.")

    from fin.config import get_settings

    init_db()
    token = pysecrets.token_urlsafe(24)
    extra = [h for h in get_settings().allowed_hosts.split(",") if h.strip()]
    app = create_app(port=port, token=token, extra_hosts=extra)
    if extra:
        print("Also allowed (private network): " + ", ".join(f"https://{h.strip()}" for h in extra))
    url = f"http://127.0.0.1:{port}/"
    print(f"finintel dashboard: {url}   (only reachable from this computer)")
    print("Leave this window open while you use it. Press Ctrl+C to stop.")
    if open_browser:
        t = threading.Timer(1.0, lambda: webbrowser.open(url))
        t.daemon = True
        t.start()
    try:
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                      access_log=False)).run()
    except KeyboardInterrupt:
        pass
    print("Dashboard stopped.")
