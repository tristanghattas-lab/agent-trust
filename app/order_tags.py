"""
Ask the Shopify app to tag a new order with its AI origin.

Tagging needs the store's Admin API token, which only the Shopify app holds,
so the API calls the app (POST /api/tag-order, authenticated with the shared
METRICS_API_KEY) and the app reads the verdict back from /metrics/orders/{id}
and writes the tags. The call waits a minute first so checkout-pixel steps
that arrive just after the order webhook count towards placed vs assisted.
Best effort: a failure is logged, never raised; tags are also refreshed
whenever the merchant opens the order in Shopify admin.
"""
from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

APP_URL = os.getenv("AGENT_TRUST_APP_URL", "https://agent-trust-shopify.onrender.com").rstrip("/")
DELAY_SECONDS = float(os.getenv("ORDER_TAG_DELAY_SECONDS", "60"))


def request_tags(shop: str, shopify_order_id: str | None, delay: float | None = None) -> bool:
    key = os.getenv("METRICS_API_KEY", "")
    if os.getenv("ORDER_TAGGING", "on").lower() == "off":
        return False
    if not (key and shop and shopify_order_id):
        return False
    time.sleep(DELAY_SECONDS if delay is None else delay)
    try:
        r = requests.post(f"{APP_URL}/api/tag-order", json={"shop": shop, "order_id": str(shopify_order_id)},
                          headers={"Authorization": f"Bearer {key}"}, timeout=60)
        if r.ok:
            logger.info("Tagged order %s on %s: %s", shopify_order_id, shop, r.text[:200])
            return True
        logger.warning("Tagging order %s failed: %s %s", shopify_order_id, r.status_code, r.text[:200])
    except requests.RequestException as exc:
        logger.warning("Tagging order %s failed: %s", shopify_order_id, exc)
    return False
