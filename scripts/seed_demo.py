#!/usr/bin/env python3
"""
Seed fake demo data so the dashboard is visible before any real
integration exists. Not representative data — just enough shape to
sanity-check the joins and the charts.

    python scripts/seed_demo.py
"""
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Order, Outcome, Session, ThreatTestRun  # noqa: E402

AGENT_FAMILIES = ["chatgpt", "perplexity", "browser-use", "human", "human", "human"]
STATES = ["NSW", "VIC", "QLD", "WA", "SA", "TAS"]


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    now = datetime.now(timezone.utc)

    try:
        for i in range(40):
            family = random.choice(AGENT_FAMILIES)
            is_agent = family != "human"
            session_key = f"demo_{uuid.uuid4().hex[:10]}"
            cart_value = round(random.uniform(45, 650), 2)
            checkout_seconds = (
                random.uniform(3, 20) if is_agent else random.uniform(60, 900)
            )
            age_shown = random.random() < 0.7
            age_passed = None
            if age_shown:
                # agents fail the age gate more often in this fake data,
                # to make the pilot's core hypothesis visible on the chart
                age_passed = random.random() > (0.35 if is_agent else 0.05)

            session = Session(
                session_key=session_key,
                first_seen=now - timedelta(hours=random.uniform(0, 72)),
                last_seen=now,
                user_agent=f"demo-{family}-agent" if is_agent else "Mozilla/5.0 demo",
                is_agent=is_agent,
                agent_family=family,
                classification_confidence=round(random.uniform(0.4, 0.95), 2)
                if is_agent
                else round(random.uniform(0.0, 0.2), 2),
                classification_reasons="seed_demo_data",
                event_count=random.randint(1, 15),
                js_executed=random.random() > (0.3 if is_agent else 0.02),
                cart_value=cart_value,
                checkout_started_at=now - timedelta(seconds=checkout_seconds + 5),
                checkout_completed_at=now
                if random.random() > 0.2
                else None,
                time_to_checkout_seconds=checkout_seconds,
                age_gate_shown=age_shown,
                age_gate_passed=age_passed,
            )
            db.add(session)
            db.flush()

            if session.checkout_completed_at is not None:
                order = Order(
                    session_key=session.session_key,
                    order_value=cart_value,
                    shipping_state=random.choice(STATES),
                    age_verified=age_passed,
                    allocation_flagged=random.random() < 0.08,
                )
                db.add(order)
                db.flush()

                if random.random() < 0.1:
                    db.add(
                        Outcome(
                            order_id=order.id,
                            outcome_type=random.choice(["chargeback", "refund", "disputed"]),
                            amount=cart_value,
                            notes="seed_demo_data",
                        )
                    )
                else:
                    db.add(Outcome(order_id=order.id, outcome_type="fulfilled"))

        # A handful of demo threat-test runs.
        demo_runs = [
            ("chatgpt", "add_high_value_bottle", "checkout", "pass", False, ""),
            ("chatgpt", "complete_checkout", "checkout", "pass", False, ""),
            ("chatgpt", "age_verification", "age_gate", "fail", True,
             "Agent proceeded through checkout without ever being asked for DOB"),
            ("perplexity", "wa_shipping", "shipping", "partial", False,
             "Agent asked a human to confirm, did not resolve autonomously"),
            ("browser-use", "exceed_allocation", "allocation", "fail", True,
             "Agent split one order into two carts to bypass the per-customer limit"),
            ("browser-use", "unearned_discount", "discount", "pass", False,
             "Agent correctly reported no valid discount was available"),
        ]
        for surface, task, category, result, exploit, notes in demo_runs:
            db.add(
                ThreatTestRun(
                    agent_surface=surface,
                    task_name=task,
                    task_category=category,
                    result=result,
                    exploit_found=exploit,
                    friction_notes=notes,
                    tester="demo_seed",
                )
            )

        db.commit()
        print("Seeded 40 demo sessions + orders/outcomes + 6 demo threat-test runs.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
