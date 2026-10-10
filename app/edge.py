"""
Edge logs: request records from a Cloudflare Worker in front of the store.

Why: crawlers, scrapers and assistants that only fetch pages never run
JavaScript, so the browser tracker can't see them. A Worker on the
merchant's own Cloudflare zone (edge/worker.js) sees every request.

What arrives: only non-browser traffic. The Worker forwards requests from
declared bots, Web Bot Auth-signed agents, HTTP libraries, clients missing
the headers every browser sends, and anything fetching agent files
(robots.txt, agents.md, llms.txt, sitemaps). Ordinary browser traffic is
the tracker's job, so it's dropped here too and nothing is counted twice.
IPs are hashed inside Cloudflare (daily salt); raw IPs never reach us.

Auth: each store has its own edge key, derived from EDGE_SIGNING_SECRET, so
no key table is needed and one store's key can't post into another store.
The Worker signs each body with HMAC-SHA256. Print a store's key with
`python -m scripts.edge_key <shop>`.

Sessions: requests are grouped into one session per (store, hashed IP, user
agent), ending after 30 minutes of inactivity, stored with js_executed = false, and classified
with the usual rules plus the edge signals (classify.py rule 9).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from app.analytics import HTTP_LIBRARIES, agent_file
from app.classify import KNOWN_AGENT_UA_SUBSTRINGS, classify_model, classify_session
from app.db import get_db
from app.security import require_admin as require_api_key
from app.models import Session as SessionModel
from app.shops import normalise_shop

router = APIRouter(prefix="/edge", tags=["edge"])

SESSION_WINDOW_SECONDS = 30 * 60
MAX_PATHS_PER_SESSION = 25
MAX_BODY_BYTES = 256 * 1024
AGENT_API = re.compile(r"^/api/(ucp|mcp)(/|$)")



# ---------------------------------------------------------------------------
# Keys and signatures
# ---------------------------------------------------------------------------
def edge_key(shop: str) -> str:
    secret = os.getenv("EDGE_SIGNING_SECRET", "")
    if not secret:
        raise RuntimeError("EDGE_SIGNING_SECRET not configured")
    return hmac.new(secret.encode(), f"edge:{shop}".encode(), hashlib.sha256).hexdigest()[:40]


def sign(body: bytes, key: str) -> str:
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
class EdgeRecord(BaseModel):
    ts: int                                   # epoch milliseconds
    ip_hash: str = Field(max_length=64)       # hashed in the Worker, never a raw IP
    ua: str | None = Field(default=None, max_length=1000)
    method: str = "GET"
    path: str = Field(max_length=2000)
    status: int | None = None
    referer: str | None = Field(default=None, max_length=2000)
    accept: str | None = Field(default=None, max_length=500)
    accept_language: bool = False             # presence only
    sec_fetch_mode: str | None = None
    sec_ch_ua: bool = False                   # presence only
    signature_agent: str | None = Field(default=None, max_length=300)
    signed: bool = False                      # Signature + Signature-Input headers present
    asn: int | None = None
    country: str | None = None
    bot_score: int | None = None              # Cloudflare Bot Management, if the zone has it
    verified_bot: bool | None = None
    # request.cf.verifiedBotCategory: Cloudflare's verified-bot category
    # ("AI Assistant", "AI Crawler" ...). Available on every plan.
    verified_category: str | None = Field(default=None, max_length=64)
    # The tracker's session key, from its first-party _at_sid cookie. Lets
    # request-level evidence join the browser session it belongs to.
    tracker_session: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # Store agent API (UCP/MCP) tool called, e.g. search_catalog. Name only.
    tool: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_./-]+$")


class EdgeBatch(BaseModel):
    records: list[EdgeRecord] = Field(max_length=100)


def signals_for(r: EdgeRecord) -> set[str]:
    """Request-level evidence for classify.py rule 9."""
    ua = (r.ua or "").lower()
    out: set[str] = set()
    if r.signed or r.signature_agent:
        domain = _signature_domain(r.signature_agent) or "unknown"
        out.add(f"signed_agent:{domain}")
    if AGENT_API.match(r.path or ""):
        out.add("agent_api")
    if r.verified_category:
        out.add("verified_bot:" + re.sub(r"[^a-z0-9]+", "-", r.verified_category.lower()).strip("-"))
    lib = next((lib for lib in HTTP_LIBRARIES if lib in ua), None)
    if lib:
        out.add(f"http_library:{lib.strip('/')}")
    elif not r.sec_fetch_mode and not r.accept_language:
        out.add("non_browser")
    return out


def _signature_domain(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().strip('"').lower()
    v = re.sub(r"^[a-z]+://", "", v).split("/")[0]
    return v.removeprefix("www.") or None


def is_browser_like(r: EdgeRecord) -> bool:
    """Ordinary browser traffic: the tracker covers it, so it isn't stored."""
    ua = (r.ua or "").lower()
    declared = any(token in ua for token in KNOWN_AGENT_UA_SUBSTRINGS)
    return (
        not declared and not signals_for(r) and not agent_file(r.path)
        and bool(r.sec_fetch_mode) and r.accept_language
    )


TRACKER_OWN_PATHS = re.compile(r"^(/[a-z]{2}(-[a-z]{2})?)?(/collections/[^/]+)?/products/[^/]+\.js$|/cart(/update)?\.js$", re.I)


def _merge_into_tracker_session(db: DBSession, shop: str, r: EdgeRecord) -> SessionModel | None:
    """A request from a browser the tracker already knows (its _at_sid
    cookie): add the edge evidence to that session instead of starting a
    separate edge session. This is how a Web Bot Auth signature or a
    Cloudflare verified-bot category names the agent behind a browser
    session that the tracker flagged on behaviour alone."""
    if not r.tracker_session:
        return None
    s = (db.query(SessionModel)
         .filter(SessionModel.session_key == r.tracker_session, SessionModel.shop_domain == shop,
                 SessionModel.js_executed.is_(True))
         .one_or_none())
    if s is None:
        return None
    sig = set(filter(None, (s.edge_signals or "").split(","))) | signals_for(r)
    s.edge_signals = ",".join(sorted(sig)) or None
    paths = json.loads(s.edge_paths) if s.edge_paths else []
    entry = f"{r.path} → {r.tool}" if r.tool else r.path
    # The tracker's own background calls (product availability, cart tagging)
    # aren't something the visitor did: keep any signals, but not the path.
    own = bool(TRACKER_OWN_PATHS.search((r.path or "").split("?")[0]))
    if not own and entry not in paths and len(paths) < MAX_PATHS_PER_SESSION:
        paths.append(entry)
    s.edge_paths = json.dumps(paths)
    result = classify_model(s)
    s.is_agent = result.is_agent
    s.agent_family = result.agent_family
    s.classification_confidence = result.confidence
    s.classification_reasons = result.reasons_csv
    return s


def apply_record(db: DBSession, shop: str, r: EdgeRecord) -> SessionModel:
    """Add one request to its session. A request from a browser the tracker
    knows joins that session; otherwise requests group by store, hashed IP
    and user agent, with the previous request less than 30 minutes earlier
    (an inactivity timeout, so sessions don't split at fixed boundaries)."""
    merged = _merge_into_tracker_session(db, shop, r)
    if merged is not None:
        return merged
    seen_at = datetime.fromtimestamp(r.ts / 1000, tz=timezone.utc)
    s = (
        db.query(SessionModel)
        .filter(
            SessionModel.shop_domain == shop,
            SessionModel.ip == r.ip_hash,  # the Worker's hash, never a raw IP
            SessionModel.user_agent == r.ua,
            SessionModel.js_executed.is_(False),
            SessionModel.last_seen >= seen_at - timedelta(seconds=SESSION_WINDOW_SECONDS),
        )
        .order_by(SessionModel.last_seen.desc())
        .first()
    )
    if s is None:
        digest = hashlib.sha256(f"{shop}|{r.ip_hash}|{r.ua or ''}|{r.ts}".encode()).hexdigest()
        s = SessionModel(
            session_key=f"edge_{digest[:20]}", shop_domain=shop, ip=r.ip_hash, user_agent=r.ua,
            referrer=r.referer, landing_path=r.path, first_seen=seen_at, last_seen=seen_at,
            event_count=0, js_executed=False,
        )
        db.add(s)
    s.last_seen = max(_aware(s.last_seen) or seen_at, seen_at)
    s.event_count = (s.event_count or 0) + 1

    paths = json.loads(s.edge_paths) if s.edge_paths else []
    entry = f"{r.path} → {r.tool}" if r.tool else r.path
    # The tracker's own background calls (product availability, cart tagging)
    # aren't something the visitor did: keep any signals, but not the path.
    own = bool(TRACKER_OWN_PATHS.search((r.path or "").split("?")[0]))
    if not own and entry not in paths and len(paths) < MAX_PATHS_PER_SESSION:
        paths.append(entry)
    s.edge_paths = json.dumps(paths)
    sig = set(filter(None, (s.edge_signals or "").split(","))) | signals_for(r)
    s.edge_signals = ",".join(sorted(sig)) or None

    result = classify_session(
        user_agent=s.user_agent, js_executed=False, event_count=s.event_count,
        time_to_checkout_seconds=None, cart_value=None, edge_signals=s.edge_signals,
    )
    s.is_agent = result.is_agent
    s.agent_family = result.agent_family
    s.classification_confidence = result.confidence
    s.classification_reasons = result.reasons_csv
    return s


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo
    return dt


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/key", dependencies=[Depends(require_api_key)])
def get_edge_key(shop: str):
    """A store's Worker key, for setup screens. Server-to-server only
    (Bearer METRICS_API_KEY), like the rest of the admin endpoints."""
    shop_n = normalise_shop(shop)
    if not shop_n:
        raise HTTPException(status_code=400, detail="invalid shop")
    if not os.getenv("EDGE_SIGNING_SECRET"):
        raise HTTPException(status_code=503, detail="EDGE_SIGNING_SECRET not configured")
    return {"shop": shop_n, "edge_key": edge_key(shop_n),
            "ingest_url_path": "/edge/ingest"}


@router.post("/ingest")
async def ingest_edge(
    request: Request,
    db: DBSession = Depends(get_db),
    x_agent_trust_shop: str | None = Header(default=None),
    x_agent_trust_signature: str | None = Header(default=None),
):
    if not os.getenv("EDGE_SIGNING_SECRET"):
        raise HTTPException(status_code=503, detail="edge ingest not configured")
    shop = normalise_shop(x_agent_trust_shop)
    if not shop:
        raise HTTPException(status_code=400, detail="missing or invalid X-Agent-Trust-Shop")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="batch too large")
    expected = sign(body, edge_key(shop))
    if not hmac.compare_digest(expected, (x_agent_trust_signature or "").lower()):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        batch = EdgeBatch.model_validate_json(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:300]) from exc

    stored = skipped = 0
    for r in batch.records:
        api_call = bool(AGENT_API.match(r.path or ""))
        if (r.method.upper() not in ("GET", "HEAD") and not api_call) or is_browser_like(r):
            skipped += 1
            continue
        apply_record(db, shop, r)
        db.flush()
        stored += 1
    db.commit()
    return {"stored": stored, "skipped": skipped}
