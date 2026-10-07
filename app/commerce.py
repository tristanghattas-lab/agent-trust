"""
Events from inside Shopify, via the Agent Trust Shopify app.

POST /webhooks/shopify/commerce   carts/create, carts/update, checkouts/create,
                                  checkouts/update. Signed by Shopify with the
                                  app's secret (listed in SHOPIFY_WEBHOOK_SECRET).
POST /pixel/events                checkout step timings from the app's web pixel.
                                  Public like the tracker; text/plain JSON so the
                                  sandboxed pixel needs no CORS preflight.

Only tokens, totals, item counts, step names and times are stored. Never
names, emails, addresses or payment details.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import CommerceEvent, WebhookLog
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


def event_from_webhook(shop: str, topic: str, payload: dict, webhook_id: str | None) -> CommerceEvent:
    items = payload.get("line_items") or []
    total = _float(payload.get("total_price"))
    if total is None and items:
        total = round(sum(_line_total(i) for i in items), 2)
    is_cart = topic.startswith("carts/")
    return CommerceEvent(
        shop_domain=shop, source="webhook", topic=topic, webhook_id=webhook_id,
        token=str(payload.get("token") or payload.get("id") or "")[:100] or None,
        cart_token=str(payload.get("token") if is_cart else payload.get("cart_token") or "")[:100] or None,
        session_key=_session_key(payload),
        order_id=str(payload["order_id"]) if payload.get("order_id") else None,
        total=total,
        item_count=sum(int(i.get("quantity") or 1) for i in items) if items else 0,
        source_name=(str(payload.get("source_name"))[:100] if payload.get("source_name") else None),
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
