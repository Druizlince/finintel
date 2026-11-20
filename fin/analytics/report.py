"""Load data from the database and print the cash-flow report."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fin.analytics.cashflow import (
    UNLINKED_CARD, ZERO, Txn, average, build_plan, complete_months, detect_recurring,
    find_internal_transfers, monthly, spending_by_category,
)
from fin.analytics.rules import Rule, apply_rules
from fin.analytics.forecast import Planned, build_outlook, upcoming
from fin.db.models import (Account, BalanceSnapshot, CategoryRule, Institution, PlannedExpense,
                           Transaction)
from fin.db.session import session_scope

CASH_TYPES = {"checking", "savings"}


def load(engine: Engine | None = None) -> tuple[list[Txn], dict, list[str]]:
    """Returns (transactions, balances, institution names)."""
    with session_scope(engine) as s:
        accounts = {a.id: a for a in s.scalars(select(Account).where(Account.is_active.is_(True)))}
        rules = [Rule(r.pattern, r.category, r.label,
                      Decimal(r.min_amount) if r.min_amount is not None else None,
                      Decimal(r.max_amount) if r.max_amount is not None else None,
                      r.direction)
                 for r in s.scalars(select(CategoryRule).order_by(CategoryRule.id))]
        txns = [
            apply_rules(
                Txn(id=t.id, account_id=t.account_id, account_type=accounts[t.account_id].type,
                    date=t.date, amount=Decimal(t.amount), merchant=t.merchant,
                    category=t.category, detail=t.category_detail, pending=bool(t.pending)),
                t.description, rules,
            )
            for t in s.scalars(select(Transaction)) if t.account_id in accounts
        ]
        balances = {"cash": ZERO, "card_owed": ZERO, "investments": ZERO, "as_of": None}
        for a in accounts.values():
            snap = s.scalar(select(BalanceSnapshot).where(BalanceSnapshot.account_id == a.id)
                            .order_by(BalanceSnapshot.as_of.desc()).limit(1))
            if snap is None:
                continue
            balances["as_of"] = max(filter(None, [balances["as_of"], snap.as_of]))
            if a.type in CASH_TYPES:
                balances["cash"] += Decimal(snap.available if snap.available is not None else snap.current)
            elif a.type == "credit":
                from fin.analytics.balances import statement_card_balance

                bal = statement_card_balance(s, a, snap)[0]
                # Charges someone else owes you back aren't your debt.
                from fin.analytics.receivables import load_receivables, owed_by_others

                others = min(owed_by_others(load_receivables(s), a.institution.name), max(bal, ZERO))
                balances["card_owed"] += bal - others
            elif a.type in {"brokerage", "ira", "roth_ira", "401k", "403b", "457b", "hsa"}:
                balances["investments"] += Decimal(snap.current)
        names = sorted({n for (n,) in s.execute(
            select(Institution.name).join(Account).where(Account.is_active.is_(True)))})
    return txns, balances, names


def _label(cat: str) -> str:
    if cat == UNLINKED_CARD:
        return "Payments to unlinked cards (e.g. Apple Card)"
    return cat.replace("_", " ").title()


def _m(x: Decimal) -> str:
    return f"{x:,.2f}"


def print_report(
    *, months: int = 3, ef_months: int = 6, lump: Decimal = ZERO, reserve: Decimal = ZERO,
    deficit_buffer_months: int = 12, include_transfers_in: bool = False,
    engine: Engine | None = None, today: dt.date | None = None, start: str | None = None,
    outlook_months: int = 6,
) -> None:
    today = today or dt.date.today()
    txns, bal, names = load(engine)
    if not txns:
        print("No transactions yet - run `fin sync` first.")
        return
    bank_txns = [t for t in txns if t.account_type in CASH_TYPES | {"credit"}]
    internal = find_internal_transfers(bank_txns)
    by_month = monthly(bank_txns, internal)
    if start:  # user-chosen start month, e.g. "2026-09"
        from fin.analytics.cashflow import _add_months

        y, m = map(int, start.split("-"))
        end_y, end_m = _add_months(today.year, today.month, -1)
        keys = []
        while (y, m) <= (end_y, end_m):
            keys.append(f"{y:04d}-{m:02d}")
            y, m = _add_months(y, m, 1)
        keys = keys[-months:]
    else:
        keys = complete_months(bank_txns, today, months)
    partial = not keys
    if partial:  # not a full month of history yet: use whatever exists
        keys = sorted(by_month)[-months:]

    print(f"CASH FLOW  -  {', '.join(n for n in names if n != 'Fidelity')}")
    if partial:
        print("  ! Less than one complete month of history for every account - numbers are rough.")
    firsts: dict[str, dt.date] = {}
    with session_scope(engine) as s:
        acct_names = {a.id: f"{a.institution.name} {a.name}"[:30]
                      for a in s.scalars(select(Account))}
    for t in bank_txns:
        name = acct_names.get(t.account_id, str(t.account_id))
        if name not in firsts or t.date < firsts[name]:
            firsts[name] = t.date
    print("  History starts: " + "; ".join(f"{n} {d:%Y-%m-%d}" for n, d in sorted(firsts.items())))
    print(f"  Months used: {', '.join(keys)}   (pending transactions excluded; "
          f"{len(internal)} transfers between your own accounts excluded)")
    print()
    print(f"  {'Month':8} {'Income':>11} {'Spending':>11} {'Money in*':>11} {'Money out*':>11} {'Net':>11}")
    for k in keys:
        m = by_month.get(k)
        if m is None:
            continue
        print(f"  {k:8} {_m(m.income):>11} {_m(m.spending):>11} {_m(m.external_in):>11} "
              f"{_m(m.external_out):>11} {_m(m.net):>11}")
    avg = average(by_month, keys)
    print(f"  {'Average':8} {_m(avg.income):>11} {_m(avg.spending):>11} {_m(avg.external_in):>11} "
          f"{_m(avg.external_out):>11} {_m(avg.net):>11}")
    this = by_month.get(f"{today:%Y-%m}")
    if this:
        print(f"  This month so far ({today:%b} 1-{today.day}): income {_m(this.income)}, "
              f"spending {_m(this.spending)}")
    print("  * transfers to/from people or accounts not linked here (e.g. family, Zelle)")

    print("\nWHERE THE MONEY GOES  (average per month)")
    cats = spending_by_category(bank_txns, internal, keys)
    total = sum(cats.values(), ZERO) or Decimal(1)
    for cat, amt in list(cats.items())[:12]:
        if amt <= 0:
            continue
        print(f"  {_label(cat)[:44]:44} {_m(amt):>10}  {amt / total * 100:5.1f}%")

    rec = detect_recurring(bank_txns, internal, today)
    outs = [r for r in rec if r.direction == "out" and r.active]
    ins = [r for r in rec if r.direction == "in" and r.active]
    print("\nRECURRING  (detected from repeat charges - check these)")
    if not rec:
        print("  None detected yet (needs ~3 occurrences of the same charge).")
    for title, group in (("Money out", outs), ("Money in", ins)):
        if not group:
            continue
        print(f"  {title}:")
        for r in group:
            print(f"    {r.merchant[:28]:28} {r.cadence:14} {_m(r.typical_amount):>9}  "
                  f"~{_m(r.per_month):>8}/mo  next ~{r.next_expected:%b %d}")
    if outs:
        print(f"    {'Total recurring out':43} ~{_m(sum((r.per_month for r in outs), ZERO)):>8}/mo")
    stopped = [r for r in rec if not r.active and r.direction == "out"]
    if stopped:
        print("  Looks stopped (no recent charge): " + ", ".join(r.merchant for r in stopped[:8]))

    # Tuition is lumpy and has known dates/amounts: take it OUT of the monthly average and plan
    # the real upcoming installments with --reserve instead (avoids counting it twice).
    from dataclasses import replace as _replace

    # One-off / planned categories (tuition, trips) come OUT of the monthly average and are
    # planned with real dates as planned expenses instead.
    edu = sum((cats.get(c, ZERO) for c in ("EDUCATION", "TRAVEL")), ZERO)
    avg_plan = _replace(avg, spending=avg.spending - edu)
    with session_scope(engine) as s:
        planned = [Planned(p.name, Decimal(p.amount), p.due_date, p.category)
                   for p in s.scalars(select(PlannedExpense))]
    future = upcoming(planned, today)
    planned_total = sum((p.amount for p in future), ZERO)
    plan = build_plan(avg_plan, bal["cash"], bal["card_owed"], ef_months=ef_months, lump=lump,
                      reserve=reserve + planned_total, deficit_buffer_months=deficit_buffer_months,
                      include_transfers_in=include_transfers_in)
    print("\nPOSITION NOW" + (f"  (balances as of {bal['as_of']:%Y-%m-%d %H:%M} UTC)" if bal["as_of"] else ""))
    print(f"  Cash (checking + savings)        {_m(plan.cash):>12}")
    print(f"  Owed on cards                    {_m(-plan.card_owed):>12}")
    print(f"  Net cash                         {_m(plan.net_cash):>12}")
    if bal["investments"]:
        print(f"  Investments (not counted as cash){_m(bal['investments']):>12}")

    print("\nHOW MUCH CAN YOU INVEST?  (a calculation from your data - not financial advice)")
    print(f"  Monthly earned income            {_m(plan.monthly_income):>12}")
    print(f"  Monthly need (spending + out)    {_m(-plan.monthly_need):>12}"
          + ("   (tuition & trips excluded - see planned expenses)" if edu else ""))
    if plan.counted_transfers_in:
        print(f"  Transfers in (counted)           {_m(plan.monthly_transfers_in):>12}")
    print(f"  Monthly surplus / shortfall      {_m(plan.monthly_surplus):>12}")
    print()
    if plan.lump:
        print(f"  Net cash + incoming lump sum     {_m(plan.pool):>12}")
    else:
        print(f"  Money available (net cash)       {_m(plan.pool):>12}")
    print(f"  - Emergency fund ({plan.ef_months} months of need) {_m(-plan.ef_target):>9}")
    if plan.deficit_buffer:
        print(f"  - Shortfall buffer ({plan.deficit_buffer_months} months)     {_m(-plan.deficit_buffer):>9}")
    if plan.reserve:
        print(f"  - Planned expenses (below)       {_m(-plan.reserve):>12}")
    print(f"  = Can invest now                 {_m(plan.investable_now):>12}")
    print(f"    Can invest each month          {_m(plan.investable_monthly):>12}")
    if plan.runway_months is not None:
        print(f"    Runway at current shortfall    {plan.runway_months:>9} months")
    for n in plan.notes:
        print(f"  Note: {n}")

    print("\nPLANNED EXPENSES" + ("" if future else "  - none (add with `fin planned add`)"))
    for p in future:
        print(f"  {p.due}  {p.name[:34]:34} {_m(p.amount):>10}")

    surplus = plan.monthly_surplus
    outlook = build_outlook(plan.net_cash + plan.lump, surplus, planned, plan.ef_target, today,
                            months=outlook_months)
    print(f"\nOUTLOOK  (assumes ~{_m(surplus)}/month surplus continues; emergency fund "
          f"{_m(plan.ef_target)})")
    print(f"  {'Month':8} {'Surplus':>10} {'Planned':>10} {'End balance':>12} {'Free*':>11}  Paying")
    for r in outlook.months:
        print(f"  {r.key:8} {_m(r.surplus):>10} {_m(-r.planned_out):>10} {_m(r.end_balance):>12} "
              f"{_m(r.free):>11}  {', '.join(r.items)[:40]}")
    print("  * Free = balance minus later planned expenses minus the emergency fund")
    if outlook.invest_from:
        r = outlook.invest_from
        print(f"  => Investing can start around {r.key}: about {_m(r.free)} free then, "
              f"plus ~{_m(max(surplus, ZERO))}/month after (until new expenses are planned).")
    else:
        print(f"  => Not within {outlook_months} months at this pace.")
    for w in outlook.warnings:
        print(f"  ! {w}")


def print_transfers(*, months: int = 3, engine: Engine | None = None,
                    today: dt.date | None = None) -> None:
    """List the items that need a human explanation: money in/out from outside your linked
    accounts, loan payments, and payments to cards that aren't linked."""
    from fin.analytics.cashflow import classify, spend_category

    today = today or dt.date.today()
    txns, _bal, _names = load(engine)
    bank = [t for t in txns if t.account_type in CASH_TYPES | {"credit"}]
    internal = find_internal_transfers(bank)
    keys = set(complete_months(bank, today, months)) | {f"{today:%Y-%m}"}
    rows = []
    for t in bank:
        if f"{t.date:%Y-%m}" not in keys:
            continue
        kind = classify(t, internal)
        cat = spend_category(t) if kind == "spending" else ""
        if kind in ("external_in", "external_out") or cat in (UNLINKED_CARD, "LOAN_PAYMENTS"):
            label = {"external_in": "MONEY IN", "external_out": "MONEY OUT"}.get(kind) or (
                "UNLINKED CARD" if cat == UNLINKED_CARD else "LOAN PAYMENT")
            rows.append((t.date, label, t.amount, t.account_type, t.merchant or ""))
    if not rows:
        print("Nothing to explain.")
        return
    print(f"  {'Date':10} {'Kind':13} {'Amount':>11}  {'Account':9} Description")
    for d, label, amt, acct, merch in sorted(rows):
        print(f"  {d}  {label:13} {amt:>11,.2f}  {acct:9} {merch[:50]}")
    print("\nTell Claude what the unclear ones are (e.g. 'the 2,000 out on Oct 5 is tuition',"
          " 'money in from Fidelity is my withdrawal') - each becomes a rule.")
