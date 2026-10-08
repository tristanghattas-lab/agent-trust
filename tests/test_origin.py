"""Order origin (agent-placed vs agent-assisted vs AI-referred) and the
demo store's attention list."""
import pandas as pd

from app import metrics
from app.analytics import AI_CHANNEL, AUTOMATION, HUMAN


def _steps(gaps):
    t = pd.Timestamp("2026-10-07T06:25:00Z")
    names = list(metrics.FORM_STEPS) + ["payment_info_submitted"]
    out = []
    for name, g in zip(names, [0] + gaps):
        t += pd.Timedelta(seconds=float(g))
        out.append((name, t))
    return out


def test_handoff_gap_matches_run_5():
    # Form steps 3-5s apart, then payment 46s later: a person took over.
    steps = _steps([4, 3, 5, 46])
    assert metrics.handoff_gap(steps) == 46.0
    assert metrics.order_origin(AUTOMATION, None, steps) == "agent_assisted"
    assert metrics.order_origin(AUTOMATION, None, _steps([4, 3, 5, 4])) == "agent_placed"


def test_origin_without_pixel_and_for_people():
    assert metrics.order_origin(AUTOMATION, None, None) == "agent"
    assert metrics.order_origin(HUMAN, "ChatGPT", None) == "ai_referred"
    assert metrics.order_origin(HUMAN, None, None) == "human"
    assert metrics.order_origin(AI_CHANNEL, None, None) == "ai_channel"


def test_identity_tiers():
    assert metrics.identity_tier(AUTOMATION, "ChatGPT agent (verified)", []) == "verified"
    assert metrics.identity_tier(AUTOMATION, "ChatGPT agent (signed)", []) == "signed"
    assert metrics.identity_tier(AUTOMATION, "ChatGPT", ["ua_match:chatgpt-user"]) == "declared"
    assert metrics.identity_tier(AUTOMATION, "Undeclared (behavioural)", ["sparse_trail_clicks:6/6"]) == "undeclared"
    assert metrics.identity_tier(HUMAN, "Human", []) is None


def test_demo_overview_has_ai_orders_block():
    f = metrics.load_frames(None, "demo", 30)
    ao = metrics.overview(f)["ai_orders"]
    origins = {b["origin"] for b in ao["by_origin"]}
    assert {"agent_placed", "agent_assisted", "ai_referred", "ai_channel"} <= origins
    assert ao["attention"] and all(a["severity"] in ("high", "medium", "info") for a in ao["attention"])
    oid = next(x["order_id"] for x in metrics.orders_list(f)["orders"] if x["origin"] == "agent_assisted")
    chain = metrics.order_detail(f, oid)["chain"]
    assert [c["step"] for c in chain] == ["Origin", "Session", "Agent identity", "Checkout", "Order"]
    assert "handed off" in chain[3]["text"]


def test_recommended_action():
    assert metrics._action(["Discount request in address"], False, "agent_placed", "undeclared", 50)[0] == "review"
    assert metrics._action([], True, "agent_placed", "undeclared", 900)[0] == "review"
    assert metrics._action([], False, "agent_placed", "verified", 900) == ("accept", "Placed through a verified agent.")
    assert metrics._action([], False, "human", None, 900)[0] == "accept"


def test_tagging_is_off_in_tests():
    from app.order_tags import request_tags
    assert request_tags("73ee52.myshopify.com", "1", delay=0) is False
