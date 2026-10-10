"""
Metrics endpoints: one per view, scoped to a store.

    GET /metrics/overview?shop=<domain>&days=30
    GET /metrics/referrals?shop=...&days=...
    GET /metrics/sessions?shop=...&days=...&class=automation&limit=50&offset=0
    GET /metrics/sessions/{session_key}?shop=...
    GET /metrics/orders?shop=...&days=...
    GET /metrics/orders/list?shop=...&days=...        (origin + evidence per order)
    GET /metrics/orders/{order_id}?shop=...           (one order's evidence chain)
    GET /metrics/products?shop=...&days=...           (products and agent behaviour)
    GET /metrics/scan?shop=...&days=60                (AI orders in recent order history)
    GET /metrics/behaviour?shop=...&days=...          (behaviour map, agent paths, journeys)
    GET /metrics/live?shop=...&minutes=30             (on the store right now)
    GET /metrics/channels?shop=...&days=...           (revenue by source)
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
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request

from app import metrics, security
from app.db import engine
from app.shops import DEMO_SHOP, normalise_shop

router = APIRouter(prefix="/metrics", tags=["metrics"])


def require_api_key(request: Request, authorization: str | None = Header(default=None)) -> str:
    """Store-level reads: the Shopify app's server or an admin tool (app/security.py)."""
    return security.require_app(request, authorization)


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


@router.get("/orders/list", dependencies=deps)
def get_orders_list(shop: str = Depends(resolve_shop), days: int = Days, limit: int = Query(200, ge=1, le=1000)):
    """Every order with its origin (agent-placed, agent-assisted, AI-referred...),
    agent identity tier and evidence score."""
    return metrics.orders_list(metrics.load_frames(engine, shop, days), limit)


@router.get("/orders/{order_id}", dependencies=deps)
def get_order(order_id: str, shop: str = Depends(resolve_shop), days: int = Query(365, ge=1, le=365)):
    """One order's evidence chain: origin, session, identity, checkout, order."""
    detail = metrics.order_detail(metrics.load_frames(engine, shop, days), order_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="order not found for this shop")
    return detail


@router.get("/scan", dependencies=deps)
def get_scan(shop: str = Depends(resolve_shop), days: int = Query(60, ge=1, le=365)):
    """AI orders and revenue across the store's recent orders, including
    past orders scanned when the Shopify app was installed."""
    return metrics.scan(metrics.load_frames(engine, shop, days))


@router.get("/behaviour", dependencies=deps)
def get_behaviour(shop: str = Depends(resolve_shop), days: int = Days):
    """Behaviour map (every visit placed by how it behaved), agent paths and
    recent agent journeys."""
    f = metrics.load_frames(engine, shop, days)
    return {**metrics.behaviour_map(f), **{k: v for k, v in metrics.agent_journeys(f).items()
                                          if k in ("paths", "recent", "agent_visits")}}


@router.get("/opportunities", dependencies=deps)
def get_opportunities(shop: str = Depends(resolve_shop), days: int = Days):
    """Agent sales the store missed, in dollars, each with a fix; the agent
    drop-off funnel; orders to check."""
    return metrics.opportunities(metrics.load_frames(engine, shop, days))


@router.get("/live", dependencies=deps)
def get_live(shop: str = Depends(resolve_shop), minutes: int = Query(30, ge=5, le=240)):
    """Visits active in the last N minutes and their latest steps."""
    return metrics.live(metrics.load_frames(engine, shop, 1), minutes)


@router.get("/channels", dependencies=deps)
def get_channels(shop: str = Depends(resolve_shop), days: int = Days):
    """Visitors, orders and revenue per source, agents and AI assistants included."""
    return metrics.channels(metrics.load_frames(engine, shop, days))


@router.get("/products", dependencies=deps)
def get_products(shop: str = Depends(resolve_shop), days: int = Days):
    """Per-product views, adds and out-of-stock hits by agents vs people,
    and how agents move through the store (from the tracker's journey)."""
    return metrics.products(metrics.load_frames(engine, shop, days))


@router.get("/report", dependencies=deps)
def get_report(
    shop: str = Depends(resolve_shop),
    since: datetime | None = Query(None, description="ISO time; default 60 minutes ago"),
    until: datetime | None = Query(None, description="ISO time; default now"),
    include_bots: bool = False,
):
    """One-call review of a test run: sessions with evidence and linked orders."""
    now = datetime.now(timezone.utc)
    until = (until or now) if (until or now).tzinfo else (until or now).replace(tzinfo=timezone.utc)
    since = since or until - timedelta(minutes=60)
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    days = max(1, min(365, (now - since).days + 1))
    return metrics.run_report(metrics.load_frames(engine, shop, days), since, until, include_bots)


@router.get("/threats", dependencies=deps)
def get_threats(shop: str = Depends(resolve_shop)):
    return metrics.threats(metrics.load_frames(engine, shop, 365))
