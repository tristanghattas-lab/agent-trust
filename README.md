# Agent Trust & Commerce Intelligence

v0 scaffold. See the North Star doc for the product framing — this repo
is the technical approach section made real.

Two modes, one database:

1. **Visibility & optimisation** — `static/tracker.js` on the site posts
   to `app/main.py`'s `/ingest` endpoint, which classifies each session
   (`app/classify.py`, rule-based for now) and stores it. Orders and
   outcomes join against sessions by `session_key` — that join is the
   whole point, not the dashboard polish.
2. **Threat testing** — `harness/tasks.py` holds the United Cellars
   validation task list. `harness/log_run.py` is a CLI for logging
   results after manually operating an agent (ChatGPT, Perplexity, a
   browser-use agent, Gemini) through each task. Don't build the
   Playwright automation (`harness/playwright_runner.py`, currently a
   stub) until manual runs show what's worth automating.

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

## Wiring it to the real site (once the pilot is greenlit)

1. Get sign-off from United Cellars, or start against a staging environment.
2. Add `<script src=".../tracker.js" data-api=".../ingest"></script>` to
   the site, and call `window.AgentTrust.cartUpdated()` /
   `.checkoutStarted()` / `.checkoutCompleted()` /
   `.ageGateShown()` / `.ageGateResult()` from the site's own checkout
   and age-gate code — this is the signal generic bot detection can't see.
3. Pull Shopify order data into the `orders` table (fill in
   `SHOPIFY_STORE_DOMAIN` / `SHOPIFY_ADMIN_API_TOKEN` in `.env`; the
   sync script isn't built yet — build it once there's a real store to
   point at).
4. If the site is behind Cloudflare, pass its bot-category header
   through as `cf_bot_category` on ingest — free signal, don't re-derive it.
5. Run the manual harness against the live/staging site per
   `harness/tasks.py`.

## Not built yet (on purpose)

- Shopify order sync (`orders` table is populated by hand or by the
  seed script until then).
- Automated threat-testing runner (`harness/playwright_runner.py`).
- Anything ML-based in `app/classify.py` — rules first, until there's
  enough labelled outcome data to justify a model.
