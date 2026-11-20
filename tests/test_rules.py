import datetime as dt
from decimal import Decimal as D

from fin.analytics.cashflow import Txn, classify, monthly
from fin.analytics.rules import Rule, apply_rules

RENT = [Rule("jane landlord", "RENT_AND_UTILITIES", "Rent")]


def _zelle(merchant, desc, amount="-1000"):
    return Txn(1, 1, "checking", dt.date(2026, 10, 1), D(amount), merchant, "TRANSFER_OUT",
               "TRANSFER_OUT_ACCOUNT_TRANSFER")


def test_rule_turns_zelle_transfer_into_rent():
    t = apply_rules(_zelle("Zelle", None), "ZELLE PAYMENT TO LANDLORD JANE 123", RENT)
    assert t.category == "RENT_AND_UTILITIES" and t.merchant == "Rent"
    assert classify(t, set()) == "spending"
    assert monthly([t], set())["2026-10"].spending == D("1000")


def test_word_order_and_case_do_not_matter_but_all_words_must_match():
    assert apply_rules(_zelle("JANE LANDLORD", None), None, RENT).merchant == "Rent"
    other = apply_rules(_zelle("Zelle", None), "ZELLE TO JANE SMITH", RENT)
    assert other.category == "TRANSFER_OUT"   # unchanged


def test_amount_ranges_split_rent_and_food():
    rules = [
        Rule("landlord", "RENT_AND_UTILITIES", "Rent", D("1000"), D("1000")),
        Rule("landlord", "FOOD_AND_DRINK", "Food (shared)", D("100"), D("999.99")),
    ]
    desc = "ZELLE PAYMENT TO LANDLORD JANE"
    assert apply_rules(_zelle("Zelle", None, "-1000"), desc, rules).merchant == "Rent"
    assert apply_rules(_zelle("Zelle", None, "-250"), desc, rules).merchant == "Food (shared)"
    small = apply_rules(_zelle("Zelle", None, "-40"), desc, rules)
    assert small.category == "TRANSFER_OUT"            # under $100: untouched
    assert apply_rules(_zelle("Zelle", None, "-1200"), desc, rules).category == "TRANSFER_OUT"


def test_direction_out_ignores_money_received():
    rules = [Rule("landlord", "FOOD_AND_DRINK", "Food (shared)", D("100"), D("999.99"), "out")]
    received = Txn(2, 1, "checking", dt.date(2026, 11, 12), D("750.01"), "Zelle", "TRANSFER_IN",
                   None)
    sent = Txn(3, 1, "checking", dt.date(2026, 11, 12), D("-250"), "Zelle", "TRANSFER_OUT", None)
    desc = "ZELLE JANE LANDLORD"
    assert apply_rules(received, desc, rules).category == "TRANSFER_IN"   # untouched
    assert apply_rules(sent, desc, rules).merchant == "Food (shared)"
