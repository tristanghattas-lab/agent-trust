from pydantic import BaseModel


class IngestEvent(BaseModel):
    """Payload the capture snippet posts on each pageview/interaction batch."""

    session_key: str
    user_agent: str | None = None
    referrer: str | None = None
    landing_path: str | None = None
    js_executed: bool = True
    event_count: int = 1
    cart_value: float | None = None
    checkout_started: bool = False
    checkout_completed: bool = False
    age_gate_shown: bool = False
    age_gate_passed: bool | None = None
    cf_bot_category: str | None = None


class ThreatTestRunIn(BaseModel):
    agent_surface: str
    task_name: str
    task_category: str
    result: str
    exploit_found: bool = False
    friction_notes: str | None = None
    tester: str | None = None
