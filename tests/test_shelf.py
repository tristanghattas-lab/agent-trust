"""Agent shelf test against a fake Shopify store (no network)."""
import json

from app import shelf


def _p(handle, title, price, available=True, ptype="", tags=(), body=""):
    return {"handle": handle, "title": title, "product_type": ptype, "tags": list(tags), "body_html": body,
            "variants": [{"price": str(price), "available": available}]}


CATALOGUE = [
    _p("turkey-flat-rose", "Turkey Flat Rosé 2026", 25.99, tags=["Augustsave"]),
    _p("mezzo-rose", "Mezzo Rosé 2025", 17.99, ptype="Rosé"),
    _p("pannell-fiano", "SC Pannell Fi Fi Fiano 2025", 29.99),
    _p("mumm", "Mumm Cordon Rouge NV", 59.99, tags=["BBR"]),
    _p("drappier", "Drappier 1er Cru Brut NV", 89.99),
    _p("voyager", "Voyager MJW Cabernet 2014", 159.99, body="Margaret River. 93 points Wine & Spirits."),
    _p("secret-mr", "Secret Deal Margaret River Cab Merlot", 16.99),
]
COLLECTIONS = [{"handle": h, "title": t} for h, t in [
    ("rose-wine", "Rosé"), ("champagne", "Champagne"), ("red-wine", "Red Wine"), ("margaret-river", "Margaret River"),
    ("white-wine", "White Wine"), ("shiraz", "Shiraz"), ("thank-you", "Thank you"), ("wine", "Wine")]]
MEMBERS = {
    "rose-wine": ["turkey-flat-rose", "mezzo-rose"], "champagne": ["mumm", "drappier"],
    "red-wine": ["voyager", "secret-mr"], "margaret-river": ["voyager", "secret-mr"],
    "white-wine": ["pannell-fiano"], "shiraz": [], "thank-you": [], "wine": [],
}
BY = {p["handle"]: p for p in CATALOGUE}
# What the agent search returns per query (handles, best first).
SEARCH = {
    "Rosé for summer under $35": ["mezzo-rose", "pannell-fiano"],
    "Best value Champagne under $60": ["drappier"],
    "Champagne for a wedding toast around $80 a bottle": ["drappier", "mumm"],
    "Margaret River Cabernet with a 95 point score": ["secret-mr", "voyager"],
    "Red wine to cellar for 10 years under $150": ["secret-mr"],
}


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def _fake_get(url, timeout=None, headers=None, params=None):
    page = (params or {}).get("page", 1)
    if page > 1:
        return _Resp({"products": [], "collections": []})
    if url.endswith("/products.json") and "/collections/" not in url:
        return _Resp({"products": CATALOGUE})
    if url.endswith("/collections.json"):
        return _Resp({"collections": COLLECTIONS})
    handle = url.split("/collections/")[1].split("/")[0]
    return _Resp({"products": [BY[h] for h in MEMBERS.get(handle, [])]})


def _fake_post(url, json=None, timeout=None, headers=None):
    q = json["params"]["arguments"]["catalog"]["query"]
    handles = SEARCH.get(q, [])
    products = []
    for h in handles:
        p = BY[h]
        cols = [c for c, ms in MEMBERS.items() if h in ms]
        products.append({"handle": h, "title": p["title"],
                         "price_range": {"min": {"amount": int(round(float(p["variants"][0]["price"]) * 100))}},
                         "variants": [{"availability": {"available": True}}],
                         "collections": [{"handle": c} for c in cols],
                         "description": {"html": p["body_html"]}})
    text = __import__("json").dumps({"products": products})
    return _Resp({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}})


def test_shelf_run_scores_and_misses(monkeypatch):
    monkeypatch.setattr(shelf.requests, "get", _fake_get)
    monkeypatch.setattr(shelf.requests, "post", _fake_post)
    run = shelf.run_shelf_test("fake.myshopify.com")
    by = {r["id"]: r for r in run["results"]}

    rose = by["w-rose-35"]
    assert rose["verdict"] == "found"  # Mezzo is a rosé under $35

    champ = by["w-champ-60"]
    assert champ["verdict"] == "missed"
    assert [p["title"] for p in champ["missed_products"]] == ["Mumm Cordon Rouge NV"]

    mr = by["w-mr-cab-95"]
    assert mr["verdict"] == "found"  # Voyager's description carries its score

    cellar = by["w-cellar-150"]
    assert cellar["verdict"] == "partial" and "cellaring" in cellar["unmet"][0]

    h = run["health"]
    assert h["no_product_type"] == 6 and h["scores_in_description"] == 1
    assert {"Augustsave", "BBR"} <= {t["tag"] for t in h["internal_tags"]}
    assert "thank-you" in h["internal_collections"]
    assert run["score"] is not None and run["found"] + run["partial"] + run["missed"] == run["scored"]
    json.dumps(run)  # storable


def test_auto_requests_skip_junk_collections():
    members = {h: [BY[x] for x in ms] for h, ms in MEMBERS.items()}
    reqs = shelf.build_requests(COLLECTIONS, members)
    ids = {r.id for r in reqs}
    assert not any("thank-you" in i for i in ids)
    assert "w-rose-35" in ids and "w-otago" not in ids  # pack requests need the store's collection
