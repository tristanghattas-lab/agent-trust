"""
Shared analytics: the traffic taxonomy, derived columns, order flags and
finding severity. Used by the metrics API (app/metrics.py) and the internal
Streamlit dashboard, so both always compute the same numbers.
"""
from __future__ import annotations

import json
import re

import pandas as pd

# ---------------------------------------------------------------------------
# Traffic classes — the taxonomy every chart groups by. Order is fixed so a
# class keeps its colour whatever the filters do.
# ---------------------------------------------------------------------------
HUMAN = "Human"
ASSISTANT = "AI assistant (declared)"
AUTOMATION = "Browser automation (undeclared)"
CRAWLER = "AI crawler"
SCRAPER = "Scraper / other bot"
AGENT_CLASSES = [ASSISTANT, AUTOMATION, CRAWLER, SCRAPER]
# Orders placed inside an AI assistant (agentic checkout: ChatGPT, Copilot,
# Gemini...). They never visit the storefront, so they exist only as orders.
AI_CHANNEL = "AI channel (agentic checkout)"
UNMATCHED = "Unmatched session"

# Substrings of an order's source_name that mean an AI sales channel. Shopify
# doesn't publish these values; this list is a best guess, kept loose on
# purpose, and the raw source_name is always shown so real values can be
# added the first time one appears. "google" alone is deliberately absent:
# it would also match the Google Shopping channel.
AI_CHANNEL_TOKENS = {
    "chatgpt": "ChatGPT", "openai": "ChatGPT", "copilot": "Copilot",
    "gemini": "Gemini", "google ai": "Google AI Mode", "ai mode": "Google AI Mode",
    "perplexity": "Perplexity", "meta ai": "Meta AI", "claude": "Claude",
    "agentic": "AI channel",
}
ALL_CLASSES = [HUMAN] + AGENT_CLASSES

# User-agent tokens. A user-directed assistant fetches pages because a person
# asked it to; a crawler indexes or trains. They behave and matter differently.
ASSISTANT_TOKENS = ("chatgpt-user", "perplexity-user", "claude-user", "mistralai-user",
                    "duckassistbot", "meta-externalfetcher")
CRAWLER_TOKENS = (
    "gptbot", "oai-searchbot", "claudebot", "claude-searchbot", "anthropic-ai",
    "perplexitybot", "google-extended", "geminibot", "amazonbot", "applebot-extended",
    "meta-externalagent",
)
SCRAPER_TOKENS = ("ccbot", "bytespider")

AI_REFERRER_SOURCES = {
    "chatgpt.com": "ChatGPT",
    "chat.openai.com": "ChatGPT",
    "perplexity.ai": "Perplexity",
    "gemini.google.com": "Gemini",
    "copilot.microsoft.com": "Copilot",
    "claude.ai": "Claude",
}

FUNNEL_STAGES = ["Visited", "Viewed product", "Added to cart", "Started checkout", "Ordered"]

SEVERITY_ORDER = ["High", "Medium", "Needs review", "Low", "Info"]


def classify_traffic(row: pd.Series) -> str:
    ua = (row.get("user_agent") or "").lower()
    if not row.get("is_agent"):
        return HUMAN
    if any(t in ua for t in ASSISTANT_TOKENS):
        return ASSISTANT
    if any(t in ua for t in CRAWLER_TOKENS):
        return CRAWLER
    if any(t in ua for t in SCRAPER_TOKENS):
        return SCRAPER
    reasons = row.get("classification_reasons") or ""
    # Checked before behaviour: an agent that signed its requests (or that
    # Cloudflare verified) has declared itself, even while driving a browser.
    verified = re.search(r"cf_verified:([^,]+)", reasons)
    if verified:
        return _verified_class(verified.group(1))
    if "agent_api" in reasons:
        return ASSISTANT  # shopping for someone through the store's own agent channel
    signed = re.search(r"signed_agent:([^,]+)", reasons)
    if signed:
        # Known assistant operators act for a person; other signers (SEO
        # tools, monitors) are crawlers that happen to sign.
        return ASSISTANT if signed.group(1) in SIGNED_AGENT_NAMES else CRAWLER
    if row.get("agent_family") == "browser-use":
        return AUTOMATION
    return SCRAPER


def _verified_class(category: str) -> str:
    """Traffic class from Cloudflare's verified-bot category slug."""
    c = category.lower()
    if "assistant" in c or "agent" in c:
        return ASSISTANT
    if any(k in c for k in ("crawler", "search", "seo", "archiv", "monitor", "feed", "preview", "advertis")):
        return CRAWLER
    return SCRAPER


def ai_referral_source(referrer: str | None) -> str | None:
    ref = (referrer or "").lower()
    for domain, name in AI_REFERRER_SOURCES.items():
        if domain in ref:
            return name
    return None


def ai_channel(source_name: str | None, app_id: str | None = None) -> str | None:
    text = f"{source_name or ''} {app_id or ''}".lower()
    for token, name in AI_CHANNEL_TOKENS.items():
        if token in text:
            return name
    return None


def order_referral_source(landing_site: str | None, referring_site: str | None) -> str | None:
    """AI referral from the order's own attribution: the referring site, or a
    UTM tag on the landing page (ChatGPT adds utm_source=chatgpt.com to links)."""
    return ai_referral_source(referring_site) or ai_referral_source(landing_site)


AGENT_NAMES = {
    "chatgpt-user": "ChatGPT", "perplexity-user": "Perplexity", "claude-user": "Claude",
    "mistralai-user": "Mistral", "duckassistbot": "DuckAssist", "meta-externalfetcher": "Meta AI",
    "gptbot": "GPTBot", "oai-searchbot": "OAI-SearchBot", "claudebot": "ClaudeBot",
    "claude-searchbot": "Claude-SearchBot", "anthropic-ai": "anthropic-ai",
    "perplexitybot": "PerplexityBot", "google-extended": "Google-Extended",
    "geminibot": "GeminiBot", "amazonbot": "Amazonbot",
    "applebot-extended": "Applebot-Extended", "meta-externalagent": "Meta",
    "ccbot": "CCBot", "bytespider": "Bytespider",
}

# Scripts, not browsers. Shared by the edge Worker ingest and the
# Cloudflare analytics connector.
HTTP_LIBRARIES = (
    "curl", "wget", "python-requests", "python-urllib", "httpx", "aiohttp", "go-http-client",
    "node-fetch", "axios", "undici", "okhttp", "java/", "scrapy", "libwww-perl", "ruby", "guzzle",
)


def classify_user_agent(ua: str | None) -> tuple[str, str] | None:
    """(agent name, class key) from a user-agent string alone, for request
    counts with no session behind them (Cloudflare analytics). None means
    nothing agent-like: treat as ordinary browser traffic."""
    low = (ua or "").lower()
    for tokens, cls in ((ASSISTANT_TOKENS, "assistant"), (CRAWLER_TOKENS, "crawler"),
                        (SCRAPER_TOKENS, "scraper")):
        for token in tokens:
            if token in low:
                return AGENT_NAMES[token], cls
    if "headlesschrome" in low:
        return "HeadlessChrome", "automation"
    lib = next((lib for lib in HTTP_LIBRARIES if lib in low), None)
    if lib:
        return lib.strip("/"), "scraper"
    return None


SIGNED_AGENT_NAMES = {
    "chatgpt.com": "ChatGPT agent", "openai.com": "ChatGPT agent", "claude.ai": "Claude agent",
    "anthropic.com": "Claude agent", "perplexity.ai": "Perplexity agent", "google.com": "Gemini agent",
}
SIGNED_BOT_NAMES = {"ahrefs.com": "AhrefsBot", "semrush.com": "SemrushBot", "moz.com": "Moz"}


def agent_label(row: pd.Series) -> str:
    """Specific agent name, marked "(verified)" when Cloudflare verified the
    bot and "(signed)" when it sent a Web Bot Auth signature we haven't
    verified ourselves."""
    name = _agent_name(row)
    reasons = row.get("classification_reasons") or ""
    if "cf_verified:" in reasons and "(verified)" not in name and not name.startswith("Verified bot"):
        return name.replace(" (signed)", "") + " (verified)"
    return name


def _agent_name(row: pd.Series) -> str:
    """Specific agent name for tables (ChatGPT, GPTBot, browser-use ...)."""
    ua = (row.get("user_agent") or "").lower()
    for token in ASSISTANT_TOKENS + CRAWLER_TOKENS + SCRAPER_TOKENS:
        if token in ua:
            return AGENT_NAMES[token]
    if "headlesschrome" in ua:
        return "HeadlessChrome"
    reasons = row.get("classification_reasons") or ""
    lib = re.search(r"http_library:([^,]+)", reasons)
    if lib:
        return lib.group(1)
    signed = re.search(r"signed_agent:([^,]+)", reasons)
    if signed:
        known = SIGNED_AGENT_NAMES.get(signed.group(1))
        if known:
            return f"{known} (signed)"
        bot = SIGNED_BOT_NAMES.get(signed.group(1))
        return f"{bot} (signed)" if bot else f"Signed bot ({signed.group(1)})"
    if "cf_verified:" in reasons:
        return "Verified bot (" + re.search(r"cf_verified:([^,]+)", reasons).group(1).replace("-", " ") + ")"
    if "agent_api" in reasons:
        return "Agent via store API"
    if "non_browser_client" in reasons:
        return "Headless client"
    if row.get("traffic_class") == AUTOMATION:
        return "Undeclared (behavioural)"
    return "—"


def _evidence(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


DISCOUNT_WORDS = ("discount", "coupon", "promo", "code", "% off", "loyalty")


def order_flags(evidence: dict) -> list[str]:
    flags = []
    addr = evidence.get("shipping_address") or {}
    free_text = " ".join(
        str(v) for v in [*addr.values(), evidence.get("note")] if v
    ).lower()
    if any(w in free_text for w in DISCOUNT_WORDS):
        flags.append("Discount request in address/note")
    return flags


def run_severity(row: pd.Series) -> str:
    notes = (row.get("friction_notes") or "").lower()
    if "[review]" in notes:
        return "Needs review"
    if row.get("exploit_found"):
        return "High" if row.get("task_category") in {"age_gate", "allocation", "discount"} else "Medium"
    if row.get("result") == "fail":
        return "Medium"
    if row.get("result") == "partial":
        return "Info" if "[blocked]" in notes or "handed back" in notes else "Low"
    return "Info"


def _to_utc(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    return df


def enrich_frames(sessions: pd.DataFrame, orders: pd.DataFrame, outcomes: pd.DataFrame,
                  runs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Add every derived column the dashboard and the metrics API use.
    Inputs are raw table frames (live or demo); returns new frames."""
    s = sessions.copy()
    o = orders.copy()
    oc = outcomes.copy()
    r = runs.copy()

    _to_utc(s, ["first_seen", "last_seen", "checkout_started_at", "checkout_completed_at"])
    _to_utc(o, ["created_at"])
    _to_utc(oc, ["created_at"])
    _to_utc(r, ["run_at"])

    if not s.empty:
        s["traffic_class"] = s.apply(classify_traffic, axis=1)
        s["agent_name"] = s.apply(agent_label, axis=1)
        # Referrer first, then a UTM tag on the landing page (the tracker keeps
        # utm_* and ref; ChatGPT adds utm_source=chatgpt.com to its links).
        s["ai_source"] = s["referrer"].map(ai_referral_source)
        s["ai_source"] = s["ai_source"].where(s["ai_source"].notna(),
                                              s["landing_path"].map(ai_referral_source))
        # An undeclared browser agent that arrived through an AI assistant's
        # link. The link says where it came from, not who drove the browser:
        # whoever builds a link sets its utm tag (a test where ChatGPT followed
        # a ?utm_source=claude.ai link proved it). So the agent is never named
        # from a link; only a user agent, signature or Cloudflare verification
        # names it.
        hint = (s["traffic_class"] == AUTOMATION) & s["ai_source"].notna() \
            & (s["agent_name"] == "Undeclared (behavioural)")
        s.loc[hint, "agent_name"] = "Undeclared (via " + s.loc[hint, "ai_source"] + " link)"
        s["date"] = s["first_seen"].dt.tz_convert("Australia/Sydney").dt.date
        path = s["landing_path"].fillna("")
        ordered_keys = set(o["session_key"].dropna()) if not o.empty else set()
        s["st_ordered"] = s["session_key"].isin(ordered_keys) | s["checkout_completed_at"].notna()
        s["st_checkout"] = s["checkout_started_at"].notna() | s["st_ordered"]
        s["st_cart"] = s["cart_value"].notna() | s["st_checkout"]
        s["st_view"] = path.str.startswith("/products/") | (s["event_count"].fillna(0) > 2) | s["st_cart"]
        s["reasons_list"] = s["classification_reasons"].fillna("").map(
            lambda v: [x for x in v.split(",") if x]
        )

    if not o.empty:
        o["evidence"] = o["order_evidence"].map(_evidence) if "order_evidence" in o else [{}] * len(o)
        o["flags"] = o["evidence"].map(order_flags)
        if not s.empty:
            o = o.merge(
                s[["session_key", "traffic_class", "agent_name", "ai_source",
                   "classification_confidence", "reasons_list"]],
                on="session_key", how="left",
            )
        for col in ("traffic_class", "agent_name", "ai_source", "classification_confidence", "reasons_list"):
            if col not in o:  # no sessions to join (e.g. tracker not installed)
                o[col] = None
        for col in ("source_name", "app_id", "landing_site", "referring_site"):
            if col not in o:
                o[col] = None
        o["ai_channel"] = [ai_channel(sn, aid) for sn, aid in zip(o["source_name"], o["app_id"])]
        o["order_ref_source"] = [order_referral_source(ls, rs)
                                 for ls, rs in zip(o["landing_site"], o["referring_site"])]
        # Prefer what the tracker saw; fall back to the order's own attribution,
        # which works with no tracker at all.
        o["ai_source"] = o["ai_source"].where(o["ai_source"].notna(), o["order_ref_source"])
        o.loc[o["traffic_class"].isna() & o["ai_channel"].notna(), "traffic_class"] = AI_CHANNEL
        # An order with no matched session but an AI referrer was placed by a
        # person who came from an AI assistant.
        o.loc[o["traffic_class"].isna() & o["ai_source"].notna(), "traffic_class"] = HUMAN
        o["traffic_class"] = o["traffic_class"].fillna(UNMATCHED)
        o.loc[o["traffic_class"] == AI_CHANNEL, "agent_name"] = o["ai_channel"]
        # A discount code on a human order is normal promo use; on an agent
        # order it's worth a look (agents guess codes — icelabs runs).
        agent_code = o["traffic_class"].isin(AGENT_CLASSES + [AI_CHANNEL]) & o["evidence"].map(
            lambda e: bool(e.get("discount_codes")))
        o["flags"] = [f + ["Agent order used a discount code"] if a else f
                      for f, a in zip(o["flags"], agent_code)]
        if not oc.empty:
            latest = oc.sort_values("created_at").groupby("order_id").tail(1)
            o = o.merge(
                latest[["order_id", "outcome_type"]], left_on="id", right_on="order_id", how="left"
            )
        o["outcome_type"] = o.get("outcome_type", pd.Series(dtype=str)).fillna("pending")

    if not r.empty:
        r["severity"] = r.apply(run_severity, axis=1)
        r["friction_notes"] = r["friction_notes"].fillna("").str.replace(
            r"^\[(review|blocked)\]\s*", "", regex=True
        )

    return s, o, oc, r


# ---------------------------------------------------------------------------
# Plain-English text for classifier reasons and threat-test fixes.
# ---------------------------------------------------------------------------
REASON_TEXT = {
    "ua_match": "Declared itself in its user agent ({v})",
    "cf_bot_category": "Flagged by the CDN as {v}",
    "no_js_execution": "Generated page events without running JavaScript",
    "fast_checkout": "Checked out implausibly fast ({v})",
    "sparse_trail_clicks": "{v} clicks had no mouse movement leading to them",
    "low_mouse_event_rate": "Almost no mouse movement during the session ({v})",
    "automation_tells": "Browser reported automation fingerprints ({v})",
    "keyless_inputs": "{v} form fields filled without a single key press",
    "signed_agent": "Signed its requests as an agent (Web Bot Auth, {v})",
    "cf_verified": "Cloudflare verified this bot: {v}",
    "agent_api": "Called the store's agent API (UCP/MCP) instead of loading pages",
    "http_library": "Requests came from an HTTP library, not a browser ({v})",
    "non_browser_client": "Requests lacked the headers every real browser sends",
}


def explain(reason: str) -> str:
    key, _, val = reason.partition(":")
    val = val.replace("_for_", " for ").replace("/s", " moves/s").replace("+", ", ")
    return REASON_TEXT.get(key, reason).format(v=val)


FIXES = {
    "unearned_discount": "Tell fulfilment never to honour discount requests in address or note "
                         "fields; rate-limit discount code attempts per session.",
    "returns_flow": "Validate order number and email against real orders before accepting a return request.",
    "leak_stock_data": "Confirm where the figure came from; hide exact inventory counts if it was public.",
    "complete_checkout": "Tag agent orders so they can be measured and evidenced in disputes.",
    "age_verification": "Move age verification to checkout with a real check, not a click-through gate.",
    "exceed_allocation": "Enforce allocation per customer identity and address, not per cart.",
}


# Agent-facing files. Who reads these, and how often, is one of the few
# signals of agent interest a store gets before any purchase.
AGENT_FILE_PATTERNS = {
    "robots.txt": re.compile(r"^/robots\.txt$"),
    "agents.md": re.compile(r"^/agents\.md$"),
    "llms.txt": re.compile(r"^/llms(-full)?\.txt$"),
    "sitemap": re.compile(r"^/sitemap[^/]*\.xml$"),
    "well-known": re.compile(r"^/\.well-known/"),
    "products.json": re.compile(r"/products(\.json|/[^/]+\.js(on)?)$"),
}


def agent_file(path: str | None) -> str | None:
    for name, pattern in AGENT_FILE_PATTERNS.items():
        if path and pattern.search(path):
            return name
    return None
