"""
Core data model. Three tables carry the passive/visibility side
(sessions -> orders -> outcomes, joined by session_key), plus one table
for the active/threat-testing side (threat_test_runs) so both modes
write into the same database and the same dashboard can read both.

Deliberately not normalised further than this for v0 — get real data
flowing first, refactor once the pilot shows what actually matters.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Session(Base):
    """One browsing/checkout session captured on the site, human or agent."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_key: Mapped[str] = mapped_column(String, unique=True, index=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    referrer: Mapped[str | None] = mapped_column(Text, nullable=True)
    landing_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Classification output (see app/classify.py) — rule-based for v0.
    is_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_family: Mapped[str] = mapped_column(String, default="unknown")
    # e.g. "chatgpt", "perplexity", "browser-use", "human", "unknown"
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    classification_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    # comma-separated rule hits, kept simple for v0

    # Free signal from the CDN/WAF, if present — never re-derived, just recorded.
    cf_bot_category: Mapped[str | None] = mapped_column(String, nullable=True)

    # Behavioural signals that matter for commerce specifically.
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    js_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    cart_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    checkout_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkout_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    time_to_checkout_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    age_gate_shown: Mapped[bool] = mapped_column(Boolean, default=False)
    age_gate_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    orders: Mapped[list["Order"]] = relationship(back_populates="session")


class Order(Base):
    """An order pulled from Shopify (or logged manually pre-integration)."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    shopify_order_id: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    session_key: Mapped[str | None] = mapped_column(
        String, ForeignKey("sessions.session_key"), nullable=True, index=True
    )

    order_value: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String, default="AUD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    shipping_state: Mapped[str | None] = mapped_column(String, nullable=True)  # AU state
    age_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    allocation_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    # true if the order tried to exceed a per-customer allocation limit

    session: Mapped["Session | None"] = relationship(back_populates="orders")
    outcomes: Mapped[list["Outcome"]] = relationship(back_populates="order")


class Outcome(Base):
    """Downstream result of an order — this is the label the fusion layer needs."""

    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)

    outcome_type: Mapped[str] = mapped_column(String)
    # "fulfilled" | "refunded" | "chargeback" | "disputed" | "cancelled"
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="outcomes")


class ThreatTestRun(Base):
    """One manual (or later, automated) threat-testing / red-team attempt.

    Logged by the harness (see harness/log_run.py) against the validation
    plan's task list. This is mode 2's data, sitting in the same DB as
    mode 1 so a single dashboard can show both.
    """

    __tablename__ = "threat_test_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agent_surface: Mapped[str] = mapped_column(String)
    # "chatgpt" | "perplexity" | "browser-use" | "gemini" | ...
    task_name: Mapped[str] = mapped_column(String)
    task_category: Mapped[str] = mapped_column(String)
    # "age_gate" | "allocation" | "checkout" | "discount" | "shipping" | "other"

    result: Mapped[str] = mapped_column(String)  # "pass" | "fail" | "partial"
    exploit_found: Mapped[bool] = mapped_column(Boolean, default=False)
    friction_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tester: Mapped[str | None] = mapped_column(String, nullable=True)
