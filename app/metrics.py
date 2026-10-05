"""
Metrics for one store over a time window, as JSON-ready dicts.

This is what any front end renders (the Shopify app, the internal
dashboard): every number is computed here once, from the same enriched
frames (app/analytics.py), so two screens can't disagree.

Data comes from Postgres filtered by shop_domain, or, for the reserved
shop "demo", from the synthetic dataset (app/demo_data.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.analytics import (
    AGENT_CLASSES, ASSISTANT, AUTOMATION, CRAWLER, FIXES, HUMAN, SCRAPER, SEVERITY_ORDER,
    enrich_frames, explain,
)
from app.shops import DEMO_SHOP

MIN_N = 20  # smallest sample a rate is reported for; below it the rate is null
BAD_OUTCOMES = {"chargeback", "disputed"}
CLASS_KEYS = {
    HUMAN: "human", ASSISTANT: "assistant", AUTOMATION: "automation",
    CRAWLER: "crawler", SCRAPER: "scraper",
}
SEGMENTS = ["human", "ai_referred", "assistant", "automation"]
SEGMENT_LABELS = {
    "human": "All human visits", "ai_referred": "AI-referred humans",
    "assistant": ASSISTANT, "automation": AUTOMATION,
}


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


@lru_cache(maxsize=2)
def _demo_frames(day_key: str):
    """Build and enrich the synthetic dataset once per day per process
    (it takes a few seconds)."""
    from app.demo_data import build_demo_dataset

    d = build_demo_dataset()
    return enrich_frames(d["sessions"], d["orders"], d["outcomes"], d["threat_test_runs"])


def load_frames(engine: Engine, shop: str, days: int) -> Frames:
    if shop == DEMO_SHOP:
        s, o, _, r = _demo_frames(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        end = s["first_seen"].max() if not s.empty else pd.Timestamp.now(tz="UTC")
        start = end - timedelta(days=days)
        s = s[s["first_seen"] > start]
        o = o[o["session_key"].isin(s["session_key"])] if not o.empty else o
        return Frames(shop, "demo", s, o, r, start, end, days)

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
    s, o, _, r = enrich_frames(sessions, orders, outcomes, runs)
    return Frames(shop, "live", s, o, r, start, end, days)


def _meta(f: Frames) -> dict:
    return {
        "shop": f.shop,
        "source": f.source,
        "synthetic": f.source == "demo",
        "window_days": f.days,
        "start": _iso(f.start),
        "end": _iso(f.end),
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
        return {k: o for k in SEGMENTS}
    return {
        "human": o[(o["traffic_class"] == HUMAN) & o["ai_source"].isna()],
        "ai_referred": o[o["ai_source"].notna()],
        "assistant": o[o["traffic_class"] == ASSISTANT],
        "automation": o[o["traffic_class"] == AUTOMATION],
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
        out.update(kpis=None, daily=[], top_agents=[], funnel=[], activity=[])
        return out

    agents = s[s["traffic_class"] != HUMAN]
    seg = _session_segments(s)
    span = timedelta(days=min(7, f.days / 2))
    recent = s[s["first_seen"] > f.end - span]
    early = s[s["first_seen"] <= f.start + span]
    ai_influenced = (
        o[o["traffic_class"].isin(AGENT_CLASSES) | o["ai_source"].notna()] if not o.empty else o
    )
    flagged = o[o["flags"].map(len) > 0] if not o.empty else o

    # Daily series: sessions per class plus AI-referred people.
    idx = _date_index(f)
    by_class = s.groupby(["date", "traffic_class"]).size().unstack(fill_value=0).reindex(idx, fill_value=0)
    ai_daily = seg["ai_referred"].groupby("date").size().reindex(idx, fill_value=0)
    agent_rev = (
        ai_influenced.assign(date=ai_influenced["created_at"].dt.tz_convert("Australia/Sydney").dt.date)
        .groupby("date")["order_value"].sum().reindex(idx, fill_value=0)
        if not ai_influenced.empty else pd.Series(0, index=idx)
    )
    daily = []
    for d in idx:
        row = {"date": d.isoformat()}
        total = 0
        for cls, key in CLASS_KEYS.items():
            n = int(by_class.at[d, cls]) if cls in by_class else 0
            row[key] = n
            total += n
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
            "ai_influenced_revenue": _num(ai_influenced["order_value"].sum(), 2) if len(ai_influenced) else 0.0,
            "ai_influenced_orders": int(len(ai_influenced)),
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
    )
    return out


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
                              "text": f"Undeclared agent placed a ${r.order_value:,.0f} order",
                              "detail": "Caught on behaviour, not user agent", "order_id": r.shopify_order_id})
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
    out.update(
        total=int(len(agents)), limit=limit, offset=offset, counts_by_class=counts,
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


def orders_summary(f: Frames) -> dict:
    o = f.orders
    out = _meta(f)
    if o.empty:
        out.update(kpis=None, by_segment=[], flagged=[])
        return out
    oseg = _order_segments(o)
    agent_o = o[o["traffic_class"].isin(AGENT_CLASSES)]
    human_o = o[o["traffic_class"] == HUMAN]
    flagged = o[o["flags"].map(len) > 0].sort_values("created_at", ascending=False)

    def dispute_rate(df):
        return _ratio(df["outcome_type"].isin(BAD_OUTCOMES).sum(), len(df)) if len(df) >= MIN_N else None

    agent_rate, human_rate = dispute_rate(agent_o), dispute_rate(human_o)
    out.update(
        min_sample=MIN_N,
        kpis={
            "orders": int(len(o)),
            "revenue": _num(o["order_value"].sum(), 2),
            "agent_orders": int(len(agent_o)),
            "agent_revenue": _num(agent_o["order_value"].sum(), 2) if len(agent_o) else 0.0,
            "agent_dispute_rate": agent_rate,
            "human_dispute_rate": human_rate,
            "agent_dispute_multiple": _num(agent_rate / human_rate, 2) if agent_rate is not None and human_rate else None,
            "flagged_orders": int(len(flagged)),
        },
        by_segment=[
            {"segment": k, "label": SEGMENT_LABELS[k], "orders": int(len(df)),
             "revenue": _num(df["order_value"].sum(), 2) if len(df) else 0.0,
             "aov": _num(df["order_value"].mean(), 2) if len(df) else None,
             "dispute_rate": dispute_rate(df)}
            for k, df in oseg.items()
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
