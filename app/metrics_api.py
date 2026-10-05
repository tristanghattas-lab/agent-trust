"""
Metrics endpoints: one per view, scoped to a store.

    GET /metrics/overview?shop=<domain>&days=30
    GET /metrics/referrals?shop=...&days=...
    GET /metrics/sessions?shop=...&days=...&class=automation&limit=50&offset=0
    GET /metrics/sessions/{session_key}?shop=...
    GET /metrics/orders?shop=...&days=...
    GET /metrics/threats?shop=...

shop=demo serves the synthetic dataset, so a front end can be built before a
store has live traffic. Responses carry "synthetic": true in that case, and
front ends must show it.

Auth: Authorization: Bearer <METRICS_API_KEY>. Callers are servers (the
Shopify app's backend, the internal dashboard), never browsers, so the key
is never exposed to a storefront. Fails closed: if METRICS_API_KEY isn't set,
every request is refused.

Later, for the Shopify app: verify Shopify session tokens per shop instead of
one shared key, so a merchant can only ever read their own store.
"""
from __future__ import annotations

import hmac
import os
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query

from app import metrics
from app.db import engine
from app.shops import DEMO_SHOP, normalise_shop

router = APIRouter(prefix="/metrics", tags=["metrics"])


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("METRICS_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="metrics API key not configured")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def resolve_shop(shop: str = Query(..., description="Store domain, or 'demo'")) -> str:
    if shop.strip().lower() == DEMO_SHOP:
        return DEMO_SHOP
    norm = normalise_shop(shop)
    if not norm:
        raise HTTPException(status_code=400, detail="invalid shop")
    return norm


Days = Query(30, ge=1, le=365)
deps = [Depends(require_api_key)]


def refresh_cloudflare(shop: str, background: BackgroundTasks) -> None:
    """Keep Cloudflare analytics fresh without a scheduler: when a store's
    overview is read, sync it in the background if it's over an hour old."""
    if shop != DEMO_SHOP:
        from app.cloudflare import sync_if_stale
        background.add_task(sync_if_stale, shop)


@router.get("/overview", dependencies=deps)
def get_overview(background: BackgroundTasks, shop: str = Depends(resolve_shop), days: int = Days):
    refresh_cloudflare(shop, background)
    return metrics.overview(metrics.load_frames(engine, shop, days))


@router.get("/referrals", dependencies=deps)
def get_referrals(shop: str = Depends(resolve_shop), days: int = Days):
    return metrics.referrals(metrics.load_frames(engine, shop, days))


@router.get("/sessions", dependencies=deps)
def get_sessions(
    shop: str = Depends(resolve_shop),
    days: int = Days,
    cls: Literal["assistant", "automation", "crawler", "scraper"] | None = Query(None, alias="class"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return metrics.sessions_list(metrics.load_frames(engine, shop, days), cls, limit, offset)


@router.get("/sessions/{session_key}", dependencies=deps)
def get_session(session_key: str, shop: str = Depends(resolve_shop), days: int = Query(365, ge=1, le=365)):
    detail = metrics.session_detail(metrics.load_frames(engine, shop, days), session_key)
    if detail is None:
        raise HTTPException(status_code=404, detail="session not found for this shop")
    return detail


@router.get("/orders", dependencies=deps)
def get_orders(shop: str = Depends(resolve_shop), days: int = Days):
    return metrics.orders_summary(metrics.load_frames(engine, shop, days))


@router.get("/threats", dependencies=deps)
def get_threats(shop: str = Depends(resolve_shop)):
    return metrics.threats(metrics.load_frames(engine, shop, 365))
