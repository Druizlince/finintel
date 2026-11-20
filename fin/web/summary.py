"""JSON-ready summaries for the web app. Same engine as the CLI report."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from fin.analytics.cashflow import (
    UNLINKED_CARD, ZERO, _add_months, average, build_plan, complete_months, detect_recurring,
    find_internal_transfers, monthly, spending_by_category,
)
from fin.analytics.forecast import Planned, build_outlook, upcoming
from fin.analytics.balances import statement_card_balance
from fin.analytics.receivables import load_receivables, owed_by_others
from fin.analytics.report import CASH_TYPES, load
from fin.db.models import (
    Account, BalanceSnapshot, Institution, InvestmentActivity, PlannedExpense, SyncRun, Transaction,
)
from fin.db.session import session_scope


def num(x: Decimal | None) -> float | None:
    return None if x is None else float(round(Decimal(x), 2))


def label(cat: str) -> str:
    if cat == UNLINKED_CARD:
        return "Payments to unlinked cards (e.g. Apple Card)"
    return cat.replace("_", " ").title()


def overview(engine: Engine | None = None) -> dict[str, Any]:
    with session_scope(engine) as s:
        accounts = s.scalars(select(Account).join(Institution).order_by(Institution.name)).all()
        rows, cash, owed, invest, as_of = [], ZERO, ZERO, ZERO, None
        recv = load_receivables(s)
        others_total = ZERO
        for a in accounts:
            snap = s.scalar(select(BalanceSnapshot).where(BalanceSnapshot.account_id == a.id)
                            .order_by(BalanceSnapshot.as_of.desc()).limit(1))
            model = InvestmentActivity if a.institution.provider == "snaptrade" else Transaction
            n = s.scalar(select(func.count()).select_from(model).where(model.account_id == a.id))
            estimated = False
            others = ZERO
            if snap:
                as_of = max(filter(None, [as_of, snap.as_of]))
                cur, estimated = statement_card_balance(s, a, snap)
                if a.type in CASH_TYPES:
                    cash += Decimal(snap.available if snap.available is not None else cur)
                elif a.type == "credit":
                    others = min(owed_by_others(recv, a.institution.name), max(cur, ZERO))
                    others_total += others
                    owed += cur - others
                elif a.type != "loan":
                    invest += cur
            rows.append({
                "institution": a.institution.name, "name": a.name, "type": a.type,
                "mask": a.mask, "current": num(cur) if snap else None, "estimated": estimated,
                "available": num(snap.available) if snap else None, "count": n,
                "owed_by_others": num(others) if others else None,
                "status": a.institution.status,
            })
        last = s.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
        return {
            "accounts": rows, "cash": num(cash), "card_owed": num(owed),
            "net_cash": num(cash - owed), "investments": num(invest),
            "owed_to_you": [{"id": r.id, "who": r.who, "amount": num(r.amount), "card": r.card,
                             "note": r.note, "since": r.created} for r in recv],
            "owed_to_you_on_cards": num(others_total),
            "balances_as_of": as_of.isoformat() if as_of else None,
            "last_sync": last.started_at.isoformat() if last else None,
            "needs_relink": [r["institution"] for r in rows if r["status"] == "needs_relink"],
        }


def _keys(bank, by_month, today: dt.date, months: int, start: str | None) -> tuple[list[str], bool]:
    if start:
        y, m = map(int, start.split("-"))
        end_y, end_m = _add_months(today.year, today.month, -1)
        keys = []
        while (y, m) <= (end_y, end_m):
            keys.append(f"{y:04d}-{m:02d}")
            y, m = _add_months(y, m, 1)
        return keys[-months:], False
    keys = complete_months(bank, today, months)
    if keys:
        return keys, False
    return sorted(by_month)[-months:], True


def cashflow(
    engine: Engine | None = None, *, months: int = 3, ef_months: int = 6, start: str | None = None,
    today: dt.date | None = None, outlook_months: int = 6, include_transfers_in: bool = False,
) -> dict[str, Any]:
    today = today or dt.date.today()
    txns, bal, names = load(engine)
    bank = [t for t in txns if t.account_type in CASH_TYPES | {"credit"}]
    internal = find_internal_transfers(bank)
    by_month = monthly(bank, internal)
    keys, partial = _keys(bank, by_month, today, months, start)
    avg = average(by_month, keys)
    cats = spending_by_category(bank, internal, keys)
    # One-off / planned categories (tuition, trips) come OUT of the monthly average and are
    # planned with real dates as planned expenses instead.
    edu = sum((cats.get(c, ZERO) for c in ("EDUCATION", "TRAVEL")), ZERO)

    with session_scope(engine) as s:
        planned = [Planned(p.name, Decimal(p.amount), p.due_date, p.category)
                   for p in s.scalars(select(PlannedExpense))]
    future = upcoming(planned, today)
    plan = build_plan(
        replace(avg, spending=avg.spending - edu), bal["cash"], bal["card_owed"],
        ef_months=ef_months, reserve=sum((p.amount for p in future), ZERO),
        include_transfers_in=include_transfers_in,
    )
    outlook = build_outlook(plan.net_cash, plan.monthly_surplus, planned, plan.ef_target, today,
                            months=outlook_months)
    rec = detect_recurring(bank, internal, today)
    this = by_month.get(f"{today:%Y-%m}")

    def month_dict(m):
        return {"key": m.key, "income": num(m.income), "spending": num(m.spending),
                "money_in": num(m.external_in), "money_out": num(m.external_out), "net": num(m.net)}

    total = sum(cats.values(), ZERO) or Decimal(1)
    return {
        "institutions": [n for n in names if n != "Fidelity"],
        "months_used": keys, "partial": partial, "internal_transfers": len(internal),
        "months": [month_dict(by_month[k]) for k in keys if k in by_month],
        "average": month_dict(avg),
        "this_month": month_dict(this) if this else None, "today": today.isoformat(),
        "categories": [{"category": c, "label": label(c), "amount": num(a),
                        "share": float(round(a / total * 100, 1))}
                       for c, a in cats.items() if a > 0],
        "recurring": [{"merchant": r.merchant, "direction": r.direction, "cadence": r.cadence,
                       "amount": num(r.typical_amount), "per_month": num(r.per_month),
                       "next": r.next_expected.isoformat(), "active": r.active}
                      for r in rec],
        "plan": {
            "monthly_income": num(plan.monthly_income), "monthly_need": num(plan.monthly_need),
            "tuition_excluded": num(edu), "monthly_surplus": num(plan.monthly_surplus),
            "cash": num(plan.cash), "card_owed": num(plan.card_owed),
            "net_cash": num(plan.net_cash), "ef_months": plan.ef_months,
            "ef_target": num(plan.ef_target), "planned_total": num(plan.reserve),
            "investable_now": num(plan.investable_now),
            "investable_monthly": num(plan.investable_monthly),
            "months_to_fund": num(plan.months_to_fund), "notes": plan.notes,
        },
        "planned": [{"name": p.name, "amount": num(p.amount), "due": p.due.isoformat(),
                     "category": p.category} for p in future],
        "outlook": {
            "months": [{"key": r.key, "surplus": num(r.surplus), "planned": num(r.planned_out),
                        "end_balance": num(r.end_balance), "free": num(r.free), "items": r.items}
                       for r in outlook.months],
            "invest_from": outlook.invest_from.key if outlook.invest_from else None,
            "invest_from_free": num(outlook.invest_from.free) if outlook.invest_from else None,
            "warnings": outlook.warnings,
        },
    }


def portfolio(engine: Engine | None = None, new_money: float = 0) -> dict[str, Any]:
    """Phase 4b: allocation vs targets, concentration, gains, and a buy plan for new money."""
    from fin.analytics.portfolio import analyze, history, load_positions, load_targets

    with session_scope(engine) as s:
        positions, extra_cash, hist, as_of = load_positions(s)
        targets = load_targets(s)
    a = analyze(positions, extra_cash, targets, Decimal(str(new_money or 0)))
    h = history(hist)

    def rows(items: list[dict]) -> list[dict]:
        out = []
        for r in items:
            out.append({k: (num(v) if isinstance(v, Decimal) else v) for k, v in r.items()})
        return out

    gains = {k: (num(v) if isinstance(v, Decimal) else v) for k, v in a.gains.items()
             if k != "by_position"}
    if a.gains.get("by_position"):
        gains["by_position"] = rows(a.gains["by_position"])
    return {
        "as_of": as_of,
        "total": num(a.total), "invested": num(a.invested), "cash": num(a.cash),
        "targets": {k: num(v) for k, v in targets.items()},
        "allocation": rows(a.allocation),
        "top_positions": rows(a.top),
        "positions": [{"ticker": p.ticker, "name": p.name, "type": p.sec_type,
                       "account": p.account, "quantity": float(p.quantity),
                       "price": num(p.price), "value": num(p.market_value),
                       "cost_basis": num(p.cost_basis)} for p in positions],
        "gains": gains,
        "history": {"series": [{"date": x["date"], "value": num(x["value"])}
                               for x in h["series"]],
                    "change": num(h["change"]), "change_pct": num(h["change_pct"]),
                    "note": h.get("note")},
        "new_money": num(a.new_money),
        "buy_plan": rows(a.buy_plan),
        "warnings": a.warnings,
    }


def _beta_lookup(tickers: list[str]) -> dict[str, float]:
    """Betas from Finnhub (cached daily). Never fails: missing -> default beta later."""
    out: dict[str, float] = {}
    if not tickers:
        return out
    try:
        from fin.analytics.risk import safe_beta
        from fin.research.engine import clients
        from fin.research.http import FetchError

        _, finnhub, _ = clients()
        if finnhub is None:
            return out
        for t in tickers:
            try:
                b = safe_beta(finnhub.metrics(t).get("beta"))
            except FetchError:
                b = None
            if b is not None:
                out[t] = b
    except Exception:  # noqa: BLE001
        pass
    return out


def risk(engine: Engine | None = None, new_money: float = 0, keep_cash: float = 0,
         pick: str | None = None, today: dt.date | None = None) -> dict[str, Any]:
    """Phase 4d: cash runway, stress tests and volatility for current holdings plus, optionally,
    a hypothetical investment of new_money split by the 4b buy plan (keep_cash = part of the
    new money kept in cash/SPAXX; pick = ticker for the individual-stock slice)."""
    from fin.analytics.portfolio import bucket_of, buy_plan, load_positions, load_targets
    from fin.analytics.risk import Holding, build, runway

    today = today or dt.date.today()
    with session_scope(engine) as s:
        positions, broker_cash, _h, _ = load_positions(s)
        targets = load_targets(s)
    holdings = [Holding(p.ticker or p.name, bucket_of(p), float(p.market_value))
                for p in positions if bucket_of(p) != "cash"]
    hypothetical = []
    if new_money and new_money > 0:
        by_bucket: dict[str, Decimal] = {}
        for h in holdings:
            by_bucket[h.bucket] = by_bucket.get(h.bucket, ZERO) + Decimal(str(h.value))
        for r in buy_plan(by_bucket, Decimal(str(new_money)), targets):
            t = (pick or "PICK").upper() if r["bucket"] == "single_stock" else r["suggest"] or r["bucket"]
            hypothetical.append(Holding(t, r["bucket"], float(r["amount"])))
    allh = holdings + hypothetical
    betas = _beta_lookup(sorted({h.ticker for h in allh if h.bucket == "single_stock"
                                 and h.ticker != "PICK"}))
    for h in allh:
        if h.bucket == "single_stock":
            h.beta = betas.get(h.ticker)

    cf = cashflow(engine, today=today)
    plan = cf["plan"]
    horizon = today + dt.timedelta(days=183)
    bills = [p for p in cf["planned"] if dt.date.fromisoformat(p["due"]) <= horizon]
    bills_6m = sum(p["amount"] for p in bills)
    tuition_m = sum(p["amount"] for p in bills if (p["category"] or "") == "EDUCATION") / 6
    paycheck = sum(r["per_month"] or 0 for r in cf["recurring"]
                   if r["direction"] == "in" and r["active"] and "payroll" in r["merchant"].lower())
    liquid = (plan["net_cash"] or 0) + float(broker_cash) + (keep_cash or 0)
    run = runway(liquid, plan["monthly_need"] or 0, paycheck, tuition_m, bills_6m)
    rep = build(allh, run)
    return {
        "today": today.isoformat(),
        "runway": rep.runway,
        "holdings": [{"ticker": h.ticker, "bucket": h.bucket, "value": round(h.value, 2),
                      "beta": h.beta, "hypothetical": any(h is x for x in hypothetical)}
                     for h in allh],
        "total": rep.total, "hypothetical_new_money": new_money or 0,
        "stress": rep.stress, "volatility": rep.volatility, "flags": rep.flags,
        "notes": ["Scenario losses and volatility are estimates from past crashes, not predictions.",
                  "Runway uses available cash minus card balances (what others owe you excluded) "
                  "plus brokerage cash; 'without sponsor' assumes only your paychecks continue.",
                  "Individual stocks use their beta (Finnhub) x the market drop, else beta 1.5."],
    }
