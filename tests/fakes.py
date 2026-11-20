"""Test doubles shared by several test files."""


def txn(tid, amount, date="2026-11-01", account="acc-chk"):
    return {"transaction_id": tid, "account_id": account, "amount": amount, "date": date,
            "name": f"TXN {tid}"}


class FakePlaid:
    """Stands in for PlaidAdapter: same methods, canned answers, no network."""

    def __init__(self):
        self.pages = {
            "": {"added": [txn("t1", 10), txn("t2", 20)], "modified": [], "removed": [],
                 "next_cursor": "c1", "has_more": True},
            "c1": {"added": [txn("t3", -500)], "modified": [], "removed": [],
                   "next_cursor": "c2", "has_more": False},
        }
        self.cursors_seen = []

    def exchange_public_token(self, public_token):
        return "access-sandbox-fake-token", "item-123"

    env = "sandbox"
    link_tokens_created = 0

    def create_link_token(self):
        self.link_tokens_created += 1
        return "link-sandbox-fake"

    def institution_info(self, access_token):
        return "ins_109508", "First Platypus Bank"

    def get_accounts(self, access_token):
        return [
            {"account_id": "acc-chk", "name": "Plaid Checking", "type": "depository",
             "subtype": "checking", "mask": "0000",
             "balances": {"current": 110, "available": 100, "limit": None}},
            {"account_id": "acc-sav", "name": "Plaid Saving", "type": "depository",
             "subtype": "savings", "mask": "1111",
             "balances": {"current": 210, "available": 200, "limit": None}},
        ]

    def transactions_sync(self, access_token, cursor):
        assert access_token == "access-sandbox-fake-token"
        self.cursors_seen.append(cursor)
        return self.pages[cursor]
