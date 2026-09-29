"""
Shopify order webhook handling.

Why a webhook and not client-side JS: Shopify's checkout is hosted by
Shopify, not the store's theme, so tracker.js (which runs on theme
pages) never executes on the checkout flow. The webhook is server-to-
server and fires the moment an order is actually created — it's the
real signal, not an inference from client-side timing.

Session matching (order -> the session that produced it) isn't wired
up yet: that needs tracker.js to write our session_key into the
Shopify cart's attributes (via the AJAX Cart API, POST /cart/update.js)
so it survives into the order as a note_attribute. Not built for v0 —
orders land with session_key=None until that's added, which is enough
to validate the webhook pipeline itself before adding that extra piece.
"""
import hashlib
import hmac
import base64
import os

from app.schemas import OrderIn

SESSION_ATTRIBUTE_NAME = "agent_trust_session"


def verify_shopify_hmac(raw_body: bytes, hmac_header: str | None, secret: str) -> bool:
    """Shopify signs the raw request body with the webhook secret (HMAC-SHA256,
    base64-encoded) and sends it in the X-Shopify-Hmac-Sha256 header. Verify
    against the RAW bytes, not a re-serialised copy of the parsed JSON —
    re-serialising can reorder keys or change whitespace and silently break
    the comparison.
    """
    if not hmac_header or not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, hmac_header)


def parse_order_payload(payload: dict) -> OrderIn:
    """Pull the fields we care about out of Shopify's order webhook payload.

    Reference: https://shopify.dev/docs/api/webhooks?reference=toml#list-of-topics-orders/create
    """
    shipping_address = payload.get("shipping_address") or {}
    note_attributes = payload.get("note_attributes") or []

    session_key = None
    for attr in note_attributes:
        if attr.get("name") == SESSION_ATTRIBUTE_NAME:
            session_key = attr.get("value")
            break

    return OrderIn(
        shopify_order_id=str(payload["id"]),
        order_value=float(payload.get("total_price", 0.0)),
        currency=payload.get("currency", "AUD"),
        shipping_state=shipping_address.get("province_code"),
        session_key=session_key,
        allocation_flagged=False,  # no allocation logic on a generic dev store
    )


def get_webhook_secret() -> str:
    return os.getenv("SHOPIFY_WEBHOOK_SECRET", "")
