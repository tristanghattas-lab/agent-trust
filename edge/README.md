# Edge logs (Cloudflare Worker)

Crawlers, scrapers and assistants that only fetch pages never run
JavaScript, so the browser tracker can't see them. This Worker can. It runs
on the merchant's own Cloudflare zone in front of Shopify, passes every
request through untouched, and forwards a small record of non-browser
requests to Agent Trust (`POST /edge/ingest`).

## What it forwards

| Forwarded | Why |
|---|---|
| Declared bots and agents (GPTBot, ChatGPT-User, ClaudeBot, PerplexityBot…) | Named in the user agent |
| Web Bot Auth-signed requests (`Signature-Agent`, `Signature` + `Signature-Input`) | Signed agents; Shopify now asks agents to sign |
| HTTP libraries (curl, python-requests, Go, node-fetch…) | Scripts, not browsers |
| Requests missing both `Sec-Fetch-Mode` and `Accept-Language` | Every real browser sends these |
| `robots.txt`, `agents.md`, `llms.txt`, sitemaps, `/.well-known/`, `products.json` | Agent-facing files, whoever fetches them |

Not forwarded: ordinary browser page views (the tracker covers them, so
nothing is counted twice), static assets, writes (POST etc.) and
`/checkout` (Cloudflare disables Workers there for Shopify).

Each record carries the path, user agent, referrer, status, ASN, country,
and header *presence* (not values). The IP is hashed inside Cloudflare with
a salt that changes daily, so raw IPs never leave Cloudflare and visitors
can't be tracked across days.

## Requirements

- The store's domain is on the merchant's own Cloudflare account, proxied
  (orange cloud), with a CNAME to `shops.myshopify.com`. This is
  Cloudflare's supported "orange-to-orange" setup for Shopify. Don't turn on
  "Always Use HTTPS": it blocks Shopify's certificate renewal path.
- Workers free tier: 100,000 requests a day. The Worker runs on every
  request, so a busy store may need the paid plan (it still only forwards a
  small share).
- `EDGE_SIGNING_SECRET` set on the Agent Trust API.

## Setup

```bash
# 1. Get the store's key (same EDGE_SIGNING_SECRET as the API)
python -m scripts.edge_key <store>.myshopify.com

# 2. In edge/: set AGENT_TRUST_SHOP and the routes in wrangler.toml, then
npx wrangler login                     # the merchant's Cloudflare account
npx wrangler secret put AGENT_TRUST_EDGE_KEY
npx wrangler secret put IP_SALT        # any long random string
npx wrangler deploy
```

Within minutes the store's dashboard shows "Edge logs" as connected, crawler
and scraper classes appear, and "What agents read" fills in.

## Notes

- Signatures are recorded, not cryptographically verified. A signed
  request is treated as a strong agent signal, not proof of identity.
  Verifying Web Bot Auth signatures against each agent's published keys is
  a later step.
- Each store has its own derived key; one store's key can't post into
  another store's data.
- Test the forwarding rules with `node --test edge/worker.test.mjs`.
