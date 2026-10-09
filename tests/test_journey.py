"""Tracker journey steps: recorded, redacted, and shown as a timeline."""
import json
import time

from fastapi.testclient import TestClient

from app import metrics
from app.db import engine
from app.main import app

SHOP = "journey-test.myshopify.com"


def _post(client, events, key="s_journey_1"):
    return client.post("/journey", content=json.dumps({"shop": SHOP, "session_key": key, "events": events}),
                       headers={"Content-Type": "text/plain"})


def test_journey_recorded_and_redacted():
    now = int(time.time() * 1000)
    with TestClient(app) as client:
        client.post("/ingest", json={"session_key": "s_journey_1", "shop": SHOP, "user_agent": "Mozilla/5.0",
                                     "landing_path": "/products/wine", "event_count": 1})
        r = _post(client, [
            {"kind": "page", "ts": now, "seq": 1, "path": "/products/wine", "page_type": "product",
             "product": "wine", "title": "Test Wine"},
            {"kind": "search", "ts": now + 2000, "seq": 2, "query": "me@example.com"},
            {"kind": "cart_add", "ts": now + 4000, "seq": 3, "product": "wine", "quantity": 2, "price": 45},
            {"kind": "leave", "ts": now + 6000, "seq": 4, "product": "wine", "dwell_ms": 6000, "scroll_pct": 30},
            {"kind": "bogus", "ts": now},
        ])
        assert r.json() == {"status": "recorded", "events": 4}
        assert _post(client, [], key="bad key!").status_code == 400

    f = metrics.load_frames(engine, SHOP, 1)
    steps = metrics.journey_steps(f, "s_journey_1")
    assert [s["text"] for s in steps] == [
        "Viewed Test Wine", 'Searched "[redacted]"', "Added 2 × Test Wine ($45.00)",
        "Left Test Wine after 6s, scrolled 30%"]
    summary = metrics.journey_summary(steps)
    assert summary["adds_to_cart"] == 1 and summary["products_viewed"] == 1


def test_demo_products_view():
    d = metrics.products(metrics.load_frames(None, "demo", 30))
    ag, pp = d["agent_behaviour"]["agents"], d["agent_behaviour"]["people"]
    assert d["products"] and ag["avg_seconds_on_page"] < pp["avg_seconds_on_page"]


def test_demo_behaviour_views():
    f = metrics.load_frames(None, "demo", 30)
    b = metrics.behaviour_map(f)
    assert len(b["points"]) > 50 and all(-1 <= p["x"] <= 1 and -1 <= p["y"] <= 1 for p in b["points"])
    ag = [p["x"] for p in b["points"] if p["class"] != "human"]
    hu = [p["x"] for p in b["points"] if p["class"] == "human"]
    assert sum(ag) / len(ag) > sum(hu) / len(hu)  # agents oriented to the right
    ch = metrics.channels(f)["channels"]
    assert any(c["kind"] == "agent" for c in ch) and any(c["channel"] == "Google" for c in ch)
    j = metrics.agent_journeys(f)
    assert j["paths"] and j["recent"] and j["recent"][0]["steps"]
    assert metrics.live(f)["visitors"] >= 0
