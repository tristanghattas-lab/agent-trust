"""Edge ingest: signatures, grouping into sessions, classification, coverage."""
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.edge import edge_key, sign
from app.main import app

SHOP = "edge-store.myshopify.com"
KEY = "test-metrics-key"
AUTH = {"Authorization": f"Bearer {KEY}"}
TS = int(time.time() * 1000) - 3_600_000  # an hour ago


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    monkeypatch.setenv("EDGE_SIGNING_SECRET", "edge-secret-for-tests")
    monkeypatch.setenv("METRICS_API_KEY", KEY)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def post(client, records, shop=SHOP, key=None):
    body = json.dumps({"records": records}).encode()
    sig = sign(body, key or edge_key(shop))
    return client.post("/edge/ingest", content=body, headers={
        "x-agent-trust-shop": shop, "x-agent-trust-signature": sig, "content-type": "application/json"})


def rec(path, ua, ip="ip1", ts=TS, **kw):
    return {"ts": ts, "ip_hash": ip, "ua": ua, "path": path, **kw}


def test_rejects_bad_signature_and_other_stores_key(client):
    good = [rec("/robots.txt", "GPTBot/1.2")]
    assert post(client, good, key="wrong").status_code == 401
    other_key = edge_key("someone-else.myshopify.com")
    assert post(client, good, key=other_key).status_code == 401  # a store's key only works for that store


def test_fails_closed_without_secret(client, monkeypatch):
    monkeypatch.delenv("EDGE_SIGNING_SECRET")
    body = b'{"records": []}'
    r = client.post("/edge/ingest", content=body, headers={"x-agent-trust-shop": SHOP,
                                                           "x-agent-trust-signature": "x"})
    assert r.status_code == 503


def test_groups_requests_and_classifies(client):
    r = post(client, [
        rec("/robots.txt", "Mozilla/5.0; compatible; GPTBot/1.2", ip="crawler"),
        rec("/agents.md", "Mozilla/5.0; compatible; GPTBot/1.2", ip="crawler", ts=TS + 60_000),
        rec("/sitemap.xml", "Mozilla/5.0; compatible; GPTBot/1.2", ip="crawler", ts=TS + 25 * 60_000),
        rec("/products/x", "Mozilla/5.0 ChatGPT-User/1.0", ip="assist"),
        rec("/products.json", "python-requests/2.32", ip="scraper"),
        rec("/products/y", "Mozilla/5.0 Chrome/129.0", ip="headless"),               # no browser headers
        rec("/products/z", "Mozilla/5.0 Chrome/129.0", ip="signed",
            signature_agent='"https://chatgpt.com"', signed=True, accept_language=True, sec_fetch_mode="navigate"),
        rec("/products/w", "Mozilla/5.0 Chrome/129.0", ip="person",                   # a real browser: dropped
            accept_language=True, sec_fetch_mode="navigate"),
        rec("/cart/add", "curl/8", ip="poster", method="POST"),                        # writes: dropped
    ])
    assert r.status_code == 200, r.text
    assert r.json() == {"stored": 7, "skipped": 2}

    s = client.get(f"/metrics/sessions?shop={SHOP}&days=7&limit=50", headers=AUTH).json()
    by_agent = {x["agent"]: x for x in s["sessions"]}
    assert s["total"] == 5  # GPTBot's three requests, each < 30 min apart, are one session
    assert by_agent["GPTBot"]["class"] == "crawler"
    assert by_agent["ChatGPT"]["class"] == "assistant"
    classes = sorted(x["class"] for x in s["sessions"])
    assert classes == ["assistant", "assistant", "crawler", "scraper", "scraper"]

    signed = next(x for x in s["sessions"] if x["landing_path"] == "/products/z")
    detail = client.get(f"/metrics/sessions/{signed['session_key']}?shop={SHOP}", headers=AUTH).json()
    assert any("Web Bot Auth" in x["text"] for x in detail["reasons"])


def test_session_ends_after_30_minutes_idle(client):
    shop = "idle-store.myshopify.com"
    ua = "Mozilla/5.0; compatible; ClaudeBot/1.0"
    post(client, [rec("/robots.txt", ua, ip="c", ts=TS),
                  rec("/sitemap.xml", ua, ip="c", ts=TS + 29 * 60_000),          # same session
                  rec("/llms.txt", ua, ip="c", ts=TS + 29 * 60_000 + 31 * 60_000)], shop=shop)  # new one
    s = client.get(f"/metrics/sessions?shop={shop}&days=7", headers=AUTH).json()
    assert s["total"] == 2


def test_edge_turns_on_coverage_and_agent_files(client):
    ov = client.get(f"/metrics/overview?shop={SHOP}&days=7", headers=AUTH).json()
    assert ov["coverage"]["edge"] is True
    assert ov["coverage"]["hidden_agent_classes"] == []
    files = {f["file"]: f for f in ov["agent_files"]}
    assert files["robots.txt"]["top_agents"][0]["agent"] == "GPTBot"
    assert "agents.md" in files and "products.json" in files


def test_worker_signature_matches_api(tmp_path):
    """The Worker (Node, WebCrypto) and the API (Python) must agree on the HMAC."""
    body = json.dumps({"records": [rec("/robots.txt", "GPTBot")]})
    key = "k3y-for-interop"
    script = tmp_path / "sig.mjs"
    script.write_text(
        "const k = await crypto.subtle.importKey('raw', new TextEncoder().encode(process.argv[2]),"
        "{name:'HMAC',hash:'SHA-256'}, false, ['sign']);"
        "const s = await crypto.subtle.sign('HMAC', k, new TextEncoder().encode(process.argv[3]));"
        "console.log([...new Uint8Array(s)].map(b=>b.toString(16).padStart(2,'0')).join(''));")
    out = subprocess.run(["node", str(script), key, body], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == sign(body.encode(), key)


def test_edge_key_endpoint_needs_api_key(client):
    assert client.get("/edge/key", params={"shop": SHOP}).status_code == 401
    r = client.get("/edge/key", params={"shop": SHOP}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["edge_key"] == edge_key(SHOP)


def test_signature_names_the_agent_behind_a_flagged_browser_session(client):
    """Tonight's case: the tracker flags a browser session on behaviour; the
    Worker sees ChatGPT's Web Bot Auth signature and Cloudflare's verified
    category on the same browser (via the _at_sid cookie). The evidence
    joins that session and names the agent; no separate edge session."""
    shop = "join-store.myshopify.com"
    chrome = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"
    client.post("/ingest", json={
        "session_key": "s_join_1", "shop": shop, "event_count": 4, "user_agent": chrome,
        "pointer_env": "fine", "mouse_event_rate": 0.0, "teleport_click_ratio": 1.0,
        "clicks_delta": 3, "sparse_trail_clicks_delta": 3})
    auth = {"Authorization": f"Bearer {KEY}"}
    before = {r["session_key"]: r for r in
              client.get(f"/metrics/sessions?shop={shop}&days=7", headers=auth).json()["sessions"]}
    assert before["s_join_1"]["agent"] == "Undeclared (behavioural)"

    r = post(client, [rec("/products/x", chrome, ip="ipj", sec_fetch_mode="navigate", accept_language=True,
                          signature_agent='"https://chatgpt.com"', signed=True,
                          verified_category="AI Assistant", tracker_session="s_join_1")], shop=shop)
    assert r.json() == {"stored": 1, "skipped": 0}

    rows = client.get(f"/metrics/sessions?shop={shop}&days=7", headers=auth).json()["sessions"]
    assert len(rows) == 1  # joined, not a second session
    s = rows[0]
    assert s["agent"] == "ChatGPT agent (verified)"
    assert s["class"] == "assistant"
    detail = client.get(f"/metrics/sessions/s_join_1?shop={shop}", headers=auth).json()
    text = " ".join(x["text"] for x in detail["reasons"])
    assert "Cloudflare verified" in text and "Signed its requests" in text and "no mouse" in text.lower()


def test_unknown_tracker_cookie_falls_back_to_edge_session(client):
    shop = "join-store-2.myshopify.com"
    r = post(client, [rec("/agents.md", "Mozilla/5.0 Claude-User/1.0", tracker_session="s_nope")], shop=shop)
    assert r.json()["stored"] == 1
    rows = client.get(f"/metrics/sessions?shop={shop}&days=7", headers={"Authorization": f"Bearer {KEY}"}).json()["sessions"]
    assert rows and rows[0]["session_key"].startswith("edge_")


def test_signed_seo_bot_is_a_crawler_not_an_assistant():
    import pandas as pd
    from app.analytics import agent_label, classify_traffic
    row = pd.Series({"user_agent": "Mozilla/5.0 (compatible; AhrefsBot/7.0)", "is_agent": True,
                     "agent_family": "signed-agent",
                     "classification_reasons": "signed_agent:ahrefs.com,cf_verified:search-engine-optimization"})
    row["traffic_class"] = classify_traffic(row)
    assert row["traffic_class"] == "AI crawler"
    assert agent_label(row) == "AhrefsBot (verified)"


def test_store_agent_api_calls_are_recorded_with_tools(client):
    shop = "ucp-store.myshopify.com"
    ua = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/129.0"
    r = post(client, [
        rec("/api/ucp/mcp", ua, ip="ipu", method="POST", tool="search_catalog"),
        rec("/api/ucp/mcp", ua, ip="ipu", method="POST", tool="update_cart", ts=TS + 5000),
        rec("/cart/add", ua, ip="ipu", method="POST", ts=TS + 6000),  # ordinary POST: still ignored
    ], shop=shop)
    assert r.json() == {"stored": 2, "skipped": 1}
    rows = client.get(f"/metrics/sessions?shop={shop}&days=7", headers={"Authorization": f"Bearer {KEY}"}).json()["sessions"]
    assert len(rows) == 1
    assert rows[0]["agent"] == "Agent via store API" and rows[0]["class"] == "assistant"
    d = client.get(f"/metrics/sessions/{rows[0]['session_key']}?shop={shop}",
                   headers={"Authorization": f"Bearer {KEY}"}).json()
    blob = json.dumps(d)
    assert "search_catalog" in blob and "update_cart" in blob
    assert "agent API" in blob


def test_run_report_returns_window_with_evidence_and_orders(client):
    shop = "report-store.myshopify.com"
    ua = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/129.0"
    client.post("/ingest", json={"session_key": "s_rep_1", "shop": shop, "event_count": 3, "user_agent": ua,
                                 "landing_path": "/?utm_source=claude.ai", "pointer_env": "fine",
                                 "mouse_event_rate": 0.0, "teleport_click_ratio": 1.0,
                                 "clicks_delta": 3, "sparse_trail_clicks_delta": 3})
    post(client, [rec("/robots.txt", "GPTBot/1.2", ip="ipr")], shop=shop)  # crawler: filtered by default
    auth = {"Authorization": f"Bearer {KEY}"}
    rep = client.get(f"/metrics/report?shop={shop}", headers=auth).json()
    assert rep["summary"]["sessions"] == 1
    s = rep["sessions"][0]
    assert s["agent"] == "Undeclared (via Claude link)" and s["reasons"] and s["signals_detail"]["clicks"] == 3
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    allrep = client.get(f"/metrics/report?shop={shop}&include_bots=true&since={since}", headers=auth).json()
    assert allrep["summary"]["sessions"] == 2
