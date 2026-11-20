import datetime as dt
from decimal import Decimal as D

from fin.analytics.forecast import Planned, build_outlook

TODAY = dt.date(2026, 11, 30)  # last day of the month -> no surplus counted for November
TUITION = [Planned("Spring tuition", D("1268.92"), dt.date(2027, mm, 1), "EDUCATION")
           for mm in (1, 2, 3, 4)]
BODYSHOP = Planned("Body shop", D("1500"), dt.date(2026, 12, 10))


def test_outlook_balances_and_when_investing_can_start():
    o = build_outlook(D("1060.33"), D("4000"), TUITION + [BODYSHOP], D("9400"), TODAY, months=6)
    by = {r.key: r for r in o.months}
    assert by["2026-11"].end_balance == D("1060.33")
    assert by["2026-12"].planned_out == D("1500.00")
    assert by["2026-12"].end_balance == D("3560.33")
    assert by["2027-01"].end_balance == D("6291.41")
    # Dec: 3560.33 - 4*1268.92 (5075.68) - 9400 < 0 ; investing possible once free > 0
    assert o.invest_from is not None and o.invest_from.key == "2027-03"
    assert by["2027-03"].committed_later == D("1268.92")


def test_warns_when_early_payment_exceeds_cash():
    o = build_outlook(D("500"), D("3000"), [Planned("Tuition", D("1268.92"), dt.date(2026, 12, 1))],
                      D("0"), TODAY, months=2)
    assert any("Tuition" in w and "768.92" in w for w in o.warnings)


def test_past_items_ignored_and_negative_balance_warned():
    old = Planned("Old bill", D("999"), dt.date(2026, 10, 1))
    o = build_outlook(D("100"), D("-500"), [old], D("0"), TODAY, months=2)
    assert o.months[0].planned_out == 0
    assert any("negative" in w for w in o.warnings)
