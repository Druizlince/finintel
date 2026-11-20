"""One-time local web page for connecting a bank through Plaid Link.

Security
- Listens on 127.0.0.1 only (never reachable from your network).
- The page and every API call require a random one-time `state` value printed in your terminal.
- Host and Origin headers are checked, which blocks other websites and DNS-rebinding tricks.
- Stops after one successful link, or after 15 minutes.
- Your bank login happens inside Plaid's window; this server only ever sees a one-time
  public token, which it exchanges for an access token stored in the OS keyring.
"""

from __future__ import annotations

import hmac
import logging
import secrets as pysecrets
import threading
import webbrowser
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.engine import Engine

from fin.db.models import Institution
from fin.db.session import init_db, session_scope
from fin.logsetup import redact
from fin.sync import link_plaid_item, run_plaid_sync

log = logging.getLogger(__name__)

TRIAL_ITEM_LIMIT = 20  # per Plaid dashboard (Nov 2026); removing a connection does not refund it
TIMEOUT_SECONDS = 15 * 60


class ExchangeIn(BaseModel):
    public_token: str = Field(pattern=r"^public-(sandbox|production)-[0-9a-fA-F-]{8,}$", max_length=200)
    institution_id: str | None = Field(default=None, max_length=50, pattern=r"^[A-Za-z0-9_]+$")
    institution_name: str | None = Field(default=None, max_length=200)


def _same(a: str | None, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), b.encode())


def create_app(
    *, state: str, port: int, adapter: Any, engine: Engine | None = None,
    on_done: Callable[[], None] = lambda: None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    @app.middleware("http")
    async def guard(request: Request, call_next):  # noqa: ANN001, ANN202
        if request.headers.get("host") not in hosts:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        if request.method != "GET" and (
            request.headers.get("origin") not in origins
            or not _same(request.headers.get("x-fin-state"), state)
        ):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def _linked() -> list[dict[str, Any]]:
        with session_scope(engine) as s:
            rows = s.scalars(select(Institution).where(Institution.provider == "plaid")).all()
            return [{"name": i.name, "status": i.status} for i in rows]

    @app.get("/", response_class=HTMLResponse)
    def page(state_q: str = Query("", alias="state")) -> str:
        if not _same(state_q, state):
            raise HTTPException(403, "Open the exact link printed in your terminal.")
        return PAGE.replace("__STATE__", state).replace("__ENV__", adapter.env)

    @app.post("/api/status")
    def status() -> dict[str, Any]:
        linked = _linked()
        return {"env": adapter.env, "linked": linked, "count": len(linked), "limit": TRIAL_ITEM_LIMIT}

    @app.post("/api/link-token")
    def link_token() -> dict[str, str]:
        try:
            return {"link_token": adapter.create_link_token()}
        except Exception as exc:
            raise HTTPException(502, redact(str(exc))) from None

    @app.post("/api/exchange")
    def exchange(body: ExchangeIn) -> dict[str, Any]:
        if not body.public_token.startswith(f"public-{adapter.env}-"):
            raise HTTPException(400, f"Token is not for the {adapter.env} environment.")
        try:
            with session_scope(engine) as s:
                if body.institution_id and s.scalar(
                    select(Institution).where(
                        Institution.provider == "plaid",
                        Institution.provider_institution_id == body.institution_id,
                    )
                ):
                    raise HTTPException(
                        409,
                        f"{body.institution_name or 'This institution'} is already linked - "
                        "not saving a second connection.",
                    )
                inst = link_plaid_item(s, adapter, body.public_token)
                name = inst.name
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, redact(str(exc))) from None

        results = run_plaid_sync(engine, adapter)
        on_done()
        return {"institution": name, "result": next((r for r in results if r["institution"] == name), None)}

    return app


def serve(port: int = 8765, open_browser: bool = True, adapter: Any = None) -> None:
    import uvicorn

    from fin.providers.plaid_adapter import PlaidAdapter

    adapter = adapter or PlaidAdapter()
    init_db()
    state = pysecrets.token_urlsafe(24)
    holder: dict[str, Any] = {}

    def _stop() -> None:
        holder["server"].should_exit = True

    def _done() -> None:
        t = threading.Timer(2.0, _stop)  # let the browser receive the final response first
        t.daemon = True
        t.start()

    app = create_app(state=state, port=port, adapter=adapter, on_done=_done)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    holder["server"] = server

    timeout = threading.Timer(TIMEOUT_SECONDS, _stop)
    timeout.daemon = True
    timeout.start()

    url = f"http://127.0.0.1:{port}/?state={state}"
    print(f"Plaid environment: {adapter.env}")
    print(f"Open this link (it works once, for 15 minutes):\n  {url}")
    print("Press Ctrl+C to cancel.")
    if open_browser:
        t = threading.Timer(1.0, lambda: webbrowser.open(url))
        t.daemon = True
        t.start()
    try:
        server.run()
    except KeyboardInterrupt:
        print("Cancelled.")
        return
    print("Link server stopped.")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>finintel - connect an account</title>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f6f7f9;color:#1d2433;
       margin:0;display:flex;justify-content:center;padding:48px 16px}
  .card{background:#fff;border-radius:14px;box-shadow:0 2px 14px rgba(0,0,0,.08);
        max-width:520px;width:100%;padding:32px}
  h1{font-size:22px;margin:0 0 4px} .sub{color:#5b6475;margin:0 0 20px}
  .env{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:99px;
       background:#e7f1fb;color:#174a7e;margin-bottom:16px}
  .env.production{background:#e5f4e4;color:#1d5b1b}
  ul{padding-left:20px;margin:6px 0 16px} li{margin:2px 0}
  .warn{background:#f9eeda;border-radius:8px;padding:10px 12px;font-size:14px;margin:12px 0}
  button{background:#1d2433;color:#fff;border:0;border-radius:8px;padding:12px 18px;
         font-size:15px;cursor:pointer;width:100%} button:disabled{opacity:.5;cursor:default}
  #msg{margin-top:16px;font-size:14px;white-space:pre-line} .ok{color:#1d5b1b} .err{color:#a11}
  .fine{color:#5b6475;font-size:12px;margin-top:18px}
</style></head><body><div class="card">
<span class="env __ENV__">__ENV__</span>
<h1>Connect an account</h1>
<p class="sub">You'll log in inside Plaid's secure window. This page never sees your password.</p>
<div id="linked">Loading...</div>
<button id="go" disabled>Connect a bank or card</button>
<div id="msg"></div>
<p class="fine">Read-only access. finintel cannot move money or trade. This page runs only on
your computer and closes after one connection.</p>
</div>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
const STATE = "__STATE__";
const $ = (id) => document.getElementById(id);
function say(text, cls){ const m=$("msg"); m.textContent=text; m.className=cls||""; }
async function api(path, body){
  const r = await fetch(path, {method:"POST",
    headers:{"Content-Type":"application/json","X-Fin-State":STATE},
    body: JSON.stringify(body||{})});
  let j = {}; try { j = await r.json(); } catch(e) {}
  if(!r.ok){ throw new Error(typeof j.detail === "string" ? j.detail : (r.status+" error")); }
  return j;
}
async function loadStatus(){
  const s = await api("/api/status");
  const box = $("linked");
  box.innerHTML = "";
  const p = document.createElement("div");
  p.innerHTML = "<strong>Already linked (" + s.count + (s.env==="production" ? " of " + s.limit + " Trial connections" : "") + "):</strong>";
  box.appendChild(p);
  const ul = document.createElement("ul");
  if(!s.linked.length){ const li=document.createElement("li"); li.textContent="Nothing yet"; ul.appendChild(li); }
  s.linked.forEach(i => { const li=document.createElement("li"); li.textContent = i.name + (i.status!=="active" ? " ("+i.status+")" : ""); ul.appendChild(li); });
  box.appendChild(ul);
  if(s.env==="production"){
    const w=document.createElement("div"); w.className="warn";
    w.textContent="Each new connection uses one of your " + s.limit + " Trial connections permanently. Don't connect a bank that is already listed.";
    box.appendChild(w);
  } else {
    const w=document.createElement("div"); w.className="warn";
    w.textContent="Sandbox: pick any bank (e.g. First Platypus Bank) and log in with user_good / pass_good.";
    box.appendChild(w);
  }
  $("go").disabled = false;
}
$("go").onclick = async () => {
  $("go").disabled = true;
  try {
    say("Preparing a secure Plaid window...");
    const {link_token} = await api("/api/link-token");
    const handler = Plaid.create({
      token: link_token,
      onSuccess: async (public_token, metadata) => {
        const inst = metadata.institution || {};
        say("Connected to " + (inst.name||"your bank") + ". Saving and syncing - this can take a minute...");
        try {
          const res = await api("/api/exchange", {public_token,
            institution_id: inst.institution_id || null, institution_name: inst.name || null});
          const r = res.result || {};
          let text = "Done: " + res.institution + " linked.\n";
          if(r.status === "ok"){
            text += r.accounts + " account(s) found, " + r.transactions_upserted + " transactions so far.";
            if(r.update_status === "NOT_READY") text += "\nYour bank is still preparing history - run `fin sync` in a few minutes.";
          } else if (r.error) { text += "First sync had a problem: " + r.error; }
          text += "\nYou can close this tab.";
          say(text, "ok");
        } catch(e){ say("Could not save the connection: " + e.message, "err"); }
      },
      onExit: (err) => {
        if(err){ say("Closed: " + (err.display_message || err.error_message || err.error_code), "err"); }
        else { say("Closed without connecting."); }
        $("go").disabled = false;
      },
    });
    handler.open();
  } catch(e){ say(e.message, "err"); $("go").disabled = false; }
};
loadStatus().catch(e => { $("linked").textContent = ""; say(e.message, "err"); });
</script></body></html>
"""
