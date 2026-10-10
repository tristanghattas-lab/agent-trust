# Security

## Keys (server-side only)

| Key | Who holds it | Can do |
|---|---|---|
| `METRICS_API_KEY` (or `APP_API_KEYS`) | the Shopify app's server | store-level reads and writes: metrics, plans, order scan, shelf test, privacy webhooks |
| `ADMIN_API_KEY` (or `ADMIN_API_KEYS`) | internal tools (threat-test harness) | everything above, plus `/orders/evidence`, `/threat-runs`, Cloudflare connections, edge keys |
| `DASHBOARD_PASSWORD` | people using the internal dashboard | the dashboard reads the database directly; locked if unset |
| `EDGE_SIGNING_SECRET` | API (+ dashboard) | derives each store's edge Worker key |
| `SHOPIFY_API_SECRET` | API + Shopify app | verifies every Shopify webhook (HMAC) |

No keys configured = every authenticated request refused. Keys are compared in constant time and never logged; each authenticated call is logged with its role and path.

**Rotate** a key without downtime: set `APP_API_KEYS=new,old` (or `ADMIN_API_KEYS`), move callers to `new`, then remove `old`.

## Abuse limits (app/security.py)

- Per-IP rate limit on every request (~300/min), tighter on public ingestion (`/ingest`, `/journey`, `/pixel/events`, `/edge/ingest`: ~120/min), per-store budget on ingestion, per-key budget on authenticated calls. Over the limit: 429.
- Request bodies over 2 MB: 413.
- Public ingestion is only stored for stores that installed the app (registered on first open) or are in `ALLOWED_SHOPS`; anything else is acknowledged and dropped.
- Shelf tests only run against the store's own domain, at most once every 30 minutes per store.
- API docs (`/docs`, `/openapi.json`) are off unless `ENABLE_DOCS=1`.
- Security headers on every response (nosniff, no-referrer, frame deny).

## Customer data

Behaviour data is keyed to random session ids. Orders hold the customer email and two free-text fields (address line 2, note) for abuse flags; Shopify's privacy webhooks (`customers/data_request`, `customers/redact`, `shop/redact`) return, redact or delete them. Search terms are shortened and emails or long numbers redacted in the browser and again on the server.
