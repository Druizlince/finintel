import datetime as dt
from decimal import Decimal as D

from fin.analytics.cashflow import (
    Month, Txn, average, build_plan, classify, complete_months, detect_recurring,
    find_internal_transfers, monthly, spending_by_category,
)

CHK, SAV, CARD = 1, 2, 3
TYPES = {CHK: "checking", SAV: "savings", CARD: "credit"}
_n = iter(range(1, 10_000))


def t(acct, date, amount, merchant="x", cat="GENERAL_MERCHANDISE", detail=None, pending=False):
    return Txn(next(_n), acct, TYPES[acct], dt.date.fromisoformat(date), D(str(amount)),
               merchant, cat, detail, pending)


def test_card_payment_between_own_accounts_is_not_double_counted():
    pay_out = t(CHK, "2026-10-20", -500, "Chase Payment", "LOAN_PAYMENTS",
                "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    pay_in = t(CARD, "2026-10-22", 500, "Payment Thank You", "TRANSFER_IN")
    groceries = t(CARD, "2026-10-05", -120, "ShopRite", "FOOD_AND_DRINK")
    internal = find_internal_transfers([pay_out, pay_in, groceries])
    assert internal == {pay_out.id, pay_in.id}
    m = monthly([pay_out, pay_in, groceries], internal)["2026-10"]
    assert m.spending == D("120") and m.income == 0 and m.external_in == 0


def test_payment_to_unlinked_card_counts_as_spending():
    apple = t(CHK, "2026-10-28", -300, "Apple Card", "LOAN_PAYMENTS",
              "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    assert classify(apple, set()) == "spending"
    cats = spending_by_category([apple], set(), ["2026-10"])
    assert cats == {"CARD_PAYMENTS_UNLINKED": D("300.00")}


def test_coincidental_equal_amounts_are_not_paired():
    spend = t(CHK, "2026-10-01", -50, "Store", "GENERAL_MERCHANDISE")
    refund = t(CARD, "2026-10-02", 50, "Other store", "GENERAL_MERCHANDISE")
    assert find_internal_transfers([spend, refund]) == set()


def test_income_refunds_and_external_transfers():
    pay = t(CHK, "2026-10-15", 500, "ACME Payroll", "INCOME")
    refund = t(CARD, "2026-10-10", 20, "Amazon", "GENERAL_MERCHANDISE")
    buy = t(CARD, "2026-10-09", -100, "Amazon", "GENERAL_MERCHANDISE")
    family = t(CHK, "2026-10-03", 1000, "Zelle from M", "TRANSFER_IN")
    zelle_out = t(CHK, "2026-10-04", -60, "Zelle to friend", "TRANSFER_OUT")
    pending = t(CARD, "2026-10-30", -999, pending=True)
    m = monthly([pay, refund, buy, family, zelle_out, pending], set())["2026-10"]
    assert (m.income, m.spending, m.external_in, m.external_out) == (D(500), D(80), D(1000), D(60))
    assert m.net == D("1360")


def test_complete_months_waits_for_every_account():
    txns = [t(CHK, "2026-06-03", -1), t(CARD, "2026-08-14", -1), t(CHK, "2026-11-10", -1)]
    assert complete_months(txns, dt.date(2026, 11, 17), 6) == ["2026-09", "2026-10"]
    assert complete_months(txns, dt.date(2026, 11, 17), 1) == ["2026-10"]


def test_average_fills_missing_months_with_zero():
    months = {"2026-09": Month("2026-09", income=D(1000), spending=D(800))}
    avg = average(months, ["2026-09", "2026-10"])
    assert avg.income == D("500.00") and avg.spending == D("400.00")


def test_detects_monthly_subscription_and_biweekly_pay():
    txns = [t(CARD, f"2026-{m:02d}-03", -11.99, f"SPOTIFY USA {m}", "ENTERTAINMENT")
            for m in (7, 8, 9, 10, 11)]
    txns += [t(CHK, d, 500, "ACME PAYROLL", "INCOME")
             for d in ("2026-09-15", "2026-09-30", "2026-10-15", "2026-10-31", "2026-11-15")]
    txns += [t(CARD, d, -a, "Random Shop") for d, a in (("2026-08-01", 5), ("2026-09-20", 80),
                                                         ("2026-10-02", 300))]
    rec = {r.merchant: r for r in detect_recurring(txns, set(), dt.date(2026, 11, 17))}
    assert rec["spotify usa"].cadence == "monthly" and rec["spotify usa"].per_month == D("11.99")
    assert rec["spotify usa"].next_expected == dt.date(2026, 12, 3)
    assert rec["acme payroll"].direction == "in" and rec["acme payroll"].cadence == "twice a month"
    assert rec["acme payroll"].per_month == D("1000.00")
    assert "random shop" not in rec   # irregular amounts/dates


def test_plan_with_shortfall_and_lump_sum():
    avg = Month("avg", income=D(1000), spending=D(2500), external_in=D(1500))
    p = build_plan(avg, cash=D(3000), card_owed=D(2000), ef_months=6, lump=D(30000),
                   deficit_buffer_months=12)
    assert p.monthly_surplus == D("-1500.00")          # family money not counted by default
    assert p.ef_target == D("15000.00") and p.deficit_buffer == D("18000.00")
    assert p.pool == D("31000.00")
    assert p.investable_now == D("0.00")               # safety targets come first
    assert p.runway_months == D("20.67")
    assert any("short" in n for n in p.notes)


def test_plan_with_surplus():
    avg = Month("avg", income=D(4000), spending=D(2500), external_out=D(100))
    p = build_plan(avg, cash=D(20000), card_owed=D(500), ef_months=6, lump=D(30000),
                   reserve=D(2000))
    assert p.monthly_surplus == D("1400.00") and p.ef_target == D("15600.00")
    assert p.investable_now == D("31900.00") and p.investable_monthly == D("1400.00")
    assert p.runway_months is None


def test_true_biweekly_pay():
    txns = [t(CHK, d, 800, "EMPLOYER PAY", "INCOME")
            for d in ("2026-09-04", "2026-09-18", "2026-10-02", "2026-10-16")]
    r = detect_recurring(txns, set(), dt.date(2026, 10, 20))[0]
    assert r.cadence == "every 2 weeks" and r.per_month == D("1733.33")


def test_tuition_excluded_from_emergency_fund_but_reserved():
    avg = Month("avg", income=D(6000), spending=D(4000))   # includes 2000/mo tuition
    p = build_plan(avg, cash=D(5000), card_owed=D(0), ef_months=6, reserve=D(6000),
                   ef_exclude=D(2000))
    assert p.ef_target == D("12000.00")                  # 6 x (4000 - 2000)
    assert p.monthly_surplus == D("2000.00")


def test_surplus_goes_to_safety_first_when_underfunded():
    avg = Month("avg", income=D(6000), spending=D(2000))
    p = build_plan(avg, cash=D(1000), card_owed=D(0), ef_months=6, reserve=D(5000))
    assert p.investable_now == D("0.00") and p.investable_monthly == D("0.00")
    assert p.months_to_fund == D("4.00")                 # (12000 + 5000 - 1000) / 4000
