"""
Events from inside Shopify, via the Agent Trust Shopify app.

POST /webhooks/shopify/commerce   carts/create, carts/update, checkouts/create,
                                  checkouts/update. Signed by Shopify with the
                                  app's secret (listed in SHOPIFY_WEBHOOK_SECRET).
POST /pixel/events                checkout step timings from the app's web pixel.
                                  Public like the tracker; text/plain JSON so the
                                  sandboxed pixel needs no CORS preflight.
POST /journey                     page-by-page steps from the tracker (pages,
                                  searches, adds to cart, dead ends, time on page).

Only tokens, totals, item counts, step names and times are stored. Never
names, emails, addresses or payment details.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import CommerceEvent, JourneyEvent, WebhookLog
from app.shopify_webhooks import SESSION_ATTRIBUTE_NAME, get_webhook_secret, verify_shopify_hmac
from app.shops import normalise_shop

logger = logging.getLogger(__name__)
router = APIRouter(tags=["commerce"])

WEBHOOK_TOPICS = {"carts/create", "carts/update", "checkouts/create", "checkouts/update"}
PIXEL_EVENTS = {
    "checkout_started", "checkout_contact_info_submitted", "checkout_address_info_submitted",
    "checkout_shipping_info_submitted", "payment_info_submitted", "checkout_completed",
}
MAX_PIXEL_BYTES = 4096
MAX_JOURNEY_BYTES = 32768
MAX_JOURNEY_EVENTS = 60
JOURNEY_KINDS = {"page", "search", "cart_add", "cart_change", "dead_end", "leave", "cart_link"}


def log_webhook(db: DBSession, shop: str | None, topic: str | None, outcome: str, detail: str | None = None) -> None:
    """Best-effort delivery log; never breaks webhook handling."""
    try:
        db.add(WebhookLog(shop_domain=shop, topic=topic, outcome=outcome, detail=(detail or "")[:300] or None))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def _secrets() -> list[str]:
    return [x.strip() for x in get_webhook_secret().split(",") if x.strip()]


def _session_key(payload: dict) -> str | None:
    """The tracker's session tag, if the cart/checkout was built in a browser
    the tracker saw. Checkouts carry note_attributes as a list of
    {name, value}; carts carry attributes as a dict or a list of {key, value}."""
    for field in ("note_attributes", "attributes"):
        attrs = payload.get(field)
        if isinstance(attrs, dict):
            pairs = attrs.items()
        elif isinstance(attrs, list):
            pairs = ((a.get("name") or a.get("key"), a.get("value")) for a in attrs if isinstance(a, dict))
        else:
            continue
        for name, value in pairs:
            if name == SESSION_ATTRIBUTE_NAME and value:
                return str(value)[:64]
    return None


def _line_total(item: dict) -> float:
    line = _float(item.get("line_price"))
    if line is not None:
        return line
    return (_float(item.get("price")) or 0.0) * int(item.get("quantity") or 1)


def _when(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _items(items: list) -> str | None:
    """Product-level line items only (no buyer data), capped at 50 lines."""
    out = []
    for i in items[:50]:
        if not isinstance(i, dict):
            continue
        price = _float(i.get("price"))  # webhook prices are decimal strings ("45.00")
        out.append({
            "product_id": str(i.get("product_id") or "")[:40] or None,
            "variant_id": str(i.get("variant_id") or i.get("id") or "")[:40] or None,
            "title": (str(i.get("title") or i.get("product_title") or "")[:120]) or None,
            "quantity": int(i.get("quantity") or 1),
            "price": price,
        })
    return json.dumps(out) if out else None


def event_from_webhook(shop: str, topic: str, payload: dict, webhook_id: str | None) -> CommerceEvent:
    items = payload.get("line_items") or []
    total = _float(payload.get("total_price"))
    if total is None and items:
        total = round(sum(_line_total(i) for i in items), 2)
    is_cart = topic.startswith("carts/")
    return CommerceEvent(
        shop_domain=shop, source="webhook", topic=topic, webhook_id=webhook_id,
        token=str(payload.get("token") or payload.get("id") or "")[:100] or None,
        cart_token=str(payload.get("token") if is_cart else payload.get("cart_token") or "").split("?")[0][:100] or None,
        session_key=_session_key(payload),
        order_id=str(payload["order_id"]) if payload.get("order_id") else None,
        total=total,
        item_count=sum(int(i.get("quantity") or 1) for i in items) if items else 0,
        source_name=(str(payload.get("source_name"))[:100] if payload.get("source_name") else None),
        items=_items(items),
        occurred_at=_when(payload.get("updated_at") or payload.get("created_at")),
    )


@router.post("/webhooks/shopify/commerce")
async def commerce_webhook(
    request: Request,
    db: DBSession = Depends(get_db),
    x_shopify_hmac_sha256: str | None = Header(default=None),
    x_shopify_topic: str | None = Header(default=None),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_webhook_id: str | None = Header(default=None),
):
    raw = await request.body()
    topic = (x_shopify_topic or "").lower()
    shop = normalise_shop(x_shopify_shop_domain)
    secrets = _secrets()
    if not any(verify_shopify_hmac(raw, x_shopify_hmac_sha256, s) for s in secrets):
        log_webhook(db, shop, topic, "rejected", f"invalid signature ({len(secrets)} secret(s) configured)")
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    if topic not in WEBHOOK_TOPICS or not shop:
        log_webhook(db, shop, topic, "ignored")
        return {"status": "ignored"}
    if x_shopify_webhook_id and db.query(CommerceEvent).filter(
            CommerceEvent.webhook_id == x_shopify_webhook_id).first():
        return {"status": "duplicate"}
    try:
        payload = json.loads(raw)
        db.add(event_from_webhook(shop, topic, payload, x_shopify_webhook_id))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log_webhook(db, shop, topic, "error", f"{type(exc).__name__}: {exc}")
        logger.exception("commerce webhook failed")
        raise HTTPException(status_code=500, detail="could not record event") from exc
    log_webhook(db, shop, topic, "recorded")
    return {"status": "recorded"}


@router.post("/pixel/events")
async def pixel_events(request: Request, db: DBSession = Depends(get_db)):
    raw = await request.body()
    if len(raw) > MAX_PIXEL_BYTES:
        raise HTTPException(status_code=413, detail="too large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    shop = normalise_shop(body.get("shop"))
    event = body.get("event")
    if not shop or event not in PIXEL_EVENTS:
        raise HTTPException(status_code=400, detail="unknown shop or event")
    key = body.get("session_key")
    db.add(CommerceEvent(
        shop_domain=shop, source="pixel", topic=event,
        token=(str(body.get("checkout_token"))[:100] if body.get("checkout_token") else None),
        session_key=(str(key)[:64] if key and str(key).replace("_", "").replace("-", "").isalnum() else None),
        order_id=(str(body.get("order_id")).rsplit("/", 1)[-1][:40] if body.get("order_id") else None),
        occurred_at=_when(body.get("ts")),
    ))
    db.commit()
    return {"status": "recorded"}


def _clean_query(q) -> str | None:
    """Search text, shortened; dropped if it looks like an email or a long number."""
    if not q:
        return None
    q = str(q).strip()[:80]
    if "@" in q or re.search(r"\d{6,}", q):
        return "[redacted]"
    return q or None


def _int(v, lo: int = 0, hi: int = 10**9) -> int | None:
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return None


def _s(v, n: int) -> str | None:
    return str(v)[:n] if v not in (None, "") else None


@router.post("/journey")
async def journey(request: Request, db: DBSession = Depends(get_db)):
    """Batched visit steps from tracker.js (text/plain JSON, no preflight)."""
    raw = await request.body()
    if len(raw) > MAX_JOURNEY_BYTES:
        raise HTTPException(status_code=413, detail="too large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    shop = normalise_shop(body.get("shop"))
    key = str(body.get("session_key") or "")[:64]
    if not shop or not key or not key.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="unknown shop or session")
    n = 0
    for e in (body.get("events") or [])[:MAX_JOURNEY_EVENTS]:
        if not isinstance(e, dict) or e.get("kind") not in JOURNEY_KINDS:
            continue
        ts = e.get("ts")
        when = (datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                if isinstance(ts, (int, float)) and ts > 1e12 else datetime.now(timezone.utc))
        db.add(JourneyEvent(
            shop_domain=shop, session_key=key, seq=_int(e.get("seq")), kind=e["kind"],
            path=_s(e.get("path"), 300), page_type=_s(e.get("page_type"), 40),
            product=_s(e.get("product"), 120), title=_s(e.get("title"), 120),
            variant_id=_s(e.get("variant_id"), 40), quantity=_int(e.get("quantity"), 0, 10000),
            price=_float(e.get("price")), query=_clean_query(e.get("query")),
            detail=_s(e.get("detail"), 60), dwell_ms=_int(e.get("dwell_ms"), 0, 86_400_000),
            scroll_pct=_int(e.get("scroll_pct"), 0, 100), hidden_ms=_int(e.get("hidden_ms"), 0, 86_400_000),
            cart_token=(_s(e.get("cart_token"), 100) or "").split("?")[0] or None,
            occurred_at=when,
        ))
        n += 1
    db.commit()
    return {"status": "recorded", "events": n}
