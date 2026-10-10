"""Keys by role, rate limits, body caps, registered stores, closed endpoints."""
from fastapi.testclient import TestClient

from app import security
from app.main import app


def test_roles_and_admin_only(monkeypatch):
    monkeypatch.setenv("APP_API_KEYS", "app-new,app-old")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-1")
    monkeypatch.delenv("METRICS_API_KEY", raising=False)
    assert security.role_for("app-old") == "app" and security.role_for("app-new") == "app"
    assert security.role_for("admin-1") == "admin" and security.role_for("nope") is None
    with TestClient(app) as c:
        app_h = {"Authorization": "Bearer app-new"}
        assert c.get("/orders/evidence", params={"email_prefix": "_" * 20}).status_code == 401
        assert c.get("/orders/evidence", params={"email_prefix": "_" * 20}, headers=app_h).status_code == 403
        r = c.get("/orders/evidence", params={"email_prefix": "_" * 20}, headers={"Authorization": "Bearer admin-1"})
        assert r.status_code == 200 and r.json() == []  # wildcards are escaped, not matched
        assert c.get("/plans", params={"shop": "sec.myshopify.com"}, headers=app_h).status_code == 200
        assert c.get("/integrations/cloudflare", params={"shop": "sec.myshopify.com"}, headers=app_h).status_code == 403


def test_fails_closed_with_no_keys(monkeypatch):
    for k in ("APP_API_KEYS", "APP_API_KEY", "ADMIN_API_KEYS", "ADMIN_API_KEY", "METRICS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with TestClient(app) as c:
        assert c.get("/plans", params={"shop": "x.myshopify.com"}, headers={"Authorization": "Bearer x"}).status_code == 503


def test_docs_are_off():
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_rate_limit_and_body_cap(monkeypatch):
    monkeypatch.setenv("RATE_LIMITS", "on")
    b = security.TokenBucket(rate=0.0, burst=3)
    assert [b.allow("k") for _ in range(4)] == [True, True, True, False]
    with TestClient(app) as c:
        big = "x" * (security.MAX_BODY_BYTES + 10)
        assert c.post("/journey", content=big, headers={"Content-Type": "text/plain"}).status_code == 413


def test_unregistered_store_is_dropped(monkeypatch):
    monkeypatch.setenv("ALLOWED_SHOPS", "")
    monkeypatch.setenv("DEFAULT_SHOP", "default.myshopify.com")
    with TestClient(app) as c:
        r = c.post("/ingest", json={"session_key": "s_unreg", "shop": "made-up-store.myshopify.com",
                                    "user_agent": "Mozilla/5.0", "event_count": 1})
        assert r.json() == {"status": "ignored"}
    assert security.is_registered("default.myshopify.com")


def test_shelf_run_refuses_foreign_domain(monkeypatch):
    monkeypatch.setenv("APP_API_KEY", "app-k")
    from app import shelf
    monkeypatch.setattr(shelf, "_get", lambda url, **kw: {"myshopify_domain": "someone-else.myshopify.com"})
    with TestClient(app) as c:
        r = c.post("/shelf/run", headers={"Authorization": "Bearer app-k"},
                   json={"shop": "mine.myshopify.com", "domain": "victim.example.com"})
        assert r.status_code == 400
