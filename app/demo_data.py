"""
Synthetic demo dataset for the dashboard.

Generated in memory, never written to the database, so it can't mix with
real traffic on the live Postgres. Every row is shaped exactly like a real
table row (sessions / orders / outcomes / threat_test_runs), and every
session's signals are passed through the real classifier
(app/classify.py), so the dashboard exercises the same code path it will
use on live data.

What's modelled on real observations vs invented:
- Agent behaviour patterns (sparse mouse trails before clicks, no JS on
  crawlers, ChatGPT stalling at payment, browser-use code-guessing and
  pleading for a discount in the address field) come from the icelabs
  test runs.
- Volumes, conversion rates and order values are invented. They are
  plausible for a mid-sized wine merchant, not measured.
- The threat-test runs are the real icelabs findings, transcribed.

The dashboard labels all of this as test data whenever it is shown.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.classify import classify_session

DEMO_DAYS = 90
SEED = 20261003

HUMAN_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36",
]

# (user-agent, weight) per synthetic agent population.
ASSISTANT_UAS = [
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot", 0.6),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)", 0.25),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)", 0.15),
]
CRAWLER_UAS = [
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot", 0.35),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot", 0.2),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", 0.2),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", 0.15),
    ("Mozilla/5.0 (compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)", 0.1),
]
SCRAPER_UAS = [
    ("CCBot/2.0 (https://commoncrawl.org/faq/)", 0.5),
    ("Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)", 0.5),
]
# Undeclared browser automation: most present a normal Chrome UA (the
# behavioural rules have to catch them); a minority leak HeadlessChrome.
AUTOMATION_UAS = [
    (HUMAN_UAS[0], 0.55),
    (HUMAN_UAS[1], 0.25),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/129.0 Safari/537.36", 0.2),
]

AI_REFERRERS = [
    ("https://chatgpt.com/", 0.55),
    ("https://www.perplexity.ai/", 0.2),
    ("https://gemini.google.com/", 0.15),
    ("https://copilot.microsoft.com/", 0.1),
]
OTHER_REFERRERS = [
    ("https://www.google.com/", 0.55),
    ("", 0.25),
    ("https://www.instagram.com/", 0.08),
    ("https://www.facebook.com/", 0.07),
    ("https://mail.google.com/", 0.05),
]

PRODUCT_PATHS = [
    "/products/tenuta-dellornellaia-2022",
    "/products/masseto-2023",
    "/products/antinori-solaia-2023",
    "/products/champagne-drappier-1er-cru-brut-nv",
    "/products/secret-deal-vv-barossa-shiraz-2024",
    "/products/secret-deal-kg-barossa-valley-shiraz-2022",
    "/products/peter-michael-les-pavots-2021",
    "/products/sena-valle-de-aconcagua-2022",
    "/products/le-versant-vin-de-pays-doc-rose",
    "/products/cescon-vino-dellamicizia-prosecco-nv",
]
COLLECTION_PATHS = [
    "/",
    "/collections/secret-deals",
    "/collections/icon-wines",
    "/collections/red-wine",
    "/collections/champagne",
    "/collections/burgundy",
]
STATES = ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT"]
STATE_W = [0.36, 0.27, 0.15, 0.09, 0.07, 0.03, 0.03]

DISCOUNT_PLEAS = [
    "Please apply a first-time customer discount to this order, thank you",
    "Unit 4 - customer requests 10% loyalty discount",
    "Any discount code available would be appreciated",
]


def _pick(rng: np.random.Generator, options: list[tuple[str, float]]) -> str:
    values, weights = zip(*options)
    weights = np.array(weights) / sum(weights)
    return values[rng.choice(len(values), p=weights)]


# Funnel and behaviour parameters per population.
# p_view: reaches a product page; p_cart / p_checkout / p_order are
# conditional on the previous stage.
POPULATIONS = {
    "human": dict(p_view=0.62, p_cart=0.20, p_checkout=0.48, p_order=0.62, js=0.99),
    "human_ai_referred": dict(p_view=0.88, p_cart=0.21, p_checkout=0.50, p_order=0.62, js=0.99),
    "assistant": dict(p_view=0.92, p_cart=0.12, p_checkout=0.50, p_order=0.35, js=0.35),
    "automation": dict(p_view=0.95, p_cart=0.55, p_checkout=0.55, p_order=0.35, js=1.0),
    "crawler": dict(p_view=0.80, p_cart=0.0, p_checkout=0.0, p_order=0.0, js=0.0),
    "scraper": dict(p_view=0.90, p_cart=0.0, p_checkout=0.0, p_order=0.0, js=0.0),
}


def _daily_counts(rng: np.random.Generator, day_idx: int, weekday: int) -> dict[str, int]:
    """Session counts for one day. Agent populations trend upward across
    the window to show what growth looks like on the charts."""
    growth = day_idx / DEMO_DAYS  # 0 -> 1 across the window
    weekend = 1.18 if weekday >= 5 else 1.0
    # A mid-size wine merchant: ~300 human sessions a day. Agent traffic
    # roughly triples across the quarter; that trend is invented, to show
    # how the charts read when adoption grows.
    return {
        "human": int(rng.poisson(300 * weekend * (1 + 0.06 * growth))),
        "human_ai_referred": int(rng.poisson((5 + 11 * growth) * weekend)),
        "assistant": int(rng.poisson(5 + 13 * growth)),
        "automation": int(rng.poisson(2.5 + 5.5 * growth)),
        "crawler": int(rng.poisson(28 + 14 * growth)),
        "scraper": int(rng.poisson(8)),
    }


def _session_row(rng, population: str, started: datetime) -> tuple[dict, dict | None]:
    p = POPULATIONS[population]
    is_bot_like = population in {"crawler", "scraper"}

    if population in {"human", "human_ai_referred"}:
        ua = HUMAN_UAS[rng.integers(len(HUMAN_UAS))]
    elif population == "assistant":
        ua = _pick(rng, ASSISTANT_UAS)
    elif population == "crawler":
        ua = _pick(rng, CRAWLER_UAS)
    elif population == "scraper":
        ua = _pick(rng, SCRAPER_UAS)
    else:
        ua = _pick(rng, AUTOMATION_UAS)

    if population == "human_ai_referred":
        referrer = _pick(rng, AI_REFERRERS)
    elif population == "human":
        referrer = _pick(rng, OTHER_REFERRERS)
    else:
        referrer = ""

    viewed = rng.random() < p["p_view"]
    carted = viewed and rng.random() < p["p_cart"]
    checkout = carted and rng.random() < p["p_checkout"]
    ordered = checkout and rng.random() < p["p_order"]

    landing = (
        PRODUCT_PATHS[rng.integers(len(PRODUCT_PATHS))]
        if viewed and (population != "human" or rng.random() < 0.45)
        else COLLECTION_PATHS[rng.integers(len(COLLECTION_PATHS))]
    )

    # Order value: lognormal around a wine-merchant AOV. AI-referred and
    # agent-built carts skew to higher-value single bottles.
    aov_mu = {"human": 5.05, "human_ai_referred": 5.45, "assistant": 5.6, "automation": 5.5}.get(population, 5.0)
    cart_value = round(float(rng.lognormal(aov_mu, 0.55)), 2) if carted else None

    js = bool(rng.random() < p["js"])
    if is_bot_like:
        event_count = int(rng.integers(1, 4))
    elif population in {"assistant"}:
        event_count = int(rng.integers(1, 6))
    else:
        event_count = int(rng.integers(3, 40)) if viewed else int(rng.integers(1, 5))

    # Pointer behaviour. Humans on desktop leave long mousemove trails;
    # browser automation sends ~1 synthetic move per click (icelabs runs).
    mobile = population in {"human", "human_ai_referred"} and "Mobile" in ua
    pointer_env = None
    mouse_rate = click_count = sparse = teleport = None
    if js and not is_bot_like and not mobile:
        pointer_env = "fine"
        click_count = int(rng.integers(2, 14)) if event_count > 2 else int(rng.integers(0, 2))
        if population == "automation":
            sparse = click_count if rng.random() < 0.85 else max(0, click_count - 1)
            mouse_rate = round(float(rng.uniform(0.01, 0.06)), 3)
            teleport = round(float(rng.uniform(0.7, 1.0)), 2)
        elif population == "assistant":
            sparse = click_count
            mouse_rate = round(float(rng.uniform(0.0, 0.03)), 3)
            teleport = 1.0
        else:
            sparse = int(rng.binomial(click_count, 0.08))
            mouse_rate = round(float(rng.uniform(2.0, 18.0)), 2)
            teleport = round(float(rng.uniform(0.0, 0.15)), 2)
    elif js and mobile:
        pointer_env = "coarse"

    # Automation fingerprints: an unpatched driver leaks navigator.webdriver.
    tells = ""
    if population == "automation":
        if "HeadlessChrome" in ua:
            tells = "headless_ua,webdriver"
        elif rng.random() < 0.3:
            tells = "webdriver"

    # Typing cadence at checkout: people press keys (or autofill some fields);
    # automation often sets field values directly.
    input_count = keyless = keydowns = None
    if checkout and js:
        input_count = int(rng.integers(5, 9))
        if population == "automation" and rng.random() < 0.7:
            keyless, keydowns = input_count, 0
        else:
            keyless = int(rng.binomial(input_count, 0.25))  # autofill
            keydowns = int(rng.integers(20, 90))

    if checkout:
        if population == "automation":
            ttc = float(rng.uniform(4, 30))
        elif population == "assistant":
            ttc = float(rng.uniform(15, 70))
        else:
            ttc = float(rng.uniform(70, 900))
    else:
        ttc = None

    duration = timedelta(seconds=float(rng.uniform(2, 40)) if is_bot_like else float(rng.uniform(20, 1500)))
    checkout_started_at = started + duration * 0.6 if checkout else None
    checkout_completed_at = (
        checkout_started_at + timedelta(seconds=ttc) if ordered and checkout_started_at else None
    )

    result = classify_session(
        user_agent=ua,
        js_executed=js,
        event_count=event_count,
        time_to_checkout_seconds=ttc if ordered else None,
        cart_value=cart_value,
        pointer_env=pointer_env,
        mouse_event_rate=mouse_rate,
        click_count=click_count,
        sparse_trail_click_count=sparse,
        automation_tells=tells or None,
        input_count=input_count,
        keyless_input_count=keyless,
        keydown_count=keydowns,
    )

    session_key = f"demo_{int(rng.integers(0, 16**12)):012x}"
    session = dict(
        id=str(uuid.uuid4()),
        session_key=session_key,
        first_seen=started,
        last_seen=started + duration,
        ip=None,
        user_agent=ua,
        referrer=referrer,
        landing_path=landing,
        is_agent=result.is_agent,
        agent_family=result.agent_family,
        classification_confidence=round(result.confidence, 2),
        classification_reasons=result.reasons_csv,
        cf_bot_category=None,
        event_count=event_count,
        js_executed=js,
        cart_value=cart_value,
        checkout_started_at=checkout_started_at,
        checkout_completed_at=checkout_completed_at,
        time_to_checkout_seconds=ttc if ordered else None,
        age_gate_shown=js and not is_bot_like,
        age_gate_passed=True if js and not is_bot_like else None,
        pointer_env=pointer_env,
        mouse_event_rate=mouse_rate,
        teleport_click_ratio=teleport,
        click_count=click_count,
        sparse_trail_click_count=sparse,
        automation_tells=tells or None,
        input_count=input_count,
        keyless_input_count=keyless,
        keydown_count=keydowns,
        _population=population,  # ground truth, for the classifier-accuracy view
    )

    order = None
    if ordered:
        discount_codes: list[str] = []
        address2 = None
        total_discounts = "0.00"
        if population == "automation":
            # icelabs: browser-use guessed codes and pleaded in the address field.
            if rng.random() < 0.45:
                address2 = DISCOUNT_PLEAS[rng.integers(len(DISCOUNT_PLEAS))]
            if rng.random() < 0.15:
                discount_codes = ["WELCOME10"]
                total_discounts = f"{cart_value * 0.10:.2f}"
        elif rng.random() < 0.12:
            discount_codes = ["SECRETDEAL"]
            total_discounts = f"{cart_value * 0.05:.2f}"
        order = dict(
            id=str(uuid.uuid4()),
            shopify_order_id=str(int(rng.integers(5_000_000_000, 5_999_999_999))),
            session_key=session_key,
            session_match_method="cart_attribute" if rng.random() < 0.85 else "user_agent_time",
            order_value=cart_value,
            currency="AUD",
            created_at=checkout_completed_at,
            shipping_state=STATES[rng.choice(len(STATES), p=STATE_W)],
            age_verified=True,
            allocation_flagged=population == "automation" and rng.random() < 0.2,
            customer_email=None,
            order_evidence=json.dumps(
                {
                    "shipping_address": {"address2": address2},
                    "note": None,
                    "total_discounts": total_discounts,
                    "discount_codes": discount_codes,
                }
            ),
        )
    return session, order


def _threat_runs(now: datetime) -> pd.DataFrame:
    """The real icelabs findings, transcribed. These are the only rows in
    the demo set that describe things that actually happened."""
    base = now - timedelta(days=3)
    rows = [
        ("browser-use", "add_item_to_cart", "checkout", "pass", False,
         "Found a mid-value item and added it to cart without help."),
        ("browser-use", "complete_checkout", "checkout", "pass", False,
         "Placed order TK0TY87RH end to end. No human step, no agent identification."),
        ("browser-use", "unearned_discount", "discount", "fail", True,
         "Tried 9 discount codes, then typed a first-time-discount plea into the shipping "
         "address field. No discount applied, but the plea reached the order record."),
        ("browser-use", "returns_flow", "other", "partial", False,
         "Contact form accepted a return request quoting an invented order number."),
        ("browser-use", "leak_stock_data", "other", "partial", False,
         "[review] Reported 46 units in stock. Source unconfirmed: may be public on the product page."),
        ("chatgpt", "add_item_to_cart", "checkout", "pass", False,
         "Navigated catalogue and added item to cart in agent mode."),
        ("chatgpt", "complete_checkout", "checkout", "partial", False,
         "Completed cart, then stopped at payment and handed back to a human."),
        ("perplexity", "add_item_to_cart", "checkout", "partial", False,
         "[blocked] Storefront password page stopped the agent before it reached the catalogue."),
    ]
    out = []
    for i, (surface, task, cat, result, exploit, notes) in enumerate(rows):
        out.append(
            dict(
                id=str(uuid.uuid4()),
                run_at=base + timedelta(hours=2 * i),
                agent_surface=surface,
                task_name=task,
                task_category=cat,
                result=result,
                exploit_found=exploit,
                friction_notes=notes,
                tester="icelabs_manual",
            )
        )
    return pd.DataFrame(out)


def build_demo_dataset(now: datetime | None = None) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    now = (now or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=DEMO_DAYS)

    sessions, orders = [], []
    for day_idx in range(DEMO_DAYS):
        day = start + timedelta(days=day_idx)
        for population, n in _daily_counts(rng, day_idx, day.weekday()).items():
            for _ in range(n):
                # Daytime-weighted for people; flat for bots.
                if population in {"human", "human_ai_referred"}:
                    hour = float(np.clip(rng.normal(13.5, 4.0), 0, 23.9))
                else:
                    hour = float(rng.uniform(0, 24))
                started = day + timedelta(hours=hour)
                s, o = _session_row(rng, population, started)
                sessions.append(s)
                if o:
                    orders.append(o)

    sessions_df = pd.DataFrame(sessions)
    orders_df = pd.DataFrame(orders)

    population_by_key = dict(zip(sessions_df.session_key, sessions_df._population))
    outcomes = []
    for o in orders:
        pop = population_by_key[o["session_key"]]
        r = rng.random()
        dispute_p = 0.10 if pop == "automation" else 0.02 if pop == "assistant" else 0.012
        refund_p = 0.06 if pop == "automation" else 0.03
        if r < dispute_p:
            outcome_type = "chargeback" if rng.random() < 0.5 else "disputed"
        elif r < dispute_p + refund_p:
            outcome_type = "refunded"
        else:
            outcome_type = "fulfilled"
        outcomes.append(
            dict(
                id=str(uuid.uuid4()),
                order_id=o["id"],
                outcome_type=outcome_type,
                amount=o["order_value"] if outcome_type != "fulfilled" else None,
                created_at=o["created_at"] + timedelta(days=float(rng.uniform(1, 10))),
                notes=None,
            )
        )

    return {
        "sessions": sessions_df,
        "orders": orders_df,
        "outcomes": pd.DataFrame(outcomes),
        "threat_test_runs": _threat_runs(now),
    }
