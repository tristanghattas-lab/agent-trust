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
2. Call `window.AgentTrust.cartUpdated()` / `.ageGateShown()` /
   `.ageGateResult()` from the site's own cart/age-gate code — this is the
   signal generic bot detection can't see. Skip `.checkoutStarted()` /
   `.checkoutCompleted()`: on Shopify those never fire (see above), the
   webhook replaces them.
   - On Shopify, `cartUpdated()` (and page load, for a cart that already
     exists) also writes the session key onto the cart itself via
     `POST /cart/update.js` — that's what lets the order webhook match a
     completed order back to the session. No-op on non-Shopify sites.
3. If the site is behind Cloudflare, pass its bot-category header
   through as `cf_bot_category` on ingest — free signal, don't re-derive it.
4. Run the manual harness against the site per `harness/tasks.py`
   (`python -m harness.log_run --site-type ecommerce_generic`, or
   `ecommerce_wine` / `lead_gen` for the other site types).

## Not built yet (on purpose)

- Automated threat-testing runner (`harness/playwright_runner.py`).
- Anything ML-based in `app/classify.py` — rules first, until there's
  enough labelled outcome data to justify a model.
