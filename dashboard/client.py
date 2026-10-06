"""
How the dashboard gets its numbers: the same metrics the API serves.

- METRICS_API_URL + METRICS_API_KEY set: calls the API over HTTP.
- Otherwise: calls app/metrics.py in-process against DATABASE_URL (or only
  the demo store when there's no database).

Either way the dashboard renders exactly what the Shopify app will.
"""
from __future__ import annotations

import os

import requests
import streamlit as st

from app import metrics
from app.shops import DEMO_SHOP

API_URL = os.getenv("METRICS_API_URL", "").rstrip("/")
API_KEY = os.getenv("METRICS_API_KEY", "")

_VIEWS = {
    "overview": metrics.overview,
    "referrals": metrics.referrals,
    "orders": metrics.orders_summary,
    "threats": metrics.threats,
}


def _engine():
    if not os.getenv("DATABASE_URL"):
        return None
    from app.db import engine
    return engine


@st.cache_data(ttl=120, show_spinner=False)
def get(view: str, shop: str, days: int, **params) -> dict:
    if API_URL and API_KEY:
        path = f"/metrics/sessions/{params.pop('session_key')}" if view == "session" else f"/metrics/{view}"
        if "cls" in params:
            params["class"] = params.pop("cls")
        r = requests.get(API_URL + path, params={"shop": shop, "days": days, **params},
                         headers={"Authorization": f"Bearer {API_KEY}"}, timeout=90)
        r.raise_for_status()
        return r.json()

    engine = _engine()
    if engine is None and shop != DEMO_SHOP:
        raise RuntimeError("No database configured")
    frames = metrics.load_frames(engine, shop, days)
    if view == "sessions":
        return metrics.sessions_list(frames, params.get("cls"), params.get("limit", 50),
                                     params.get("offset", 0))
    if view == "session":
        return metrics.session_detail(frames, params["session_key"]) or {}
    return _VIEWS[view](frames)


@st.cache_data(ttl=300, show_spinner=False)
def shops() -> list[str]:
    """Live stores first, then the demo store."""
    live: list[str] = []
    engine = _engine()
    if engine is not None and not API_URL:
        try:
            live = metrics.list_shops(engine)
        except Exception:  # database asleep or unreachable: demo still works
            live = []
    return live + [DEMO_SHOP]


# ---------------------------------------------------------------------------
# Cloudflare connection (Connections page). Same API or in-process split.
# ---------------------------------------------------------------------------
def _cf_http(method: str, path: str = "", **kw) -> dict:
    r = requests.request(method, f"{API_URL}/integrations/cloudflare{path}",
                         headers={"Authorization": f"Bearer {API_KEY}"}, timeout=120, **kw)
    if r.status_code >= 400:
        raise RuntimeError((r.json() or {}).get("detail", r.text) if r.headers.get(
            "content-type", "").startswith("application/json") else r.text)
    return r.json()


def _cf_local(fn):
    from app import cloudflare
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        return fn(cloudflare, db)
    except cloudflare.CloudflareError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        db.close()


def report(shop: str, minutes: int, include_bots: bool = False) -> dict:
    """Test-run report for the last `minutes` (not cached: always fresh)."""
    from datetime import datetime, timedelta, timezone
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=minutes)
    if API_URL and API_KEY:
        r = requests.get(f"{API_URL}/metrics/report", headers={"Authorization": f"Bearer {API_KEY}"},
                         params={"shop": shop, "since": since.isoformat(), "until": until.isoformat(),
                                 "include_bots": str(include_bots).lower()}, timeout=90)
        r.raise_for_status()
        return r.json()
    engine = _engine()
    frames = metrics.load_frames(engine, shop, max(1, minutes // 1440 + 1))
    return metrics.run_report(frames, since, until, include_bots)


def connections_available() -> bool:
    return bool(API_URL and API_KEY) or _engine() is not None


def cf_status(shop: str) -> dict:
    if API_URL and API_KEY:
        return _cf_http("GET", params={"shop": shop})
    return _cf_local(lambda cf, db: cf.status(cf._get(db, shop)))


def cf_connect(shop: str, zone_id: str, api_token: str) -> dict:
    st.cache_data.clear()
    if API_URL and API_KEY:
        return _cf_http("POST", json={"shop": shop, "zone_id": zone_id, "api_token": api_token})
    return _cf_local(lambda cf, db: cf.connect(db, shop, zone_id, api_token))


def cf_sync(shop: str) -> dict:
    st.cache_data.clear()
    if API_URL and API_KEY:
        return _cf_http("POST", "/sync", params={"shop": shop})
    return _cf_local(lambda cf, db: cf.sync(db, cf._get(db, shop)))


def cf_disconnect(shop: str) -> dict:
    st.cache_data.clear()
    if API_URL and API_KEY:
        return _cf_http("DELETE", params={"shop": shop})

    def drop(cf, db):
        from app.models import EdgeAggregate, Integration
        db.query(EdgeAggregate).filter_by(shop_domain=shop, source="cloudflare").delete()
        db.query(Integration).filter_by(shop_domain=shop, kind="cloudflare").delete()
        db.commit()
        return {"connected": False}
    return _cf_local(drop)


def edge_key(shop: str) -> str:
    """The store's Worker key: from the API, or derived locally from the
    same EDGE_SIGNING_SECRET the API uses."""
    if API_URL and API_KEY:
        r = requests.get(f"{API_URL}/edge/key", params={"shop": shop},
                         headers={"Authorization": f"Bearer {API_KEY}"}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(r.text)
        return r.json()["edge_key"]
    from app.edge import edge_key as derive
    return derive(shop)


def ingest_url() -> str:
    base = API_URL or os.getenv("AGENT_TRUST_API_URL", "https://agent-trust-api-o7u9.onrender.com")
    return base.rstrip("/") + "/edge/ingest"


def refresh_in_background(shop: str) -> None:
    """In-process mode: keep Cloudflare analytics fresh like the API does."""
    if shop == DEMO_SHOP or (API_URL and API_KEY) or _engine() is None:
        return
    import threading
    from app.cloudflare import sync_if_stale
    threading.Thread(target=sync_if_stale, args=(shop,), daemon=True).start()
