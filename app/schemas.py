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

    # Automation-detection signal (see static/tracker.js for why this
    # works) — only meaningful on a fine/mouse pointer, null otherwise.
    pointer_env: str | None = None  # "fine" | "coarse" | "unknown"
    mouse_event_rate: float | None = None
    teleport_click_ratio: float | None = None


class OrderIn(BaseModel):
    """Normalised shape we store, after pulling the bits we need out of
    Shopify's much larger order webhook payload."""

    shopify_order_id: str
    order_value: float
    currency: str = "AUD"
    shipping_state: str | None = None
    session_key: str | None = None
    allocation_flagged: bool = False


class ThreatTestRunIn(BaseModel):
    agent_surface: str
    task_name: str
    task_category: str
    result: str
    exploit_found: bool = False
    friction_notes: str | None = None
    tester: str | None = None
