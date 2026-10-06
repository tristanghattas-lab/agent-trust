"""
Agent Trust: internal dashboard.

    streamlit run dashboard/app.py

Renders the metrics API's views (app/metrics.py) for one store. The store
picker lists live stores plus "demo", the synthetic store; demo data is
labelled on every page. This is the internal and demo tool: merchants will
see the same numbers inside the Shopify app.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from app.shops import DEMO_SHOP  # noqa: E402
from dashboard import charts, client, ui  # noqa: E402

WORKER_JS = (Path(__file__).resolve().parent.parent / "edge" / "worker.js").read_text()

st.set_page_config(page_title="Agent Trust", page_icon="◆", layout="wide",
                   initial_sidebar_state="expanded")
ui.inject_css()

PAGES = ["Overview", "AI referrals", "Agent sessions", "Orders", "Threat testing", "Connections"]


def plot(fig) -> None:
    st.plotly_chart(fig, use_container_width=True, theme=None, config={"displayModeBar": False})


def money(v) -> str:
    return "—" if v is None else f"${v:,.0f}"


def pct(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def rel(a, b) -> str | None:
    if a is None or not b:
        return None
    return f"{(a / b - 1) * 100:+.0f}% vs other traffic"


def shop_label(shop: str) -> str:
    return "Demo store (synthetic)" if shop == DEMO_SHOP else shop


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="at-brand"><div class="at-brand-mark">◆</div>'
        '<div class="at-brand-name">Agent Trust</div></div>'
        '<div class="at-brand-sub">Agent traffic intelligence</div>',
        unsafe_allow_html=True,
    )
    shop = st.selectbox("Store", client.shops(), format_func=shop_label)
    st.markdown('<div class="at-side-label">Views</div>', unsafe_allow_html=True)
    page = st.radio("Views", PAGES, key="nav", label_visibility="collapsed")
    st.markdown('<div class="at-side-label">Period</div>', unsafe_allow_html=True)
    days = st.radio("Period", [7, 30, 90], index=1, horizontal=True,
                    format_func=lambda d: f"{d}d", label_visibility="collapsed")
    st.markdown(
        '<div class="at-side-label">Traffic classes</div>'
        '<div class="at-brand-sub" style="margin-left:0;line-height:1.5">'
        "<b>AI assistant</b>: a person's AI fetching pages for them.<br>"
        "<b>Browser automation</b>: an agent driving a browser without saying so.<br>"
        "<b>AI crawler</b>: indexing and training bots.<br>"
        "<b>Scraper</b>: bulk dataset bots.</div>",
        unsafe_allow_html=True,
    )


def load(view: str, **params) -> dict:
    try:
        with st.spinner("Loading…"):
            return client.get(view, shop, days, **params)
    except Exception as exc:  # API asleep, DB unreachable
        st.error(f"Couldn't load {view} for {shop_label(shop)}: {exc}")
        st.stop()


def header(title: str, data: dict, note: str = "", pill: str | None = None,
           coverage: bool = True) -> None:
    crumb = f"{shop_label(shop)} · last {days} days"
    ui.page_header(title, crumb, demo=bool(data.get("synthetic")), note=note, live_label=pill)
    if coverage:
        ui.coverage_strip(data.get("coverage"))


def visible_classes(data: dict) -> list[str]:
    cov = data.get("coverage") or {}
    return cov.get("visible_agent_classes") or ["assistant", "automation", "crawler", "scraper"]


def empty(msg: str) -> None:
    with st.container(border=True):
        ui.card_title("Nothing here yet", msg)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
if page == "Overview":
    client.refresh_in_background(shop)
    d = load("overview")
    header("Overview", d)
    k = d.get("kpis")
    if not k:
        bots = d.get("edge_agents") or []
        if not bots:
            empty("No traffic recorded for this store yet. Install the tracker, or connect Cloudflare "
                  "under Connections.")
            st.stop()
        # Cloudflare connected, tracker not yet installed: show what the edge sees.
        with st.container(border=True):
            ui.card_title("Bots and fetchers", "Requests per agent from Cloudflare analytics. Install the "
                          "tracker to see browser-based agents, AI referrals and the full overview.")
            plot(charts.hbars([b["name"] for b in bots], [b["requests"] for b in bots],
                              [charts.CLASS_COLOURS.get(b["class"], "#898781") for b in bots], fmt=",d"))
        st.stop()
    daily = d["daily"]
    chg = k["agent_share_change_pts"] or 0
    shown = visible_classes(d)
    ui.kpi_row([
        ui.kpi("Sessions", f"{k['sessions']:,}", f"{k['agent_sessions']:,} from agents",
               spark=[r["total"] for r in daily], colour="#86b6ef"),
        ui.kpi("Agent share of traffic" if "crawler" in shown else "Agent share (browser-visible)",
               pct(k["agent_share"]), f"{chg:+.1f} pts over period",
               tone="neutral", spark=[r["agent_share"] or 0 for r in daily],
               help="Share of sessions from agents this store's data sources can see."),
        ui.kpi("Visits referred by AI", f"{k['ai_referred_visits']:,}", "from AI assistants",
               spark=[r["ai_referred"] for r in daily], colour="#4a3aa7",
               help="People who clicked through from an AI assistant's answer."),
        ui.kpi("AI-influenced revenue", money(k["ai_influenced_revenue"]),
               f"{k['ai_influenced_orders']} order{'' if k['ai_influenced_orders'] == 1 else 's'} · "
               f"{k['ai_channel_orders']} inside AI apps", tone="up",
               spark=pd.Series([r["ai_influenced_revenue"] or 0 for r in daily]).rolling(7, 1).mean(),
               colour="#0ca30c",
               help="Orders placed by an agent, or by a person an AI assistant referred."),
        ui.kpi("Orders flagged", f"{k['flagged_orders']}", "need review",
               tone="down" if k["flagged_orders"] else "neutral"),
    ])
    st.write("")

    left, right = st.columns([2, 1], gap="medium")
    with left, st.container(border=True):
        units = {r.get("crawler_units") for r in daily}
        since = d.get("edge_since")
        if units == {"requests"}:
            note = " Crawler and scraper bars are requests (Cloudflare analytics); the rest are sessions."
        elif "requests" in units and since:
            note = (f" Crawler and scraper bars are Cloudflare request counts up to "
                    f"{pd.Timestamp(since):%d %b} (before the edge Worker started), sessions after.")
        else:
            note = " Sessions."
        ui.card_title("Agent traffic", "Per day by class. People excluded so the mix is visible." + note)
        plot(charts.agent_traffic(daily, shown))
    with right, st.container(border=True):
        ui.card_title("Recent activity", "Notable agent behaviour, newest first.")
        colour = {"high": "#d03b3b", "medium": "#eb6834", "info": "#2a78d6"}
        items = [
            {"ts": pd.Timestamp(a["ts"]), "colour": colour.get(a["severity"], "#c3c2b7"),
             "text": ui.esc(a["text"]), "meta": a["detail"]}
            for a in d["activity"][:6]
        ]
        ui.feed(items, pd.Timestamp(d["end"]))

    left, right = st.columns([3, 2], gap="medium")
    with left, st.container(border=True):
        ui.card_title("Visitor journey", "Share of each segment's visits reaching each step.")
        plot(charts.funnel(d["funnel"]))
        with st.expander("Counts"):
            st.dataframe(pd.DataFrame([{
                "Segment": f["label"], "Visits": f["visits"], "Viewed product": f["viewed_product"],
                "Added to cart": f["added_to_cart"], "Started checkout": f["started_checkout"],
                "Ordered": f["ordered"]} for f in d["funnel"]]),
                hide_index=True, use_container_width=True)
    with right, st.container(border=True):
        ui.card_title("Who the agents are", "Sessions by agent this period.")
        top = [t for t in d["top_agents"] if t["class"] in shown]
        if top:
            plot(charts.hbars([t["name"] for t in top], [t["sessions"] for t in top],
                              [charts.CLASS_COLOURS[t["class"]] for t in top], fmt=",d"))
        bots = d.get("edge_agents") or []
        if bots:
            ui.card_title("Bots and fetchers", "Requests per agent, Cloudflare analytics.")
            plot(charts.hbars([b["name"] for b in bots[:8]], [b["requests"] for b in bots[:8]],
                              [charts.CLASS_COLOURS.get(b["class"], "#898781") for b in bots[:8]], fmt=",d"))

# ---------------------------------------------------------------------------
# AI referrals
# ---------------------------------------------------------------------------
elif page == "AI referrals":
    d = load("referrals")
    header("AI referrals", d)
    k = d.get("kpis")
    if not k:
        empty("No visits from AI assistants in this period.")
        st.stop()
    ui.kpi_row([
        ui.kpi("AI-referred visits", f"{k['visits']:,}",
               f"{pct(k['share_of_human_visits'])} of human visits",
               spark=[sum(v for kk, v in r.items() if kk != "date") for r in d["daily"]], colour="#4a3aa7"),
        ui.kpi("Conversion", pct(k["conversion"]), rel(k["conversion"], k["conversion_other"]),
               tone="up" if (k["conversion"] or 0) > (k["conversion_other"] or 0) else "down"),
        ui.kpi("Average order", money(k["aov"]), rel(k["aov"], k["aov_other"]),
               tone="up" if (k["aov"] or 0) > (k["aov_other"] or 0) else "down"),
        ui.kpi("Revenue from AI referrals", money(k["revenue"]), f"{k['orders']} orders", tone="up"),
    ])
    st.write("")
    left, right = st.columns([3, 2], gap="medium")
    with left, st.container(border=True):
        ui.card_title("Referrals per day", "By the assistant that sent them.")
        plot(charts.referrals_daily(d["daily"], d["sources"]))
    with right, st.container(border=True):
        ui.card_title("By assistant", "Visits, orders and revenue.")
        st.dataframe(pd.DataFrame([{
            "Assistant": s["source"], "Visits": s["visits"], "Orders": s["orders"],
            "Conversion": pct(s["conversion"]), "Revenue": money(s["revenue"])} for s in d["by_source"]]),
            hide_index=True, use_container_width=True)
        st.markdown(
            '<div class="at-card-sub" style="margin-top:10px">An AI-referred visit is a person, not an '
            "agent: someone asked an assistant, got a recommendation with a link, and clicked it. "
            "Shopify's own reports don't break this out.</div>", unsafe_allow_html=True)
    with st.container(border=True):
        ui.card_title("Where they land", "Top landing pages for AI-referred visits.")
        lp = d["landing_pages"][:8]
        plot(charts.hbars(
            [p["path"].replace("/products/", "").replace("/collections/", "▸ ") or "/" for p in lp],
            [p["visits"] for p in lp], ["#4a3aa7"] * len(lp), fmt=",d"))

# ---------------------------------------------------------------------------
# Agent sessions
# ---------------------------------------------------------------------------
elif page == "Agent sessions":
    classes = {"All agents": None, "AI assistant": "assistant", "Browser automation": "automation",
               "AI crawler": "crawler", "Scraper": "scraper"}
    base = load("sessions", limit=1)
    header("Agent sessions", base)
    counts = base.get("counts_by_class") or {}
    req_counts = base.get("request_counts_by_class") or {}
    if not counts and not req_counts:
        empty("No agent sessions recorded for this store yet.")
        st.stop()
    shown = visible_classes(base)
    ui.kpi_row([
        ui.kpi(charts.CLASS_LABELS[c],
               "—" if c not in shown else (f"{counts.get(c, 0):,}" if counts.get(c) or not req_counts.get(c)
                                           else f"{req_counts[c]:,}"),
               "needs edge data" if c not in shown else
               ("requests · Cloudflare" if not counts.get(c) and req_counts.get(c) else "sessions"),
               colour=charts.CLASS_COLOURS[c])
        for c in ["assistant", "automation", "crawler", "scraper"]
    ])
    if "crawler" not in shown:
        classes = {k: v for k, v in classes.items() if v in (None, "assistant", "automation")}
    st.write("")

    with st.container(border=True):
        ui.card_title("Sessions", "Every session classified as an agent, newest first.")
        choice = st.radio("Class", list(classes), horizontal=True, label_visibility="collapsed")
        data = load("sessions", cls=classes[choice], limit=300)
        rows = data["sessions"]
        st.dataframe(pd.DataFrame([{
            "Seen": pd.Timestamp(r["first_seen"]).tz_convert("Australia/Sydney").strftime("%d %b %H:%M"),
            "Agent": r["agent"], "Class": r["class_label"], "Confidence": r["confidence"],
            "Landing page": r["landing_path"],
            "Cart": "" if r["cart_value"] is None else money(r["cart_value"]),
            "Ordered": r["ordered"], "Signals": r["signals"]} for r in rows]),
            hide_index=True, use_container_width=True, height=330,
            column_config={
                "Confidence": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
                "Ordered": st.column_config.CheckboxColumn()})
        st.markdown(f'<div class="at-card-sub">Showing {len(rows)} of {data["total"]:,}.</div>',
                    unsafe_allow_html=True)

    if "crawler" in shown:
        files = load("overview").get("agent_files") or []
        if files:
            with st.container(border=True):
                ui.card_title("What agents read", "Fetches of agent-facing files, from edge data. "
                              "agents.md and llms.txt tell agents how to use the store; products.json "
                              "is a favourite of price scrapers.")
                cols = st.columns(min(len(files), 6))
                for col, fl in zip(cols, files[:6]):
                    tops = ", ".join(f"{t['agent']} ({t['count']})" for t in fl["top_agents"][:3])
                    col.markdown(ui.kpi(fl["file"], f"{fl['count']:,}",
                                        f"{fl['unit']} · {fl['agents']} agents"),
                                 unsafe_allow_html=True)
                    col.markdown(f'<div class="at-card-sub">{ui.esc(tops)}</div>', unsafe_allow_html=True)

    # Detail: lead with undeclared agents caught on behaviour that bought something.
    pool = load("sessions", cls="automation", limit=300)["sessions"] + \
        load("sessions", cls="assistant", limit=100)["sessions"]
    pool.sort(key=lambda r: (r["behaviour_only"], r["ordered"], r["cart_value"] or 0), reverse=True)
    if pool:
        with st.container(border=True):
            ui.card_title("Why a session was flagged", "Pick a session to see the evidence behind its class.")
            labels = {
                r["session_key"]: f"{r['agent']} · {r['class_label']} · "
                f"{pd.Timestamp(r['first_seen']).tz_convert('Australia/Sydney'):%d %b %H:%M}"
                + (" · ordered" if r["ordered"] else " · carted" if r["cart_value"] else "")
                for r in pool[:150]
            }
            key = st.selectbox("Session", list(labels), format_func=labels.get, label_visibility="collapsed")
            det = load("session", session_key=key)
            a, b = st.columns([3, 2], gap="large")
            with a:
                st.markdown(
                    f'<div class="at-finding-title">{ui.esc(det["agent"])} '
                    f'<span class="at-finding-meta">· {ui.esc(det["class_label"])} · confidence '
                    f'{det["confidence"]:.2f}</span></div>', unsafe_allow_html=True)
                dot = charts.CLASS_COLOURS.get(det["class"], "#898781")
                sig = "".join(
                    f'<div class="at-signal"><span style="color:{dot}">●</span>'
                    f'<span>{ui.esc(x["text"])}</span></div>' for x in det["reasons"]
                ) or '<div class="at-signal">No rule fired.</div>'
                st.markdown(sig, unsafe_allow_html=True)
            with b:
                sd = det["signals_detail"]

                def pair(x, y):
                    return "—" if x is None else f"{x} / {y if y is not None else 0}"

                ui.kv_list({
                    "Landing page": det["landing_path"] or "—",
                    "Referrer": det["referrer"] or "none",
                    "Ran JavaScript": "yes" if det["js_executed"] else "no",
                    "Clicks / no mouse trail": pair(sd["clicks"], sd["clicks_without_mouse_trail"]),
                    "Fields filled / no keys": pair(sd["fields_filled"], sd["fields_filled_without_keys"]),
                    "Automation fingerprints": ", ".join(sd["automation_tells"]) or "none",
                    "Cart": money(det["cart_value"]),
                    "Checkout time": f"{sd['checkout_seconds']:.0f}s" if sd["checkout_seconds"] else "—",
                })
                reqs = det.get("edge_requests") or []
                if reqs:
                    st.markdown('<div class="at-card-sub" style="margin-top:10px"><b>Requests seen at the edge'
                                '</b></div>' + "".join(f'<div class="at-signal"><code>{ui.esc(x)}</code></div>'
                                                       for x in reqs), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
elif page == "Orders":
    d = load("orders")
    header("Orders", d)
    k = d.get("kpis")
    if not k:
        empty("No orders recorded for this store yet. Connect the Shopify order webhook.")
        st.stop()
    n = d["min_sample"]
    mult = k["agent_dispute_multiple"]
    rate = k["agent_dispute_rate"]
    if rate is None:
        dispute_note, dispute_tone = f"needs {n}+ agent orders", "neutral"
    elif mult is None:
        dispute_note, dispute_tone = "human rate not yet measurable", "neutral"
    else:
        dispute_note = f"{mult:.1f}× the human rate"
        dispute_tone = "down" if mult > 1 else "up"
    ui.kpi_row([
        ui.kpi("Orders", f"{k['orders']:,}", money(k["revenue"]) + " revenue"
               + (f" · {k['test_orders']} test, not in revenue" if k.get("test_orders") else "")),
        ui.kpi("Placed by agents", f"{k['agent_orders']:,}", money(k["agent_revenue"]) + " revenue",
               colour=charts.CLASS_COLOURS["automation"]),
        ui.kpi("Agent dispute rate", pct(rate), dispute_note, tone=dispute_tone,
               help="Share of agent-placed orders that ended in a dispute or chargeback."),
        ui.kpi("Orders flagged", f"{k['flagged_orders']}", "review before fulfilling",
               tone="down" if k["flagged_orders"] else "neutral"),
    ])
    st.write("")
    segs = [s for s in d["by_segment"] if s["orders"]]
    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        ui.card_title("Average order value", "AUD, by who placed the order.")
        plot(charts.hbars([f"{s['label']}  (n={s['orders']})" for s in segs], [s["aov"] for s in segs],
                          [charts.SEGMENT_COLOURS[s["segment"]] for s in segs], fmt=",.0f", prefix="$"))
    with right, st.container(border=True):
        ui.card_title("Disputes and chargebacks", f"Segments with fewer than {n} orders are left out.")
        rated = [s for s in segs if s["dispute_rate"] is not None]
        if rated:
            plot(charts.hbars([f"{s['label']}  (n={s['orders']})" for s in rated],
                              [s["dispute_rate"] for s in rated],
                              [charts.SEGMENT_COLOURS[s["segment"]] for s in rated], fmt=".1%"))
    with st.container(border=True):
        ui.card_title("Order sources", "Shopify's sales channel for each order. Orders placed inside an AI "
                      "assistant never visit the store; the order feed is the only way to see them.")
        st.dataframe(pd.DataFrame([{
            "Sales channel (source_name)": s["source_name"], "AI channel": s["ai_channel"] or "",
            "Orders": s["orders"], "Test orders": s.get("test_orders", 0),
            "Revenue (excl. test)": money(s["revenue"])} for s in d["by_source_name"]]),
            hide_index=True, use_container_width=True)
    with st.container(border=True):
        ui.card_title("Flagged orders", "Discount requests typed into address or note fields, and agent "
                      "orders using discount codes. Models don't treat this as harmful, so agents keep trying.")
        if d["flagged"]:
            st.dataframe(pd.DataFrame([{
                "Placed": pd.Timestamp(f["created_at"]).tz_convert("Australia/Sydney").strftime("%d %b %H:%M"),
                "Order": f["shopify_order_id"], "Placed by": f["class_label"], "Value": money(f["order_value"]),
                "Flag": " · ".join(f["flags"]), "Text found": f["free_text"] or "", "Outcome": f["outcome"],
            } for f in d["flagged"]]), hide_index=True, use_container_width=True)
        else:
            st.markdown('<div class="at-card-sub">Nothing flagged this period.</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Threat testing
# ---------------------------------------------------------------------------
elif page == "Threat testing":
    d = load("threats")
    header("Threat testing", d, pill="REAL RESULTS", coverage=False)
    k = d.get("kpis")
    if not k:
        empty("No threat-test runs for this store yet. Run agents through harness/tasks.py.")
        st.stop()
    st.markdown('<div class="at-card-sub" style="margin:-6px 0 10px">Real agents run through buying and '
                "abuse tasks on the store. These are actual test results, never synthetic.</div>",
                unsafe_allow_html=True)
    ui.kpi_row([
        ui.kpi("Runs", f"{k['runs']}"),
        ui.kpi("Agent surfaces", f"{k['agent_surfaces']}"),
        ui.kpi("Exploits found", f"{k['exploits']}", "needs a fix" if k["exploits"] else None,
               tone="down" if k["exploits"] else "neutral"),
        ui.kpi("Need review", f"{k['needs_review']}"),
    ])
    st.write("")
    left, right = st.columns([3, 1], gap="medium")
    with left, st.container(border=True):
        ui.card_title("Results by agent and task", "Latest run per cell. ⚠ marks an exploit; hover for notes.")
        plot(charts.threat_matrix(d["matrix"], d["surfaces"], d["tasks"]))
    with right, st.container(border=True):
        ui.card_title("Severity")
        plot(charts.severity_bars(k["by_severity"]))
    with st.container(border=True):
        ui.card_title("Findings", "What broke, and what to change.")
        text_colour = {"Needs review": "#5c4400"}
        rows = []
        for f in d["findings"]:
            bg = charts.SEVERITY_COLOURS[f["severity"]]
            rows.append(
                f'<div class="at-finding"><span class="at-sev" style="background:{bg};'
                f'color:{text_colour.get(f["severity"], "white")}">{ui.esc(f["severity"])}</span>'
                f'<span class="at-finding-title">{ui.esc(f["task"].replace("_", " ").capitalize())}</span>'
                f'<span class="at-finding-meta"> · {ui.esc(f["agent_surface"])} · '
                f'{pd.Timestamp(f["run_at"]).tz_convert("Australia/Sydney"):%d %b}</span>'
                f'<div class="at-finding-body">{ui.esc(f["notes"])}</div>'
                + (f'<div class="at-fix">Fix: {ui.esc(f["fix"])}</div>' if f["fix"] else "")
                + "</div>")
        st.markdown("".join(rows), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
elif page == "Connections":
    ui.page_header("Connections", shop_label(shop), demo=False, live_label="SETUP")
    if not client.connections_available():
        empty("No database or API configured, so connections can't be managed from here.")
        st.stop()

    from app.shops import normalise_shop
    entered = st.text_input("Store (myshopify domain)", value="" if shop == DEMO_SHOP else shop,
                            help="Pick a store in the sidebar, or type a new one to connect it first.")
    shop = normalise_shop(entered) or ""
    if not shop:
        empty("Enter the store's myshopify domain to manage its connections.")
        st.stop()

    with st.container(border=True):
        ui.card_title("Cloudflare analytics", "Two-minute setup, nothing to deploy. Shows AI crawlers, "
                      "assistants and scrapers as hourly request counts, and who reads your agent files.")
        try:
            status = client.cf_status(shop)
        except Exception as exc:
            st.error(f"Couldn't load status: {exc}")
            status = {"connected": False}

        if status.get("connected"):
            when = status.get("synced_through") or "not yet"
            ui.kv_list({
                "Status": "Error: " + status["last_error"] if status.get("last_error") else "Connected",
                "Zone ID": status.get("zone_id") or "—",
                "Data through": when.replace("T", " ")[:16] + (" UTC" if "T" in when else ""),
                "Last sync": (status.get("last_sync_at") or "—").replace("T", " ")[:16],
            })
            a, b, _ = st.columns([1, 1, 3])
            if a.button("Sync now"):
                with st.spinner("Pulling from Cloudflare…"):
                    try:
                        res = client.cf_sync(shop)
                        if res.get("error"):
                            st.error(res["error"])
                        else:
                            st.success(f"Synced {res['rows']:,} rows.")
                    except Exception as exc:
                        st.error(str(exc))
            if b.button("Disconnect"):
                client.cf_disconnect(shop)
                client.shops.clear()
                st.rerun()
        else:
            st.markdown(
                '<div class="at-card-sub" style="line-height:1.6">'
                "1. In Cloudflare, open <b>Manage account → Account API tokens → Create token → Start from scratch</b>.<br>"
                "2. Scope: <b>Specified Domains →</b> the store's domain. Tick only <b>Analytics &amp; Logs → "
                "Zone Analytics → Read</b>. Create it and copy the token (starts <code>cfat_</code>).<br>"
                "3. Copy the <b>Zone ID</b> (not the Account ID) from the domain's Overview page, right-hand "
                "column under API.<br>"
                "The token is tested before it's saved, stored encrypted, and never shown again.</div>",
                unsafe_allow_html=True)
            with st.form("cf_connect"):
                zone = st.text_input("Zone ID", placeholder="32 hex characters")
                token = st.text_input("API token", type="password")
                if st.form_submit_button("Connect Cloudflare"):
                    if len(token.strip()) < 20:
                        st.error("Paste the API token first (the long value that starts cfat_).")
                        st.stop()
                    with st.spinner("Testing the token and pulling the last 7 days…"):
                        try:
                            res = client.cf_connect(shop, zone.strip(), token.strip())
                            client.shops.clear()
                            if res.get("last_error"):
                                st.warning(f"Connected, but the first sync failed: {res['last_error']}")
                            else:
                                st.success("Connected. Crawler and agent-file data now appear on the "
                                           "Overview and Agent sessions pages.")
                        except Exception as exc:
                            st.error(str(exc))

    with st.container(border=True):
        ui.card_title("Edge Worker (full detail)", "Per-session detail for traffic that never runs "
                      "JavaScript: signed agents, HTTP libraries, headless clients. Needs a Cloudflare "
                      "Worker deployed on the store's domain.")
        try:
            key = client.edge_key(shop)
        except Exception as exc:
            key = None
            st.error(f"Couldn't create the Worker key: {exc}")
        if key:
            st.markdown(
                '<div class="at-card-sub" style="line-height:1.6">'
                "1. In Cloudflare: <b>Workers &amp; Pages → Create → Create Worker</b>, name it "
                "<code>agent-trust-edge</code>, Deploy, then <b>Edit code</b>. Replace everything with the "
                "code below and Deploy.<br>"
                "2. <b>Settings → Variables and Secrets</b>: add the four values below "
                "(the two marked secret as type <b>Secret</b>).<br>"
                "3. <b>Settings → Domains &amp; Routes → Add → Route</b>: zone = the store's domain, "
                "route = <code>*yourdomain.com/*</code> (no dot after the star: covers the domain and www). "
                "Set <b>Failure mode</b> to <b>Fail open (proceed)</b>.<br>"
                "4. <b>Settings → Trigger events → Add → Cron trigger</b>: every 10 minutes "
                "(<code>*/10 * * * *</code>). Keeps a free-tier API awake so no records are lost.</div>",
                unsafe_allow_html=True)
            st.code(
                f"AGENT_TRUST_INGEST_URL = {client.ingest_url()}\n"
                f"AGENT_TRUST_SHOP       = {shop}\n"
                f"AGENT_TRUST_EDGE_KEY   = {key}    (secret)\n"
                f"IP_SALT                = {secrets.token_hex(24)}    (secret, any random string)",
                language=None)
            with st.expander("Worker code (copy all)"):
                st.code(WORKER_JS, language="javascript")
