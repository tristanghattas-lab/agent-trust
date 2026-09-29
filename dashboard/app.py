"""
Streamlit dashboard — reads straight from Postgres, no separate API layer.

Two sections matching the two product modes:
  1. Visibility & optimisation — agent sessions joined to order outcomes.
  2. Threat testing — results logged via harness/log_run.py.

Run with: streamlit run dashboard/app.py
"""
import os
import sys

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://agenttrust:agenttrust@localhost:5432/agenttrust"
)
engine = create_engine(DATABASE_URL)

st.set_page_config(page_title="Agent Trust & Commerce Intelligence", layout="wide")
st.title("Agent Trust & Commerce Intelligence")
st.caption("Pilot dashboard — United Cellars")

tab_visibility, tab_threat = st.tabs(["Visibility & optimisation", "Threat testing"])

# ---------------------------------------------------------------------------
# Mode 1: visibility & optimisation
# ---------------------------------------------------------------------------
with tab_visibility:
    sessions_df = pd.read_sql("SELECT * FROM sessions ORDER BY last_seen DESC", engine)

    if sessions_df.empty:
        st.info(
            "No session data yet. Run `python scripts/seed_demo.py` for demo data, "
            "or install the tracker snippet on the site to start capturing real traffic."
        )
    else:
        col1, col2, col3, col4 = st.columns(4)
        total = len(sessions_df)
        agent_sessions = sessions_df[sessions_df["is_agent"]]
        col1.metric("Total sessions", total)
        col2.metric(
            "Agent sessions",
            len(agent_sessions),
            f"{len(agent_sessions) / total:.0%} of traffic" if total else None,
        )
        col3.metric(
            "Checkout completion (agents)",
            f"{(agent_sessions['checkout_completed_at'].notna().mean() if len(agent_sessions) else 0):.0%}",
        )
        col4.metric(
            "Age-gate fail rate (agents)",
            f"{(agent_sessions.loc[agent_sessions['age_gate_shown'], 'age_gate_passed'].eq(False).mean() if agent_sessions['age_gate_shown'].any() else 0):.0%}",
        )

        st.subheader("Agent sessions by family")
        if not agent_sessions.empty:
            st.bar_chart(agent_sessions["agent_family"].value_counts())

        st.subheader("Sessions joined to order outcomes")
        orders_df = pd.read_sql("SELECT * FROM orders", engine)
        outcomes_df = pd.read_sql("SELECT * FROM outcomes", engine)

        if not orders_df.empty:
            joined = sessions_df.merge(
                orders_df, on="session_key", how="inner", suffixes=("_session", "_order")
            )
            if not outcomes_df.empty:
                joined = joined.merge(
                    outcomes_df, left_on="id_order", right_on="order_id", how="left"
                )
            display_cols = [
                c
                for c in [
                    "session_key",
                    "is_agent",
                    "agent_family",
                    "classification_confidence",
                    "order_value",
                    "shipping_state",
                    "age_verified",
                    "allocation_flagged",
                    "outcome_type",
                ]
                if c in joined.columns
            ]
            st.dataframe(joined[display_cols], use_container_width=True)
        else:
            st.caption(
                "No orders joined yet — connect the Shopify order feed to populate this."
            )

        with st.expander("Raw session log"):
            st.dataframe(sessions_df, use_container_width=True)

# ---------------------------------------------------------------------------
# Mode 2: threat testing
# ---------------------------------------------------------------------------
with tab_threat:
    runs_df = pd.read_sql(
        "SELECT * FROM threat_test_runs ORDER BY run_at DESC", engine
    )

    if runs_df.empty:
        st.info(
            "No threat-test runs logged yet. Run `python -m harness.log_run` "
            "after operating an agent through a task from harness/tasks.py."
        )
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Runs logged", len(runs_df))
        col2.metric("Fails", int((runs_df["result"] == "fail").sum()))
        col3.metric("Exploits found", int(runs_df["exploit_found"].sum()))

        st.subheader("Result by agent surface")
        pivot = pd.crosstab(runs_df["agent_surface"], runs_df["result"])
        st.bar_chart(pivot)

        st.subheader("Exploit findings")
        exploits = runs_df[runs_df["exploit_found"]]
        if not exploits.empty:
            st.dataframe(
                exploits[
                    ["run_at", "agent_surface", "task_name", "task_category", "friction_notes"]
                ],
                use_container_width=True,
            )
        else:
            st.caption("No exploit-level findings logged yet.")

        with st.expander("All runs"):
            st.dataframe(runs_df, use_container_width=True)
