"""
Agent shelf test: would an AI shopping agent find this store's products?

We send realistic shopper requests to the store's own agent search (Shopify's
UCP catalog search, the endpoint ChatGPT and other agents call) and score
the top three results:

    found    a suitable, in-stock product within the request's limits
    partial  suitable products, but a detail the shopper asked for (a critic
             score, cellaring life) isn't in the data the agent gets
    missed   nothing suitable in the top three

"Suitable" is judged by the store's own collections: a request for rosé is
answered well by a product in the rosé collection, under the price limit and
in stock. For each request we also list in-stock products that would have
fitted but didn't come back: the sales an agent can't make.

Requests come from the store's own range (its biggest collections and their
price bands) plus a vertical pack where we know the category well (wine).
Everything here is read-only: public catalogue endpoints and catalog search.
No carts, checkouts or orders are created.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

PROFILE_URL = os.getenv("UCP_AGENT_PROFILE",
                        "https://agent-trust-api-o7u9.onrender.com/ucp-agent.json")
TIMEOUT = 25
TOP_N = 3
MAX_AUTO_REQUESTS = 14

# Collections that are navigation, promotions or internal plumbing, not a
# kind of product a shopper would ask for.
JUNK_COLLECTION = re.compile(
    r"^(all|all-products|frontpage|home|catalog|sale|new|new-releases|best-?sellers?|featured|thank-you|"
    r".*(boxing|black-friday|cyber|event|ex-events|clearance|offer|deal|gift-card|test|hidden|archive|"
    r"subscription|email|newsletter|instagram|tiktok|google|facebook|feed).*)$", re.I)
# Tags that look like internal codes rather than words a shopper would use.
INTERNAL_TAG = re.compile(r"^(?:[A-Za-z]+save|\d+-x-\d+|[A-Z]{2,5}|.*-list|available|instock|out of stock|"
                          r".*-(certified|imports?|ex-events)|[a-z]{2,4}\d*)$", re.I)
COMMON_WORDS = {"red", "gin", "rum", "rose", "port", "cava", "gift", "sale", "new", "wine", "beer", "oak", "dry",
                "sweet", "vegan", "nz", "usa"}


def is_internal_tag(tag: str) -> bool:
    t = tag.strip()
    return bool(INTERNAL_TAG.match(t)) and t.lower() not in COMMON_WORDS
SCORE_TEXT = re.compile(r"\b(9\d|100)\s*(?:points|pts)\b|\b(9\d|100)\s*/\s*100\b", re.I)


@dataclass
class ShopperRequest:
    id: str
    query: str
    collections: list[str]            # any of these counts as suitable
    max_price: float | None = None
    needs: list[str] = field(default_factory=list)   # details the shopper asked for: "score", "cellar"
    source: str = "auto"


# Wine pack: the requests a fine-wine shopper actually makes. Collection
# handles are the common Shopify ones; a request only runs if the store has
# at least one of its collections.
WINE_PACK = [
    ShopperRequest("w-shiraz-60", "Shiraz under $60 for a dinner party", ["shiraz"], 60, source="wine"),
    ShopperRequest("w-champ-80", "Champagne for a wedding toast around $80 a bottle", ["champagne"], 100, source="wine"),
    ShopperRequest("w-organic-40", "Organic red wine under $40", ["organic-wines", "organic"], 40, source="wine"),
    ShopperRequest("w-noalc", "Non-alcoholic sparkling wine", ["non-alcohol", "non-alcoholic", "alcohol-free"], source="wine"),
    ShopperRequest("w-otago", "Central Otago Pinot Noir", ["central-otago"], source="wine"),
    ShopperRequest("w-mr-cab-95", "Margaret River Cabernet with a 95 point score", ["margaret-river"], needs=["score"], source="wine"),
    ShopperRequest("w-cellar-150", "Red wine to cellar for 10 years under $150", ["red-wine"], 150, needs=["cellar"], source="wine"),
    ShopperRequest("w-burg-chard", "Burgundy Chardonnay", ["burgundy"], source="wine"),
    ShopperRequest("w-whisky-300", "Whisky gift around $300", ["whisky", "spirits"], 400, source="wine"),
    ShopperRequest("w-riesling", "Riesling to drink with Thai food", ["riesling"], source="wine"),
    ShopperRequest("w-rose-35", "Rosé for summer under $35", ["rose-wine", "rose"], 35, source="wine"),
    ShopperRequest("w-barossa-40", "Barossa Shiraz under $40", ["shiraz"], 40, source="wine"),
    ShopperRequest("w-tuscan-100", "Tuscan red under $100", ["tuscany"], 100, source="wine"),
    ShopperRequest("w-champ-60", "Best value Champagne under $60", ["champagne"], 60, source="wine"),
    ShopperRequest("w-sparkling-30", "Sparkling wine under $30 for a party", ["sparkling"], 30, source="wine"),
    ShopperRequest("w-pinot-50", "Pinot Noir under $50", ["pinot-noir"], 50, source="wine"),
]


# ---------------------------------------------------------------------------
# Catalogue (public storefront endpoints)
# ---------------------------------------------------------------------------
def _get(url: str, **kw):
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "AgentTrust-ShelfTest/1.0"}, **kw)
    r.raise_for_status()
    return r.json()


def fetch_catalogue(domain: str, max_pages: int = 20) -> list[dict]:
    products = []
    for page in range(1, max_pages + 1):
        batch = _get(f"https://{domain}/products.json", params={"limit": 250, "page": page}).get("products", [])
        if not batch:
            break
        products.extend(batch)
    return products


def fetch_collections(domain: str, max_pages: int = 4) -> list[dict]:
    cols = []
    for page in range(1, max_pages + 1):
        batch = _get(f"https://{domain}/collections.json", params={"limit": 250, "page": page}).get("collections", [])
        if not batch:
            break
        cols.extend(batch)
    return cols


def fetch_collection_members(domain: str, handle: str, max_pages: int = 6) -> list[dict]:
    out = []
    for page in range(1, max_pages + 1):
        try:
            batch = _get(f"https://{domain}/collections/{handle}/products.json",
                         params={"limit": 250, "page": page}).get("products", [])
        except requests.RequestException:
            break
        if not batch:
            break
        out.extend(batch)
    return out


def _price(p: dict) -> float:
    prices = [float(v.get("price") or 0) for v in p.get("variants", [])]
    return min(prices) if prices else 0.0


def _available(p: dict) -> bool:
    return any(v.get("available") for v in p.get("variants", []))


def _nice_price(x: float) -> int:
    for step in (10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if x <= step * 4:
            return int(round(x / step) * step) or step
    return int(round(x, -3))


def build_requests(collections: list[dict], members: dict[str, list[dict]]) -> list[ShopperRequest]:
    """Auto requests from the store's biggest real collections, plus the wine
    pack when the store sells wine."""
    handles = {c["handle"] for c in collections}
    reqs: list[ShopperRequest] = []
    is_wine = any("wine" in h for h in handles) and any(h in handles for h in ("red-wine", "shiraz", "champagne"))
    if is_wine:
        reqs += [r for r in WINE_PACK if any(h in handles for h in r.collections)]
    ranked = sorted(
        (c for c in collections if not JUNK_COLLECTION.match(c["handle"]) and members.get(c["handle"])),
        key=lambda c: -len([p for p in members[c["handle"]] if _available(p)]))
    used = {h for r in reqs for h in r.collections}
    for c in ranked:
        if len([r for r in reqs if r.source == "auto"]) >= MAX_AUTO_REQUESTS:
            break
        if c["handle"] in used:
            continue
        stock = [p for p in members[c["handle"]] if _available(p)]
        if len(stock) < 3:
            continue
        median = statistics.median(_price(p) for p in stock)
        reqs.append(ShopperRequest(f"a-{c['handle']}", c["title"], [c["handle"]], source="auto"))
        if median > 0:
            cap = _nice_price(median)
            reqs.append(ShopperRequest(f"a-{c['handle']}-u{cap}", f"{c['title']} under ${cap}", [c["handle"]], cap,
                                       source="auto"))
        used.add(c["handle"])
    return reqs


# ---------------------------------------------------------------------------
# Agent search (UCP catalog search over MCP)
# ---------------------------------------------------------------------------
def ucp_search(domain: str, query: str, country: str = "AU", currency: str = "AUD", limit: int = 5) -> list[dict]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_catalog", "arguments": {
        "meta": {"ucp-agent": {"profile": PROFILE_URL}},
        "catalog": {"query": query, "context": {"address_country": country, "currency": currency},
                    "pagination": {"limit": limit}}}}}
    r = requests.post(f"https://{domain}/api/ucp/mcp", json=body, timeout=TIMEOUT,
                      headers={"Accept": "application/json", "User-Agent": "AgentTrust-ShelfTest/1.0"})
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"agent search error: {j['error'].get('message')} {j['error'].get('data', '')}")
    payload = json.loads(j["result"]["content"][0]["text"])
    out = []
    for p in payload.get("products", []):
        amount = ((p.get("price_range") or {}).get("min") or {}).get("amount")
        out.append({
            "handle": p.get("handle"), "title": p.get("title"),
            "price": (amount / 100) if isinstance(amount, (int, float)) else None,
            "available": any((v.get("availability") or {}).get("available") for v in p.get("variants", [])),
            "collections": [c.get("handle") for c in p.get("collections", [])],
            "description": re.sub(r"<[^>]+>", " ", (p.get("description") or {}).get("html") or ""),
            "url": p.get("url"),
        })
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_request(req: ShopperRequest, results: list[dict], members: dict[str, list[dict]]) -> dict:
    allowed = set()
    for h in req.collections:
        allowed |= {p["handle"] for p in members.get(h, [])}

    def suits(r: dict) -> bool:
        in_col = bool(set(r["collections"]) & set(req.collections)) or r["handle"] in allowed
        under = req.max_price is None or (r["price"] is not None and r["price"] <= req.max_price)
        return in_col and under and r["available"]

    top = results[:TOP_N]
    good = [r for r in top if suits(r)]
    verdict = "found" if good else "missed"
    unmet = []
    if good and req.needs:
        for need in req.needs:
            if need == "score" and not any(SCORE_TEXT.search(r["description"]) for r in good):
                unmet.append("critic scores aren't in the data agents get")
            if need == "cellar" and not any(re.search(r"cellar|drink (now|by|until)|\d+\s*\+?\s*years", r["description"], re.I)
                                            for r in good):
                unmet.append("cellaring life isn't in the data agents get")
        if unmet:
            verdict = "partial"
    # In-stock products that would have suited but didn't come back.
    shown = {r["handle"] for r in results}
    pool = {}
    for h in req.collections:
        for p in members.get(h, []):
            if p["handle"] in shown or not _available(p):
                continue
            if req.max_price is not None and _price(p) > req.max_price:
                continue
            pool[p["handle"]] = {"handle": p["handle"], "title": p["title"], "price": _price(p)}
    missed_products = sorted(pool.values(), key=lambda p: -p["price"] if req.max_price else p["price"])[:5]
    return {
        "id": req.id, "query": req.query, "source": req.source, "collections": req.collections,
        "max_price": req.max_price, "verdict": verdict, "unmet": unmet,
        "top": [{k: r[k] for k in ("title", "price", "available", "handle", "url")} | {"suits": suits(r)} for r in top],
        "missed_products": missed_products if verdict != "found" else [],
        "suitable_in_stock": len(pool) + len(good),
    }


def catalogue_health(products: list[dict], collections: list[dict]) -> dict:
    total = len(products)
    no_type = [p for p in products if not (p.get("product_type") or "").strip()]
    tags: dict[str, int] = {}
    for p in products:
        for t in p.get("tags", []):
            tags[t] = tags.get(t, 0) + 1
    internal_tags = sorted((t for t in tags if is_internal_tag(t)), key=lambda t: -tags[t])
    internal_cols = [c["handle"] for c in collections if JUNK_COLLECTION.match(c["handle"])
                     and c["handle"] not in ("all", "frontpage", "new-releases")]
    with_score = sum(1 for p in products if SCORE_TEXT.search(p.get("body_html") or ""))
    return {
        "products": total, "in_stock": sum(1 for p in products if _available(p)),
        "no_product_type": len(no_type), "no_product_type_share": round(len(no_type) / total, 3) if total else None,
        "scores_in_description": with_score,
        "internal_tags": [{"tag": t, "products": tags[t]} for t in internal_tags[:25]],
        "internal_collections": internal_cols[:25],
    }


# ---------------------------------------------------------------------------
# A full run
# ---------------------------------------------------------------------------
def run_shelf_test(domain: str, country: str = "AU", currency: str = "AUD") -> dict:
    """Run every request against the store's agent search. `domain` is the
    store's myshopify or storefront domain."""
    started = datetime.now(timezone.utc)
    products = fetch_catalogue(domain)
    collections = fetch_collections(domain)
    handles = [c["handle"] for c in collections]
    pack = {h for r in WINE_PACK for h in r.collections}
    # Members for the wine-pack collections and up to 40 other real ones
    # (each is a few public requests, so keep the run to a couple of minutes).
    others = [h for h in handles if h not in pack and not JUNK_COLLECTION.match(h)][:40]
    members = {h: fetch_collection_members(domain, h, max_pages=4) for h in [*[h for h in handles if h in pack], *others]}
    reqs = build_requests(collections, members)
    results = []
    for req in reqs:
        try:
            res = ucp_search(domain, req.query, country, currency)
            results.append(score_request(req, res, members))
        except Exception as exc:  # noqa: BLE001 - one bad request shouldn't stop the run
            logger.warning("shelf request %s failed: %s", req.id, exc)
            results.append({"id": req.id, "query": req.query, "source": req.source, "verdict": "error",
                            "error": str(exc)[:200], "top": [], "missed_products": []})
    scored = [r for r in results if r["verdict"] != "error"]
    counts = {v: sum(1 for r in scored if r["verdict"] == v) for v in ("found", "partial", "missed")}
    return {
        "domain": domain, "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "requests": len(results), "scored": len(scored), **counts,
        "score": round((counts["found"] + 0.5 * counts["partial"]) / len(scored) * 100) if scored else None,
        "results": sorted(results, key=lambda r: {"missed": 0, "partial": 1, "error": 2, "found": 3}[r["verdict"]]),
        "health": catalogue_health(products, collections),
    }


# ---------------------------------------------------------------------------
# Storage and scheduling
# ---------------------------------------------------------------------------
_running: set[str] = set()


def save_run(shop: str, run: dict) -> str:
    from app.db import SessionLocal
    from app.models import ShelfRun

    with SessionLocal() as db:
        row = ShelfRun(shop_domain=shop, finished_at=datetime.fromisoformat(run["finished_at"]),
                       score=run["score"], found=run["found"], partial=run["partial"], missed=run["missed"],
                       requests=run["requests"], result=json.dumps(run))
        db.add(row)
        db.commit()
        return row.id


def run_and_save(shop: str, domain: str | None = None) -> None:
    if shop in _running:
        return
    _running.add(shop)
    try:
        save_run(shop, run_shelf_test(domain or shop))
    except Exception:  # noqa: BLE001
        logger.exception("shelf test failed for %s", shop)
    finally:
        _running.discard(shop)


def start_in_background(shop: str, domain: str | None = None) -> bool:
    if shop in _running:
        return False
    threading.Thread(target=run_and_save, args=(shop, domain), daemon=True).start()
    return True


def latest(shop: str, history: int = 12) -> dict:
    from app.db import SessionLocal
    from app.models import ShelfRun

    with SessionLocal() as db:
        rows = (db.query(ShelfRun).filter(ShelfRun.shop_domain == shop)
                .order_by(ShelfRun.finished_at.desc()).limit(history).all())
        if not rows:
            return {"shop": shop, "status": "running" if shop in _running else "never", "run": None, "history": []}
        last = rows[0]
        fin = last.finished_at if last.finished_at.tzinfo else last.finished_at.replace(tzinfo=timezone.utc)
        return {
            "shop": shop, "status": "running" if shop in _running else "done",
            "stale": datetime.now(timezone.utc) - fin > timedelta(days=7),
            "run": json.loads(last.result),
            "history": [{"at": (r.finished_at.isoformat()), "score": r.score, "found": r.found,
                         "partial": r.partial, "missed": r.missed, "requests": r.requests} for r in reversed(rows)],
        }
