import datetime as dt
from decimal import Decimal

from fin.providers.plaid_mapping import map_account, map_account_type, map_transaction


def test_account_types():
    assert map_account_type("depository", "checking") == "checking"
    assert map_account_type("depository", "savings") == "savings"
    assert map_account_type("credit", "credit card") == "credit"
    assert map_account_type("investment", "roth") == "roth_ira"
    assert map_account_type("investment", "401k") == "401k"
    assert map_account_type("investment", "something new") == "brokerage"
    assert map_account_type("weird", None) == "other"


def test_map_account_balances_and_mask():
    m = map_account({
        "account_id": "a1", "name": "Plaid Checking", "official_name": None,
        "type": "depository", "subtype": "checking", "mask": "0000",
        "balances": {"current": 110.0, "available": 100.0, "limit": None, "iso_currency_code": "USD"},
    })
    assert m["type"] == "checking" and m["mask"] == "0000"
    assert m["balances"]["current"] == Decimal("110.0")
    assert m["balances"]["credit_limit"] is None


def test_transaction_sign_is_flipped():
    spend = map_transaction({
        "transaction_id": "t1", "account_id": "a1", "amount": 12.99,
        "date": dt.date(2026, 11, 1), "name": "SPOTIFY", "merchant_name": "Spotify",
        "personal_finance_category": {"primary": "ENTERTAINMENT", "detailed": "ENTERTAINMENT_MUSIC"},
    })
    pay = map_transaction({
        "transaction_id": "t2", "account_id": "a1", "amount": -500, "date": "2026-11-15",
        "name": "ACME PAYROLL",
    })
    assert spend["amount"] == Decimal("-12.99")   # money out -> negative
    assert pay["amount"] == Decimal("500")        # paycheck -> positive
    assert pay["date"] == dt.date(2026, 11, 15)
    assert spend["merchant"] == "Spotify" and pay["merchant"] == "ACME PAYROLL"
    assert spend["category"] == "ENTERTAINMENT"
