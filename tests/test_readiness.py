"""Agent readiness checks (no network)."""
import json

from app import readiness

ROBOTS = """
User-agent: *
Disallow: /cart
Disallow: /checkout

User-agent: GPTBot
User-agent: PerplexityBot
Disallow: /

User-agent: OAI-SearchBot
Disallow: /collections
Allow: /products/
"""


def test_robots_rules():
    g = readiness.parse_robots(ROBOTS)
    assert readiness.allowed(g, "Googlebot", "/products/x")
    assert not readiness.allowed(g, "Googlebot", "/checkout")
    assert not readiness.allowed(g, "PerplexityBot", "/products/x")
    assert readiness.allowed(g, "OAI-SearchBot", "/products/x")
    assert not readiness.allowed(g, "OAI-SearchBot", "/collections/red")


PAGE_FULL = """<html><script type="application/ld+json">{"@context":"http://schema.org/","@type":"Product",
"name":"Boot","image":["a.jpg"],"description":"A boot","brand":{"@type":"Brand","name":"Acme"},"sku":"B1",
"offers":[{"@type":"Offer","price":"150.00","priceCurrency":"AUD","availability":"http://schema.org/InStock",
"gtin13":"9300000000001"}]}</script></html>"""
PAGE_THIN = """<script type='application/ld+json'>{"@graph":[{"@type":"ProductGroup","name":"Sock",
"hasVariant":[{"@type":"Product","offers":{"price":"12","availability":"InStock"}}]}]}</script>"""


def test_product_fields():
    full = readiness.product_fields(PAGE_FULL)
    assert all(full[k] for k in ("price", "availability", "brand", "sku", "gtin", "image", "description"))
    assert not full["rating"]
    thin = readiness.product_fields(PAGE_THIN)
    assert thin["price"] and thin["availability"] and not thin["gtin"] and not thin["brand"]
    assert readiness.product_fields("<html>no data</html>") is None


class _R:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


def _fake_get(url, timeout=None, headers=None, allow_redirects=True, **kw):
    ua = (headers or {}).get("User-Agent", "")
    if url.endswith("/robots.txt"):
        return _R(text=ROBOTS)
    if "/products/" in url:
        return _R(text=PAGE_THIN if url.endswith("sock") else PAGE_FULL)
    if "ChatGPT-User" in ua:  # Cloudflare-style block on one agent
        return _R(403, "Just a moment... challenge", {"cf-mitigated": "challenge"})
    return _R(200, "<html>home</html>")


def test_full_check(monkeypatch):
    monkeypatch.setattr(readiness.requests, "get", _fake_get)
    products = [
        {"handle": "boot", "title": "Boot", "body_html": "<p>" + "Waterproof leather boot. " * 10 + "</p>",
         "images": [{"src": "a", "alt": ""}], "variants": [{"available": True, "sku": "B1"}]},
        {"handle": "sock", "title": "Sock", "body_html": "Sock", "images": [],
         "variants": [{"available": True, "sku": ""}]},
    ]
    r = readiness.check("store.example", products)
    by = {c["id"]: c for c in r["checks"]}
    assert by["robots"]["status"] == "fail" and "PerplexityBot" in by["robots"]["items"]
    assert "OAI-SearchBot" not in by["robots"]["items"]
    assert by["edge"]["status"] == "fail" and by["edge"]["items"] == ["ChatGPT-User"]
    assert by["jsonld-gtin"]["status"] == "warn" and by["jsonld-rating"]["status"] == "fail"
    assert by["descriptions"]["status"] == "fail"  # 1 of 2 thin
    assert by["alt"]["status"] == "warn"
    assert r["checks"][0]["status"] == "fail" and 0 <= r["score"] < 100
    json.dumps(r)
