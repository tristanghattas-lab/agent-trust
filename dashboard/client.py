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
