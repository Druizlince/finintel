"""Tool implementations (plain functions, testable without MCP). Read-only toward banks/brokers.

Every result has the same shape:  {"data": ..., "sources": [...], "warnings": [...]}
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from fin.analytics.cashflow import Txn
from fin.analytics.rules import Rule, apply_rules
from fin.db.models import (
    Account, BalanceSnapshot, CategoryRule, HoldingSnapshot, Institution, InvestmentActivity,
    PlannedExpense, Security, Transaction,
)
from fin.db.session import init_db, session_scope
from fin.web import summary

NOT_ADVICE = "Calculations from your own data - not financial advice."


def _iso(x: dt.datetime | dt.date | None) -> str | None:
    return x.isoformat() if x else None


def _sources(engine: Engine | None) -> list[dict[str, Any]]:
    with session_scope(engine) as s:
        out = []
        for i in s.scalars(select(Institution).order_by(Institution.name)):
            quality = "delayed_market" if i.provider == "snaptrade" else "verified_account"
            out.append({"name": i.name, "via": i.provider, "last_synced": _iso(i.last_synced_at),
                        "status": i.status, "quality": quality})
        return out


def _wrap(data: Any, engine: Engine | None, warnings: list[str] | None = None) -> dict[str, Any]:
    return {"data": data, "sources": _sources(engine), "warnings": warnings or []}


def data_status(engine: Engine | None = None) -> dict[str, Any]:
    """Which accounts are connected, how far back history goes, when data was last refreshed."""
    init_db(engine)
    with session_scope(engine) as s:
        rows = []
        for a in s.scalars(select(Account).join(Institution).order_by(Institution.name)):
            model = InvestmentActivity if a.institution.provider == "snaptrade" else Transaction
            first, last, n = s.execute(select(func.min(model.date), func.max(model.date),
                                              func.count()).where(model.account_id == a.id)).one()
            bal = s.scalar(select(func.max(BalanceSnapshot.as_of))
                           .where(BalanceSnapshot.account_id == a.id))
            rows.append({"institution": a.institution.name, "account": a.name, "type": a.type,
                         "mask": a.mask, "records": n, "history_from": _iso(first),
                         "history_to": _iso(last), "balance_as_of": _iso(bal),
                         "needs_relink": a.institution.status == "needs_relink"})
    warnings = []
    if any(r["needs_relink"] for r in rows):
        warnings.append("Some institutions need re-login: run `fin plaid link`.")
    return _wrap(rows, engine, warnings)


def overview(engine: Engine | None = None) -> dict[str, Any]:
    """Balances across all accounts, net cash and investments."""
    init_db(engine)
    o = summary.overview(engine)
    warnings = ["Credit card 'current' is the amount owed."]
    if o.get("owed_to_you_on_cards"):
        warnings.append(f"card_owed and net_cash exclude {o['owed_to_you_on_cards']:,.2f} that "
                        "others owe you for charges on his cards (see owed_to_you).")
    if o["needs_relink"]:
        warnings.append("Needs re-login: " + ", ".join(o["needs_relink"]))
    return _wrap(o, engine, warnings)


def cashflow(engine: Engine | None = None, months: int = 3, ef_months: int = 6) -> dict[str, Any]:
    """Monthly income vs spending, categories, recurring charges, plan and outlook."""
    init_db(engine)
    c = summary.cashflow(engine, months=months, ef_months=ef_months)
    warnings = [NOT_ADVICE]
    if c["partial"] or len(c["months_used"]) < 2:
        warnings.append(f"Averages use only {len(c['months_used'])} month(s) of complete data "
                        f"({', '.join(c['months_used'])}); treat them as rough.")
    warnings.append("Some cash earnings may be spent before being deposited, so both income and "
                    "spending can be slightly understated.")
    return _wrap(c, engine, warnings)


def plan(engine: Engine | None = None, ef_months: int = 6) -> dict[str, Any]:
    """How much can be invested, when, and the month-by-month outlook."""
    c = summary.cashflow(engine, ef_months=ef_months)
    data = {"plan": c["plan"], "planned_expenses": c["planned"], "outlook": c["outlook"],
            "months_used": c["months_used"]}
    return _wrap(data, engine, [NOT_ADVICE] + c["outlook"]["warnings"])


def upcoming(engine: Engine | None = None, days: int = 60) -> dict[str, Any]:
    """Planned expenses and expected recurring charges/income in the next N days."""
    today = dt.date.today()
    end = today + dt.timedelta(days=days)
    c = summary.cashflow(engine)
    items = [{"date": p["due"], "name": p["name"], "amount": -p["amount"], "kind": "planned"}
             for p in c["planned"] if p["due"] <= end.isoformat()]
    items += [{"date": r["next"], "name": r["merchant"],
               "amount": r["amount"] if r["direction"] == "in" else -r["amount"],
               "kind": "recurring (estimated)"}
              for r in c["recurring"] if r["active"] and today.isoformat() <= r["next"] <= end.isoformat()]
    return _wrap(sorted(items, key=lambda x: x["date"]), engine,
                 ["Recurring dates are estimated from past charges."])


def search_transactions(engine: Engine | None = None, query: str = "", month: str | None = None,
                        limit: int = 25) -> dict[str, Any]:
    """Find transactions by merchant/description words, optionally within a month (YYYY-MM)."""
    init_db(engine)
    limit = max(1, min(limit, 100))
    q = query.lower().strip()
    with session_scope(engine) as s:
        rules = [Rule(r.pattern, r.category, r.label,
                      Decimal(r.min_amount) if r.min_amount is not None else None,
                      Decimal(r.max_amount) if r.max_amount is not None else None, r.direction)
                 for r in s.scalars(select(CategoryRule).order_by(CategoryRule.id))]
        stmt = (select(Transaction, Account, Institution).select_from(Transaction)
                .join(Account, Transaction.account_id == Account.id)
                .join(Institution, Account.institution_id == Institution.id)
                .order_by(Transaction.date.desc()))
        out = []
        for t, a, i in s.execute(stmt):
            text = f"{t.merchant or ''} {t.description or ''}".lower()
            if q and q not in text:
                continue
            if month and t.date.strftime("%Y-%m") != month:
                continue
            labeled = apply_rules(Txn(t.id, a.id, a.type, t.date, Decimal(t.amount), t.merchant,
                                      t.category, t.category_detail, bool(t.pending)),
                                  t.description, rules)
            out.append({"date": t.date.isoformat(), "amount": summary.num(t.amount),
                        "merchant": labeled.merchant, "category": labeled.category,
                        "account": f"{i.name} {a.name}", "pending": bool(t.pending)})
            if len(out) >= limit:
                break
    return _wrap(out, engine, ["Amounts: negative = money out, positive = money in."])


def holdings(engine: Engine | None = None) -> dict[str, Any]:
    """Brokerage positions (Fidelity via SnapTrade), latest snapshot per account."""
    init_db(engine)
    with session_scope(engine) as s:
        result = []
        for a in s.scalars(select(Account).join(Institution).where(Institution.provider == "snaptrade")):
            latest = s.scalar(select(func.max(HoldingSnapshot.as_of)).where(HoldingSnapshot.account_id == a.id))
            positions = []
            if latest:
                for h, sec in s.execute(select(HoldingSnapshot, Security).join(Security).where(
                        HoldingSnapshot.account_id == a.id, HoldingSnapshot.as_of == latest)):
                    positions.append({"ticker": sec.ticker, "name": sec.name, "type": sec.type,
                                      "quantity": float(h.quantity), "price": summary.num(h.price),
                                      "market_value": summary.num(h.market_value),
                                      "cost_basis": summary.num(h.cost_basis),
                                      "cost_basis_quality": h.cost_basis_quality,
                                      "quality": h.quality})
            bal = s.scalar(select(BalanceSnapshot).where(BalanceSnapshot.account_id == a.id)
                           .order_by(BalanceSnapshot.as_of.desc()).limit(1))
            result.append({"account": f"{a.institution.name} {a.name}", "mask": a.mask,
                           "type": a.type, "total_value": summary.num(bal.current) if bal else None,
                           "cash": summary.num(bal.available) if bal else None,
                           "as_of": _iso(latest or (bal.as_of if bal else None)),
                           "positions": positions})
    return _wrap(result, engine, ["Prices come from the brokerage and may be delayed."])


def investment_activity(engine: Engine | None = None, limit: int = 50) -> dict[str, Any]:
    """Recent brokerage activity: buys, sells, dividends, deposits, withdrawals."""
    init_db(engine)
    limit = max(1, min(limit, 500))
    with session_scope(engine) as s:
        rows = s.execute(
            select(InvestmentActivity, Security, Account).select_from(InvestmentActivity)
            .join(Account, InvestmentActivity.account_id == Account.id)
            .outerjoin(Security, InvestmentActivity.security_id == Security.id)
            .order_by(InvestmentActivity.date.desc()).limit(limit)).all()
        data = [{"date": a.date.isoformat(), "type": a.type, "ticker": sec.ticker if sec else None,
                 "quantity": float(a.quantity) if a.quantity is not None else None,
                 "price": summary.num(a.price), "amount": summary.num(a.amount),
                 "account_mask": acct.mask} for a, sec, acct in rows]
    return _wrap(data, engine)


def sync_now(engine: Engine | None = None) -> dict[str, Any]:
    """Refresh data from Plaid (banks/cards) and SnapTrade (Fidelity). Read-only toward them."""
    from fin import secrets
    from fin.logsetup import redact
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
    return _wrap(out, engine, ["Apple Card is imported from statement PDFs, not synced."])


def owed_to_me(engine: Engine | None = None, action: str = "list", who: str = "",
               amount: float | None = None, card: str | None = None, note: str = "",
               item_id: int | None = None) -> dict[str, Any]:
    """Track money people owe the user for charges on his cards (bookkeeping only, moves no money).
    action: list | add (who, amount, card, note) | settle (item_id, optional partial amount)."""
    from fin.analytics.receivables import add_receivable, load_receivables, settle_receivable

    init_db(engine)
    with session_scope(engine) as s:
        if action == "add":
            if not who or not amount or amount <= 0:
                return _wrap(None, engine, ["add needs who and a positive amount"])
            r = add_receivable(s, who, Decimal(str(amount)), card=card, note=note)
            msg = f"Added: {r.who} owes {r.amount:,.2f}" + (f" on {r.card}" if r.card else "")
        elif action == "settle":
            r = settle_receivable(s, int(item_id or 0),
                                  Decimal(str(amount)) if amount else None)
            msg = "No item with that id." if r is None else f"Updated item {r.id} ({r.who})."
        else:
            msg = ""
        items = [{"id": r.id, "who": r.who, "amount": float(r.amount), "card": r.card,
                  "note": r.note, "since": r.created} for r in load_receivables(s)]
    return _wrap({"result": msg, "open": items}, engine,
                 ["Open items on a card are excluded from card_owed and net_cash."])


def portfolio(engine: Engine | None = None, new_money: float = 0) -> dict[str, Any]:
    """Allocation vs targets, concentration, gains, and how to split new money."""
    init_db(engine)
    p = summary.portfolio(engine, new_money=new_money)
    warnings = list(p.pop("warnings"))
    if not p["positions"]:
        warnings.append("No invested positions yet - allocation is empty until money is invested.")
    warnings.append("Cash in the brokerage (e.g. SPAXX) is shown separately, not in the allocation.")
    warnings.append(NOT_ADVICE)
    return _wrap(p, engine, warnings)


def research(engine: Engine | None = None, ticker: str = "", planned: float = 0.0,
             refresh: bool = False) -> dict[str, Any]:
    """12-point memo for a stock from public data (FRED, Finnhub, FMP)."""
    from fin.research.engine import research as run

    init_db(engine)
    try:
        memo = run(ticker, refresh=refresh, planned=planned, engine=engine)
    except ValueError as exc:
        return _wrap(None, engine, [str(exc)])
    warnings = list(memo.get("gaps", [])) + [memo["disclaimer"]]
    return {"data": memo, "sources": [{"name": n, "via": "public API", "quality": "delayed_market"}
                                      for n in ("FRED", "Finnhub", "FMP")], "warnings": warnings}


def macro(engine: Engine | None = None, refresh: bool = False) -> dict[str, Any]:
    """Latest macro indicators from FRED."""
    from fin.research.engine import macro as run

    m = run(refresh=refresh)
    return {"data": m["series"], "sources": [{"name": "FRED", "via": "public API"}],
            "warnings": m["gaps"]}


def risk(engine: Engine | None = None, new_money: float = 0, keep_cash: float = 0,
         pick: str | None = None) -> dict[str, Any]:
    """Cash runway, stress tests and volatility (optionally for a hypothetical investment)."""
    init_db(engine)
    r = summary.risk(engine, new_money=new_money, keep_cash=keep_cash, pick=pick)
    return _wrap(r, engine, r["flags"] + r["notes"] + [NOT_ADVICE])
