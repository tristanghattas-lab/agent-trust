import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import app

SHOP = "privacy-test.myshopify.com"


def _order(c, oid, email):
    body = json.dumps({"id": oid, "total_price": "50.00", "currency": "AUD", "email": email,
                       "note": "leave at door", "created_at": "2026-10-01T00:00:00Z"}).encode()
    sig = base64.b64encode(hmac.new(b"test_secret", body, hashlib.sha256).digest()).decode()
    r = c.post("/webhooks/shopify/orders", content=body, headers={
        "x-shopify-hmac-sha256": sig, "x-shopify-shop-domain": SHOP, "content-type": "application/json"})
    assert r.status_code == 200, r.text


def test_privacy_webhooks(monkeypatch):
    monkeypatch.setenv("METRICS_API_KEY", "k")
    h = {"Authorization": "Bearer k"}
    with TestClient(app) as c:
        _order(c, 66001, "Jo@Example.com")
        _order(c, 66002, "sam@example.com")
        assert c.post("/privacy/shop-redact", json={"shop": SHOP}).status_code == 401
        d = c.post("/privacy/customers-data-request", headers=h,
                   json={"shop": SHOP, "customer": {"email": "jo@example.com"}}).json()
        assert [o["shopify_order_id"] for o in d["orders"]] == ["66001"]
        r = c.post("/privacy/customers-redact", headers=h,
                   json={"shop": SHOP, "customer": {"email": "jo@example.com"}, "orders_to_redact": [66001]}).json()
        assert r["redacted_orders"] == 1
        d = c.post("/privacy/customers-data-request", headers=h,
                   json={"shop": SHOP, "customer": {"email": "jo@example.com"}}).json()
        assert d["orders"] == []
        r = c.post("/privacy/shop-redact", headers=h, json={"shop": SHOP}).json()
        assert r["deleted"]["orders"] == 2
