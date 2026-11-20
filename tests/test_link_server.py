import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fakes import FakePlaid
from fin.db.models import Institution
from fin.db.session import session_scope
from fin.link_server import create_app

PORT = 8765
STATE = "test-state-123"
ORIGIN = f"http://127.0.0.1:{PORT}"
GOOD = {"Origin": ORIGIN, "X-Fin-State": STATE}
TOKEN = "public-sandbox-0000aaaa-1111-2222-3333-444455556666"


@pytest.fixture
def ctx(engine):
    fake = FakePlaid()
    done = []
    app = create_app(state=STATE, port=PORT, adapter=fake, engine=engine,
                     on_done=lambda: done.append(True))
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    return client, fake, done


def test_page_requires_state(ctx):
    client, _, _ = ctx
    assert client.get("/").status_code == 403
    assert client.get("/?state=wrong").status_code == 403
    r = client.get(f"/?state={STATE}")
    assert r.status_code == 200 and "Connect an account" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_foreign_host_blocked(engine):
    app = create_app(state=STATE, port=PORT, adapter=FakePlaid(), engine=engine)
    evil = TestClient(app, base_url="http://evil.example.com")
    assert evil.get(f"/?state={STATE}").status_code == 403


def test_post_requires_origin_and_state(ctx):
    client, _, _ = ctx
    assert client.post("/api/link-token").status_code == 403
    assert client.post("/api/link-token", headers={"Origin": "http://evil.com",
                                                   "X-Fin-State": STATE}).status_code == 403
    assert client.post("/api/link-token", headers={"Origin": ORIGIN,
                                                   "X-Fin-State": "nope"}).status_code == 403
    r = client.post("/api/link-token", headers=GOOD)
    assert r.status_code == 200 and r.json()["link_token"] == "link-sandbox-fake"


def test_exchange_links_syncs_and_shuts_down(ctx, engine):
    client, _, done = ctx
    r = client.post("/api/exchange", headers=GOOD, json={
        "public_token": TOKEN, "institution_id": "ins_109508", "institution_name": "First Platypus Bank"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["institution"] == "First Platypus Bank"
    assert body["result"]["status"] == "ok" and body["result"]["accounts"] == 2
    assert done == [True]
    with session_scope(engine) as s:
        assert s.scalars(select(Institution)).one().provider_institution_id == "ins_109508"


def test_duplicate_institution_refused(ctx):
    client, _, _ = ctx
    payload = {"public_token": TOKEN, "institution_id": "ins_109508"}
    assert client.post("/api/exchange", headers=GOOD, json=payload).status_code == 200
    r = client.post("/api/exchange", headers=GOOD, json=payload)
    assert r.status_code == 409 and "already linked" in r.json()["detail"]


def test_bad_or_wrong_env_token_rejected(ctx):
    client, _, _ = ctx
    assert client.post("/api/exchange", headers=GOOD,
                       json={"public_token": "<script>"}).status_code == 422
    prod = "public-production-0000aaaa-1111-2222-3333-444455556666"
    assert client.post("/api/exchange", headers=GOOD,
                       json={"public_token": prod}).status_code == 400


def test_status_lists_linked(ctx):
    client, _, _ = ctx
    client.post("/api/exchange", headers=GOOD, json={"public_token": TOKEN})
    s = client.post("/api/status", headers=GOOD).json()
    assert s["count"] == 1 and s["linked"][0]["name"] == "First Platypus Bank"
