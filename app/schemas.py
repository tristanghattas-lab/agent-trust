from pydantic import BaseModel


class IngestEvent(BaseModel):
    """Payload the capture snippet posts on each pageview/interaction batch."""

    session_key: str
    # The store the tracker is running on: Shopify.shop when available,
    # otherwise the page's hostname. Normalised server-side.
    shop: str | None = None
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
    # Deltas since the tracker's previous post, summed server-side across
    # every page of the session (the fields above are per-page snapshots).
    clicks_delta: int | None = None
    sparse_trail_clicks_delta: int | None = None
    # Comma-separated browser-automation fingerprints (navigator.webdriver,
    # HeadlessChrome UA, driver globals ...), sent with each pageview.
    automation_tells: str | None = None
    # Typing cadence deltas — counts only, never field contents.
    inputs_delta: int | None = None
    keyless_inputs_delta: int | None = None
    keydowns_delta: int | None = None


class OrderIn(BaseModel):
    """Normalised shape we store, after pulling the bits we need out of
    Shopify's much larger order webhook payload."""

    shopify_order_id: str
    order_value: float
    currency: str = "AUD"
    shipping_state: str | None = None
    session_key: str | None = None
    # client_details.user_agent from the order payload — the fallback
    # for matching an order to its session when the cart-attribute tag
    # is missing (e.g. "Buy it now", which bypasses the cart entirely).
    client_user_agent: str | None = None
    allocation_flagged: bool = False
    # Evidence of what was actually submitted at checkout (free-text fields,
    # discounts) -- lets the threat-test runner judge a run from the order
    # record itself instead of trusting the agent's own report.
    customer_email: str | None = None
    order_evidence: str | None = None  # JSON string
    # Shopify's own attribution for the order. These come from the order
    # itself, so they work for orders that never touched the storefront
    # (e.g. placed inside ChatGPT via agentic checkout) and need no tracker.
    source_name: str | None = None     # sales channel: "web", "pos", or the channel app
    app_id: str | None = None          # id of the app/channel that created the order
    landing_site: str | None = None    # first page of the buyer's visit, incl. UTM tags
    referring_site: str | None = None  # where the buyer came from
    is_test: bool = False              # Shopify test-mode order


class ThreatTestRunIn(BaseModel):
    agent_surface: str
    task_name: str
    task_category: str
    result: str
    exploit_found: bool = False
    friction_notes: str | None = None
    tester: str | None = None
    shop: str | None = None  # defaults to DEFAULT_SHOP server-side
