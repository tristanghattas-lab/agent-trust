"""
Agent readiness: can AI agents reach the store, and is the product data they
read complete? Works for any kind of store, from public pages only.

    robots      robots.txt rules for each AI agent (search and shopping
                agents must be allowed; training crawlers are the merchant's call)
    access      the home page fetched as each agent, to catch edge blocks
                (Cloudflare "Block AI bots", firewall rules) robots.txt doesn't show
    structured  JSON-LD on a sample of product pages: price, stock, brand,
                SKU, barcode (GTIN), rating, image, description
    catalogue   thin descriptions, missing images, missing SKUs

check(domain, products) returns {"checks": [...], "score": 0-100, ...}; each
check has a status (pass / warn / fail / info), what was found, and the fix.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)
TIMEOUT = 10
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0 Safari/537.36")

# (robots token, who it is, kind, user agent for the access check or None)
# kind: "shopping" agents fetch pages when a shopper asks; "search" builds the
# index AI answers come from; "training" is model training only.
AGENTS = [
    ("OAI-SearchBot", "ChatGPT search", "search",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.3; +https://openai.com/searchbot"),
    ("ChatGPT-User", "ChatGPT (fetching for a shopper)", "shopping",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot"),
    ("PerplexityBot", "Perplexity search", "search",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"),
    ("Perplexity-User", "Perplexity (fetching for a shopper)", "shopping", None),
    ("Claude-SearchBot", "Claude search", "search", None),
    ("Claude-User", "Claude (fetching for a shopper)", "shopping",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)"),
    ("Googlebot", "Google (AI Overviews, Gemini, Shopping)", "search", None),
    ("Bingbot", "Bing (Copilot)", "search", None),
    ("GPTBot", "OpenAI training", "training", None),
    ("ClaudeBot", "Anthropic training", "training", None),
    ("Google-Extended", "Gemini training", "training", None),
    ("CCBot", "Common Crawl", "training", None),
]


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
def parse_robots(text: str) -> dict[str, list[tuple[str, str]]]:
    """{user-agent (lowercase): [(allow|disallow, path pattern)]}."""
    groups: dict[str, list[tuple[str, str]]] = {}
    agents: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(val.lower())
            for a in agents:
                groups.setdefault(a, [])
        elif key in ("allow", "disallow"):
            in_rules = True
            for a in agents:
                if val or key == "allow":
                    groups.setdefault(a, []).append((key, val))
    return groups


def _rule_matches(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    rx = re.escape(pattern).replace(r"\*", ".*")
    if rx.endswith(r"\$"):
        rx = rx[:-2] + "$"
    return re.match(rx, path) is not None


def allowed(groups: dict, token: str, path: str) -> bool:
    """Google-style: the most specific group, then the longest matching rule;
    Allow wins a tie."""
    t = token.lower()
    rules = next((groups[a] for a in groups if a != "*" and a in t), None)
    if rules is None:
        rules = groups.get("*", [])
    best = None
    for kind, pat in rules:
        if _rule_matches(pat, path):
            if best is None or len(pat) > len(best[1]) or (len(pat) == len(best[1]) and kind == "allow"):
                best = (kind, pat)
    return best is None or best[0] == "allow"


def check_robots(domain: str, sample_path: str) -> dict:
    try:
        r = requests.get(f"https://{domain}/robots.txt", timeout=TIMEOUT, headers={"User-Agent": BROWSER_UA})
        text = r.text if r.status_code == 200 else ""
    except requests.RequestException:
        return {"fetched": False, "agents": []}
    groups = parse_robots(text)
    out = []
    for token, who, kind, _ in AGENTS:
        out.append({"agent": token, "who": who, "kind": kind,
                    "home": allowed(groups, token, "/"), "products": allowed(groups, token, sample_path)})
    return {"fetched": bool(text), "agents": out}


# ---------------------------------------------------------------------------
# Edge access (what robots.txt can't show)
# ---------------------------------------------------------------------------
def _status(url: str, ua: str) -> dict:
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": ua, "Accept": "text/html"}, allow_redirects=True)
        challenged = r.headers.get("cf-mitigated") == "challenge" or (
            r.status_code in (403, 503) and "challenge" in r.text[:3000].lower())
        return {"status": r.status_code, "blocked": r.status_code in (401, 403, 429, 503) or challenged,
                "challenge": challenged}
    except requests.RequestException as exc:
        return {"status": None, "blocked": None, "error": type(exc).__name__}


def check_access(domain: str) -> dict:
    url = f"https://{domain}/"
    probes = [("browser", BROWSER_UA)] + [(t, ua) for t, _, _, ua in AGENTS if ua]
    with ThreadPoolExecutor(max_workers=5) as pool:
        res = dict(zip([p[0] for p in probes], pool.map(lambda p: _status(url, p[1]), probes)))
    base = res.pop("browser")
    # If an ordinary browser is refused too, the store is blocking our server,
    # not AI agents: we can't tell, so don't report a block.
    usable = base.get("blocked") is False
    return {"baseline": base, "usable": usable,
            "agents": [{"agent": t, "who": who, **res[t]} for t, who, _, ua in AGENTS if ua]}


# ---------------------------------------------------------------------------
# Structured data on product pages
# ---------------------------------------------------------------------------
LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
GTIN_KEYS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "isbn")
FIELDS = ("price", "availability", "brand", "sku", "gtin", "rating", "image", "description")


def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for k in ("@graph", "hasVariant", "mainEntity"):
            if k in node:
                yield from _walk(node[k])


def product_fields(html: str) -> dict | None:
    """Which agent-relevant fields a page's JSON-LD carries, or None if it
    has no Product at all."""
    found: dict[str, bool] = {f: False for f in FIELDS}
    has_product = False
    for block in LD.findall(html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for n in _walk(data):
            types = n.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(t in ("Product", "ProductGroup") for t in types):
                continue
            has_product = True
            offers = n.get("offers") or []
            offers = offers if isinstance(offers, list) else [offers]
            for o in offers:
                if isinstance(o, dict):
                    found["price"] |= bool(o.get("price") or o.get("lowPrice") or o.get("priceSpecification"))
                    found["availability"] |= bool(o.get("availability"))
                    found["sku"] |= bool(o.get("sku"))
                    found["gtin"] |= any(o.get(k) for k in GTIN_KEYS)
            found["brand"] |= bool(n.get("brand"))
            found["sku"] |= bool(n.get("sku"))
            found["gtin"] |= any(n.get(k) for k in GTIN_KEYS)
            found["rating"] |= bool(n.get("aggregateRating"))
            found["image"] |= bool(n.get("image"))
            found["description"] |= bool(n.get("description"))
    return found if has_product else None


def check_structured(domain: str, products: list[dict], sample: int = 6) -> dict:
    instock = [p for p in products if any(v.get("available") for v in p.get("variants", []))] or products
    step = max(1, len(instock) // sample)
    picks = instock[::step][:sample]

    def fetch(p):
        try:
            r = requests.get(f"https://{domain}/products/{p['handle']}", timeout=TIMEOUT,
                             headers={"User-Agent": BROWSER_UA})
            return p, (product_fields(r.text) if r.status_code == 200 else "error")
        except requests.RequestException:
            return p, "error"

    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(fetch, picks))
    ok = [(p, f) for p, f in pages if f != "error"]
    with_product = [(p, f) for p, f in ok if f]
    counts = {k: sum(1 for _, f in with_product if f[k]) for k in FIELDS}
    return {
        "sampled": len(ok), "with_product": len(with_product), "fields": counts,
        "pages": [{"handle": p["handle"], "title": p.get("title"),
                   "missing": ([k for k in FIELDS if not f[k]] if f else ["no product data"])} for p, f in ok],
    }


# ---------------------------------------------------------------------------
# Catalogue quality (from products.json)
# ---------------------------------------------------------------------------
def _text(html: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def catalogue_quality(products: list[dict]) -> dict:
    thin = [p for p in products if len(_text(p.get("body_html"))) < 120]
    no_image = [p for p in products if not p.get("images")]
    no_sku = [p for p in products if p.get("variants") and not any((v.get("sku") or "").strip() for v in p["variants"])]
    imgs = [i for p in products for i in p.get("images", [])]
    alt_known = any("alt" in i for i in imgs)
    no_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip()) if alt_known else None
    ex = lambda rows: [{"handle": p["handle"], "title": p.get("title")} for p in rows[:5]]  # noqa: E731
    return {"products": len(products), "thin_description": len(thin), "thin_examples": ex(thin),
            "no_image": len(no_image), "no_sku": len(no_sku), "images": len(imgs), "images_without_alt": no_alt}


# ---------------------------------------------------------------------------
# Checks and score
# ---------------------------------------------------------------------------
def _pct(n: int, d: int) -> float:
    return n / d if d else 0.0


def build_checks(robots: dict, access: dict, structured: dict, cat: dict) -> list[dict]:
    checks = []

    # 1. robots.txt: search and shopping agents
    if robots.get("fetched"):
        blocked = [a for a in robots["agents"] if a["kind"] != "training" and not (a["home"] and a["products"])]
        training = [a for a in robots["agents"] if a["kind"] == "training" and not a["products"]]
        checks.append({
            "id": "robots", "area": "Access", "weight": 3,
            "title": "AI shopping and search agents allowed in robots.txt",
            "status": "fail" if blocked else "pass",
            "detail": ("Blocked: " + ", ".join(f"{a['who']} ({a['agent']})" for a in blocked)) if blocked
            else "Every AI search and shopping agent may read your products.",
            "fix": "In Shopify: Online Store > Themes > ... > Edit code > templates/robots.txt.liquid. Remove the "
                   "Disallow lines for these agents (or delete the file to go back to Shopify's default).",
            "fix_kind": "guide", "items": [a["agent"] for a in blocked],
        })
        checks.append({
            "id": "robots-training", "area": "Access", "weight": 0, "title": "AI training crawlers",
            "status": "info",
            "detail": ("You block " + ", ".join(a["who"] for a in training) + ". That's your call: it doesn't stop "
                       "shopping agents, but some AI products learn about brands from training data.") if training
            else "Training crawlers are allowed. Block them in robots.txt if you'd rather they didn't use your content.",
            "fix": "", "fix_kind": "none",
        })

    # 2. Edge blocks
    if access.get("usable"):
        edge = [a for a in access["agents"] if a.get("blocked")]
        checks.append({
            "id": "edge", "area": "Access", "weight": 3,
            "title": "AI agents can load your store",
            "status": "fail" if edge else "pass",
            "detail": ("Refused at your firewall or CDN: " + ", ".join(
                f"{a['who']} ({a['status']}{', challenge page' if a.get('challenge') else ''})" for a in edge))
            if edge else "Your store loads for ChatGPT, Perplexity and Claude.",
            "fix": "This is usually Cloudflare's 'Block AI bots' or a firewall rule. In Cloudflare: Security > Bots, "
                   "turn off 'Block AI bots', or in AI Crawl Control allow OAI-SearchBot, ChatGPT-User, PerplexityBot "
                   "and Claude-User. Other CDNs have a similar bot setting.",
            "fix_kind": "guide", "items": [a["agent"] for a in edge],
        })

    # 3. Structured data
    n = structured.get("with_product", 0)
    s = structured.get("sampled", 0)
    if s:
        f = structured.get("fields", {})
        checks.append({
            "id": "jsonld", "area": "Product data", "weight": 2,
            "title": "Product pages carry structured product data",
            "status": "pass" if n == s else ("warn" if n else "fail"),
            "detail": f"{n} of {s} product pages checked have product data (JSON-LD) agents can read." if n
            else "None of the product pages checked have product data (JSON-LD). Agents have to guess price and stock.",
            "fix": "Most Shopify themes add this automatically. If yours doesn't, switch to an Online Store 2.0 theme "
                   "or add a structured-data app.", "fix_kind": "guide",
        })
        if n:
            gaps = [(k, label, w) for k, label, w in [
                ("gtin", "barcodes (GTIN/UPC/EAN)", 2), ("brand", "brand", 1), ("availability", "stock status", 2),
                ("price", "price", 2), ("rating", "review ratings", 1), ("sku", "SKU", 1)] if f.get(k, 0) < n]
            for k, label, w in gaps:
                fix = {
                    "gtin": "Add each variant's barcode in Shopify (Products > variant > Barcode). ChatGPT and Google "
                            "match products across stores by barcode; your theme publishes it once it's set.",
                    "brand": "Set the Vendor on each product to the brand name; themes publish it as the brand.",
                    "availability": "Your theme isn't publishing stock status. Update the theme or its product schema.",
                    "price": "Your theme isn't publishing price. Update the theme or its product schema.",
                    "rating": "Use a reviews app that adds ratings to your product data (most major ones do), so "
                              "agents can quote your star rating.",
                    "sku": "Add SKUs to variants (Products > variant > SKU).",
                }[k]
                checks.append({
                    "id": f"jsonld-{k}", "area": "Product data", "weight": w,
                    "title": f"Product data includes {label}",
                    "status": "fail" if f.get(k, 0) == 0 else "warn",
                    "detail": f"{f.get(k, 0)} of {n} product pages checked include {label}.",
                    "fix": fix, "fix_kind": "guide",
                })
            ok_fields = [label for k, label in [("gtin", "barcodes"), ("brand", "brand"), ("availability", "stock"),
                                                ("price", "price"), ("rating", "ratings"), ("sku", "SKU")]
                         if f.get(k, 0) == n]
            if ok_fields:
                checks.append({"id": "jsonld-ok", "area": "Product data", "weight": 0, "status": "pass",
                               "title": "Fields already published", "detail": ", ".join(ok_fields).capitalize() + ".",
                               "fix": "", "fix_kind": "none"})

    # 4. Catalogue
    total = cat.get("products", 0)
    if total:
        thin = cat["thin_description"]
        checks.append({
            "id": "descriptions", "area": "Catalogue", "weight": 2,
            "title": "Descriptions agents can match against",
            "status": "pass" if _pct(thin, total) < 0.1 else ("warn" if _pct(thin, total) < 0.4 else "fail"),
            "detail": f"{thin:,} of {total:,} products have a description under about 20 words. Agents match shopper "
                      "requests (occasion, style, size, use) against the description.",
            "fix": "Add a few sentences on what it is, who it's for and what makes it different. Start with "
                   "best sellers.", "fix_kind": "guide", "examples": cat.get("thin_examples", []),
        })
        if cat.get("images_without_alt") is not None and cat.get("images"):
            na = cat["images_without_alt"]
            checks.append({
                "id": "alt", "area": "Catalogue", "weight": 1, "title": "Image descriptions (alt text)",
                "status": "pass" if _pct(na, cat["images"]) < 0.1 else "warn",
                "detail": f"{na:,} of {cat['images']:,} product images have no alt text.",
                "fix": "Fixes can fill these from the product and variant names.", "fix_kind": "app",
            })
        if cat["no_image"]:
            checks.append({"id": "images", "area": "Catalogue", "weight": 1, "title": "Every product has an image",
                           "status": "warn", "detail": f"{cat['no_image']:,} products have no image.",
                           "fix": "Add at least one image. Agents rarely recommend products without one.",
                           "fix_kind": "guide"})
    return checks


def score(checks: list[dict]) -> int | None:
    scored = [c for c in checks if c["weight"] and c["status"] in ("pass", "warn", "fail")]
    if not scored:
        return None
    pts = sum(c["weight"] * {"pass": 1, "warn": 0.5, "fail": 0}[c["status"]] for c in scored)
    return round(pts / sum(c["weight"] for c in scored) * 100)


def check(domain: str, products: list[dict]) -> dict:
    """Every readiness check for one store. `domain` should be the store's
    primary (customer-facing) domain, which is what agents reach."""
    sample_path = f"/products/{products[0]['handle']}" if products else "/products/x"
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_robots = pool.submit(check_robots, domain, sample_path)
        f_access = pool.submit(check_access, domain)
        f_struct = pool.submit(check_structured, domain, products)
        robots, access, structured = f_robots.result(), f_access.result(), f_struct.result()
    cat = catalogue_quality(products)
    checks = build_checks(robots, access, structured, cat)
    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3}
    checks.sort(key=lambda c: (order[c["status"]], -c["weight"]))
    return {"domain": domain, "score": score(checks), "checks": checks, "robots": robots, "access": access,
            "structured": structured, "catalogue": cat}
