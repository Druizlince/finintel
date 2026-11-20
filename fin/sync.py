"""Pull data from providers into the database. Claude never calls providers directly -
it reads what these jobs stored."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fin import crypto, secrets
from fin.db.models import Account, BalanceSnapshot, Institution, SyncCursor, SyncRun, Transaction
from fin.db.session import session_scope
from fin.logsetup import redact
from fin.providers.plaid_mapping import map_account, map_transaction

log = logging.getLogger(__name__)

RELINK_CODES = {"ITEM_LOGIN_REQUIRED", "ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN", "ITEM_LOCKED"}
MUTATION_CODE = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _s(value: Any) -> str | None:
    value = getattr(value, "value", value)
    return None if value is None else str(value)


def _token_name(item_id: str) -> str:
    return f"plaid_token:{item_id}"


def link_plaid_item(session: Session, adapter: Any, public_token: str) -> Institution:
    """Exchange a public token, store the access token in the keyring, record the institution."""
    access_token, item_id = adapter.exchange_public_token(public_token)
    secrets.set_secret(_token_name(item_id), access_token)
    plaid_inst_id, name = adapter.institution_info(access_token)
    inst = Institution(
        name=name,
        provider="plaid",
        provider_institution_id=plaid_inst_id,
        provider_ref_enc=crypto.encrypt(item_id),
    )
    session.add(inst)
    session.flush()
    log.info("Linked institution id=%s name=%s", inst.id, inst.name)
    return inst


def _upsert_accounts(session: Session, inst: Institution, raw_accounts: list[dict], now) -> dict:
    by_provider_id: dict[str, int] = {}
    for raw in raw_accounts:
        m = map_account(raw)
        acct = session.scalar(
            select(Account).where(
                Account.institution_id == inst.id,
                Account.provider_account_id == m["provider_account_id"],
            )
        )
        if acct is None:
            acct = Account(institution_id=inst.id, provider_account_id=m["provider_account_id"])
            session.add(acct)
        for field in ("name", "official_name", "type", "subtype", "mask", "currency"):
            setattr(acct, field, m[field])
        acct.is_emergency_fund = acct.is_emergency_fund or m["type"] == "savings"
        session.flush()

        b = m["balances"]
        if b["current"] is not None:
            session.add(
                BalanceSnapshot(
                    account_id=acct.id, as_of=now, current=b["current"],
                    available=b["available"], credit_limit=b["credit_limit"],
                    source="plaid", quality="verified_account",
                )
            )
        by_provider_id[m["provider_account_id"]] = acct.id
    return by_provider_id


def _apply_transactions(session: Session, accounts: dict[str, int], page: dict) -> tuple[int, int]:
    upserted = removed = 0
    for raw in list(page.get("added") or []) + list(page.get("modified") or []):
        m = map_transaction(raw)
        account_id = accounts.get(m.pop("provider_account_id"))
        if account_id is None:
            log.warning("Skipping transaction for an unknown account")
            continue
        txn = session.scalar(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.provider_txn_id == m["provider_txn_id"],
            )
        )
        if txn is None:
            txn = Transaction(account_id=account_id, source="plaid", **m)
            session.add(txn)
        else:
            for k, v in m.items():
                setattr(txn, k, v)
        upserted += 1
    ids = [r["transaction_id"] for r in (page.get("removed") or [])]
    if ids and accounts:
        res = session.execute(
            delete(Transaction).where(
                Transaction.provider_txn_id.in_(ids),
                Transaction.account_id.in_(list(accounts.values())),
            )
        )
        removed = res.rowcount or 0
    session.flush()
    return upserted, removed


def sync_plaid_institution(session: Session, adapter: Any, inst: Institution) -> dict[str, int]:
    item_id = crypto.decrypt(inst.provider_ref_enc)
    token = secrets.require_secret(_token_name(item_id))
    now = _utcnow()

    accounts = _upsert_accounts(session, inst, adapter.get_accounts(token), now)

    cursor_row = session.get(SyncCursor, (inst.id, "transactions"))
    start_cursor = cursor_row.cursor if cursor_row else ""
    update_status = None
    for attempt in range(3):
        cursor, upserted, removed = start_cursor, 0, 0
        try:
            while True:
                page = adapter.transactions_sync(token, cursor)
                u, r = _apply_transactions(session, accounts, page)
                upserted, removed = upserted + u, removed + r
                cursor = page["next_cursor"]
                update_status = _s(page.get("transactions_update_status"))
                if not page.get("has_more"):
                    break
            break
        except Exception as exc:  # Plaid asks you to restart pagination if data changed mid-way
            if getattr(exc, "code", None) == MUTATION_CODE and attempt < 2:
                log.info("Transactions changed during sync; restarting from last saved cursor")
                continue
            raise

    # Plaid returns a cursor even when its data is NOT_READY. Saving that cursor would skip
    # every transaction that arrives once the data is ready, so only save "ready" cursors.
    if update_status != "NOT_READY":
        if cursor_row is None:
            session.add(SyncCursor(institution_id=inst.id, kind="transactions", cursor=cursor))
        else:
            cursor_row.cursor = cursor
    inst.last_synced_at = now
    inst.status = "active"
    session.flush()
    return {
        "accounts": len(accounts),
        "transactions_upserted": upserted,
        "transactions_removed": removed,
        "update_status": update_status,
    }


def run_plaid_sync(engine: Engine | None = None, adapter: Any = None) -> list[dict[str, Any]]:
    """Sync every active Plaid institution. Each runs in its own transaction, so one failing
    bank never rolls back another, and every attempt is recorded in sync_runs."""
    if adapter is None:
        from fin.providers.plaid_adapter import PlaidAdapter

        adapter = PlaidAdapter()

    with session_scope(engine) as s:
        targets = [
            (i.id, i.name)
            for i in s.scalars(
                select(Institution).where(
                    Institution.provider == "plaid", Institution.status == "active"
                )
            )
        ]

    results = []
    for inst_id, name in targets:
        with session_scope(engine) as s:
            run = SyncRun(provider=f"plaid:{inst_id}")
            s.add(run)
            s.flush()
            run_id = run.id

        counts: dict[str, int] = {}
        status, error = "ok", None
        try:
            with session_scope(engine) as s:
                counts = sync_plaid_institution(s, adapter, s.get(Institution, inst_id))
        except Exception as exc:
            status, error = "error", redact(str(exc))
            log.error("Sync failed for %s: %s", name, error)
            if getattr(exc, "code", None) in RELINK_CODES:
                with session_scope(engine) as s:
                    s.get(Institution, inst_id).status = "needs_relink"

        with session_scope(engine) as s:
            run = s.get(SyncRun, run_id)
            run.status, run.error, run.finished_at = status, error, _utcnow()
            run.records = counts.get("transactions_upserted", 0)

        results.append({"institution": name, "status": status, "error": error, **counts})
    return results


def reset_plaid_cursors(engine: Engine | None = None) -> int:
    """Forget saved transaction cursors so the next sync re-reads full history (idempotent)."""
    with session_scope(engine) as s:
        res = s.execute(delete(SyncCursor).where(SyncCursor.kind == "transactions"))
        return res.rowcount or 0
