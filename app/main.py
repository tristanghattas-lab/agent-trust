"""
FastAPI ingestion + query API.

- POST /ingest        — the capture snippet posts session events here
- POST /threat-runs    — the harness logs manual/automated test runs here
- GET  /health         — sanity check
"""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session as DBSession

from app.classify import classify_session
from app.db import Base, engine, get_db
from app.models import Session as SessionModel
from app.models import ThreatTestRun
from app.schemas import IngestEvent, ThreatTestRunIn

load_dotenv()

app = FastAPI(title="Agent Trust & Commerce Intelligence — ingestion API")

# Loose CORS for v0 — tighten to SITE_ORIGIN once this is pointed at the
# real United Cellars domain.
site_origin = os.getenv("SITE_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[site_origin] if site_origin != "*" else ["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ingest")
def ingest(event: IngestEvent, db: DBSession = Depends(get_db)):
    now = datetime.now(timezone.utc)

    session = (
        db.query(SessionModel)
        .filter(SessionModel.session_key == event.session_key)
        .one_or_none()
    )
    if session is None:
        session = SessionModel(
            session_key=event.session_key,
            user_agent=event.user_agent,
            referrer=event.referrer,
            landing_path=event.landing_path,
            first_seen=now,
            event_count=0,
        )
        db.add(session)

    session.last_seen = now
    # Python-side column defaults only apply on flush, so a freshly
    # constructed session's event_count is explicitly seeded above —
    # this `or 0` is just belt-and-braces against that same class of bug.
    session.event_count = (session.event_count or 0) + event.event_count
    if event.js_executed:
        session.js_executed = True
    if event.cart_value is not None:
        session.cart_value = event.cart_value
    if event.age_gate_shown:
        session.age_gate_shown = True
    if event.age_gate_passed is not None:
        session.age_gate_passed = event.age_gate_passed
    if event.cf_bot_category:
        session.cf_bot_category = event.cf_bot_category

    if event.checkout_started and session.checkout_started_at is None:
        session.checkout_started_at = now
    if event.checkout_completed and session.checkout_completed_at is None:
        session.checkout_completed_at = now
        if session.checkout_started_at is not None:
            started_at = session.checkout_started_at
            # SQLite drops tzinfo on round-trip (Postgres's timestamptz
            # won't) — normalise so this subtraction is safe on either.
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            session.time_to_checkout_seconds = (now - started_at).total_seconds()

    result = classify_session(
        user_agent=session.user_agent,
        js_executed=session.js_executed,
        event_count=session.event_count,
        time_to_checkout_seconds=session.time_to_checkout_seconds,
        cart_value=session.cart_value,
        cf_bot_category=session.cf_bot_category,
    )
    session.is_agent = result.is_agent
    session.agent_family = result.agent_family
    session.classification_confidence = result.confidence
    session.classification_reasons = result.reasons_csv

    db.commit()
    db.refresh(session)

    return {
        "session_key": session.session_key,
        "is_agent": session.is_agent,
        "agent_family": session.agent_family,
        "confidence": session.classification_confidence,
    }


@app.post("/threat-runs")
def log_threat_run(run: ThreatTestRunIn, db: DBSession = Depends(get_db)):
    row = ThreatTestRun(**run.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "run_at": row.run_at}
