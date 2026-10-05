/**
 * Agent Trust edge Worker.
 *
 * Runs on the merchant's own Cloudflare zone in front of Shopify
 * ("orange-to-orange"). Every request passes through untouched; the Worker
 * never changes or delays the response. Afterwards, in the background, it
 * forwards a small record of requests the browser tracker can't see:
 *
 *   - declared bots and AI agents (GPTBot, ChatGPT-User, ClaudeBot, ...)
 *   - Web Bot Auth-signed agents (Signature-Agent / Signature headers)
 *   - HTTP libraries (curl, python-requests, ...) and clients missing the
 *     headers every real browser sends
 *   - anything fetching agent files: robots.txt, agents.md, llms.txt,
 *     sitemaps, /.well-known/, products.json
 *
 * Ordinary browser traffic is not forwarded (the tracker covers it).
 * Static assets are ignored. Cloudflare disables Workers on /checkout.
 * IPs are hashed here with a daily salt; the raw IP never leaves Cloudflare.
 *
 * Optional: a Cron Trigger (every 10 minutes) pings the API's /health so a
 * free-tier API stays awake.
 *
 * Configuration (wrangler.toml / dashboard):
 *   vars:    AGENT_TRUST_INGEST_URL  e.g. https://<api>/edge/ingest
 *            AGENT_TRUST_SHOP        the store's myshopify domain
 *   secrets: AGENT_TRUST_EDGE_KEY    from `python -m scripts.edge_key <shop>`
 *            IP_SALT                 any long random string
 */

const AGENT_FILES = [
  /^\/robots\.txt$/,
  /^\/agents\.md$/,
  /^\/llms(-full)?\.txt$/,
  /^\/sitemap[^/]*\.xml$/,
  /^\/\.well-known\//,
  /\/products(\.json|\/[^/]+\.js(on)?)$/,
];
const STATIC_EXT = /\.(css|js|mjs|map|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|otf|mp4|webm)$/i;
const BOT_TOKENS = [
  "gptbot", "oai-searchbot", "chatgpt-user", "perplexitybot", "perplexity-user", "claudebot",
  "claude-user", "claude-searchbot", "anthropic-ai", "google-extended", "googlebot", "bingbot",
  "amazonbot", "applebot", "meta-externalagent", "meta-externalfetcher", "bytespider", "ccbot",
  "duckassistbot", "mistralai-user", "cohere-ai", "headlesschrome", "playwright", "puppeteer",
  "selenium", "bot", "crawler", "spider",
];
const HTTP_LIBRARIES = [
  "curl", "wget", "python-requests", "python-urllib", "httpx", "aiohttp", "go-http-client",
  "node-fetch", "axios", "undici", "okhttp", "java/", "scrapy", "libwww-perl", "ruby", "guzzle",
];

/** Why this request should be forwarded, or null to ignore it. */
export function forwardReason(request) {
  const url = new URL(request.url);
  const method = request.method.toUpperCase();
  if (method !== "GET" && method !== "HEAD") return null;
  const path = url.pathname;
  const isAgentFile = AGENT_FILES.some((re) => re.test(path));
  if (!isAgentFile && STATIC_EXT.test(path)) return null;
  if (path.startsWith("/checkout") || path.startsWith("/cdn/")) return null;

  const h = request.headers;
  const ua = (h.get("user-agent") || "").toLowerCase();
  if (isAgentFile) return "agent_file";
  if (h.get("signature-agent") || (h.get("signature") && h.get("signature-input"))) return "signed_agent";
  if (BOT_TOKENS.some((t) => ua.includes(t))) return "declared_bot";
  if (HTTP_LIBRARIES.some((t) => ua.includes(t))) return "http_library";
  if (!h.get("sec-fetch-mode") && !h.get("accept-language")) return "non_browser";
  return null;
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacHex(key, body) {
  const k = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(body));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The record sent to Agent Trust. Header presence only, never values that identify a person. */
export async function buildRecord(request, response, env, now = Date.now()) {
  const h = request.headers;
  const url = new URL(request.url);
  const ip = h.get("cf-connecting-ip") || "";
  const day = new Date(now).toISOString().slice(0, 10);
  const cf = request.cf || {};
  const bm = cf.botManagement || {};
  return {
    ts: now,
    ip_hash: (await sha256Hex(`${ip}|${env.IP_SALT || ""}|${day}`)).slice(0, 32),
    ua: h.get("user-agent"),
    method: request.method,
    path: url.pathname,
    status: response ? response.status : null,
    referer: h.get("referer"),
    accept: (h.get("accept") || "").slice(0, 200) || null,
    accept_language: Boolean(h.get("accept-language")),
    sec_fetch_mode: h.get("sec-fetch-mode"),
    sec_ch_ua: Boolean(h.get("sec-ch-ua")),
    signature_agent: h.get("signature-agent"),
    signed: Boolean(h.get("signature") && h.get("signature-input")),
    asn: typeof cf.asn === "number" ? cf.asn : null,
    country: cf.country || null,
    bot_score: typeof bm.score === "number" ? bm.score : null,
    verified_bot: typeof bm.verifiedBot === "boolean" ? bm.verifiedBot : null,
  };
}

export async function send(records, env) {
  const body = JSON.stringify({ records });
  return fetch(env.AGENT_TRUST_INGEST_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-agent-trust-shop": env.AGENT_TRUST_SHOP,
      "x-agent-trust-signature": await hmacHex(env.AGENT_TRUST_EDGE_KEY, body),
    },
    body,
  });
}

export default {
  async fetch(request, env, ctx) {
    const response = await fetch(request); // the store's response, unchanged
    try {
      if (forwardReason(request) && env.AGENT_TRUST_INGEST_URL && env.AGENT_TRUST_EDGE_KEY) {
        ctx.waitUntil(
          buildRecord(request, response, env)
            .then((rec) => send([rec], env))
            .then(async (r) => {
              // Visible in the Worker's Observability > Logs. 401 = wrong
              // edge key or shop; 5xx/timeouts = the API was asleep.
              if (!r.ok) console.log("agent-trust ingest failed", r.status, (await r.text()).slice(0, 200));
            })
            .catch((e) => console.log("agent-trust ingest error", String(e))) // never affects the store
        );
      }
    } catch (e) {
      /* never let logging break the store */
    }
    return response;
  },

  // Cron trigger (every 10 minutes): keeps a free-tier API awake so records
  // aren't lost to cold starts. Harmless on a paid API.
  async scheduled(event, env, ctx) {
    if (!env.AGENT_TRUST_INGEST_URL) return;
    const health = new URL("/health", env.AGENT_TRUST_INGEST_URL).toString();
    ctx.waitUntil(fetch(health).then((r) => console.log("agent-trust keep-warm", r.status))
      .catch((e) => console.log("agent-trust keep-warm error", String(e))));
  },
};
