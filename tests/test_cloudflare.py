"""Cloudflare analytics connector, against a simulated Cloudflare API."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.cloudflare as cf
from app.db import SessionLocal
from app.main import app
from app.models import EdgeAggregate, Integration

SHOP = "cf-store.myshopify.com"
ZONE = "0123456789abcdef0123456789abcdef"
TOKEN = "cf-test-token-abcdefghijklmnopqrstuvwxyz"
KEY = "test-metrics-key"
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("METRICS_API_KEY", KEY)
    monkeypatch.setenv("EDGE_SIGNING_SECRET", "edge-secret-for-tests")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def fake_rows(start, end):
    """What Cloudflare would return for one window: an hour's mix of agents."""
    hour = start.replace(minute=0, second=0, microsecond=0)
    h = hour.strftime("%Y-%m-%dT%H:00:00Z")
    return [
        {"count": 40, "dimensions": {"datetimeHour": h, "userAgent": "Mozilla/5.0; compatible; GPTBot/1.2", "clientRequestPath": "/products/x"}},
        {"count": 6, "dimensions": {"datetimeHour": h, "userAgent": "Mozilla/5.0; compatible; GPTBot/1.2", "clientRequestPath": "/robots.txt"}},
        {"count": 3, "dimensions": {"datetimeHour": h, "userAgent": "Mozilla/5.0 ChatGPT-User/1.0", "clientRequestPath": "/agents.md"}},
        {"count": 25, "dimensions": {"datetimeHour": h, "userAgent": "python-requests/2.32", "clientRequestPath": "/products.json"}},
        {"count": 2, "dimensions": {"datetimeHour": h, "userAgent": "Mozilla/5.0 Chrome/129.0", "clientRequestPath": "/llms.txt"}},
    ]


def test_bad_token_is_rejected_and_nothing_saved(client, monkeypatch):
    def reject(*a, **k):
        raise cf.CloudflareError("token rejected: it needs Zone > Analytics > Read for this zone")
    monkeypatch.setattr(cf, "run_query", reject)
    r = client.post("/integrations/cloudflare", headers=AUTH,
                    json={"shop": SHOP, "zone_id": ZONE, "api_token": TOKEN})
    assert r.status_code == 400 and "Analytics > Read" in r.json()["detail"]
    assert client.get(f"/integrations/cloudflare?shop={SHOP}", headers=AUTH).json() == {"connected": False}


def test_needs_api_key(client):
    assert client.get(f"/integrations/cloudflare?shop={SHOP}").status_code == 401


def test_connect_backfills_and_encrypts(client, monkeypatch):
    calls = []
    def ok(token, zone, start, end):
        calls.append((token, zone))
        return fake_rows(start, end)
    monkeypatch.setattr(cf, "run_query", ok)
    r = client.post("/integrations/cloudflare", headers=AUTH,
                    json={"shop": SHOP, "zone_id": ZONE, "api_token": TOKEN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] and body["last_error"] is None and TOKEN not in str(body)
    assert all(c == (TOKEN, ZONE) for c in calls)

    db = SessionLocal()
    integ = db.query(Integration).filter_by(shop_domain=SHOP).one()
    assert TOKEN not in (integ.secret_encrypted or "")  # stored encrypted
    assert db.query(EdgeAggregate).filter_by(shop_domain=SHOP).count() > 0
    db.close()


def test_metrics_use_request_counts(client):
    ov = client.get(f"/metrics/overview?shop={SHOP}&days=7", headers=AUTH).json()
    cov = ov["coverage"]
    assert cov["cloudflare"] is True and cov["edge"] is False
    assert cov["edge_unit"] == "requests" and cov["hidden_agent_classes"] == []
    names = {a["name"]: a for a in ov["edge_agents"]}
    assert names["GPTBot"]["class"] == "crawler"
    assert names["ChatGPT"]["class"] == "assistant"
    assert names["python-requests"]["class"] == "scraper"
    assert "Unidentified" not in names  # plain browsers aren't agents
    files = {f["file"]: f for f in ov["agent_files"]}
    assert files["robots.txt"]["unit"] == "requests"
    assert files["agents.md"]["top_agents"][0]["agent"] == "ChatGPT"
    assert files["llms.txt"]["top_agents"][0]["agent"] == "Unidentified"  # counted as a fetch, not an agent
    assert sum(d["crawler"] for d in ov["daily"]) > 0

    s = client.get(f"/metrics/sessions?shop={SHOP}&days=7", headers=AUTH).json()
    assert s["request_counts_by_class"]["crawler"] > 0


def test_resync_is_idempotent(client, monkeypatch):
    monkeypatch.setattr(cf, "run_query", lambda t, z, s, e: fake_rows(s, e))
    db = SessionLocal()
    before = db.query(EdgeAggregate).filter_by(shop_domain=SHOP).count()
    integ = db.query(Integration).filter_by(shop_domain=SHOP).one()
    integ.synced_through = None  # force a full backfill again
    db.commit()
    cf.sync(db, integ)
    after = db.query(EdgeAggregate).filter_by(shop_domain=SHOP).count()
    db.close()
    assert after == before  # windows are replaced, not duplicated


def test_sync_error_is_recorded_not_raised(client, monkeypatch):
    def boom(*a, **k):
        raise cf.CloudflareError("zone not found, or the token can't read it")
    monkeypatch.setattr(cf, "run_query", boom)
    r = client.post(f"/integrations/cloudflare/sync?shop={SHOP}", headers=AUTH).json()
    assert "zone not found" in r["error"]
    st = client.get(f"/integrations/cloudflare?shop={SHOP}", headers=AUTH).json()
    assert st["last_error"]
    cov = client.get(f"/metrics/overview?shop={SHOP}&days=7", headers=AUTH).json()["coverage"]
    assert cov["cloudflare"] is False  # a broken connection isn't shown as connected


def test_disconnect_deletes_data(client):
    r = client.delete(f"/integrations/cloudflare?shop={SHOP}", headers=AUTH)
    assert r.json() == {"connected": False}
    db = SessionLocal()
    assert db.query(EdgeAggregate).filter_by(shop_domain=SHOP).count() == 0
    db.close()


def test_query_filter_shape():
    now = datetime.now(timezone.utc)
    f = cf.build_filter(now - timedelta(hours=1), now)
    either = f["AND"][2]["OR"]
    assert {"userAgent_like": "%GPTBot%"} in either
    assert any("clientRequestPath_in" in x for x in either)
