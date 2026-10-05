"""Order-level AI attribution (works with no tracker) and data-source coverage."""
import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.analytics import ai_channel, order_referral_source
from app.main import app

KEY = "test-metrics-key"
AUTH = {"Authorization": f"Bearer {KEY}"}
SHOP = "attrib-store.myshopify.com"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("METRICS_API_KEY", KEY)


def webhook(c, shop, order_id, total, **fields):
    body = json.dumps({"id": order_id, "total_price": total, "currency": "AUD",
                       "shipping_address": {"province_code": "VIC"}, "note_attributes": [],
                       **fields}).encode()
    sig = base64.b64encode(hmac.new(b"test_secret", body, hashlib.sha256).digest()).decode()
    r = c.post("/webhooks/shopify/orders", content=body, headers={
        "x-shopify-hmac-sha256": sig, "x-shopify-shop-domain": shop, "content-type": "application/json"})
    assert r.status_code == 200, r.text


def test_channel_and_referral_parsing():
    assert ai_channel("chatgpt") == "ChatGPT"
    assert ai_channel("Microsoft Copilot") == "Copilot"
    assert ai_channel("web") is None
    assert ai_channel("google") is None  # Google Shopping must not count as an AI channel
    assert order_referral_source("/products/x?utm_source=chatgpt.com", None) == "ChatGPT"
    assert order_referral_source("/", "https://www.perplexity.ai/") == "Perplexity"
    assert order_referral_source("/", "https://www.google.com/") is None


def test_orders_attributed_without_any_tracker(client):
    # No /ingest calls at all for this store: the order feed alone.
    webhook(client, SHOP, 7001, "300.00", source_name="chatgpt")                       # inside ChatGPT
    webhook(client, SHOP, 7002, "150.00", source_name="web",
            landing_site="/products/grange?utm_source=chatgpt.com")                     # referred by ChatGPT
    webhook(client, SHOP, 7003, "80.00", source_name="web", referring_site="https://www.google.com/")

    o = client.get(f"/metrics/orders?shop={SHOP}&days=7", headers=AUTH).json()
    seg = {s["segment"]: s for s in o["by_segment"]}
    assert seg["ai_channel"]["orders"] == 1 and seg["ai_channel"]["revenue"] == 300.0
    assert seg["ai_referred"]["orders"] == 1 and seg["ai_referred"]["revenue"] == 150.0
    assert o["kpis"]["agent_orders"] == 1  # the order placed inside ChatGPT
    names = {s["source_name"]: s for s in o["by_source_name"]}
    assert names["chatgpt"]["ai_channel"] == "ChatGPT" and names["web"]["orders"] == 2


def test_coverage_reflects_connected_sources(client):
    # Orders only, so far.
    o = client.get(f"/metrics/orders?shop={SHOP}&days=7", headers=AUTH).json()
    cov = o["coverage"]
    assert cov["orders"] is True and cov["tracker"] is False and cov["edge"] is False
    assert cov["hidden_agent_classes"] == ["crawler", "scraper"]

    client.post("/ingest", json={"session_key": "cov1", "shop": SHOP, "event_count": 1,
                                 "user_agent": "Mozilla/5.0 Chrome/129.0"})
    cov = client.get(f"/metrics/overview?shop={SHOP}&days=7", headers=AUTH).json()["coverage"]
    assert cov["tracker"] is True and cov["edge"] is False
    assert {s["key"]: s["connected"] for s in cov["sources"]} == {
        "tracker": True, "orders": True, "edge": False, "cloudflare": False, "pixel": False}


def test_demo_coverage_is_marked_simulated(client):
    d = client.get("/metrics/overview?shop=demo&days=30", headers=AUTH).json()
    assert d["coverage"]["simulated"] is True
    assert d["coverage"]["visible_agent_classes"] == ["assistant", "automation", "crawler", "scraper"]
    assert d["kpis"]["ai_channel_orders"] > 0
    kinds = {a["kind"] for a in d["activity"]}
    assert "ai_channel_order" in kinds



def test_webhook_accepts_any_listed_store_secret(client, monkeypatch):
    def post(order_id):
        body = json.dumps({"id": order_id, "total_price": 10.0, "currency": "AUD"}).encode()
        sig = base64.b64encode(hmac.new(b"test_secret", body, hashlib.sha256).digest()).decode()
        return client.post("/webhooks/shopify/orders", content=body, headers={
            "x-shopify-hmac-sha256": sig, "x-shopify-shop-domain": "second-store.myshopify.com",
            "content-type": "application/json"}).status_code
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "other_store_secret, test_secret")
    assert post(990001) == 200
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "other_store_secret")
    assert post(990002) == 401


def test_test_mode_orders_are_counted_but_not_revenue(client):
    shop = "test-flag-store.myshopify.com"
    webhook(client, shop, 880001, 100.0, test=True)
    webhook(client, shop, 880002, 50.0)
    k = client.get(f"/metrics/orders?shop={shop}&days=7",
                   headers={"Authorization": "Bearer test-metrics-key"}).json()
    assert k["kpis"]["orders"] == 2 and k["kpis"]["test_orders"] == 1
    assert k["kpis"]["revenue"] == 50.0
    assert k["by_source_name"][0]["test_orders"] == 1


def test_utm_on_landing_page_counts_as_ai_referral(client):
    """No referrer (private window, agent browser) but ?utm_source=chatgpt.com
    on the landing page: still an AI referral, for the session and its order."""
    shop = "utm-store.myshopify.com"
    client.post("/ingest", json={
        "session_key": "utm1", "shop": shop, "event_count": 3, "referrer": "",
        "landing_path": "/?utm_source=chatgpt.com", "user_agent": "Mozilla/5.0 Chrome/129.0",
        "pointer_env": "fine", "mouse_event_rate": 12.0, "teleport_click_ratio": 0.0,
        "clicks_delta": 3, "sparse_trail_clicks_delta": 0})
    webhook(client, shop, 770001, 40.0, note_attributes=[{"name": "agent_trust_session", "value": "utm1"}])
    auth = {"Authorization": f"Bearer {KEY}"}
    ov = client.get(f"/metrics/overview?shop={shop}&days=7", headers=auth).json()
    assert ov["kpis"]["ai_referred_visits"] == 1
    orders = client.get(f"/metrics/orders?shop={shop}&days=7", headers=auth).json()
    seg = {s["segment"]: s["orders"] for s in orders["by_segment"]}
    assert seg.get("ai_referred") == 1


def test_automation_via_ai_link_is_named_likely_agent(client):
    shop = "utm-agent.myshopify.com"
    client.post("/ingest", json={
        "session_key": "utm2", "shop": shop, "event_count": 3, "landing_path": "/?utm_source=chatgpt.com",
        "user_agent": "Mozilla/5.0 Chrome/129.0", "pointer_env": "fine", "mouse_event_rate": 0.0,
        "teleport_click_ratio": 1.0, "clicks_delta": 3, "sparse_trail_clicks_delta": 3})
    s = client.get(f"/metrics/sessions?shop={shop}&days=7", headers={"Authorization": f"Bearer {KEY}"}).json()
    names = {r["agent"] for r in s["sessions"]}
    assert "ChatGPT agent (likely)" in names, names
