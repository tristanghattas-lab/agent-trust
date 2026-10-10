import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

KEY = "test-metrics-key"
AUTH = {"Authorization": f"Bearer {KEY}"}
HUMAN_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"
AGENT_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("METRICS_API_KEY", KEY)


def ingest(c, key, shop, ua=HUMAN_UA, **extra):
    r = c.post("/ingest", json={"session_key": key, "shop": shop, "user_agent": ua,
                                "landing_path": "/products/x", "event_count": 1, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def order_webhook(c, order_id, shop, session_key, total="120.00"):
    body = json.dumps({
        "id": order_id, "total_price": total, "currency": "AUD",
        "shipping_address": {"province_code": "NSW", "address2": None},
        "note_attributes": [{"name": "agent_trust_session", "value": session_key}],
    }).encode()
    sig = base64.b64encode(hmac.new(b"test_secret", body, hashlib.sha256).digest()).decode()
    r = c.post("/webhooks/shopify/orders", content=body, headers={
        "x-shopify-hmac-sha256": sig, "x-shopify-shop-domain": shop,
        "content-type": "application/json"})
    assert r.status_code == 200, r.text
    return r.json()


# --- auth -------------------------------------------------------------------
def test_fails_closed_without_configured_key(client, monkeypatch):
    monkeypatch.delenv("METRICS_API_KEY")
    assert client.get("/metrics/overview?shop=demo", headers=AUTH).status_code == 503


def test_rejects_wrong_or_missing_key(client):
    assert client.get("/metrics/overview?shop=demo").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/metrics/overview?shop=demo", headers=bad).status_code == 401


def test_invalid_shop_rejected(client):
    assert client.get("/metrics/overview?shop=not a shop!", headers=AUTH).status_code == 400


# --- store isolation ----------------------------------------------------------
def test_live_metrics_are_scoped_to_one_store(client):
    a, b = "store-a.myshopify.com", "store-b.myshopify.com"
    ingest(client, "a1", a)
    ingest(client, "a2", f"https://www.{a}/")  # normalised to the same store
    ingest(client, "a3", a, ua=AGENT_UA, referrer="")
    ingest(client, "b1", b)
    order_webhook(client, 9001, a, "a1")
    order_webhook(client, 9002, b, "b1", total="999.00")

    ov = client.get(f"/metrics/overview?shop={a}&days=7", headers=AUTH).json()
    assert ov["source"] == "live" and ov["synthetic"] is False
    assert ov["kpis"]["sessions"] == 3
    assert ov["kpis"]["agent_sessions"] == 1
    assert [t["name"] for t in ov["top_agents"]] == ["ChatGPT"]

    orders = client.get(f"/metrics/orders?shop={a}&days=7", headers=AUTH).json()
    assert orders["kpis"]["orders"] == 1
    assert orders["kpis"]["revenue"] == 120.0  # store B's $999 order not included

    ob = client.get(f"/metrics/overview?shop={b}&days=7", headers=AUTH).json()
    assert ob["kpis"]["sessions"] == 1


def test_session_detail_only_found_in_its_own_store(client):
    ingest(client, "iso1", "store-c.myshopify.com", ua=AGENT_UA)
    ok = client.get("/metrics/sessions/iso1?shop=store-c.myshopify.com", headers=AUTH)
    assert ok.status_code == 200
    assert ok.json()["agent"] == "ChatGPT"
    assert ok.json()["reasons"][0]["text"].startswith("Declared itself")
    other = client.get("/metrics/sessions/iso1?shop=store-a.myshopify.com", headers=AUTH)
    assert other.status_code == 404


def test_threat_runs_are_scoped(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "admin-key")
    assert client.post("/threat-runs", headers=AUTH, json={}).status_code == 403  # the app key can't log runs
    r = client.post("/threat-runs", headers={"Authorization": "Bearer admin-key"}, json={"agent_surface": "browser-use", "task_name": "unearned_discount",
                                          "task_category": "discount", "result": "fail",
                                          "exploit_found": True, "shop": "store-d.myshopify.com"})
    assert r.status_code == 200
    d = client.get("/metrics/threats?shop=store-d.myshopify.com", headers=AUTH).json()
    assert d["kpis"]["exploits"] == 1
    assert d["findings"][0]["severity"] == "High"
    assert d["findings"][0]["fix"]
    e = client.get("/metrics/threats?shop=store-a.myshopify.com", headers=AUTH).json()
    assert e["kpis"] is None


def test_untagged_ingest_falls_back_to_default_store(client):
    from app.shops import DEFAULT_SHOP
    client.post("/ingest", json={"session_key": "untagged1", "user_agent": HUMAN_UA, "event_count": 1})
    ov = client.get(f"/metrics/overview?shop={DEFAULT_SHOP}&days=7", headers=AUTH).json()
    assert ov["kpis"]["sessions"] >= 1


# --- demo store ---------------------------------------------------------------
def test_demo_overview_is_labelled_and_populated(client):
    d = client.get("/metrics/overview?shop=demo&days=90", headers=AUTH).json()
    assert d["synthetic"] is True and d["source"] == "demo"
    k = d["kpis"]
    assert k["sessions"] > 20000 and 0 < k["agent_share"] < 1
    assert len(d["daily"]) >= 90
    assert {"human", "assistant", "automation", "crawler", "scraper", "ai_referred"} <= d["daily"][0].keys()
    assert [f["segment"] for f in d["funnel"]] == ["human", "ai_referred", "assistant", "automation"]
    assert d["activity"] and d["activity"][0]["ts"] >= d["activity"][-1]["ts"]


def test_demo_referrals_orders_sessions(client):
    ref = client.get("/metrics/referrals?shop=demo&days=90", headers=AUTH).json()
    assert ref["kpis"]["visits"] > 0 and ref["sources"][0] == "ChatGPT"

    orders = client.get("/metrics/orders?shop=demo&days=90", headers=AUTH).json()
    auto = next(s for s in orders["by_segment"] if s["segment"] == "automation")
    assert auto["orders"] >= orders["min_sample"] and auto["dispute_rate"] is not None
    small = [s for s in orders["by_segment"] if s["orders"] < orders["min_sample"]]
    assert all(s["dispute_rate"] is None for s in small)  # no rates from tiny samples

    sess = client.get("/metrics/sessions?shop=demo&days=30&class=automation&limit=5", headers=AUTH).json()
    assert len(sess["sessions"]) == 5 and all(s["class"] == "automation" for s in sess["sessions"])
    key = sess["sessions"][0]["session_key"]
    detail = client.get(f"/metrics/sessions/{key}?shop=demo", headers=AUTH).json()
    assert detail["reasons"] and detail["signals_detail"]["clicks"] is not None

    threats = client.get("/metrics/threats?shop=demo", headers=AUTH).json()
    assert threats["synthetic"] is False  # real icelabs results even in demo
    assert threats["kpis"]["runs"] == 8


def test_cors_allows_each_listed_storefront(monkeypatch):
    """SITE_ORIGIN takes a list; each storefront gets CORS, others don't."""
    import importlib
    import app.main as main_mod
    monkeypatch.setenv("SITE_ORIGIN", "https://icelabs-bdy57pfy.myshopify.com, https://checkskincare.com/")
    m = importlib.reload(main_mod)
    with TestClient(m.app) as c:
        def preflight(origin):
            return c.options("/ingest", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        assert preflight("https://checkskincare.com").headers.get("access-control-allow-origin") == "https://checkskincare.com"
        assert preflight("https://icelabs-bdy57pfy.myshopify.com").status_code == 200
        assert preflight("https://evil.example").status_code == 400
    monkeypatch.delenv("SITE_ORIGIN")
    importlib.reload(main_mod)
