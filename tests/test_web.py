import datetime as dt
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from fin.db.models import Account, BalanceSnapshot, Institution, Transaction
from fin.db.session import session_scope
from fin.web.app import create_app

PORT, TOKEN = 8800, "tok-123"
GOOD = {"Origin": f"http://127.0.0.1:{PORT}", "X-Fin-Token": TOKEN}


@pytest.fixture
def client(engine):
    with session_scope(engine) as s:
        inst = Institution(name="TD Bank", provider="plaid")
        chk = Account(institution=inst, provider_account_id="c", name="Checking", type="checking", mask="5445")
        s.add_all([inst, chk])
        s.flush()
        s.add(BalanceSnapshot(account_id=chk.id, as_of=dt.datetime(2026, 11, 17, tzinfo=dt.timezone.utc),
                              current=D("1500"), available=D("1500"), source="plaid"))
        for i, (d, amt, cat) in enumerate([("2026-10-15", 600, "INCOME"), ("2026-10-20", -200, "FOOD_AND_DRINK"),
                                           ("2026-11-15", 600, "INCOME")]):
            s.add(Transaction(account_id=chk.id, provider_txn_id=f"t{i}", date=dt.date.fromisoformat(d),
                              amount=D(amt), merchant="X", category=cat, source="plaid"))
    return TestClient(create_app(port=PORT, token=TOKEN, engine=engine), base_url=f"http://127.0.0.1:{PORT}")


def test_page_has_token_and_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and TOKEN in r.text
    assert r.headers["x-frame-options"] == "DENY"


def test_foreign_host_blocked(engine):
    evil = TestClient(create_app(port=PORT, token=TOKEN, engine=engine), base_url="http://evil.com")
    assert evil.get("/api/overview").status_code == 403


def test_overview_and_cashflow(client):
    o = client.get("/api/overview").json()
    assert o["cash"] == 1500.0 and o["accounts"][0]["mask"] == "5445"
    c = client.get("/api/cashflow").json()
    assert {"months", "categories", "plan", "outlook", "recurring"} <= set(c)


def test_changes_require_token_and_origin(client):
    body = {"name": "Body shop", "amount": "1500", "due_date": "2027-01-10"}
    assert client.post("/api/planned", json=body).status_code == 403
    assert client.post("/api/planned", json=body, headers={"Origin": "http://evil.com", "X-Fin-Token": TOKEN}).status_code == 403
    r = client.post("/api/planned", json={**body, "repeat": 2}, headers=GOOD)
    assert r.status_code == 200 and len(r.json()["ids"]) == 2
    items = client.get("/api/planned").json()
    assert [p["name"] for p in items] == ["Body shop (1/2)", "Body shop (2/2)"]
    assert client.delete(f"/api/planned/{items[0]['id']}", headers=GOOD).json() == {"ok": True}


def test_rules_crud_and_validation(client):
    bad = client.post("/api/rules", headers=GOOD, json={"pattern": "x", "category": "DROP TABLE;"})
    assert bad.status_code == 422
    r = client.post("/api/rules", headers=GOOD, json={"pattern": "landlord", "category": "rent_and_utilities",
                                                       "label": "Rent", "min_amount": "1000", "max_amount": "1000",
                                                       "direction": "out"})
    rid = r.json()["id"]
    rules = client.get("/api/rules").json()
    assert rules[0]["category"] == "RENT_AND_UTILITIES" and rules[0]["direction"] == "out"
    assert client.delete(f"/api/rules/{rid}", headers=GOOD).status_code == 200


def test_apple_import_requires_token(client):
    assert client.post("/api/import/apple-card").status_code == 403


def test_tailscale_host_allowed_only_when_configured(engine):
    app = create_app(port=PORT, token=TOKEN, engine=engine, extra_hosts=["mylaptop.tail1234.ts.net"])
    ts = TestClient(app, base_url="https://mylaptop.tail1234.ts.net")
    assert ts.get("/api/overview").status_code == 200
    ok = {"Origin": "https://mylaptop.tail1234.ts.net", "X-Fin-Token": TOKEN}
    assert ts.post("/api/planned", headers=ok, json={"name": "x", "amount": "1",
                                                     "due_date": "2027-01-01"}).status_code == 200
    other = TestClient(app, base_url="https://someone-else.ts.net")
    assert other.get("/api/overview").status_code == 403


def test_manifest_and_icon(client):
    assert client.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert client.get("/icon.svg").headers["content-type"].startswith("image/svg")


def test_apple_card_balance_reduced_by_later_td_payment(engine):
    from fin.web import summary

    with session_scope(engine) as s:
        td = Institution(name="TD Bank", provider="plaid")
        chk = Account(institution=td, provider_account_id="c", name="Checking", type="checking")
        ap = Institution(name="Apple Card", provider="csv", provider_institution_id="apple-card")
        card = Account(institution=ap, provider_account_id="apple-card", name="Apple Card", type="credit")
        s.add_all([td, chk, ap, card])
        s.flush()
        s.add(BalanceSnapshot(account_id=card.id, as_of=dt.datetime(2026, 10, 31, 23, 59, tzinfo=dt.timezone.utc),
                              current=D("133.12"), source="apple_pdf"))
        s.add(Transaction(account_id=chk.id, provider_txn_id="p", date=dt.date(2026, 11, 12),
                          amount=D("-133.12"), merchant="Apple Card", source="plaid"))
    o = summary.overview(engine)
    apple = next(a for a in o["accounts"] if a["institution"] == "Apple Card")
    assert apple["current"] == 0.0 and apple["estimated"] is True
