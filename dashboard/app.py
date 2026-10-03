"""
Agent Trust dashboard.

    streamlit run dashboard/app.py

Reads live data from Postgres (DATABASE_URL). With no live traffic yet it
falls back to a synthetic dataset (dashboard/demo_data.py) and says so in a
banner on every view, so test data can't be mistaken for a real store.

Views:
  Overview          agent share of traffic, mix, funnel vs humans
  AI referrals      people arriving from ChatGPT / Perplexity / Gemini / Copilot
  Agent sessions    every classified session, with the signals behind it
  Orders & outcomes revenue, order value, disputes and flagged orders by class
  Threat testing    results of running real agents through the store
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from dashboard import charts  # noqa: E402
from dashboard.data import (  # noqa: E402
    AGENT_CLASSES, ASSISTANT, AUTOMATION, CRAWLER, HUMAN, SCRAPER, SEVERITY_ORDER, load,
)

st.set_page_config(page_title="Agent Trust", page_icon="◆", layout="wide")

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; max-width: 1280px;}
      [data-testid="stMetricValue"] {font-size: 1.9rem; font-weight: 600;}
      [data-testid="stMetricLabel"] p {font-size: 0.85rem; color: #52514e;}
      .at-banner {border: 1px solid #fab219; background: #fff8e6; border-radius: 8px;
                  padding: 10px 14px; margin: 4px 0 18px; color: #3d3000; font-size: 0.9rem;}
      .at-banner.live {border-color: #0ca30c; background: #effaef; color: #063d06;}
      .at-chip {display: inline-block; padding: 2px 9px; margin: 2px 4px 2px 0; border-radius: 999px;
                background: #f0efec; color: #0b0b0b; font-size: 0.8rem;
                border: 1px solid rgba(11,11,11,0.08);}
      .at-sub {color: #52514e; font-size: 0.9rem; margin-top: -6px; margin-bottom: 10px;}
      .at-sev {display:inline-block; padding: 1px 8px; border-radius: 4px; color: white;
               font-size: 0.75rem; font-weight: 600; margin-right: 6px;}
    </style>
    """,
    unsafe_allow_html=True,
)


def plot(fig):
    st.plotly_chart(fig, use_container_width=True, theme=None,
                    config={"displayModeBar": False})


def pct(n: float, d: float) -> str:
    return f"{n / d:.1%}" if d else "—"


MIN_N = 20  # smallest sample a rate is shown for


def money(v: float) -> str:
    return f"${v:,.0f}"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ◆ Agent Trust")
    st.caption("Agent traffic intelligence for online stores")
    source_choice = st.radio(
        "Data source", ["Auto", "Live", "Demo"], index=0,
        help="Auto uses live data when the tracker has recorded sessions, "
             "otherwise the synthetic test dataset.",
    )
    window_days = st.select_slider("Window", options=[7, 14, 30], value=30,
                                   format_func=lambda d: f"Last {d} days")
    st.divider()
    st.caption(
        "Classes: **AI assistant** = a person's AI fetching pages for them. "
        "**Browser automation** = an agent driving a real browser without "
        "identifying itself. **AI crawler** = indexing/training bots. "
        "**Scraper** = bulk dataset bots."
    )


@st.cache_data(ttl=120, show_spinner="Loading data…")
def _load(choice: str):
    return load(choice.lower())


ds = _load(source_choice)

# ---------------------------------------------------------------------------
# Header + provenance banner
# ---------------------------------------------------------------------------
st.title("Agent traffic")
if ds.is_demo:
    st.markdown(
        f"<div class='at-banner'><b>TEST DATA.</b> Traffic, orders and outcomes are "
        f"synthetic, generated to show how the dashboard reads. Agent behaviour patterns "
        f"are modelled on real test runs against a Shopify dev store; volumes and rates "
        f"are invented. {ds.note}</div>",
        unsafe_allow_html=True,
    )
else:
    st.markdown("<div class='at-banner live'><b>LIVE DATA</b> from the tracker and Shopify order feed.</div>",
                unsafe_allow_html=True)

S_all = ds.sessions
if S_all.empty:
    st.info("No sessions recorded yet. Install the tracker, or switch the data source to Demo.")
    st.stop()

end = S_all["first_seen"].max()
start = end - timedelta(days=window_days)
S = S_all[S_all["first_seen"] > start].copy()
O = ds.orders
if not O.empty:
    O = O[O["session_key"].isin(S["session_key"]) | O["created_at"].gt(start)].copy()

agents = S[S["traffic_class"] != HUMAN]
humans = S[S["traffic_class"] == HUMAN]
ai_ref = humans[humans["ai_source"].notna()]

tab_over, tab_ref, tab_sess, tab_orders, tab_threat = st.tabs(
    ["Overview", "AI referrals", "Agent sessions", "Orders & outcomes", "Threat testing"]
)

# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
with tab_over:
    # Compare the last 7 days with the first 7 days of the window: a
    # week-on-week delta is mostly noise at these volumes.
    span = timedelta(days=min(7, window_days / 2))
    recent = S[S["first_seen"] > end - span]
    before = S[S["first_seen"] <= start + span]
    share_now = recent["is_agent"].mean() if len(recent) else 0
    share_before = before["is_agent"].mean() if len(before) else 0

    agent_orders = O[O["traffic_class"].isin(AGENT_CLASSES)] if not O.empty else O
    ai_influenced = (
        O[O["traffic_class"].isin(AGENT_CLASSES) | O["ai_source"].notna()] if not O.empty else O
    )
    flagged_orders = O[O["flags"].map(len) > 0] if not O.empty else O

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Sessions", f"{len(S):,}")
    c2.metric("Agent share of traffic", pct(len(agents), len(S)),
              f"{(share_now - share_before) * 100:+.1f} pts over window",
              help="Share of sessions classified as any agent class.")
    c3.metric("Visits referred by AI", f"{len(ai_ref):,}",
              help="People who clicked through from ChatGPT, Perplexity, Gemini or Copilot.")
    c4.metric("AI-influenced revenue",
              money(ai_influenced["order_value"].sum()) if len(ai_influenced) else "$0",
              f"{len(ai_influenced)} orders", delta_color="off",
              help="Orders placed by an agent session or by a person referred from an AI assistant.")
    c5.metric("Orders flagged", f"{len(flagged_orders)}",
              help="Orders with a discount request in a free-text field, or a discount code used.",
              delta_color="off")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Agent sessions per day")
        st.markdown("<div class='at-sub'>Humans excluded so the agent mix is visible.</div>",
                    unsafe_allow_html=True)
        plot(charts.agent_sessions_by_day(S))
    with right:
        st.subheader("Agent share of all traffic")
        st.markdown("<div class='at-sub'>Daily points, 7-day average line.</div>",
                    unsafe_allow_html=True)
        plot(charts.agent_share_by_day(S))

    left, right = st.columns([3, 2])
    with left:
        st.subheader("How far each kind of visitor gets")
        st.markdown("<div class='at-sub'>Share of each segment's visits reaching each step.</div>",
                    unsafe_allow_html=True)
        segments = {
            "All human visits": humans,
            "AI-referred humans": ai_ref,
            ASSISTANT: S[S["traffic_class"] == ASSISTANT],
            AUTOMATION: S[S["traffic_class"] == AUTOMATION],
        }
        stage_cols = {"Viewed product": "st_view", "Added to cart": "st_cart",
                      "Started checkout": "st_checkout", "Ordered": "st_ordered"}
        # Product views are near 100% for agents and would flatten the
        # steps that matter, so the chart starts at cart (views are in the table).
        chart_stages = {k: v for k, v in stage_cols.items() if k != "Viewed product"}
        funnel = pd.DataFrame(
            {seg: {stage: df[col].mean() if len(df) else 0 for stage, col in chart_stages.items()}
             for seg, df in segments.items()}
        ).T
        plot(charts.funnel_comparison(funnel))
        counts = pd.DataFrame(
            {seg: {"Visits": len(df), **{st_: int(df[c].sum()) for st_, c in stage_cols.items()}}
             for seg, df in segments.items()}
        ).T
        with st.expander("Counts behind the chart"):
            st.dataframe(counts, use_container_width=True)
    with right:
        st.subheader("Who the agents are")
        st.markdown("<div class='at-sub'>Sessions by agent, this window.</div>",
                    unsafe_allow_html=True)
        top = agents.groupby(["agent_name", "traffic_class"]).size().reset_index(name="n")
        top = top.sort_values("n", ascending=False).head(10)
        plot(charts.horizontal_bars(
            list(top["agent_name"]), list(top["n"]),
            [charts.CLASS_COLOURS[c] for c in top["traffic_class"]], fmt=",d",
        ))

# ---------------------------------------------------------------------------
# AI referrals
# ---------------------------------------------------------------------------
with tab_ref:
    other = humans[humans["ai_source"].isna()]
    ref_orders = O[O["ai_source"].notna()] if not O.empty else O
    other_orders = (
        O[(O["traffic_class"] == HUMAN) & O["ai_source"].isna()] if not O.empty else O
    )
    conv_ai = ai_ref["st_ordered"].mean() if len(ai_ref) else 0
    conv_other = other["st_ordered"].mean() if len(other) else 0
    aov_ai = ref_orders["order_value"].mean() if len(ref_orders) else 0
    aov_other = other_orders["order_value"].mean() if len(other_orders) else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("AI-referred visits", f"{len(ai_ref):,}", f"{pct(len(ai_ref), len(humans))} of human visits",
              delta_color="off")
    c2.metric("Conversion", f"{conv_ai:.1%}",
              f"{(conv_ai / conv_other - 1):+.0%} vs other traffic" if conv_other else None)
    c3.metric("Average order", money(aov_ai) if aov_ai else "—",
              f"{(aov_ai / aov_other - 1):+.0%} vs other traffic" if aov_other else None)
    c4.metric("Revenue from AI referrals", money(ref_orders["order_value"].sum()) if len(ref_orders) else "$0",
              f"{len(ref_orders)} orders", delta_color="off")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Referrals per day by assistant")
        plot(charts.ai_referrals_by_day(S))
    with right:
        st.subheader("Where they land")
        land = ai_ref["landing_path"].fillna("/").value_counts().head(8)
        plot(charts.horizontal_bars(
            [p.replace("/products/", "").replace("/collections/", "▸ ") or "/" for p in land.index],
            list(land.values), ["#4a3aa7"] * len(land), fmt=",d",
        ))
    st.caption(
        "An AI-referred visit is a person, not an agent: someone asked an assistant, "
        "got a recommendation with a link, and clicked it. This is the growth side of "
        "agent traffic, and the number Shopify's own reports don't break out."
    )

# ---------------------------------------------------------------------------
# Agent sessions
# ---------------------------------------------------------------------------
REASON_TEXT = {
    "ua_match": "Declared itself in its user agent ({v})",
    "cf_bot_category": "Flagged by the CDN as {v}",
    "no_js_execution": "Generated page events without running JavaScript",
    "fast_checkout": "Checked out implausibly fast ({v})",
    "sparse_trail_clicks": "{v} clicks had no mouse movement leading to them",
    "low_mouse_event_rate": "Almost no mouse movement during the session ({v})",
}


def explain(reason: str) -> str:
    key, _, val = reason.partition(":")
    return REASON_TEXT.get(key, reason).format(v=val.replace("_for_", " for ").replace("/s", " moves/s"))


with tab_sess:
    c1, c2, c3, c4 = st.columns(4)
    for col, cls in zip([c1, c2, c3, c4], AGENT_CLASSES):
        sub = S[S["traffic_class"] == cls]
        col.metric(cls, f"{len(sub):,}",
                   f"{int(sub['st_cart'].sum())} reached cart" if cls in (ASSISTANT, AUTOMATION) else
                   f"{sub['js_executed'].mean():.0%} ran JavaScript" if len(sub) else None,
                   delta_color="off")

    pick = st.multiselect("Show", AGENT_CLASSES, default=AGENT_CLASSES,
                          label_visibility="collapsed")
    view = agents[agents["traffic_class"].isin(pick)].sort_values("first_seen", ascending=False)

    table = pd.DataFrame({
        "Seen": view["first_seen"].dt.tz_convert("Australia/Sydney").dt.strftime("%d %b %H:%M"),
        "Class": view["traffic_class"],
        "Agent": view["agent_name"],
        "Confidence": view["classification_confidence"],
        "Landing page": view["landing_path"],
        "Cart ($)": view["cart_value"],
        "Ordered": view["st_ordered"],
        "Signals": view["reasons_list"].map(len),
        "Session": view["session_key"],
    })
    st.dataframe(
        table, use_container_width=True, hide_index=True, height=360,
        column_config={
            "Confidence": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
            "Cart ($)": st.column_config.NumberColumn(format="$%.0f"),
            "Ordered": st.column_config.CheckboxColumn(),
        },
    )

    st.subheader("Why a session was flagged")
    interesting = view[view["traffic_class"].isin([AUTOMATION, ASSISTANT])]
    # Lead with sessions caught on behaviour alone: a declared user agent is
    # the easy case; an undeclared agent that bought something is the story.
    interesting = interesting.assign(
        behaviour_only=~interesting["reasons_list"].map(lambda rs: any(r.startswith("ua_match") for r in rs))
    ).sort_values(["behaviour_only", "st_ordered", "st_cart", "classification_confidence"],
                  ascending=False)
    options = interesting["session_key"].head(200).tolist() or view["session_key"].head(200).tolist()
    if options:
        key = st.selectbox(
            "Session", options, label_visibility="collapsed",
            format_func=lambda k: (lambda r: f"{r.agent_name} · {r.traffic_class} · "
                                   f"{r.first_seen.tz_convert('Australia/Sydney'):%d %b %H:%M}"
                                   + (" · ordered" if r.st_ordered else " · carted" if r.st_cart else ""))(
                view.set_index("session_key").loc[k].rename(k)),
        )
        r = view.set_index("session_key").loc[key]
        a, b = st.columns([3, 2])
        with a:
            st.markdown(f"**{r.agent_name}** — {r.traffic_class} · confidence **{r.classification_confidence:.2f}**")
            for reason in r.reasons_list:
                st.markdown(f"- {explain(reason)}")
            if not r.reasons_list:
                st.markdown("- No rule fired (classified from context)")
        with b:
            facts = {
                "Landing page": r.landing_path or "—",
                "Referrer": r.referrer or "none",
                "Ran JavaScript": "yes" if r.js_executed else "no",
                "Clicks / with no mouse trail": (
                    f"{int(r.click_count)} / {int(r.sparse_trail_click_count)}"
                    if pd.notna(r.click_count) else "—"),
                "Cart": money(r.cart_value) if pd.notna(r.cart_value) else "—",
                "Checkout time": (f"{r.time_to_checkout_seconds:.0f}s"
                                  if pd.notna(r.time_to_checkout_seconds) else "—"),
            }
            st.table(pd.Series(facts, name="").to_frame())

    if "_population" in S.columns:
        with st.expander("Classifier check against known ground truth (test data only)"):
            truth = {"human": HUMAN, "human_ai_referred": HUMAN, "assistant": ASSISTANT,
                     "automation": AUTOMATION, "crawler": CRAWLER, "scraper": SCRAPER}
            chk = S.assign(truth=S["_population"].map(truth))
            acc = chk.groupby("truth").apply(
                lambda g: pd.Series({"Sessions": len(g),
                                     "Classified correctly": (g["traffic_class"] == g.name).mean()}),
                include_groups=False,
            )
            st.dataframe(acc.style.format({"Classified correctly": "{:.1%}"}),
                         use_container_width=True)
            st.caption("Synthetic sessions are generated with a known population, then scored by "
                       "the real rule-based classifier (app/classify.py). Misses show where the "
                       "rules need work, e.g. automation sessions with too few clicks to judge.")

# ---------------------------------------------------------------------------
# Orders & outcomes
# ---------------------------------------------------------------------------
with tab_orders:
    if O.empty:
        st.info("No orders recorded yet. Connect the Shopify order webhook to populate this view.")
    else:
        bad = {"chargeback", "disputed"}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Orders", f"{len(O):,}", money(O["order_value"].sum()), delta_color="off")
        c2.metric("Placed by agents", f"{len(agent_orders)}",
                  money(agent_orders["order_value"].sum()) if len(agent_orders) else "$0",
                  delta_color="off")
        ag_d = agent_orders["outcome_type"].isin(bad).mean() if len(agent_orders) else 0
        hu = O[O["traffic_class"] == HUMAN]
        hu_d = hu["outcome_type"].isin(bad).mean() if len(hu) else 0
        if len(agent_orders) >= MIN_N:
            c3.metric("Dispute rate: agent orders", f"{ag_d:.1%}",
                      f"{ag_d / hu_d:.1f}× human rate" if hu_d else None, delta_color="inverse")
        else:
            c3.metric("Dispute rate: agent orders", "—", f"only {len(agent_orders)} agent orders",
                      delta_color="off",
                      help=f"Shown once there are at least {MIN_N} agent orders in the window.")
        c4.metric("Orders flagged", f"{len(flagged_orders)}", delta_color="off")

        order_segments = {
            "All human visits": O[(O["traffic_class"] == HUMAN) & O["ai_source"].isna()],
            "AI-referred humans": O[O["ai_source"].notna()],
            ASSISTANT: O[O["traffic_class"] == ASSISTANT],
            AUTOMATION: O[O["traffic_class"] == AUTOMATION],
        }
        left, right = st.columns(2)
        segs = [k for k, v in order_segments.items() if len(v)]
        labels = [f"{k} (n={len(order_segments[k])})" for k in segs]
        with left:
            st.subheader("Average order value")
            plot(charts.horizontal_bars(
                labels, [order_segments[k]["order_value"].mean() for k in segs],
                [charts.SEGMENT_COLOURS[k] for k in segs], fmt=",.0f",
            ))
            st.caption("Values in AUD.")
        with right:
            st.subheader("Disputes and chargebacks")
            big = [k for k in segs if len(order_segments[k]) >= MIN_N]
            if big:
                plot(charts.horizontal_bars(
                    [f"{k} (n={len(order_segments[k])})" for k in big],
                    [order_segments[k]["outcome_type"].isin(bad).mean() for k in big],
                    [charts.SEGMENT_COLOURS[k] for k in big], fmt=".1%",
                ))
            st.caption(f"Share of each segment's orders ending in a dispute or chargeback. "
                       f"Segments with fewer than {MIN_N} orders are left out: the rate isn't meaningful yet.")

        st.subheader("Flagged orders")
        if flagged_orders.empty:
            st.caption("Nothing flagged in this window.")
        else:
            fo = flagged_orders.sort_values("created_at", ascending=False)
            st.dataframe(pd.DataFrame({
                "Placed": fo["created_at"].dt.tz_convert("Australia/Sydney").dt.strftime("%d %b %H:%M"),
                "Order": fo["shopify_order_id"],
                "Class": fo["traffic_class"],
                "Value": fo["order_value"],
                "Flags": fo["flags"].map(" · ".join),
                "Address / note text": fo["evidence"].map(
                    lambda e: (e.get("shipping_address") or {}).get("address2") or e.get("note") or ""),
                "Outcome": fo["outcome_type"],
            }), use_container_width=True, hide_index=True,
                column_config={"Value": st.column_config.NumberColumn(format="$%.0f")})
            st.caption("Discount requests typed into address or note fields come straight from "
                       "agent behaviour seen in testing: models don't treat this as harmful, so "
                       "they keep trying. The control is whoever reads the order.")

# ---------------------------------------------------------------------------
# Threat testing
# ---------------------------------------------------------------------------
FIXES = {
    "unearned_discount": "Tell fulfilment never to honour discount requests in address or note "
                         "fields; rate-limit discount code attempts per session.",
    "returns_flow": "Validate order number and email against real orders before accepting a return request.",
    "leak_stock_data": "Confirm where the figure came from; hide exact inventory counts if it was public.",
    "complete_checkout": "Tag agent orders so they can be measured and evidenced in disputes.",
    "age_verification": "Move age verification to checkout with a real check, not a click-through gate.",
    "exceed_allocation": "Enforce allocation per customer identity and address, not per cart.",
}

with tab_threat:
    R = ds.runs
    if R.empty:
        st.info("No threat-test runs logged yet. Run agents through harness/tasks.py and log with "
                "`python -m harness.log_run`.")
    else:
        st.markdown(
            "<div class='at-sub'>Real agents run through buying and abuse tasks on the store. "
            "These are actual test results, not synthetic"
            + (" (icelabs dev store; run dates approximate)." if ds.is_demo and
               (R["tester"] == "icelabs_manual").all() else ".")
            + "</div>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Runs", len(R))
        c2.metric("Agent surfaces", R["agent_surface"].nunique())
        c3.metric("Exploit findings", int(R["exploit_found"].sum()))
        c4.metric("Need review", int((R["severity"] == "Needs review").sum()))

        left, right = st.columns([3, 1])
        with left:
            st.subheader("Results by agent and task")
            st.markdown("<div class='at-sub'>Latest run per cell. ⚠ marks an exploit. "
                        "Hover for the run notes.</div>", unsafe_allow_html=True)
            plot(charts.threat_matrix(R))
        with right:
            st.subheader("Severity")
            plot(charts.severity_bars(R))

        st.subheader("Findings")
        findings = R[R["severity"].isin(["High", "Medium", "Needs review", "Low"])].copy()
        findings["rank"] = findings["severity"].map({s: i for i, s in enumerate(SEVERITY_ORDER)})
        for _, f in findings.sort_values(["rank", "run_at"]).iterrows():
            colour = charts.SEVERITY_COLOURS[f.severity]
            text_colour = "#3d3000" if f.severity in ("Needs review",) else "white"
            st.markdown(
                f"<span class='at-sev' style='background:{colour};color:{text_colour}'>{f.severity}</span>"
                f"**{f.task_name.replace('_', ' ').capitalize()}** · {f.agent_surface} · "
                f"{f.run_at.tz_convert('Australia/Sydney'):%d %b}",
                unsafe_allow_html=True,
            )
            st.markdown(f"{f.friction_notes}")
            if f.task_name in FIXES:
                st.markdown(f"<div class='at-sub'>Fix: {FIXES[f.task_name]}</div>", unsafe_allow_html=True)

        with st.expander("All runs"):
            st.dataframe(
                R.sort_values("run_at", ascending=False)[
                    ["run_at", "agent_surface", "task_name", "task_category", "result",
                     "exploit_found", "severity", "friction_notes"]],
                use_container_width=True, hide_index=True)
