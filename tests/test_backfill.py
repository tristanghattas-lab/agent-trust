"""Past-order scan: orders posted by the Shopify app become attributed orders."""
import os

from fastapi.testclient import TestClient

from app import metrics
from app.db import engine
from app.main import app

SHOP = "scan-test.myshopify.com"


def test_backfill_and_scan(monkeypatch):
    monkeypatch.setenv("METRICS_API_KEY", "k")
    h = {"Authorization": "Bearer k"}
    orders = [
        {"id": "gid://shopify/Order/5550001", "created_at": "2026-10-01T01:00:00Z", "total": 200,
         "landing_site": "https://scan-test.myshopify.com/products/x?utm_source=chatgpt.com"},
        {"id": "5550002", "created_at": "2026-10-02T01:00:00Z", "total": 120, "source_name": "web",
         "referring_site": "https://www.perplexity.ai/"},
        {"id": "5550003", "created_at": "2026-10-03T01:00:00Z", "total": 80, "source_name": "1234567",
         "app": "1234567 ChatGPT"},
        {"id": "5550004", "created_at": "2026-10-04T01:00:00Z", "total": 100, "source_name": "web"},
        {"id": "5550005", "created_at": "2026-10-04T02:00:00Z", "total": 999, "test": True,
         "referring_site": "https://chatgpt.com/"},
    ]
    with TestClient(app) as client:
        assert client.post("/backfill/orders", json={"shop": SHOP, "orders": orders}).status_code == 401
        r = client.post("/backfill/orders", headers=h, json={"shop": SHOP, "orders": orders[:3]}).json()
        assert r["added"] == 3
        r2 = client.post("/backfill/orders", headers=h,
                         json={"shop": SHOP, "run_id": r["run_id"], "orders": orders[2:], "done": True}).json()
        assert r2["added"] == 2 and r2["scanned"] == 6  # order 5550003 sent twice, stored once
        st = client.get(f"/backfill/status?shop={SHOP}", headers=h).json()
        assert st["status"] == "done" and st["orders_added"] == 5

    d = metrics.scan(metrics.load_frames(engine, SHOP, 365))
    assert d["ai_orders"] == 3 and d["ai_revenue"] == 400 and d["test_orders"] == 1
    sources = {b["source"] for b in d["by_source"]}
    assert "ChatGPT (referred a shopper)" in sources and "ChatGPT (checkout inside the app)" in sources
    assert "Perplexity (referred a shopper)" in sources
