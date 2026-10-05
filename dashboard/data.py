"""
Data loading for the internal Streamlit dashboard.

Two sources, same shape:
- live: the four tables in Postgres (DATABASE_URL)
- demo: app/demo_data.py, generated in memory and never written back

Derived columns come from app/analytics.py, shared with the metrics API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import create_engine, text

from app.analytics import (  # noqa: F401  (re-exported for dashboard modules)
    AGENT_CLASSES, AI_REFERRER_SOURCES, ALL_CLASSES, ASSISTANT, AUTOMATION, CRAWLER, FIXES,
    HUMAN, SCRAPER, SEVERITY_ORDER, enrich_frames, explain,
)


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
    from app.demo_data import build_demo_dataset

    d = build_demo_dataset()
    return Dataset(
        source="demo",
        sessions=d["sessions"],
        orders=d["orders"],
        outcomes=d["outcomes"],
        runs=d["threat_test_runs"],
    )


def enrich(ds: Dataset) -> Dataset:
    s, o, oc, r = enrich_frames(ds.sessions, ds.orders, ds.outcomes, ds.runs)
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
