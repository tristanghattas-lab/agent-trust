# Agent Trust & Commerce Intelligence

v0 scaffold. See the North Star doc for the product framing — this repo
is the technical approach section made real.

Two modes, one database:

1. **Visibility & optimisation** — `static/tracker.js` on the site posts
   to `app/main.py`'s `/ingest` endpoint, which classifies each session
   (`app/classify.py`, rule-based for now) and stores it. Orders land via
   the Shopify order webhook (`app/shopify_webhooks.py` — checkout is
   Shopify-hosted, so client JS never sees it; the webhook is the real
   signal, not an inference from page timing). Orders and outcomes join
   against sessions by `session_key` — that join is the whole point, not
   the dashboard polish.
2. **Threat testing** — `harness/tasks.py` holds task lists by site type
   (`ecommerce_generic`, `ecommerce_wine`, `lead_gen`). `harness/log_run.py`
   is a CLI for logging results after manually operating an agent
   (ChatGPT, Perplexity, a browser-use agent, Gemini) through each task.
   Don't build the Playwright automation (`harness/playwright_runner.py`,
   currently a stub) until manual runs show what's worth automating.

Both feed `dashboard/app.py`, a single Streamlit dashboard.

## Dashboard and test data

The dashboard has five views: Overview, AI referrals, Agent sessions,
Orders & outcomes, and Threat testing. The sidebar's **Data source** switch
picks between:

- **Live**: the four tables in Postgres.
- **Demo**: a synthetic dataset built in memory by `dashboard/demo_data.py`.
  It is never written to the database, so it can't mix with real traffic.
  Agent behaviour is modelled on the icelabs test runs and every session is
  scored by the real classifier; volumes and rates are invented. The threat
  testing view shows the real icelabs findings (or live runs, if the DB has any).
- **Auto** (default): live when the tracker has recorded sessions, otherwise demo.

Demo mode shows a "TEST DATA" banner on every view. Keep it that way: the
dashboard is shown to merchants, and synthetic numbers must never pass as
a real store's traffic.

## Quickstart

```bash
# 1. Postgres (local dev)
docker compose up -d

# 2. Python env
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# 3. Tables + demo data (so the dashboard isn't blank)
python scripts/init_db.py
python scripts/seed_demo.py

# 4. Run the API
uvicorn app.main:app --reload --port 8000

# 5. Run the dashboard (separate terminal)
streamlit run dashboard/app.py
```

## Logging a manual threat-test run

With the API running:

```bash
python -m harness.log_run
# or non-interactively:
python -m harness.log_run --surface chatgpt --task age_verification \
    --result fail --exploit --notes "Agent proceeded without asking DOB"
```

## Deploying (Render)

Local testing needs a public URL — an agent hitting the real storefront
can't reach `localhost`, and Shopify webhooks need somewhere real to POST to.

1. Push this repo to GitHub (already done for `tristanghattas-lab/agent-trust`).
2. In Render: **New + → Blueprint**, point it at the repo. `render.yaml`
   defines the API, the dashboard, and a free Postgres together — Render
   reads it automatically.
3. After the first deploy, set `SHOPIFY_WEBHOOK_SECRET` (and, later,
   `SHOPIFY_ADMIN_API_TOKEN` if needed) in the API service's Environment
   tab — these aren't committed to the repo.
4. In the Shopify store: **Settings → Notifications → Webhooks → Create
   webhook**, event `Order creation`, format JSON, URL
   `https://<api-service>.onrender.com/webhooks/shopify/orders`. Shopify
   shows the signing secret once, at creation — that's what goes into step 3.
5. Free tier: web services sleep after 15 min idle (~1 min cold start on
   the next hit), and the free Postgres expires 30 days after creation.
   Fine for a testing window; upgrade both if this needs to outlive that.

## Wiring the tracker to a site

1. Add `<script src="https://<api-service>.onrender.com/tracker.js"
   data-api="https://<api-service>.onrender.com/ingest"></script>` to the
   theme (Shopify: Online Store → Themes → Edit code → `theme.liquid`,
   just before `</head>`).
2. On Shopify, cart tracking needs no further wiring: `tracker.js`
   auto-detects the theme's own AJAX cart calls (`/cart/add.js`,
   `/cart/change.js`, etc.) and treats a successful one as a
   `cartUpdated()` — covering Dawn and most modern themes. It also writes
   the session key onto the cart via `POST /cart/update.js` at the same
   time, which is what lets the order webhook match a completed order back
   to the session. For age-gate tracking (any site type), or a cart flow
   that doesn't use `fetch`, call `window.AgentTrust.ageGateShown()` /
   `.ageGateResult()` / `.cartUpdated()` directly from the site's own code.
   Skip `.checkoutStarted()` / `.checkoutCompleted()`: on Shopify those
   never fire (see above), the webhook replaces them.
3. If the site is behind Cloudflare, pass its bot-category header
   through as `cf_bot_category` on ingest — free signal, don't re-derive it.
4. Run the manual harness against the site per `harness/tasks.py`
   (`python -m harness.log_run --site-type ecommerce_generic`, or
   `ecommerce_wine` / `lead_gen` for the other site types).

### Installing on a live store through Google Tag Manager

For a store that already runs GTM (United Cellars does), this needs no
theme edit and is easy for the store's team to approve and remove:

1. GTM → **Tags → New → Custom HTML**, paste:

   ```html
   <script async src="https://agent-trust-api-o7u9.onrender.com/tracker.js"
           data-api="https://agent-trust-api-o7u9.onrender.com/ingest"
           data-sample="1"></script>
   ```

2. Trigger: **All Pages**. Name it "Agent Trust tracker". Preview, then publish.
3. Set `SITE_ORIGIN` on the API service to the store's domain (for example
   `https://unitedcellars.com.au`) so CORS accepts the tracker's posts.
4. Add one line to the store's privacy policy, along the lines of: "We use
   behavioural analytics to detect automated and AI-agent traffic. This
   records interaction patterns such as clicks and timings, never what you
   type."
5. Lower `data-sample` (for example `0.25`) only if traffic grows enough to
   strain the free database tier. Sampling is decided once per session.

What the tracker collects: page path, referrer, user agent, cart value,
click and mouse-movement counts, keystroke and form-field-change counts,
and browser-automation fingerprints such as `navigator.webdriver`. It never
reads field contents. If it's installed twice (theme and GTM), it runs once.

## Metrics API (what the Shopify app reads)

Every number a merchant sees comes from `app/metrics.py`, served by
`app/metrics_api.py`, scoped to one store:

| Endpoint | Returns |
|---|---|
| `GET /metrics/overview?shop=&days=30` | KPIs, daily series by traffic class, top agents, funnel by segment, activity feed |
| `GET /metrics/referrals?shop=&days=` | AI-referred visits: KPIs vs other traffic, daily by assistant, by source, landing pages |
| `GET /metrics/sessions?shop=&days=&class=&limit=&offset=` | Agent sessions, newest first, with counts by class |
| `GET /metrics/sessions/{session_key}?shop=` | One session: every signal in plain English |
| `GET /metrics/orders?shop=&days=` | Orders by segment (AOV, dispute rate), flagged orders |
| `GET /metrics/threats?shop=` | Threat-test matrix, findings with fixes |

- **Auth:** `Authorization: Bearer $METRICS_API_KEY`. Server-to-server only;
  never put the key in a storefront or browser. Refuses everything if the
  key isn't set.
- **Stores:** every session, order and test run carries `shop_domain`. The
  tracker reports `Shopify.shop` (or the hostname), Shopify's order webhook
  sends `X-Shopify-Shop-Domain`. Rows from before stores were tagged are
  backfilled to `DEFAULT_SHOP` on startup.
- **`shop=demo`** serves the synthetic store, with `"synthetic": true` in
  every response (threat-test results excepted: those are real). Any front
  end must show a test-data label when it sees that flag.
- **Small samples:** rates over fewer than 20 orders come back as `null`
  rather than a misleading number.
- **Coverage:** every response carries a `coverage` block saying which data
  sources the store has (browser tracker, order feed, edge logs, checkout
  pixel). Crawlers, scrapers and fetch-only assistants never run JavaScript,
  so only edge logs can see them; without edge data they're listed in
  `hidden_agent_classes` and front ends must show them as "not visible",
  never as zero.
- **Order attribution:** the order webhook stores Shopify's `source_name`,
  `app_id`, `landing_site` and `referring_site`. From these, orders placed
  inside an AI assistant (agentic checkout) and orders from people an AI
  referred (referrer or `utm_source=chatgpt.com`) are counted even with no
  tracker installed. Shopify doesn't publish the `source_name` values for AI
  channels: the mapping in `app/analytics.py` (`AI_CHANNEL_TOKENS`) is a best
  guess, and the Orders view lists raw values so the real ones can be added.

Run the tests with `python -m pytest tests`.

## Not built yet (on purpose)

- Automated threat-testing runner (`harness/playwright_runner.py`).
- Anything ML-based in `app/classify.py` — rules first, until there's
  enough labelled outcome data to justify a model.
