"""Sync brokerage accounts (Fidelity) from SnapTrade into the database."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fin.db.models import (
    Account, BalanceSnapshot, HoldingSnapshot, Institution, InvestmentActivity, Security,
    SyncCursor, SyncRun,
)
from fin.db.session import session_scope
from fin.logsetup import redact
from fin.providers.snaptrade_mapping import dec, map_account, map_activity, map_position

log = logging.getLogger(__name__)

HISTORY_START = dt.date(2015, 1, 1)
OVERLAP_DAYS = 10  # re-read recent days each sync; activities can post late


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _security(session: Session, sec: dict[str, Any]) -> Security:
    row = session.scalar(
        select(Security).where(Security.ticker == sec["ticker"], Security.type == sec["type"])
    )
    if row is None:
        row = Security(**sec)
        session.add(row)
        session.flush()
    return row


def _institution(session: Session, m: dict[str, Any]) -> Institution:
    key = m["authorization_id"] or m["institution_name"]
    inst = session.scalar(
        select(Institution).where(
            Institution.provider == "snaptrade", Institution.provider_institution_id == key
        )
    )
    if inst is None:
        inst = Institution(name=m["institution_name"], provider="snaptrade",
                           provider_institution_id=key)
        session.add(inst)
        session.flush()
    return inst


def sync_snaptrade_account(session: Session, adapter: Any, raw: dict[str, Any], now) -> dict:
    m = map_account(raw)
    inst = _institution(session, m)
    acct = session.scalar(
        select(Account).where(Account.institution_id == inst.id,
                              Account.provider_account_id == m["provider_account_id"])
    )
    if acct is None:
        acct = Account(institution_id=inst.id, provider_account_id=m["provider_account_id"])
        session.add(acct)
    for f in ("name", "type", "subtype", "mask", "currency"):
        setattr(acct, f, m[f])
    session.flush()

    # Balances: total account value + cash
    cash = sum((dec(b.get("cash")) or 0 for b in adapter.get_balances(m["provider_account_id"])),
               start=dec(0))
    positions = [p for p in (map_position(x) for x in adapter.get_positions(m["provider_account_id"])) if p]
    invested = sum((p["market_value"] or 0 for p in positions), start=dec(0))
    total = m["total_value"] if m["total_value"] is not None else cash + invested
    session.add(BalanceSnapshot(account_id=acct.id, as_of=now, current=total, available=cash,
                                source="snaptrade", quality="verified_account"))

    for p in positions:
        sec = _security(session, p["security"])
        session.add(HoldingSnapshot(
            account_id=acct.id, security_id=sec.id, as_of=now, quantity=p["quantity"],
            price=p["price"], price_as_of=now, market_value=p["market_value"],
            cost_basis=p["cost_basis"],
            cost_basis_quality="verified_account" if p["cost_basis"] is not None else None,
            source="snaptrade",
            # Brokerage-reported price; may be delayed - treat value as delayed market data.
            quality="delayed_market",
        ))

    # Activities, incrementally with a small overlap window
    cur = session.get(SyncCursor, (inst.id, f"activities:{acct.id}"))
    start = HISTORY_START
    if cur:
        start = dt.date.fromisoformat(cur.cursor) - dt.timedelta(days=OVERLAP_DAYS)
    today = now.date()
    new = 0
    for raw_act in adapter.get_activities(m["provider_account_id"], start, today):
        a = map_activity(raw_act)
        if a is None:
            continue
        sec = _security(session, a.pop("security")) if a.get("security") else None
        a.pop("security", None)
        exists = session.scalar(select(InvestmentActivity).where(
            InvestmentActivity.account_id == acct.id,
            InvestmentActivity.provider_activity_id == a["provider_activity_id"]))
        if exists is None:
            session.add(InvestmentActivity(account_id=acct.id, security_id=sec.id if sec else None,
                                           source="snaptrade", **a))
            new += 1
    if cur is None:
        session.add(SyncCursor(institution_id=inst.id, kind=f"activities:{acct.id}",
                               cursor=today.isoformat()))
    else:
        cur.cursor = today.isoformat()
    inst.last_synced_at = now
    session.flush()
    return {"account": f"{m['institution_name']} {m['name']}", "positions": len(positions),
            "activities_added": new}


def run_snaptrade_sync(engine: Engine | None = None, adapter: Any = None) -> list[dict[str, Any]]:
    if adapter is None:
        from fin.providers.snaptrade_adapter import SnapTradeAdapter

        adapter = SnapTradeAdapter()
    now = _utcnow()
    with session_scope(engine) as s:
        run = SyncRun(provider="snaptrade")
        s.add(run)
        s.flush()
        run_id = run.id
    results: list[dict[str, Any]] = []
    status, error = "ok", None
    try:
        for raw in adapter.list_accounts():
            with session_scope(engine) as s:
                results.append(sync_snaptrade_account(s, adapter, raw, now))
    except Exception as exc:
        status, error = "error", redact(str(exc))
        log.error("SnapTrade sync failed: %s", error)
    with session_scope(engine) as s:
        run = s.get(SyncRun, run_id)
        run.status, run.error, run.finished_at = status, error, _utcnow()
        run.records = sum(r["activities_added"] for r in results)
    if status == "error":
        results.append({"error": error})
    return results
