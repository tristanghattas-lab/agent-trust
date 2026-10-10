"""
Shopify's mandatory privacy webhooks, as the API's side of them.

The Shopify app receives customers/data_request, customers/redact and
shop/redact (signed by Shopify, see shopify-app/app/routes/webhooks.compliance.tsx)
and forwards them here:

    POST /privacy/customers-data-request  {shop, customer: {email}, orders_requested: [ids]}
    POST /privacy/customers-redact        {shop, customer: {email}, orders_to_redact: [ids]}
    POST /privacy/shop-redact             {shop}

What we hold about a customer: the email and evidence fields on their orders
(address line 2 and order note, kept for flags such as discount requests).
Behaviour data is keyed to random session ids, never to a person.
Auth: Bearer METRICS_API_KEY (server to server).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DBSession

from app.backfill import require_key
from app.db import get_db
from app import models
from app.shops import normalise_shop

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/privacy", tags=["privacy"])

SHOP_TABLES = [models.Session, models.Order, models.ThreatTestRun, models.Integration, models.EdgeAggregate,
               models.CommerceEvent, models.JourneyEvent, models.WebhookLog, models.BackfillRun, models.ShopPlan,
               models.ShelfRun]


class Customer(BaseModel):
    email: str | None = None


class CustomerRequest(BaseModel):
    shop: str
    customer: Customer = Field(default_factory=Customer)
    orders_requested: list[int | str] = Field(default_factory=list)
    orders_to_redact: list[int | str] = Field(default_factory=list)


def _orders(db: DBSession, shop: str, email: str | None, ids: list) -> list:
    conds = []
    if ids:
        conds.append(models.Order.shopify_order_id.in_([str(i) for i in ids]))
    if email:
        conds.append(func.lower(models.Order.customer_email) == email.lower())
    if not conds:
        return []
    return db.query(models.Order).filter(models.Order.shop_domain == shop, or_(*conds)).all()


@router.post("/customers-data-request", dependencies=[Depends(require_key)])
def customers_data_request(body: CustomerRequest, db: DBSession = Depends(get_db)):
    shop = normalise_shop(body.shop)
    rows = _orders(db, shop, body.customer.email, body.orders_requested)
    logger.info("Data request for %s on %s: %d orders", body.customer.email, shop, len(rows))
    return {"shop": shop, "orders": [
        {"shopify_order_id": o.shopify_order_id, "created_at": o.created_at, "order_value": o.order_value,
         "customer_email": o.customer_email, "evidence": o.order_evidence} for o in rows]}


@router.post("/customers-redact", dependencies=[Depends(require_key)])
def customers_redact(body: CustomerRequest, db: DBSession = Depends(get_db)):
    shop = normalise_shop(body.shop)
    rows = _orders(db, shop, body.customer.email, body.orders_to_redact)
    for o in rows:
        o.customer_email = None
        o.order_evidence = None
    db.commit()
    logger.info("Redacted %d orders on %s", len(rows), shop)
    return {"shop": shop, "redacted_orders": len(rows)}


class ShopRequest(BaseModel):
    shop: str


@router.post("/shop-redact", dependencies=[Depends(require_key)])
def shop_redact(body: ShopRequest, db: DBSession = Depends(get_db)):
    """48 hours after uninstall Shopify asks for the store's data to go: all of it."""
    shop = normalise_shop(body.shop)
    deleted = {}
    order_ids = db.query(models.Order.id).filter(models.Order.shop_domain == shop)
    deleted["outcomes"] = db.query(models.Outcome).filter(models.Outcome.order_id.in_(order_ids)).delete(
        synchronize_session=False)
    for m in SHOP_TABLES:
        if hasattr(m, "shop_domain"):
            deleted[m.__tablename__] = db.query(m).filter(m.shop_domain == shop).delete(synchronize_session=False)
    db.commit()
    logger.info("Shop redact for %s: %s", shop, deleted)
    return {"shop": shop, "deleted": deleted}
