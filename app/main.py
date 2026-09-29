"""
FastAPI ingestion + query API.

- GET  /tracker.js              — the capture snippet itself, for <script src="">
- POST /ingest                  — the capture snippet posts session events here
- POST /webhooks/shopify/orders — Shopify posts here the moment an order completes
- POST /threat-runs             — the harness logs manual/automated test runs here
- GET  /health                  — sanity check
"""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession
from starlette.middleware.base import BaseHTTPMiddleware

from app.classify import classify_session
from app.db import Base, engine, get_db
from app.models import Order
from app.models import Session as SessionModel
from app.models import ThreatTestRun
from app.schemas import IngestEvent, ThreatTestRunIn
from app.shopify_webhooks import get_webhook_secret, parse_order_payload, verify_shopify_hmac

logger = logging.getLogger("agent_trust.webhooks")
cors_logger = logging.getLogger("agent_trust.cors")

load_dotenv()

app = FastAPI(title="Agent Trust & Commerce Intelligence — ingestion API")

# Loose CORS for v0 — tighten to SITE_ORIGIN once this is pointed at the
# real United Cellars domain.
site_origin = os.getenv("SITE_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[site_origin] if site_origin != "*" else ["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


class OriginLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request's Origin header before CORSMiddleware gets a
    chance to reject it (with a 400) — which is exactly the case where the
    request never reaches a route handler, so nothing else ever sees it.

    Deliberately doesn't change what's allowed, only what's visible: a
    site whose whole purpose is trustworthy traffic signal shouldn't loosen
    CORS on its own ingestion endpoint just to see who got rejected. Added
    *after* CORSMiddleware so it wraps it (outer middleware runs first) and
    sees the request regardless of what CORSMiddleware does with it.
    """

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != site_origin and site_origin != "*":
            cors_logger.info(
                "Cross-origin request (blocked by CORS): origin=%s method=%s path=%s ua=%s",
                origin,
                request.method,
                request.url.path,
                request.headers.get("user-agent"),
            )
        return await call_next(request)


app.add_middleware(OriginLoggingMiddleware)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/tracker.js")
def tracker_js():
    # Served explicitly (not via StaticFiles mount) so the URL is exactly
    # /tracker.js, matching the <script src="..."> in the README/theme
    # snippet, rather than /static/tracker.js.
    return FileResponse(
        STATIC_DIR / "tracker.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.post("/ingest")
def ingest(event: IngestEvent, db: DBSession = Depends(get_db)):
    now = datetime.now(timezone.utc)

    session = (
        db.query(SessionModel)
        .filter(SessionModel.session_key == event.session_key)
        .one_or_none()
    )
    if session is None:
        session = SessionModel(
            session_key=event.session_key,
            user_agent=event.user_agent,
            referrer=event.referrer,
            landing_path=event.landing_path,
            first_seen=now,
            event_count=0,
        )
        db.add(session)

    session.last_seen = now
    # Python-side column defaults only apply on flush, so a freshly
    # constructed session's event_count is explicitly seeded above —
    # this `or 0` is just belt-and-braces against that same class of bug.
    session.event_count = (session.event_count or 0) + event.event_count
    if event.js_executed:
        session.js_executed = True
    if event.cart_value is not None:
        session.cart_value = event.cart_value
    if event.age_gate_shown:
        session.age_gate_shown = True
    if event.age_gate_passed is not None:
        session.age_gate_passed = event.age_gate_passed
    if event.cf_bot_category:
        session.cf_bot_category = event.cf_bot_category

    if event.checkout_started and session.checkout_started_at is None:
        session.checkout_started_at = now
    if event.checkout_completed and session.checkout_completed_at is None:
        session.checkout_completed_at = now
        if session.checkout_started_at is not None:
            started_at = session.checkout_started_at
            # SQLite drops tzinfo on round-trip (Postgres's timestamptz
            # won't) — normalise so this subtraction is safe on either.
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            session.time_to_checkout_seconds = (now - started_at).total_seconds()

    result = classify_session(
        user_agent=session.user_agent,
        js_executed=session.js_executed,
        event_count=session.event_count,
        time_to_checkout_seconds=session.time_to_checkout_seconds,
        cart_value=session.cart_value,
        cf_bot_category=session.cf_bot_category,
    )
    session.is_agent = result.is_agent
    session.agent_family = result.agent_family
    session.classification_confidence = result.confidence
    session.classification_reasons = result.reasons_csv

    db.commit()
    db.refresh(session)

    return {
        "session_key": session.session_key,
        "is_agent": session.is_agent,
        "agent_family": session.agent_family,
        "confidence": session.classification_confidence,
    }


@app.post("/webhooks/shopify/orders")
async def shopify_order_webhook(
    request: Request,
    db: DBSession = Depends(get_db),
    x_shopify_hmac_sha256: str | None = Header(default=None),
    x_shopify_topic: str | None = Header(default=None),
):
    raw_body = await request.body()
    secret = get_webhook_secret()

    if not verify_shopify_hmac(raw_body, x_shopify_hmac_sha256, secret):
        # Fails closed: an unconfigured secret (empty string) also fails
        # verification, so this can't be silently bypassed by leaving
        # SHOPIFY_WEBHOOK_SECRET unset.
        logger.warning("Rejected Shopify webhook with invalid HMAC (topic=%s)", x_shopify_topic)
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    payload = await request.json()
    order_in = parse_order_payload(payload)

    existing = (
        db.query(Order)
        .filter(Order.shopify_order_id == order_in.shopify_order_id)
        .one_or_none()
    )
    if existing is not None:
        # Shopify retries webhooks on timeout/non-2xx; treat a duplicate
        # delivery as a no-op rather than erroring or double-counting.
        return {"status": "already_recorded", "order_id": existing.id}

    order = Order(
        shopify_order_id=order_in.shopify_order_id,
        session_key=order_in.session_key,
        order_value=order_in.order_value,
        currency=order_in.currency,
        shipping_state=order_in.shipping_state,
        allocation_flagged=order_in.allocation_flagged,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    return {"status": "recorded", "order_id": order.id, "session_matched": order.session_key is not None}


@app.post("/threat-runs")
def log_threat_run(run: ThreatTestRunIn, db: DBSession = Depends(get_db)):
    row = ThreatTestRun(**run.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "run_at": row.run_at}
