"""Shopify app events: cart/checkout webhooks and checkout pixel steps."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app

SHOP = "app-store.myshopify.com"
KEY = "test-metrics-key"
AUTH = {"Authorization": f"Bearer {KEY}"}
APP_SECRET = "app_client_secret"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("METRICS_API_KEY", KEY)
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", f"test_secret,{APP_SECRET}")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def hook(client, topic, payload, webhook_id, secret=APP_SECRET):
    body = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return client.post("/webhooks/shopify/commerce", content=body, headers={
        "x-shopify-hmac-sha256": sig, "x-shopify-topic": topic, "x-shopify-shop-domain": SHOP,
        "x-shopify-webhook-id": webhook_id, "content-type": "application/json"})


def now_iso(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def test_cart_webhooks_flag_carts_built_off_site(client):
    assert hook(client, "carts/create", {"token": "c1"}, "w0", secret="wrong").status_code == 401
    # Built through the agent API: no tracker session on the cart.
    r = hook(client, "carts/create", {"token": "cart_api_1", "created_at": now_iso(),
                                      "line_items": [{"quantity": 1, "line_price": "300.00"}]}, "w1")
    assert r.json() == {"status": "recorded"}
    assert hook(client, "carts/create", {"token": "cart_api_1"}, "w1").json() == {"status": "duplicate"}
    # Built in a browser the tracker saw.
    hook(client, "checkouts/create", {"token": "ck_1", "cart_token": "cart_web_1", "created_at": now_iso(),
                                      "total_price": "40.00",
                                      "note_attributes": [{"name": "agent_trust_session", "value": "s_web_1"}],
                                      "line_items": [{"quantity": 2, "price": "20.00"}]}, "w2")
    hook(client, "carts/create", {"token": "cart_web_1", "created_at": now_iso()}, "w3")
    rep = client.get(f"/metrics/report?shop={SHOP}", headers=AUTH).json()
    ev = {(e["topic"], e["token"]): e for e in rep["commerce_events"]}
    assert ev[("carts/create", "rt_api_1")]["storefront_session"] is False
    assert ev[("carts/create", "rt_api_1")]["total"] == 300.0
    assert ev[("carts/create", "rt_web_1")]["storefront_session"] is True  # linked via its checkout
    assert ev[("checkouts/create", "ck_1")]["items"] == 2
    assert rep["summary"]["carts_without_storefront_session"] == 1
    blob = json.dumps(rep)
    assert "@" not in blob  # no emails anywhere


def test_pixel_steps_attach_to_the_session_and_light_up_coverage(client):
    client.post("/ingest", json={"session_key": "s_pix_1", "shop": SHOP, "event_count": 3,
                                 "user_agent": "Mozilla/5.0 Chrome/129.0", "pointer_env": "fine",
                                 "mouse_event_rate": 0.0, "teleport_click_ratio": 1.0,
                                 "clicks_delta": 3, "sparse_trail_clicks_delta": 3})
    for i, step in enumerate(["checkout_started", "checkout_contact_info_submitted",
                              "payment_info_submitted", "checkout_completed"]):
        r = client.post("/pixel/events", content=json.dumps({
            "shop": SHOP, "event": step, "ts": now_iso(i * 2), "session_key": "s_pix_1",
            "checkout_token": "ck_9", "order_id": "gid://shopify/OrderIdentity/123" if i == 3 else None}),
            headers={"content-type": "text/plain"})
        assert r.status_code == 200
    assert client.post("/pixel/events", content=json.dumps({"shop": SHOP, "event": "page_viewed"})).status_code == 400
    rep = client.get(f"/metrics/report?shop={SHOP}", headers=AUTH).json()
    s = next(x for x in rep["sessions"] if x["session_key"] == "s_pix_1")
    assert [c["step"] for c in s["checkout_steps"]][-1] == "checkout_completed"
    assert s["checkout_steps"][-1]["seconds"] == 6.0
    ov = client.get(f"/metrics/overview?shop={SHOP}", headers=AUTH).json()
    src = {x["key"]: x["connected"] for x in ov["coverage"]["sources"]}
    assert src["pixel"] is True


def test_rejected_webhooks_are_logged_for_diagnosis(client):
    hook(client, "carts/update", {"token": "zz"}, "w-bad", secret="nope")
    rep = client.get(f"/metrics/report?shop={SHOP}", headers=AUTH).json()
    rej = [w for w in rep["webhook_deliveries"] if w["outcome"] == "rejected"]
    assert rej and "invalid signature" in rej[0]["detail"] and "2 secret(s)" in rej[0]["detail"]
