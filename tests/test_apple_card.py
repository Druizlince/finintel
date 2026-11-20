import datetime as dt
from decimal import Decimal as D

from fin.analytics.cashflow import Txn, classify, find_internal_transfers
from fin.importers.apple_card import parse_statement

SAMPLE = """
                                                                                                       Previous Total Balance                          $191.13
                                                                                                       as of Sep 30, 2026
                                                                                                       Total Balance                                   $133.12
                                                                                                       as of Oct 31, 2026
Payments
Date                   Description                                                                     Amount
10/01/2026             ACH Deposit Internet transfer from account ending in 5445                    -$191.13
Total payments for this period                                                                      -$241.13
Transactions
Date                   Description                                                   Daily Cash        Amount
10/01/2026             APPLE.COM/BILL ONE APPLE PARK WAY CUPERTINO 95014 CA USA        3%   $0.32       $10.65
10/02/2026             APPLE.COM/BILL ONE APPLE PARK WAY CUPERTINO 95014 CA USA (RETURN)                -$1.16
                       Daily Cash Adjustment                                          -3%                $0.03
10/05/2026             PMUSA PARKMOBILE 1100 CIRCLE 75 ATLANTA 30339 GA USA            2%   $0.08        $4.25
10/05/2026             PMUSA PARKMOBILE 1100 CIRCLE 75 ATLANTA 30339 GA USA            2%   $0.08        $4.25
10/13/2026             FUEL 4 SPRINGFIELD 100 MAIN ST SPRINGFIELD 07000 NJ USA          2%   $0.20       $10.00
Total charges, credits and returns                                                                  $27.99
Daily Cash
Daily Cash from Apple Card                                                                             $3.86
"""


def test_parse_signs_categories_and_balance():
    st = parse_statement(SAMPLE)
    assert st.balance == D("133.12") and st.period_end == dt.date(2026, 10, 31)
    kinds = [(ln.kind, ln.amount, ln.category) for ln in st.lines]
    assert kinds[0] == ("payment", D("191.13"), "TRANSFER_IN")
    assert ("purchase", D("-10.65"), "SUBSCRIPTIONS") in kinds
    assert ("return", D("1.16"), "SUBSCRIPTIONS") in kinds
    assert [ln.merchant for ln in st.lines].count("ParkMobile") == 2
    assert len(st.lines) == 6                     # daily-cash adjustment and totals ignored


def test_same_charge_twice_gets_two_stable_ids():
    a = parse_statement(SAMPLE)
    b = parse_statement(SAMPLE)
    ids = [ln.txn_id for ln in a.lines]
    assert len(set(ids)) == len(ids)              # two identical ParkMobile charges stay two
    assert ids == [ln.txn_id for ln in b.lines]   # re-import -> same ids -> no duplicates


def test_td_payment_pairs_with_apple_card_payment():
    st = parse_statement(SAMPLE)
    pay = st.lines[0]
    apple = Txn(1, 9, "credit", pay.date, pay.amount, pay.merchant, pay.category)
    td = Txn(2, 1, "checking", dt.date(2026, 10, 2), D("-191.13"), "Apple Card", "LOAN_PAYMENTS",
             "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    assert find_internal_transfers([apple, td]) == {1, 2}
    assert classify(td, {1, 2}) == "internal"
