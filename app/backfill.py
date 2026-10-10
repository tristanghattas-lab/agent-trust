"""
Past orders, scanned when the Shopify app is installed.

    POST /backfill/orders   {shop, run_id?, orders: [...], done: bool}
    GET  /backfill/status?shop=...

The Shopify app pages through the store's recent orders (Shopify allows
about 60 days without extra permission) and posts them here in batches.
Each becomes an Order with no tracker session: its AI origin comes from the
order's own attribution (referring site, landing-page UTM tags, sales
channel), the same rules live orders use. Orders already recorded are
skipped. Product and attribution fields only; no customer details.

Auth: Bearer METRICS_API_KEY (server to server, from the Shopify app).
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import BackfillRun, Order
from app.shops import normalise_shop

router = APIRouter(prefix="/backfill", tags=["backfill"])
MAX_BATCH = 250


def require_key(request: Request, authorization: str | None = Header(default=None)) -> str:
    """The Shopify app's server or an admin tool (app/security.py)."""
    from app.security import require_app
    return require_app(request, authorization)


class PastOrder(BaseModel):
    id: str = Field(..., max_length=40)
    created_at: datetime
    total: float = 0.0
    currency: str | None = Field(None, max_length=8)
    test: bool = False
    source_name: str | None = Field(None, max_length=200)
    app: str | None = Field(None, max_length=200)          # app id and title, e.g. "123 ChatGPT"
    landing_site: str | None = Field(None, max_length=2000)
    referring_site: str | None = Field(None, max_length=2000)


class Batch(BaseModel):
    shop: str
    run_id: str | None = None
    orders: list[PastOrder] = Field(default_factory=list, max_length=MAX_BATCH)
    done: bool = False


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/orders", dependencies=[Depends(require_key)])
def backfill_orders(batch: Batch, db: DBSession = Depends(get_db)):
    shop = normalise_shop(batch.shop)
    if not shop:
        raise HTTPException(status_code=400, detail="invalid shop")
    run = db.get(BackfillRun, batch.run_id) if batch.run_id else None
    if run is None or run.shop_domain != shop:
        run = BackfillRun(shop_domain=shop)
        db.add(run)
        db.flush()
    ids = [o.id.rsplit("/", 1)[-1] for o in batch.orders]
    existing = {r[0] for r in db.query(Order.shopify_order_id).filter(Order.shopify_order_id.in_(ids)).all()} if ids else set()
    added = 0
    for o, oid in zip(batch.orders, ids):
        created = _aware(o.created_at)
        if run.oldest_order_at is None or created < _aware(run.oldest_order_at):
            run.oldest_order_at = created
        if oid in existing:
            continue
        existing.add(oid)
        db.add(Order(
            shopify_order_id=oid, shop_domain=shop, session_key=None, session_match_method="backfill",
            order_value=o.total, currency=o.currency or "AUD", created_at=created,
            source_name=o.source_name, app_id=o.app, landing_site=o.landing_site,
            referring_site=o.referring_site, is_test=o.test,
        ))
        added += 1
    run.orders_scanned = (run.orders_scanned or 0) + len(batch.orders)
    run.orders_added = (run.orders_added or 0) + added
    if batch.done:
        run.finished_at = datetime.now(timezone.utc)
    db.commit()
    return {"run_id": run.id, "added": added, "scanned": run.orders_scanned, "done": batch.done}


@router.get("/status", dependencies=[Depends(require_key)])
def backfill_status(shop: str = Query(...), db: DBSession = Depends(get_db)):
    norm = normalise_shop(shop)
    run = (db.query(BackfillRun).filter(BackfillRun.shop_domain == norm)
           .order_by(BackfillRun.started_at.desc()).first())
    if run is None:
        return {"shop": norm, "status": "never"}
    return {"shop": norm, "status": "done" if run.finished_at else "running", "run_id": run.id,
            "started_at": run.started_at, "finished_at": run.finished_at,
            "orders_scanned": run.orders_scanned, "orders_added": run.orders_added,
            "oldest_order_at": run.oldest_order_at}



# ---------------------------------------------------------------------------
# Agent shelf test (app/shelf.py): start a run, read the latest
# ---------------------------------------------------------------------------
shelf_router = APIRouter(prefix="/shelf", tags=["shelf"])


class ShelfStart(BaseModel):
    shop: str
    domain: str | None = None


SHELF_COOLDOWN_MINUTES = 30


@shelf_router.post("/run")
def shelf_run(body: ShelfStart, role: str = Depends(require_key)):
    """Start a run. The domain must be the store's own (its myshopify domain,
    or a storefront domain whose public meta names that store), so this
    can't be pointed at someone else's site. One run per store every 30
    minutes, except for admin tools."""
    from app import shelf
    shop = normalise_shop(body.shop)
    if not shop:
        raise HTTPException(status_code=400, detail="invalid shop")
    domain = (body.domain or shop).strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if domain != shop:
        try:
            meta = shelf._get(f"https://{domain}/meta.json")
            owner = (meta.get("myshopify_domain") or "").lower()
        except Exception:  # noqa: BLE001
            owner = ""
        if owner != shop:
            raise HTTPException(status_code=400, detail="domain doesn't belong to this store")
    if role != "admin":
        last = shelf.latest(shop, history=1)
        run = last.get("run") or {}
        fin = run.get("finished_at")
        if fin:
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            if _dt.now(_tz.utc) - _dt.fromisoformat(fin) < _td(minutes=SHELF_COOLDOWN_MINUTES):
                raise HTTPException(status_code=429, detail=f"one run every {SHELF_COOLDOWN_MINUTES} minutes")
    started = shelf.start_in_background(shop, domain)
    return {"shop": shop, "started": started, "status": "running"}


@shelf_router.get("", dependencies=[Depends(require_key)])
def shelf_latest(shop: str = Query(...), auto: bool = True):
    """Latest run and score history. With auto, a store never tested, or last
    tested over a week ago, gets a new run in the background."""
    from app import shelf
    norm = normalise_shop(shop)
    if not norm:
        raise HTTPException(status_code=400, detail="invalid shop")
    out = shelf.latest(norm)
    if auto and (out["status"] == "never" or out.get("stale")):
        if shelf.start_in_background(norm):
            out["status"] = "running"
    return out
