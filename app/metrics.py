"""
Metrics for one store over a time window, as JSON-ready dicts.

This is what any front end renders (the Shopify app, the internal
dashboard): every number is computed here once, from the same enriched
frames (app/analytics.py), so two screens can't disagree.

Data comes from Postgres filtered by shop_domain, or, for the reserved
shop "demo", from the synthetic dataset (app/demo_data.py).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.analytics import (
    AGENT_CLASSES, AI_CHANNEL, agent_file, classify_user_agent, ASSISTANT, AUTOMATION, CRAWLER, FIXES, HUMAN, SCRAPER,
    SEVERITY_ORDER, enrich_frames, explain,
)
from app.shops import DEMO_SHOP

MIN_N = 20  # smallest sample a rate is reported for; below it the rate is null
BAD_OUTCOMES = {"chargeback", "disputed"}
CLASS_KEYS = {
    HUMAN: "human", ASSISTANT: "assistant", AUTOMATION: "automation",
    CRAWLER: "crawler", SCRAPER: "scraper", AI_CHANNEL: "ai_channel",
}
SEGMENTS = ["human", "ai_referred", "assistant", "automation"]          # sessions
ORDER_SEGMENTS = SEGMENTS + ["ai_channel"]                              # orders
SEGMENT_LABELS = {
    "human": "All human visits", "ai_referred": "AI-referred humans",
    "assistant": ASSISTANT, "automation": AUTOMATION, "ai_channel": AI_CHANNEL,
}
AGENT_ORDER_CLASSES = AGENT_CLASSES + [AI_CHANNEL]

# What each data source can see. Front ends show this so a merchant knows
# what's covered; agent classes a store's sources can't see are hidden
# rather than shown as zero.
SOURCES = {
    "tracker": "Browser tracker: people and agents that run a real browser",
    "orders": "Order feed: every order, including ones placed inside AI assistants",
    "edge": "Edge Worker (Cloudflare): per-session detail for fetch-only assistants, crawlers and scrapers",
    "cloudflare": "Cloudflare analytics: hourly request counts for AI crawlers, assistants and scrapers",
    "pixel": "Checkout pixel: checkout steps for browser-based visitors",
}
EDGE_ONLY_CLASSES = {"crawler", "scraper"}


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def _num(x, digits: int | None = None):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    v = float(x)
    if math.isinf(v):
        return None
    if digits is not None:
        v = round(v, digits)
    return int(v) if digits == 0 else v


def _ratio(n, d, digits: int = 4):
    return _num(n / d, digits) if d else None


def _iso(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).isoformat()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
@dataclass
class Frames:
    shop: str
    source: str  # "live" | "demo"
    sessions: pd.DataFrame
    orders: pd.DataFrame
    runs: pd.DataFrame
    start: pd.Timestamp
    end: pd.Timestamp
    days: int
    coverage: dict | None = None
    # Hourly request counts from Cloudflare analytics (not sessions).
    agg: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Cart/checkout webhooks and checkout-pixel steps (Shopify app).
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Webhook delivery outcomes (setup diagnostics).
    hooks: pd.DataFrame = field(default_factory=pd.DataFrame)


@lru_cache(maxsize=2)
def _demo_frames(day_key: str):
    """Build and enrich the synthetic dataset once per day per process
    (it takes a few seconds)."""
    from app.demo_data import build_demo_dataset

    d = build_demo_dataset()
    return enrich_frames(d["sessions"], d["orders"], d["outcomes"], d["threat_test_runs"])


def _demo_pixel(s: pd.DataFrame, o: pd.DataFrame) -> pd.DataFrame:
    """Synthetic checkout-pixel steps for the demo store's agent orders, so
    agent-placed vs agent-assisted shows. Modelled on test run 5: form steps
    3-5s apart; about a third hand off to a person before payment."""
    import numpy as np

    if o.empty or s.empty:
        return pd.DataFrame()
    agentic = o[o["traffic_class"].isin([ASSISTANT, AUTOMATION]) & o["session_key"].notna()]
    rng = np.random.default_rng(7)
    rows = []
    for r in agentic.itertuples():
        t = r.created_at - timedelta(minutes=3)
        handoff = rng.random() < 0.35
        for step in FORM_STEPS:
            t += timedelta(seconds=float(rng.uniform(3, 6)))
            rows.append({"source": "pixel", "topic": step, "session_key": r.session_key, "occurred_at": t})
        t += timedelta(seconds=float(rng.uniform(35, 120) if handoff else rng.uniform(2, 8)))
        rows.append({"source": "pixel", "topic": "payment_info_submitted", "session_key": r.session_key,
                     "occurred_at": t})
        rows.append({"source": "pixel", "topic": "checkout_completed", "session_key": r.session_key,
                     "occurred_at": t + timedelta(seconds=2)})
    ev = pd.DataFrame(rows)
    for col in ("cart_token", "order_id", "token", "total", "item_count", "source_name"):
        ev[col] = None
    return ev


def load_frames(engine: Engine, shop: str, days: int) -> Frames:
    if shop == DEMO_SHOP:
        s, o, _, r = _demo_frames(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        end = s["first_seen"].max() if not s.empty else pd.Timestamp.now(tz="UTC")
        start = end - timedelta(days=days)
        s = s[s["first_seen"] > start]
        o = o[o["created_at"] > start] if not o.empty else o
        coverage = {"tracker": True, "orders": True, "edge": True, "pixel": True, "simulated": True}
        return Frames(shop, "demo", s, o, r, start, end, days, coverage, events=_demo_pixel(s, o))

    end = pd.Timestamp.now(tz="UTC")
    start = end - timedelta(days=days)
    params = {"shop": shop, "start": start.to_pydatetime()}
    with engine.connect() as conn:
        def q(sql: str, p: dict = params) -> pd.DataFrame:
            return pd.read_sql(text(sql), conn, params=p)

        sessions = q("SELECT * FROM sessions WHERE shop_domain = :shop AND first_seen > :start")
        orders = q("SELECT * FROM orders WHERE shop_domain = :shop AND created_at > :start")
        outcomes = q(
            "SELECT oc.* FROM outcomes oc JOIN orders o ON o.id = oc.order_id "
            "WHERE o.shop_domain = :shop AND o.created_at > :start"
        )
        runs = q("SELECT * FROM threat_test_runs WHERE shop_domain = :shop", {"shop": shop})
        try:
            agg = q("SELECT hour, user_agent, path, requests FROM edge_aggregates "
                    "WHERE shop_domain = :shop AND hour > :start")
        except Exception:  # table not created yet on an older database
            agg = pd.DataFrame()
        events = (q("SELECT * FROM commerce_events WHERE shop_domain = :shop AND occurred_at > :start")
                  if _has_table(conn, "commerce_events") else pd.DataFrame())
        hooks = (q("SELECT topic, outcome, detail, received_at FROM webhook_log "
                   "WHERE (shop_domain = :shop OR shop_domain IS NULL) AND received_at > :start")
                 if _has_table(conn, "webhook_log") else pd.DataFrame())
        coverage = _live_coverage(conn, shop)
    s, o, _, r = enrich_frames(sessions, orders, outcomes, runs)
    if not events.empty:
        events["occurred_at"] = pd.to_datetime(events["occurred_at"], utc=True)
    if not hooks.empty:
        hooks["received_at"] = pd.to_datetime(hooks["received_at"], utc=True)
    return Frames(shop, "live", s, o, r, start, end, days, coverage, _enrich_agg(agg), events, hooks)


def _enrich_agg(agg: pd.DataFrame) -> pd.DataFrame:
    """Name and class each user agent; drop rows that aren't agent-like and
    aren't agent-file fetches (an ordinary browser reading robots.txt still
    counts as an agent-file fetch, under "Unidentified")."""
    if agg.empty:
        return agg
    agg = agg.copy()
    agg["hour"] = pd.to_datetime(agg["hour"], utc=True)
    agg["date"] = agg["hour"].dt.tz_convert("Australia/Sydney").dt.date
    info = agg["user_agent"].map(classify_user_agent)
    agg["agent"] = info.map(lambda x: x[0] if x else "Unidentified")
    agg["class"] = info.map(lambda x: x[1] if x else None)
    agg["file"] = agg["path"].map(agent_file)
    return agg[agg["class"].notna() | agg["file"].notna()]


def _live_coverage(conn, shop: str) -> dict:
    """Which sources have ever sent data for this store. Edge logs arrive as
    sessions that never ran JavaScript: the tracker can't produce those."""
    def exists(sql: str) -> bool:
        return conn.execute(text(sql), {"shop": shop}).first() is not None

    return {
        "tracker": exists("SELECT 1 FROM sessions WHERE shop_domain = :shop AND js_executed LIMIT 1"),
        "orders": exists("SELECT 1 FROM orders WHERE shop_domain = :shop LIMIT 1"),
        "edge": exists("SELECT 1 FROM sessions WHERE shop_domain = :shop AND NOT js_executed LIMIT 1"),
        "cloudflare": exists("SELECT 1 FROM integrations WHERE shop_domain = :shop "
                             "AND kind = 'cloudflare' AND last_error IS NULL "
                             "AND synced_through IS NOT NULL LIMIT 1") if _has_table(conn, "integrations") else False,
        "pixel": exists("SELECT 1 FROM commerce_events WHERE shop_domain = :shop "
                        "AND source = 'pixel' LIMIT 1") if _has_table(conn, "commerce_events") else False,
        "simulated": False,
    }


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect
    return inspect(conn).has_table(name)


def coverage_block(f: Frames) -> dict:
    cov = dict(f.coverage or {})
    edge_any = cov.get("edge") or cov.get("cloudflare")
    visible = ["assistant", "automation"] + (["crawler", "scraper"] if edge_any else [])
    return {
        **cov,
        "sources": [{"key": k, "label": v, "connected": bool(cov.get(k))} for k, v in SOURCES.items()],
        "visible_agent_classes": visible,
        # Crawler/scraper figures come in sessions (edge Worker) or, with
        # only Cloudflare analytics, in requests. Front ends must say which.
        "edge_unit": "sessions" if cov.get("edge") else ("requests" if cov.get("cloudflare") else None),
        "hidden_agent_classes": [c for c in ("crawler", "scraper") if c not in visible],
    }


def list_shops(engine: Engine) -> list[str]:
    """Stores with any data (tracker sessions, orders or a connection), most
    recently active first."""
    parts = [
        "SELECT shop_domain, MAX(last_seen) AS t FROM sessions GROUP BY shop_domain",
        "SELECT shop_domain, MAX(created_at) AS t FROM orders GROUP BY shop_domain",
    ]
    with engine.connect() as conn:
        if _has_table(conn, "integrations"):
            parts.append("SELECT shop_domain, MAX(created_at) AS t FROM integrations GROUP BY shop_domain")
        rows = conn.execute(text(
            "SELECT shop_domain FROM (" + " UNION ALL ".join(parts) + ") x "
            "WHERE shop_domain IS NOT NULL GROUP BY shop_domain ORDER BY MAX(t) DESC"
        )).fetchall()
    return [r[0] for r in rows]


def _meta(f: Frames) -> dict:
    return {
        "shop": f.shop,
        "source": f.source,
        "synthetic": f.source == "demo",
        "window_days": f.days,
        "start": _iso(f.start),
        "end": _iso(f.end),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage_block(f),
    }


# ---------------------------------------------------------------------------
# Segments shared by several views
# ---------------------------------------------------------------------------
def _session_segments(s: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if s.empty:
        return {k: s for k in SEGMENTS}
    humans = s[s["traffic_class"] == HUMAN]
    return {
        "human": humans,
        "ai_referred": humans[humans["ai_source"].notna()],
        "assistant": s[s["traffic_class"] == ASSISTANT],
        "automation": s[s["traffic_class"] == AUTOMATION],
    }


def _order_segments(o: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if o.empty:
        return {k: o for k in ORDER_SEGMENTS}
    return {
        "human": o[(o["traffic_class"] == HUMAN) & o["ai_source"].isna()],
        "ai_referred": o[(o["traffic_class"] == HUMAN) & o["ai_source"].notna()],
        "assistant": o[o["traffic_class"] == ASSISTANT],
        "automation": o[o["traffic_class"] == AUTOMATION],
        "ai_channel": o[o["traffic_class"] == AI_CHANNEL],
    }


def _date_index(f: Frames) -> pd.Index:
    tz = "Australia/Sydney"
    days = pd.date_range(f.start.tz_convert(tz).normalize(), f.end.tz_convert(tz).normalize(), freq="D")
    return pd.Index([d.date() for d in days], name="date")


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def overview(f: Frames) -> dict:
    s, o = f.sessions, f.orders
    out = _meta(f)
    if s.empty:
        # No tracker sessions yet. Cloudflare analytics may still show agent
        # traffic, so return that rather than an empty page.
        daily = []
        if not f.agg.empty:
            idx = _date_index(f)
            by = (f.agg[f.agg["class"].isin(["crawler", "scraper"])]
                  .groupby(["date", "class"])["requests"].sum().unstack(fill_value=0)
                  .reindex(idx, fill_value=0))
            daily = [{"date": d.isoformat(), "human": 0, "assistant": 0, "automation": 0,
                      "crawler": int(by.at[d, "crawler"]) if "crawler" in by else 0,
                      "scraper": int(by.at[d, "scraper"]) if "scraper" in by else 0,
                      "total": 0, "agent_share": None, "ai_referred": 0, "ai_influenced_revenue": 0.0}
                     for d in idx]
        out.update(kpis=None, daily=daily, top_agents=[], funnel=[], activity=[],
                   agent_files=agent_files(f), edge_agents=edge_agents(f))
        return out

    agents = s[s["traffic_class"] != HUMAN]
    seg = _session_segments(s)
    span = timedelta(days=min(7, f.days / 2))
    recent = s[s["first_seen"] > f.end - span]
    early = s[s["first_seen"] <= f.start + span]
    ai_influenced = (
        o[o["traffic_class"].isin(AGENT_ORDER_CLASSES) | o["ai_source"].notna()] if not o.empty else o
    )
    flagged = o[o["flags"].map(len) > 0] if not o.empty else o

    # Daily series: sessions per class plus AI-referred people.
    idx = _date_index(f)
    by_class = s.groupby(["date", "traffic_class"]).size().unstack(fill_value=0).reindex(idx, fill_value=0)
    ai_daily = seg["ai_referred"].groupby("date").size().reindex(idx, fill_value=0)
    agent_rev = (
        _real(ai_influenced).assign(date=lambda d: d["created_at"].dt.tz_convert("Australia/Sydney").dt.date)
        .groupby("date")["order_value"].sum().reindex(idx, fill_value=0)
        if not ai_influenced.empty else pd.Series(0, index=idx)
    )
    # Crawler and scraper bars: Worker sessions where the Worker was running,
    # otherwise Cloudflare request counts. Days up to and including the day
    # the Worker started use the counts, so connecting a Worker doesn't wipe
    # the history (or today's partial day). Units are labelled via
    # `crawler_units` on each day and `edge_since` in the response.
    edge_since = None
    if (f.coverage or {}).get("edge") and "js_executed" in s:
        edge_rows = s[~s["js_executed"].astype(bool)]
        if not edge_rows.empty:
            edge_since = edge_rows["first_seen"].min().tz_convert("Australia/Sydney").date()
    agg_daily = (
        f.agg[f.agg["class"].isin(["crawler", "scraper"])]
        .groupby(["date", "class"])["requests"].sum().unstack(fill_value=0).reindex(idx, fill_value=0)
        if not f.agg.empty else None
    )
    daily = []
    for d in idx:
        row = {"date": d.isoformat()}
        total = 0
        for cls, key in CLASS_KEYS.items():
            n = int(by_class.at[d, cls]) if cls in by_class else 0
            row[key] = n
            total += n
        use_counts = agg_daily is not None and (edge_since is None and not (f.coverage or {}).get("edge")
                                                or (edge_since is not None and d <= edge_since))
        if use_counts:
            for key in ("crawler", "scraper"):
                row[key] = int(agg_daily.at[d, key]) if key in agg_daily else 0
        row["crawler_units"] = "requests" if use_counts else "sessions"
        row["total"] = total
        row["agent_share"] = _ratio(total - row["human"], total)
        row["ai_referred"] = int(ai_daily.at[d])
        row["ai_influenced_revenue"] = _num(agent_rev.at[d], 2)
        daily.append(row)

    top = (
        agents.groupby(["agent_name", "traffic_class"]).size().reset_index(name="sessions")
        .sort_values("sessions", ascending=False).head(10)
    )
    stages = [("viewed_product", "st_view"), ("added_to_cart", "st_cart"),
              ("started_checkout", "st_checkout"), ("ordered", "st_ordered")]
    funnel = []
    for key in SEGMENTS:
        df = seg[key]
        entry = {"segment": key, "label": SEGMENT_LABELS[key], "visits": int(len(df))}
        for name, col in stages:
            n = int(df[col].sum()) if len(df) else 0
            entry[name] = n
            entry[f"{name}_rate"] = _ratio(n, len(df))
        funnel.append(entry)

    out.update(
        kpis={
            "sessions": int(len(s)),
            "agent_sessions": int(len(agents)),
            "agent_share": _ratio(len(agents), len(s)),
            "agent_share_change_pts": _num(
                ((recent["is_agent"].mean() if len(recent) else 0)
                 - (early["is_agent"].mean() if len(early) else 0)) * 100, 2),
            "ai_referred_visits": int(len(seg["ai_referred"])),
            "ai_influenced_revenue": _num(_real(ai_influenced)["order_value"].sum(), 2) if len(ai_influenced) else 0.0,
            "ai_influenced_orders": int(len(ai_influenced)),
            "ai_channel_orders": int((o["traffic_class"] == AI_CHANNEL).sum()) if not o.empty else 0,
            "ai_channel_revenue": _num(_real(o).loc[_real(o)["traffic_class"] == AI_CHANNEL, "order_value"].sum(), 2)
            if not o.empty else 0.0,
            "flagged_orders": int(len(flagged)),
        },
        daily=daily,
        top_agents=[
            {"name": r.agent_name, "class": CLASS_KEYS[r.traffic_class], "class_label": r.traffic_class,
             "sessions": int(r.sessions)}
            for r in top.itertuples()
        ],
        funnel=funnel,
        activity=activity(f),
        agent_files=agent_files(f),
        edge_agents=edge_agents(f),
        edge_since=edge_since.isoformat() if edge_since else None,
        ai_orders=ai_orders_block(f),
    )
    return out


def edge_agents(f: Frames) -> list[dict]:
    """Request counts per agent from Cloudflare analytics, busiest first."""
    if f.agg.empty:
        return []
    a = f.agg[f.agg["class"].notna()]
    g = a.groupby(["agent", "class"])["requests"].sum().reset_index().sort_values("requests", ascending=False)
    return [{"name": r["agent"], "class": r["class"], "requests": int(r["requests"])}
            for _, r in g.head(15).iterrows()]


def agent_files(f: Frames) -> list[dict]:
    """Which agents fetched robots.txt, agents.md, llms.txt, sitemaps and
    product feeds. Edge data only (Worker sessions, else Cloudflare request
    counts): these requests never run JavaScript."""
    cov = f.coverage or {}
    if not cov.get("edge"):
        if not cov.get("cloudflare") or f.agg.empty:
            return []
        files = f.agg[f.agg["file"].notna()]
        out = []
        for name, g in files.groupby("file"):
            top = g.groupby("agent")["requests"].sum().sort_values(ascending=False).head(5)
            out.append({"file": name, "count": int(g["requests"].sum()), "unit": "requests",
                        "agents": int(g["agent"].nunique()),
                        "top_agents": [{"agent": k, "count": int(v)} for k, v in top.items()]})
        return sorted(out, key=lambda x: x["count"], reverse=True)
    s = f.sessions
    if s.empty or "edge_paths" not in s:
        return []
    rows = []
    for r in s[s["edge_paths"].notna()].itertuples():
        try:
            paths = json.loads(r.edge_paths)
        except (TypeError, ValueError):
            continue
        for name in {agent_file(p) for p in paths} - {None}:
            rows.append((name, r.agent_name if r.traffic_class != HUMAN else "Unidentified"))
    if not rows:
        return []
    df = pd.DataFrame(rows, columns=["file", "agent"])
    out = []
    for name, g in df.groupby("file"):
        top = g["agent"].value_counts().head(5)
        out.append({"file": name, "count": int(len(g)), "unit": "sessions",
                    "agents": int(g["agent"].nunique()),
                    "top_agents": [{"agent": a, "count": int(n)} for a, n in top.items()]})
    return sorted(out, key=lambda x: x["count"], reverse=True)


def activity(f: Frames, limit: int = 10) -> list[dict]:
    """Notable events, newest first: undeclared agents buying, discount
    requests on orders, assistant purchases, agents seen for the first time."""
    s, o = f.sessions, f.orders
    items: list[dict] = []
    if not o.empty:
        for r in o.itertuples():
            if r.flags:
                items.append({"ts": r.created_at, "kind": "flagged_order", "severity": "high",
                              "text": f"{r.flags[0]} on a ${r.order_value:,.0f} order",
                              "detail": r.traffic_class, "order_id": r.shopify_order_id})
            elif r.traffic_class == AUTOMATION:
                items.append({"ts": r.created_at, "kind": "agent_order", "severity": "medium",
                              "text": (f"{r.agent_name} placed a ${r.order_value:,.0f} order"
                                       if isinstance(getattr(r, "agent_name", None), str)
                                       and r.agent_name != "Undeclared (behavioural)"
                                       else f"Undeclared agent placed a ${r.order_value:,.0f} order"),
                              "detail": "Caught on behaviour, not user agent", "order_id": r.shopify_order_id})
            elif r.traffic_class == AI_CHANNEL:
                items.append({"ts": r.created_at, "kind": "ai_channel_order", "severity": "info",
                              "text": f"${r.order_value:,.0f} order placed inside {r.ai_channel}",
                              "detail": "Agentic checkout: never visited the store", "order_id": r.shopify_order_id})
            elif r.traffic_class == ASSISTANT:
                items.append({"ts": r.created_at, "kind": "assistant_order", "severity": "info",
                              "text": f"{r.agent_name} completed a ${r.order_value:,.0f} purchase",
                              "detail": ASSISTANT, "order_id": r.shopify_order_id})
    if not s.empty:
        agents = s[s["traffic_class"] != HUMAN]
        first = agents.sort_values("first_seen").groupby("agent_name").head(1)
        grace = f.start + timedelta(days=min(3, f.days / 4))
        for r in first[first["first_seen"] > grace].itertuples():
            items.append({"ts": r.first_seen, "kind": "new_agent", "severity": "info",
                          "text": f"New agent seen: {r.agent_name}", "detail": r.traffic_class,
                          "order_id": None})
    items.sort(key=lambda i: i["ts"], reverse=True)
    for i in items:
        i["ts"] = _iso(i["ts"])
    return items[:limit]


def referrals(f: Frames) -> dict:
    s, o = f.sessions, f.orders
    out = _meta(f)
    seg = _session_segments(s)
    ai, humans = seg["ai_referred"], seg["human"]
    if s.empty or ai.empty:
        out.update(kpis=None, daily=[], by_source=[], landing_pages=[])
        return out
    other = humans[humans["ai_source"].isna()]
    oseg = _order_segments(o)
    ref_o, other_o = oseg["ai_referred"], oseg["human"]

    idx = _date_index(f)
    by_src = ai.groupby(["date", "ai_source"]).size().unstack(fill_value=0).reindex(idx, fill_value=0)
    sources = list(by_src.sum().sort_values(ascending=False).index)
    daily = [{"date": d.isoformat(), **{src: int(by_src.at[d, src]) for src in sources}} for d in idx]

    by_source = []
    for src in sources:
        visits = ai[ai["ai_source"] == src]
        orders = ref_o[ref_o["ai_source"] == src] if not ref_o.empty else ref_o
        by_source.append({
            "source": src, "visits": int(len(visits)), "orders": int(len(orders)),
            "conversion": _ratio(len(orders), len(visits)),
            "revenue": _num(orders["order_value"].sum(), 2) if len(orders) else 0.0,
        })
    land = ai["landing_path"].fillna("/").value_counts().head(10)

    conv_ai = ai["st_ordered"].mean()
    conv_other = other["st_ordered"].mean() if len(other) else None
    out.update(
        kpis={
            "visits": int(len(ai)),
            "share_of_human_visits": _ratio(len(ai), len(humans)),
            "conversion": _num(conv_ai, 4),
            "conversion_other": _num(conv_other, 4),
            "aov": _num(ref_o["order_value"].mean(), 2) if len(ref_o) else None,
            "aov_other": _num(other_o["order_value"].mean(), 2) if len(other_o) else None,
            "revenue": _num(ref_o["order_value"].sum(), 2) if len(ref_o) else 0.0,
            "orders": int(len(ref_o)),
        },
        daily=daily,
        sources=sources,
        by_source=by_source,
        landing_pages=[{"path": p, "visits": int(n)} for p, n in land.items()],
    )
    return out


def _session_row(r) -> dict:
    return {
        "session_key": r.session_key,
        "first_seen": _iso(r.first_seen),
        "class": CLASS_KEYS.get(r.traffic_class, "human"),
        "class_label": r.traffic_class,
        "agent": r.agent_name,
        "confidence": _num(r.classification_confidence, 2),
        "landing_path": r.landing_path,
        "cart_value": _num(r.cart_value, 2),
        "ordered": bool(r.st_ordered),
        "signals": len(r.reasons_list),
        # Caught on behaviour alone, not because it named itself: the cases
        # a user-agent list would have missed.
        "behaviour_only": bool(r.reasons_list)
        and not any(code.startswith("ua_match") for code in r.reasons_list),
    }


def sessions_list(f: Frames, cls: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    s = f.sessions
    out = _meta(f)
    agents = s[s["traffic_class"] != HUMAN] if not s.empty else s
    if cls and not agents.empty:
        label = {v: k for k, v in CLASS_KEYS.items()}.get(cls)
        agents = agents[agents["traffic_class"] == label]
    agents = agents.sort_values("first_seen", ascending=False) if not agents.empty else agents
    counts = (
        {CLASS_KEYS[c]: int((s["traffic_class"] == c).sum()) for c in AGENT_CLASSES} if not s.empty else {}
    )
    page = agents.iloc[offset: offset + limit] if not agents.empty else agents
    req_counts = (
        {k: int(v) for k, v in f.agg[f.agg["class"].notna()].groupby("class")["requests"].sum().items()}
        if not f.agg.empty else {}
    )
    out.update(
        total=int(len(agents)), limit=limit, offset=offset, counts_by_class=counts,
        request_counts_by_class=req_counts,  # Cloudflare analytics: requests, not sessions
        sessions=[_session_row(r) for r in page.itertuples()],
    )
    return out


def session_detail(f: Frames, session_key: str) -> dict | None:
    s = f.sessions
    if s.empty:
        return None
    match = s[s["session_key"] == session_key]
    if match.empty:
        return None
    r = next(match.itertuples())

    def opt_int(name):
        v = getattr(r, name, None)
        return _num(v, 0)

    out = _meta(f)
    out.update(_session_row(r))
    out.update(
        referrer=r.referrer or None,
        user_agent=r.user_agent,
        js_executed=bool(r.js_executed),
        ai_source=r.ai_source,
        reasons=[{"code": code, "text": explain(code)} for code in r.reasons_list],
        # Requests seen at the edge, in order: pages, agent files, and store
        # agent-API calls with the tool used ("/api/ucp/mcp → update_cart").
        edge_requests=_json_list(getattr(r, "edge_paths", None)),
        signals_detail={
            "clicks": opt_int("click_count"),
            "clicks_without_mouse_trail": opt_int("sparse_trail_click_count"),
            "mouse_event_rate": _num(getattr(r, "mouse_event_rate", None), 3),
            "fields_filled": opt_int("input_count"),
            "fields_filled_without_keys": opt_int("keyless_input_count"),
            "automation_tells": [t for t in (getattr(r, "automation_tells", None) or "").split(",") if t],
            "checkout_seconds": _num(r.time_to_checkout_seconds, 1),
        },
    )
    return out


def run_report(f: Frames, since: datetime, until: datetime, include_bots: bool = False) -> dict:
    """Everything that happened in a time window, in one response: each
    session with its full evidence, and each order with the session it was
    linked to. Built for reviewing a test run ("what did the agent do between
    19:14 and 19:17, and what did we catch?"). Crawlers and scrapers are left
    out unless include_bots, because they drown out a single test."""
    s, o = f.sessions, f.orders
    out = _meta(f)
    out.update(since=_iso(since), until=_iso(until))
    sessions = []
    if not s.empty:
        last = s["last_seen"] if "last_seen" in s else s["first_seen"]
        w = s[(s["first_seen"] <= until) & (last.fillna(s["first_seen"]) >= since)]
        if not include_bots:
            w = w[~w["traffic_class"].isin([CRAWLER, SCRAPER])]
        for r in w.sort_values("first_seen").itertuples():
            d = session_detail(f, r.session_key) or {}
            sessions.append({k: d.get(k) for k in (
                "session_key", "first_seen", "class_label", "agent", "confidence", "landing_path",
                "referrer", "ai_source", "user_agent", "js_executed", "cart_value", "ordered",
                "behaviour_only", "reasons", "signals_detail", "edge_requests")}
                | {"last_seen": _iso(getattr(r, "last_seen", None))})
    orders = []
    if not o.empty:
        w = o[(o["created_at"] >= since) & (o["created_at"] <= until)]
        for r in w.sort_values("created_at").itertuples():
            orders.append({
                "shopify_order_id": r.shopify_order_id, "created_at": _iso(r.created_at),
                "order_value": _num(r.order_value, 2), "is_test": bool(getattr(r, "is_test", False)),
                "source_name": getattr(r, "source_name", None), "class_label": r.traffic_class,
                "agent": getattr(r, "agent_name", None), "ai_source": r.ai_source,
                "session_key": r.session_key, "session_match": getattr(r, "session_match_method", None),
                "flags": list(r.flags),
            })
    ev = f.events
    commerce, off_site = [], 0
    if ev is not None and not ev.empty:
        steps = ev[(ev["source"] == "pixel") & ev["session_key"].notna()].sort_values("occurred_at")
        for x in sessions:
            mine = steps[steps["session_key"] == x["session_key"]]
            if not mine.empty:
                t0 = mine["occurred_at"].iloc[0]
                x["checkout_steps"] = [{"step": r.topic, "seconds": round((r.occurred_at - t0).total_seconds(), 1)}
                                       for r in mine.itertuples()]
        w = ev[(ev["occurred_at"] >= since) & (ev["occurred_at"] <= until) & (ev["source"] == "webhook")]
        carts_with_session = set(ev.loc[ev["session_key"].notna() & ev["cart_token"].notna(), "cart_token"])
        for r in w.sort_values("occurred_at").itertuples():
            storefront = bool(r.session_key) or (r.cart_token in carts_with_session)
            commerce.append({"topic": r.topic, "occurred_at": _iso(r.occurred_at),
                             "token": (r.token or "")[-8:] or None, "session_key": r.session_key,
                             "storefront_session": storefront, "total": _num(r.total, 2),
                             "items": None if pd.isna(r.item_count) else int(r.item_count),
                             "source_name": r.source_name})
            if r.topic == "carts/create" and not storefront:
                off_site += 1
    deliveries = []
    hk = f.hooks
    if hk is not None and not hk.empty:
        hw = hk[(hk["received_at"] >= since) & (hk["received_at"] <= until)]
        for (topic, outcome, detail), g in hw.groupby(["topic", "outcome", hk["detail"].fillna("")], dropna=False):
            deliveries.append({"topic": topic, "outcome": outcome, "detail": detail or None, "count": int(len(g)),
                               "last": _iso(g["received_at"].max())})
    out.update(webhook_deliveries=deliveries)
    out.update(sessions=sessions, orders=orders, commerce_events=commerce,
               summary={"sessions": len(sessions), "agent_sessions": sum(x["class_label"] != HUMAN for x in sessions),
                        "orders": len(orders), "agent_orders": sum(x["class_label"] not in (HUMAN, None) for x in orders),
                        "carts_without_storefront_session": off_site})
    return out


def _json_list(raw) -> list[str]:
    if not isinstance(raw, str) or not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else []
    except ValueError:
        return []


def _real(o: pd.DataFrame) -> pd.DataFrame:
    """Orders that aren't Shopify test-mode orders (revenue excludes tests)."""
    if o.empty or "is_test" not in o:
        return o
    return o[~o["is_test"].fillna(False).astype(bool)]


def _test_count(o: pd.DataFrame) -> int:
    return int(len(o) - len(_real(o))) if not o.empty else 0


def orders_summary(f: Frames) -> dict:
    o = f.orders
    out = _meta(f)
    if o.empty:
        out.update(kpis=None, by_segment=[], flagged=[])
        return out
    oseg = _order_segments(o)
    agent_o = o[o["traffic_class"].isin(AGENT_ORDER_CLASSES)]
    human_o = o[o["traffic_class"] == HUMAN]
    flagged = o[o["flags"].map(len) > 0].sort_values("created_at", ascending=False)

    def dispute_rate(df):
        return _ratio(df["outcome_type"].isin(BAD_OUTCOMES).sum(), len(df)) if len(df) >= MIN_N else None

    agent_rate, human_rate = dispute_rate(agent_o), dispute_rate(human_o)
    out.update(
        min_sample=MIN_N,
        kpis={
            "orders": int(len(o)),
            "test_orders": _test_count(o),
            "revenue": _num(_real(o)["order_value"].sum(), 2),
            "agent_orders": int(len(agent_o)),
            "agent_revenue": _num(_real(agent_o)["order_value"].sum(), 2) if len(agent_o) else 0.0,
            "agent_dispute_rate": agent_rate,
            "human_dispute_rate": human_rate,
            "agent_dispute_multiple": _num(agent_rate / human_rate, 2) if agent_rate is not None and human_rate else None,
            "flagged_orders": int(len(flagged)),
        },
        by_segment=[
            {"segment": k, "label": SEGMENT_LABELS[k], "orders": int(len(df)),
             "revenue": _num(_real(df)["order_value"].sum(), 2) if len(df) else 0.0,
             "aov": _num(df["order_value"].mean(), 2) if len(df) else None,
             "dispute_rate": dispute_rate(df)}
            for k, df in oseg.items()
        ],
        # Raw sales-channel values, so unknown AI channels can be spotted and
        # added to AI_CHANNEL_TOKENS the first time they appear.
        by_source_name=[
            {"source_name": name, "orders": int(len(g)), "test_orders": _test_count(g),
             "revenue": _num(_real(g)["order_value"].sum(), 2),
             "ai_channel": g["ai_channel"].dropna().iloc[0] if g["ai_channel"].notna().any() else None}
            for name, g in o.assign(source_name=o["source_name"].fillna("(not reported)"))
            .groupby("source_name", sort=False)
        ],
        flagged=[
            {"shopify_order_id": r.shopify_order_id, "created_at": _iso(r.created_at),
             "class": CLASS_KEYS.get(r.traffic_class), "class_label": r.traffic_class,
             "order_value": _num(r.order_value, 2), "flags": list(r.flags),
             "free_text": (r.evidence.get("shipping_address") or {}).get("address2") or r.evidence.get("note"),
             "outcome": r.outcome_type}
            for r in flagged.head(100).itertuples()
        ],
    )
    return out


# ---------------------------------------------------------------------------
# Order origin and evidence
# ---------------------------------------------------------------------------
# Three kinds of AI order, kept apart (docs: metrics review): a person an AI
# sent (ai_referred), an agent that built the cart but a person paid
# (agent_assisted), and an agent that did it all (agent_placed). Without
# checkout-pixel timings the last two can't be told apart ("agent").
ORIGINS = {
    "agent_placed": "Agent-placed",
    "agent_assisted": "Agent-assisted",
    "agent": "Agent order",
    "ai_channel": "Inside an AI app",
    "ai_referred": "AI-referred person",
    "human": "Person",
    "unmatched": "No session linked",
}
AI_ORIGINS = ["agent_placed", "agent_assisted", "agent", "ai_channel", "ai_referred"]
# A pause this long between the last form step and payment means someone
# took over (test run 5: steps 3-5s apart, then payment at +46s).
HANDOFF_SECONDS = 30.0
FORM_STEPS = ("checkout_started", "checkout_contact_info_submitted",
              "checkout_address_info_submitted", "checkout_shipping_info_submitted")
PAY_STEPS = ("payment_info_submitted", "checkout_completed")
IDENTITY_LABELS = {
    "verified": "Verified", "signed": "Signed, not verified",
    "declared": "Declared in user agent", "undeclared": "Undeclared (behaviour only)",
}
# Value above which an agent order that didn't prove who it is gets a review.
REVIEW_VALUE = 250.0


def _pixel_by_session(f: Frames) -> dict[str, list[tuple[str, pd.Timestamp]]]:
    ev = f.events
    if ev is None or ev.empty or "source" not in ev:
        return {}
    px = ev[(ev["source"] == "pixel") & ev["session_key"].notna()].sort_values("occurred_at")
    return {k: list(zip(g["topic"], g["occurred_at"])) for k, g in px.groupby("session_key")}


def handoff_gap(steps: list[tuple[str, pd.Timestamp]]) -> float | None:
    """Seconds from the last form step to payment, or None if not measurable."""
    pay = [t for name, t in steps if name in PAY_STEPS]
    if not pay:
        return None
    first_pay = min(pay)
    form = [t for name, t in steps if name in FORM_STEPS and t <= first_pay]
    if not form:
        return None
    return round((first_pay - max(form)).total_seconds(), 1)


def identity_tier(cls: str | None, agent_name: str | None, reasons) -> str | None:
    if cls not in AGENT_CLASSES:
        return None
    name = agent_name or ""
    if "(verified)" in name:
        return "verified"
    if "(signed)" in name:
        return "signed"
    if any(str(c).startswith("ua_match") for c in (reasons if isinstance(reasons, list) else [])):
        return "declared"
    return "undeclared"


def order_origin(cls: str | None, ai_source, steps) -> str:
    if cls == AI_CHANNEL:
        return "ai_channel"
    if cls in AGENT_CLASSES:
        gap = handoff_gap(steps) if steps else None
        if gap is None:
            return "agent"
        return "agent_assisted" if gap >= HANDOFF_SECONDS else "agent_placed"
    if cls == HUMAN:
        return "ai_referred" if isinstance(ai_source, str) and ai_source else "human"
    return "unmatched"


def _str(v) -> str | None:
    return v if isinstance(v, str) and v else (None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))


def _order_rows(f: Frames) -> list[dict]:
    """Every order with its origin, agent identity and evidence status."""
    o = f.orders
    if o.empty:
        return []
    px = _pixel_by_session(f)
    rows = []
    for r in o.sort_values("created_at", ascending=False).itertuples():
        steps = px.get(r.session_key) if isinstance(r.session_key, str) else None
        origin = order_origin(r.traffic_class, r.ai_source, steps)
        ident = identity_tier(r.traffic_class, r.agent_name, getattr(r, "reasons_list", None))
        chain = _chain(r, steps, origin, ident)
        is_test = str(getattr(r, "is_test", False)).lower() in ("true", "1", "1.0")
        review = bool(r.flags) or (
            origin in ("agent_placed", "agent") and ident in ("undeclared", "declared", "signed")
            and (r.order_value or 0) >= REVIEW_VALUE and not is_test)
        rows.append({
            "order_id": r.id, "shopify_order_id": _str(r.shopify_order_id), "created_at": _iso(r.created_at),
            "order_value": _num(r.order_value, 2), "is_test": is_test,
            "origin": origin, "origin_label": ORIGINS[origin],
            "class": CLASS_KEYS.get(r.traffic_class), "class_label": r.traffic_class,
            "agent": _str(r.agent_name) if r.traffic_class in AGENT_ORDER_CLASSES else None,
            "identity": ident, "identity_label": IDENTITY_LABELS.get(ident),
            "confidence": _num(getattr(r, "classification_confidence", None), 2) if ident else None,
            "ai_source": r.ai_source if isinstance(r.ai_source, str) else None,
            "handoff_seconds": handoff_gap(steps) if steps else None,
            "evidence_score": f"{sum(c['ok'] for c in chain)}/{len(chain)}",
            "flags": list(r.flags), "needs_review": review, "outcome": _str(r.outcome_type),
            "session_key": r.session_key if isinstance(r.session_key, str) else None,
            "chain": chain,
        })
    return rows


def _chain(r, steps, origin: str, ident: str | None) -> list[dict]:
    """The evidence chain for one order: origin -> session -> identity ->
    checkout -> order. Each link is present (ok) or missing, in plain words."""
    has_session = isinstance(r.session_key, str) and bool(r.session_key)
    src = r.ai_source if isinstance(r.ai_source, str) else None
    landing = getattr(r, "landing_site", None)
    if src:
        origin_text = f"Arrived via {src}"
    elif r.traffic_class == AI_CHANNEL:
        origin_text = f"Placed inside {r.ai_channel or 'an AI app'}; never visited the store"
    elif has_session:
        origin_text = "Direct or non-AI referral"
    else:
        origin_text = f"Landing page from the order: {landing}" if isinstance(landing, str) and landing else "Unknown"
    agentic = r.traffic_class in AGENT_CLASSES
    conf = _num(getattr(r, "classification_confidence", None), 2)
    chain = [
        {"step": "Origin", "ok": bool(src) or has_session or r.traffic_class == AI_CHANNEL, "text": origin_text},
        {"step": "Session", "ok": has_session,
         "text": (f"{r.traffic_class}" + (f", confidence {conf:.2f}" if conf is not None else "")
                  + (f" (matched by {r.session_match_method})" if isinstance(r.session_match_method, str) else ""))
         if has_session else "No storefront session linked to this order"},
    ]
    if agentic:
        chain.append({"step": "Agent identity", "ok": ident in ("verified", "signed"),
                      "text": f"{r.agent_name}: {IDENTITY_LABELS.get(ident, 'unknown')}"})
    if r.traffic_class == AI_CHANNEL:
        chain = chain[:1]  # checkout happened inside the AI app: no session or steps to see
    elif steps:
        gap = handoff_gap(steps)
        verdict = ("" if gap is None or not agentic else
                   f"; {'handed off to a person' if gap >= HANDOFF_SECONDS else 'agent completed'} "
                   f"({gap:.0f}s before payment)")
        chain.append({"step": "Checkout", "ok": True, "text": f"{len(steps)} checkout steps recorded{verdict}"})
    else:
        chain.append({"step": "Checkout", "ok": False, "text": "Checkout steps not recorded (pixel)"})
    chain.append({"step": "Order", "ok": True,
                  "text": f"${(r.order_value or 0):,.2f}" + (" · test order" if str(getattr(r, "is_test", False)).lower() in ("true", "1", "1.0") else "")
                  + (f" · {r.source_name}" if isinstance(getattr(r, 'source_name', None), str) else "")})
    return chain


def orders_list(f: Frames, limit: int = 200) -> dict:
    out = _meta(f)
    rows = _order_rows(f)
    for x in rows:
        x.pop("chain")
    out.update(total=len(rows), orders=rows[:limit])
    return out


def order_detail(f: Frames, order_id: str) -> dict | None:
    for x in _order_rows(f):
        if order_id in (x["order_id"], x["shopify_order_id"]):
            out = _meta(f)
            out.update(x)
            if x["session_key"]:
                sd = session_detail(f, x["session_key"]) or {}
                out["session"] = {k: sd.get(k) for k in (
                    "first_seen", "landing_path", "referrer", "reasons", "signals_detail", "edge_requests")}
            steps = _pixel_by_session(f).get(x["session_key"]) or []
            if steps:
                t0 = steps[0][1]
                out["checkout_steps"] = [{"step": n, "seconds": round((t - t0).total_seconds(), 1)}
                                         for n, t in steps]
            return out
    return None


def _offsite_carts(f: Frames) -> tuple[int, int]:
    """Carts created with no storefront session (Shopify's agent API, or
    carts the tracker didn't see), and how many of those reached an order."""
    ev = f.events
    if ev is None or ev.empty or "source" not in ev:
        return 0, 0
    with_session = set(ev.loc[ev["session_key"].notna() & ev["cart_token"].notna(), "cart_token"])
    carts = ev[(ev["source"] == "webhook") & (ev["topic"] == "carts/create")]
    off = carts[carts["session_key"].isna() & ~carts["cart_token"].isin(with_session)]
    ordered = ev.loc[ev["order_id"].notna(), "cart_token"] if "order_id" in ev else pd.Series(dtype=str)
    return int(len(off)), int(off["cart_token"].isin(set(ordered.dropna())).sum())


def attention(f: Frames, rows: list[dict]) -> list[dict]:
    """Things a merchant should look at, most urgent first. Each has a
    severity (high/medium/info), a title, one line of detail and where to go."""
    items = []
    hk = f.hooks
    if hk is not None and not hk.empty:
        bad = hk[hk["outcome"].isin(["rejected", "error"])]
        recent = bad[bad["received_at"] > f.end - timedelta(days=1)]
        if len(recent):
            items.append({"severity": "high", "title": f"{len(recent)} Shopify webhook deliveries failed today",
                          "detail": str(recent.sort_values("received_at")["detail"].dropna().iloc[-1]
                                        if recent["detail"].notna().any() else recent["outcome"].iloc[-1]),
                          "page": "Connections"})
    flagged = [x for x in rows if x["flags"]]
    if flagged:
        items.append({"severity": "high", "title": f"{len(flagged)} order{'s' if len(flagged) != 1 else ''} flagged",
                      "detail": flagged[0]["flags"][0], "page": "Orders"})
    unproven = [x for x in rows if x["needs_review"] and not x["flags"]]
    if unproven:
        v = sum(x["order_value"] or 0 for x in unproven)
        items.append({"severity": "medium",
                      "title": f"{len(unproven)} agent order{'s' if len(unproven) != 1 else ''} over "
                               f"${REVIEW_VALUE:,.0f} from agents that didn't prove who they are",
                      "detail": f"${v:,.0f} in total. Check the evidence before fulfilling.", "page": "Orders"})
    off, off_ordered = _offsite_carts(f)
    if off:
        items.append({"severity": "medium",
                      "title": f"{off} cart{'s' if off != 1 else ''} built off your storefront",
                      "detail": f"Likely Shopify's agent API (never touches your site). {off_ordered} became orders.",
                      "page": "Run report"})
    s = f.sessions
    if not s.empty:
        ag = s[s["traffic_class"].isin([ASSISTANT, AUTOMATION])]
        stuck = ag[ag["st_checkout"] & ~ag["st_ordered"]]
        if len(stuck):
            items.append({"severity": "info",
                          "title": f"{len(stuck)} agent checkout{'s' if len(stuck) != 1 else ''} abandoned",
                          "detail": "Agents reached checkout and stopped. Payment methods agents can't use "
                                    "are the usual cause.", "page": "Agent sessions"})
    cov = f.coverage or {}
    if not cov.get("simulated"):
        if cov.get("tracker") and not cov.get("pixel"):
            items.append({"severity": "info", "title": "Checkout pixel not reporting",
                          "detail": "Without it, agent-placed and agent-assisted orders can't be told apart.",
                          "page": "Connections"})
        if not cov.get("edge") and not cov.get("cloudflare"):
            items.append({"severity": "info", "title": "No edge data",
                          "detail": "Crawlers and fetch-only assistants are invisible. Connect Cloudflare.",
                          "page": "Connections"})
    return items


def ai_orders_block(f: Frames) -> dict:
    """Origin breakdown, agent conversion and the attention list, for the
    overview (also usable by the Shopify app)."""
    rows = _order_rows(f)
    real = [x for x in rows if not x["is_test"]]
    total_rev = sum(x["order_value"] or 0 for x in real)
    by_origin = []
    for key in AI_ORIGINS + ["human", "unmatched"]:
        mine = [x for x in rows if x["origin"] == key]
        if not mine:
            continue
        rev = sum(x["order_value"] or 0 for x in mine if not x["is_test"])
        by_origin.append({"origin": key, "label": ORIGINS[key], "orders": len(mine),
                          "test_orders": sum(x["is_test"] for x in mine), "revenue": _num(rev, 2),
                          "aov": _num(sum(x["order_value"] or 0 for x in mine) / len(mine), 2)})
    ai = [x for x in real if x["origin"] in AI_ORIGINS]
    agent = [x for x in real if x["origin"] in ("agent_placed", "agent_assisted", "agent", "ai_channel")]
    s = f.sessions
    conv = {}
    if not s.empty:
        ag = s[s["traffic_class"].isin([ASSISTANT, AUTOMATION])]
        hu = s[s["traffic_class"] == HUMAN]
        conv = {"agent_conversion": _ratio(int(ag["st_ordered"].sum()), len(ag)),
                "human_conversion": _ratio(int(hu["st_ordered"].sum()), len(hu)),
                "agent_browser_sessions": int(len(ag))}
    for x in rows:
        x.pop("chain", None)
    return {
        "ai_revenue": _num(sum(x["order_value"] or 0 for x in ai), 2),
        "ai_revenue_share": _ratio(sum(x["order_value"] or 0 for x in ai), total_rev),
        "ai_orders": len([x for x in rows if x["origin"] in AI_ORIGINS]),
        "agent_revenue": _num(sum(x["order_value"] or 0 for x in agent), 2),
        "agent_orders": len([x for x in rows if x["origin"] in ("agent_placed", "agent_assisted", "agent", "ai_channel")]),
        "needs_review": sum(x["needs_review"] for x in rows),
        "test_orders": sum(x["is_test"] for x in rows),
        **conv,
        "by_origin": by_origin,
        "recent_ai_orders": [x for x in rows if x["origin"] in AI_ORIGINS][:8],
        "attention": attention(f, rows),
    }


def threats(f: Frames) -> dict:
    r = f.runs
    out = _meta(f)
    out["synthetic"] = False  # threat-test runs are always real results
    if r.empty:
        out.update(kpis=None, matrix=[], findings=[], runs=[])
        return out
    latest = r.sort_values("run_at").groupby(["agent_surface", "task_name"]).tail(1)
    rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    findings = r[r["severity"].isin(["High", "Medium", "Needs review", "Low"])].copy()
    findings = findings.assign(_rank=findings["severity"].map(rank)).sort_values(["_rank", "run_at"])
    out.update(
        kpis={
            "runs": int(len(r)),
            "agent_surfaces": int(r["agent_surface"].nunique()),
            "exploits": int(r["exploit_found"].sum()),
            "needs_review": int((r["severity"] == "Needs review").sum()),
            "by_severity": {s: int((r["severity"] == s).sum()) for s in SEVERITY_ORDER},
        },
        surfaces=sorted(latest["agent_surface"].unique().tolist()),
        tasks=list(dict.fromkeys(r.sort_values("run_at")["task_name"])),
        matrix=[
            {"agent_surface": x.agent_surface, "task": x.task_name, "result": x.result,
             "exploit": bool(x.exploit_found), "severity": x.severity, "notes": x.friction_notes,
             "run_at": _iso(x.run_at)}
            for x in latest.itertuples()
        ],
        findings=[
            {"severity": x.severity, "task": x.task_name, "agent_surface": x.agent_surface,
             "run_at": _iso(x.run_at), "notes": x.friction_notes, "fix": FIXES.get(x.task_name)}
            for x in findings.itertuples()
        ],
    )
    return out
