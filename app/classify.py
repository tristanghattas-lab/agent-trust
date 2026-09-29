"""
Rule-based agent classification for v0. No ML yet — the technical
approach doc is explicit about this: get real data flowing first,
build the fusion layer against outcomes, and only reach for a model
once there's enough labelled signal to train on.

Each rule contributes a reason string and a confidence bump. This is
intentionally crude and easy to argue with — the point is to produce
something inspectable, not something accurate on day one.
"""
from dataclasses import dataclass, field

# Known AI-agent / crawler user-agent substrings, lowercase.
# Not exhaustive — extend as real traffic reveals gaps. Cloudflare's
# verified-bot list and radar.cloudflare.com/traffic are the best
# reference for what to add here.
KNOWN_AGENT_UA_SUBSTRINGS: dict[str, str] = {
    "gptbot": "chatgpt",
    "oai-searchbot": "chatgpt",
    "chatgpt-user": "chatgpt",
    "perplexitybot": "perplexity",
    "perplexity-user": "perplexity",
    "claude-user": "claude",
    "claudebot": "claude",
    "anthropic-ai": "claude",
    "google-extended": "gemini",
    "geminibot": "gemini",
    "amazonbot": "amazon",
    "bytespider": "unknown",
    "ccbot": "unknown",
    "headlesschrome": "browser-use",
    "playwright": "browser-use",
    "puppeteer": "browser-use",
    "selenium": "browser-use",
}


@dataclass
class ClassificationResult:
    is_agent: bool
    agent_family: str
    confidence: float
    reasons: list[str] = field(default_factory=list)

    @property
    def reasons_csv(self) -> str:
        return ",".join(self.reasons)


def classify_session(
    *,
    user_agent: str | None,
    js_executed: bool,
    event_count: int,
    time_to_checkout_seconds: float | None,
    cart_value: float | None,
    cf_bot_category: str | None = None,
) -> ClassificationResult:
    reasons: list[str] = []
    confidence = 0.0
    agent_family = "unknown"

    ua = (user_agent or "").lower()

    # Rule 1: known agent/crawler UA string — strong signal.
    for substring, family in KNOWN_AGENT_UA_SUBSTRINGS.items():
        if substring in ua:
            reasons.append(f"ua_match:{substring}")
            confidence += 0.6
            agent_family = family
            break

    # Rule 2: free signal from the CDN, if the site is behind Cloudflare.
    # Trust it heavily but don't treat it as the only input — this system's
    # value is in what it adds on top, not in re-deriving this.
    if cf_bot_category:
        cat = cf_bot_category.strip().lower()
        if cat in {"agent", "training", "ai_agent"}:
            reasons.append(f"cf_bot_category:{cat}")
            confidence += 0.5
            if agent_family == "unknown":
                agent_family = "cf_flagged"

    # Rule 3: JS never executed but the session still generated events —
    # classic scripted-client tell (a real browser executes JS).
    if not js_executed and event_count > 0:
        reasons.append("no_js_execution")
        confidence += 0.3

    # Rule 4: checkout completed implausibly fast relative to cart value.
    # A human doesn't add a $400 allocation order to cart and check out
    # in under ~8 seconds; an agent following a scripted flow can.
    if time_to_checkout_seconds is not None and cart_value is not None:
        implausible_floor_seconds = max(8.0, cart_value / 50.0)
        if time_to_checkout_seconds < implausible_floor_seconds:
            reasons.append(
                f"fast_checkout:{time_to_checkout_seconds:.1f}s_for_${cart_value:.0f}"
            )
            confidence += 0.25

    confidence = min(confidence, 1.0)
    is_agent = confidence >= 0.4

    return ClassificationResult(
        is_agent=is_agent,
        agent_family=agent_family if is_agent else "human",
        confidence=confidence,
        reasons=reasons,
    )
