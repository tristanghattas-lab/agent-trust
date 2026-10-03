"""
Data loading and derived columns for the dashboard.

Two sources, same shape:
- live: the four tables in Postgres (DATABASE_URL)
- demo: dashboard/demo_data.py, generated in memory and never written back

Everything the charts need is derived here, from columns that exist in the
live schema, so a chart that works on demo data works on live data.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import create_engine, text

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
ALL_CLASSES = [HUMAN] + AGENT_CLASSES

# User-agent tokens. A user-directed assistant fetches pages because a person
# asked it to; a crawler indexes or trains. They behave and matter differently.
ASSISTANT_TOKENS = ("chatgpt-user", "perplexity-user", "claude-user")
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
    if row.get("agent_family") == "browser-use":
        return AUTOMATION
    return SCRAPER


def ai_referral_source(referrer: str | None) -> str | None:
    ref = (referrer or "").lower()
    for domain, name in AI_REFERRER_SOURCES.items():
        if domain in ref:
            return name
    return None


def agent_label(row: pd.Series) -> str:
    """Specific agent name for tables (ChatGPT, GPTBot, browser-use ...)."""
    ua = (row.get("user_agent") or "").lower()
    for token in ASSISTANT_TOKENS + CRAWLER_TOKENS + SCRAPER_TOKENS:
        if token in ua:
            return {
                "chatgpt-user": "ChatGPT", "perplexity-user": "Perplexity", "claude-user": "Claude",
                "gptbot": "GPTBot", "oai-searchbot": "OAI-SearchBot", "claudebot": "ClaudeBot",
                "claude-searchbot": "Claude-SearchBot", "anthropic-ai": "anthropic-ai",
                "perplexitybot": "PerplexityBot", "google-extended": "Google-Extended",
                "geminibot": "GeminiBot", "amazonbot": "Amazonbot",
                "applebot-extended": "Applebot-Extended", "meta-externalagent": "Meta",
                "ccbot": "CCBot", "bytespider": "Bytespider",
            }[token]
    if "headlesschrome" in ua:
        return "HeadlessChrome"
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


@dataclass
class Dataset:
    source: str  # "live" | "demo"
    sessions: pd.DataFrame
    orders: pd.DataFrame
    outcomes: pd.DataFrame
    runs: pd.DataFrame
    note: str = ""

    @property
    def is_demo(self) -> bool:
        return self.source == "demo"


def _to_utc(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    return df


def load_live(database_url: str) -> Dataset:
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        def q(sql: str) -> pd.DataFrame:
            try:
                return pd.read_sql(text(sql), conn)
            except Exception:  # table missing on a fresh DB
                return pd.DataFrame()
        return Dataset(
            source="live",
            sessions=q("SELECT * FROM sessions"),
            orders=q("SELECT * FROM orders"),
            outcomes=q("SELECT * FROM outcomes"),
            runs=q("SELECT * FROM threat_test_runs"),
        )


def load_demo() -> Dataset:
    from dashboard.demo_data import build_demo_dataset

    d = build_demo_dataset()
    return Dataset(
        source="demo",
        sessions=d["sessions"],
        orders=d["orders"],
        outcomes=d["outcomes"],
        runs=d["threat_test_runs"],
    )


def enrich(ds: Dataset) -> Dataset:
    s = ds.sessions.copy()
    o = ds.orders.copy()
    oc = ds.outcomes.copy()
    r = ds.runs.copy()

    _to_utc(s, ["first_seen", "last_seen", "checkout_started_at", "checkout_completed_at"])
    _to_utc(o, ["created_at"])
    _to_utc(oc, ["created_at"])
    _to_utc(r, ["run_at"])

    if not s.empty:
        s["traffic_class"] = s.apply(classify_traffic, axis=1)
        s["agent_name"] = s.apply(agent_label, axis=1)
        s["ai_source"] = s["referrer"].map(ai_referral_source)
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
                s[["session_key", "traffic_class", "agent_name", "ai_source"]],
                on="session_key", how="left",
            )
        o["traffic_class"] = o.get("traffic_class", pd.Series(dtype=str)).fillna("Unmatched session")
        # A discount code on a human order is normal promo use; on an agent
        # order it's worth a look (agents guess codes — icelabs runs).
        agent_code = o["traffic_class"].isin(AGENT_CLASSES) & o["evidence"].map(
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

    return Dataset(ds.source, s, o, oc, r, ds.note)


def load(preferred: str = "auto") -> Dataset:
    """preferred: 'auto' | 'live' | 'demo'.

    auto = live if the database is reachable and has sessions, else demo.
    Threat-test runs are always live when the DB is reachable, because those
    are real results even before the tracker is installed anywhere.
    """
    url = os.getenv("DATABASE_URL")
    live: Dataset | None = None
    error = ""
    if preferred != "demo" and url:
        try:
            live = load_live(url)
        except Exception as exc:  # unreachable DB, bad URL, sleeping free tier
            error = f"Database unreachable ({type(exc).__name__})."

    if preferred == "live" and live is not None:
        return enrich(live)
    if preferred == "auto" and live is not None and not live.sessions.empty:
        return enrich(live)

    demo = load_demo()
    if live is not None and not live.runs.empty:
        demo.runs = live.runs
        demo.note = "Traffic is synthetic. Threat-test results are live from the database."
    else:
        demo.note = error or "No live traffic yet."
    return enrich(demo)
