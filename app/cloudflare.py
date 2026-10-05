"""
Cloudflare analytics connector: edge visibility without deploying anything.

The merchant creates a read-only Cloudflare API token (Zone > Analytics >
Read, scoped to the store's zone) and connects it with their zone ID. Every
hour we pull request counts from Cloudflare's GraphQL Analytics API
(httpRequestsAdaptiveGroups, available on all plans) for:

  - user agents of AI crawlers, AI assistants, scrapers, headless browsers
    and HTTP libraries
  - any request for agent files (robots.txt, agents.md, llms.txt,
    sitemaps, /.well-known/, products.json), whoever made it

Stored as hourly counts per user agent and path (EdgeAggregate). These are
request counts, not sessions; Cloudflare's adaptive counts are sampled
estimates. For per-session detail, deploy the edge Worker (edge/).

Endpoints (Bearer METRICS_API_KEY, server-to-server):
    POST /integrations/cloudflare          connect: {shop, zone_id, api_token}
    GET  /integrations/cloudflare?shop=    status (never returns the token)
    POST /integrations/cloudflare/sync?shop=
    DELETE /integrations/cloudflare?shop=  disconnect and delete pulled data
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from app.db import SessionLocal, get_db
from app.metrics_api import require_api_key
from app.models import EdgeAggregate, Integration
from app.secrets_box import decrypt, encrypt
from app.shops import normalise_shop

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
KIND = "cloudflare"
SYNC_EVERY = timedelta(hours=1)
BACKFILL = timedelta(days=7)
CHUNK = timedelta(hours=6)  # smaller windows keep each query under the row limit
ROW_LIMIT = 10000

# Cloudflare's userAgent_like is a SQL LIKE match, so patterns use the
# casing agents actually send.
UA_PATTERNS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User", "ClaudeBot",
    "Claude-User", "Claude-SearchBot", "anthropic-ai", "Google-Extended", "Amazonbot",
    "Applebot-Extended", "meta-externalagent", "meta-externalfetcher", "Bytespider", "CCBot",
    "DuckAssistBot", "MistralAI-User", "HeadlessChrome", "python-requests", "python-urllib",
    "curl/", "Wget", "Go-http-client", "node-fetch", "axios", "Scrapy", "okhttp", "aiohttp", "httpx",
]
AGENT_FILE_PATHS = ["/robots.txt", "/agents.md", "/llms.txt", "/llms-full.txt", "/sitemap.xml",
                    "/products.json"]

QUERY = """
query AgentTraffic($zoneTag: string!, $start: Time!, $end: Time!, $filter: ZoneHttpRequestsAdaptiveGroupsFilter_InputObject!) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      rows: httpRequestsAdaptiveGroups(limit: %d, filter: $filter, orderBy: [count_DESC]) {
        count
        dimensions { datetimeHour userAgent clientRequestPath }
      }
    }
  }
}
""" % ROW_LIMIT


def build_filter(start: datetime, end: datetime) -> dict:
    either = [{"userAgent_like": f"%{p}%"} for p in UA_PATTERNS]
    either.append({"clientRequestPath_in": AGENT_FILE_PATHS})
    either.append({"clientRequestPath_like": "/.well-known/%"})
    either.append({"clientRequestPath_like": "/sitemap%.xml"})
    return {"AND": [{"datetime_geq": _iso(start)}, {"datetime_lt": _iso(end)}, {"OR": either}]}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CloudflareError(RuntimeError):
    pass


def run_query(api_token: str, zone_id: str, start: datetime, end: datetime) -> list[dict]:
    """One GraphQL call. Raises CloudflareError with Cloudflare's message on
    failure. Kept as a single function so tests can replace it."""
    try:
        resp = requests.post(
            GRAPHQL_URL,
            json={"query": QUERY, "variables": {"zoneTag": zone_id, "start": _iso(start),
                                                "end": _iso(end), "filter": build_filter(start, end)}},
            headers={"Authorization": f"Bearer {api_token}"}, timeout=30,
        )
    except requests.RequestException as exc:
        raise CloudflareError(f"couldn't reach Cloudflare: {type(exc).__name__}") from exc
    try:
        body = resp.json()
    except ValueError as exc:
        raise CloudflareError(f"Cloudflare returned HTTP {resp.status_code}") from exc
    if resp.status_code == 401 or resp.status_code == 403:
        raise CloudflareError("token rejected: it needs Zone > Analytics > Read for this zone")
    if body.get("errors"):
        raise CloudflareError("; ".join(e.get("message", "unknown error") for e in body["errors"])[:500])
    zones = (body.get("data") or {}).get("viewer", {}).get("zones") or []
    if not zones:
        raise CloudflareError("zone not found, or the token can't read it")
    return zones[0].get("rows") or []


def _rows_to_aggregates(shop: str, rows: list[dict]) -> list[EdgeAggregate]:
    out = []
    for r in rows:
        dim = r.get("dimensions") or {}
        hour = datetime.fromisoformat(dim["datetimeHour"].replace("Z", "+00:00"))
        out.append(EdgeAggregate(shop_domain=shop, source=KIND, hour=hour,
                                 user_agent=(dim.get("userAgent") or "")[:1000],
                                 path=(dim.get("clientRequestPath") or "")[:2000],
                                 requests=int(r.get("count") or 0)))
    return out


def sync(db: DBSession, integ: Integration, now: datetime | None = None) -> dict:
    """Pull everything since the last sync (or the backfill window), in
    chunks. Re-syncing a window replaces its rows, so it's idempotent."""
    now = (now or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    token = decrypt(integ.secret_encrypted)
    start = _aware(integ.synced_through) or now - BACKFILL
    start = min(start, now - timedelta(hours=2))  # re-pull the last hours: counts settle late
    total = 0
    try:
        cursor = start
        while cursor < now:
            end = min(cursor + CHUNK, now)
            rows = run_query(token, integ.external_id, cursor, end)
            db.query(EdgeAggregate).filter(
                EdgeAggregate.shop_domain == integ.shop_domain, EdgeAggregate.source == KIND,
                EdgeAggregate.hour >= cursor, EdgeAggregate.hour < end,
            ).delete(synchronize_session=False)
            db.add_all(_rows_to_aggregates(integ.shop_domain, rows))
            total += len(rows)
            cursor = end
        integ.synced_through = now
        integ.last_error = None
    except CloudflareError as exc:
        integ.last_error = str(exc)
    integ.last_sync_at = datetime.now(timezone.utc)
    db.commit()
    return {"rows": total, "synced_through": _iso(now), "error": integ.last_error}


def sync_if_stale(shop: str) -> None:
    """Background job: sync a store if its last sync is over an hour old."""
    db = SessionLocal()
    try:
        integ = _get(db, shop)
        last = _aware(integ.last_sync_at) if integ else None
        if integ and (last is None or datetime.now(timezone.utc) - last > SYNC_EVERY):
            sync(db, integ)
    except Exception:  # background: never raise into the request cycle
        db.rollback()
    finally:
        db.close()


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo
    return dt


def _get(db: DBSession, shop: str) -> Integration | None:
    return db.query(Integration).filter(Integration.shop_domain == shop,
                                        Integration.kind == KIND).one_or_none()


def status(integ: Integration | None) -> dict:
    if integ is None:
        return {"connected": False}
    return {
        "connected": True,
        "zone_id": integ.external_id,
        "connected_at": integ.created_at.isoformat() if integ.created_at else None,
        "last_sync_at": _aware(integ.last_sync_at).isoformat() if integ.last_sync_at else None,
        "synced_through": _aware(integ.synced_through).isoformat() if integ.synced_through else None,
        "last_error": integ.last_error,
    }


def connect(db: DBSession, shop: str, zone_id: str, api_token: str) -> dict:
    """Test the token with a real one-hour query, then store it encrypted
    and backfill. A bad token is rejected before anything is saved."""
    if not re.fullmatch(r"[0-9a-f]{32}", zone_id.strip().lower()):
        raise CloudflareError("zone ID should be 32 hex characters (Cloudflare > Overview > API)")
    zone_id = zone_id.strip().lower()
    now = datetime.now(timezone.utc)
    run_query(api_token.strip(), zone_id, now - timedelta(hours=1), now)  # raises if unusable
    integ = _get(db, shop) or Integration(shop_domain=shop, kind=KIND)
    integ.external_id = zone_id
    integ.secret_encrypted = encrypt(api_token.strip())
    integ.synced_through = None
    integ.last_error = None
    db.add(integ)
    db.commit()
    result = sync(db, integ)
    return {**status(integ), "backfill": result}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/integrations/cloudflare", tags=["integrations"],
                   dependencies=[Depends(require_api_key)])


class ConnectIn(BaseModel):
    shop: str
    zone_id: str = Field(min_length=32, max_length=32)
    api_token: str = Field(min_length=20, max_length=200)


def _shop(value: str) -> str:
    shop = normalise_shop(value)
    if not shop:
        raise HTTPException(status_code=400, detail="invalid shop")
    return shop


@router.post("")
def post_connect(body: ConnectIn, db: DBSession = Depends(get_db)):
    try:
        return connect(db, _shop(body.shop), body.zone_id, body.api_token)
    except CloudflareError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def get_status(shop: str = Query(...), db: DBSession = Depends(get_db)):
    return status(_get(db, _shop(shop)))


@router.post("/sync")
def post_sync(shop: str = Query(...), db: DBSession = Depends(get_db)):
    integ = _get(db, _shop(shop))
    if integ is None:
        raise HTTPException(status_code=404, detail="Cloudflare not connected for this shop")
    return sync(db, integ)


@router.delete("")
def delete_connection(shop: str = Query(...), db: DBSession = Depends(get_db)):
    shop = _shop(shop)
    db.query(EdgeAggregate).filter(EdgeAggregate.shop_domain == shop,
                                   EdgeAggregate.source == KIND).delete(synchronize_session=False)
    db.query(Integration).filter(Integration.shop_domain == shop,
                                 Integration.kind == KIND).delete(synchronize_session=False)
    db.commit()
    return {"connected": False}

