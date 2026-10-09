"""
Plans: what each store can see.

    GET  /plans?shop=...                     current plan and what it unlocks
    POST /plans  {shop, plan}                switch plan (free | growth | trust)
    POST /plans  {shop, start_trial: true}   14 days of every feature, once per store

Server to server (Bearer METRICS_API_KEY), from the Shopify app. Billing
goes through Shopify once the app is publicly listed (custom-distribution
apps can't use the Billing API); until then plans are switched here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from app.backfill import require_key
from app.db import get_db
from app.models import ShopPlan
from app.shops import normalise_shop

router = APIRouter(prefix="/plans", tags=["plans"])

TRIAL_DAYS = 14
RANK = {"free": 0, "growth": 1, "trust": 2}
# What each plan unlocks (the app decides what to lock from this list).
FEATURES = {
    "free": ["overview", "scan", "missed_sales", "orders_list"],
    "growth": ["overview", "scan", "missed_sales", "orders_list", "fixes", "behaviour", "journeys", "products",
               "live", "channels"],
    "trust": ["overview", "scan", "missed_sales", "orders_list", "fixes", "behaviour", "journeys", "products",
              "live", "channels", "evidence", "review", "tags"],
}


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def describe(row: ShopPlan | None, shop: str) -> dict:
    now = datetime.now(timezone.utc)
    plan = row.plan if row else "free"
    ends = _aware(row.trial_ends_at) if row else None
    on_trial = bool(ends and ends > now)
    effective = "trust" if on_trial else plan
    return {
        "shop": shop, "plan": plan, "effective_plan": effective, "on_trial": on_trial,
        "trial_ends_at": ends.isoformat() if ends else None,
        "trial_days_left": max(0, (ends - now).days + (1 if (ends - now).seconds else 0)) if on_trial else 0,
        "trial_available": not (row and row.trial_used),
        "features": FEATURES[effective],
    }


@router.get("", dependencies=[Depends(require_key)])
def get_plan(shop: str = Query(...), db: DBSession = Depends(get_db)):
    norm = normalise_shop(shop)
    if not norm:
        raise HTTPException(status_code=400, detail="invalid shop")
    return describe(db.get(ShopPlan, norm), norm)


class PlanChange(BaseModel):
    shop: str
    plan: str | None = None
    start_trial: bool = False


@router.post("", dependencies=[Depends(require_key)])
def set_plan(body: PlanChange, db: DBSession = Depends(get_db)):
    norm = normalise_shop(body.shop)
    if not norm:
        raise HTTPException(status_code=400, detail="invalid shop")
    row = db.get(ShopPlan, norm) or ShopPlan(shop_domain=norm, plan="free")
    db.add(row)
    if body.start_trial:
        if row.trial_used:
            raise HTTPException(status_code=409, detail="trial already used")
        row.trial_used = True
        row.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)
    if body.plan:
        if body.plan not in RANK:
            raise HTTPException(status_code=400, detail="unknown plan")
        row.plan = body.plan
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return describe(row, norm)
