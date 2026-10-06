// node --test edge/worker.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { forwardReason, buildRecord, trackerSession, toolName } from "./worker.js";

const req = (path, headers = {}, method = "GET") =>
  new Request(`https://store.example${path}`, { method, headers });
const browser = {
  "user-agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/129.0 Safari/537.36",
  "accept-language": "en-AU", "sec-fetch-mode": "navigate", accept: "text/html",
};

test("ordinary browser page views are not forwarded", () => {
  assert.equal(forwardReason(req("/products/ornellaia-2022", browser)), null);
  assert.equal(forwardReason(req("/collections/red-wine", browser)), null);
});

test("static assets and checkout are ignored", () => {
  assert.equal(forwardReason(req("/cdn/shop/files/x.png", { "user-agent": "GPTBot/1.2" })), null);
  assert.equal(forwardReason(req("/assets/theme.css", { "user-agent": "curl/8.0" })), null);
  assert.equal(forwardReason(req("/checkout", { "user-agent": "curl/8.0" })), null);
  assert.equal(forwardReason(req("/products/x", { "user-agent": "curl/8.0" }, "POST")), null);
  assert.equal(forwardReason(req("/cart/add", browser, "POST")), null);
});

test("declared bots, signed agents, libraries and headless clients are forwarded", () => {
  assert.equal(forwardReason(req("/products/x", { "user-agent": "Mozilla/5.0; compatible; GPTBot/1.2" })), "declared_bot");
  assert.equal(forwardReason(req("/products/x", { ...browser, "signature-agent": '"https://chatgpt.com"' })), "signed_agent");
  assert.equal(forwardReason(req("/products/x", { "user-agent": "python-requests/2.32" })), "http_library");
  assert.equal(forwardReason(req("/products/x", { "user-agent": "Mozilla/5.0 Chrome/129.0" })), "non_browser");
});

test("agent files are always forwarded, even from browsers", () => {
  for (const p of ["/robots.txt", "/agents.md", "/llms.txt", "/llms-full.txt", "/sitemap.xml",
                   "/sitemap_products_1.xml", "/.well-known/ucp", "/products.json", "/products/x.js"]) {
    assert.equal(forwardReason(req(p, browser)), "agent_file", p);
  }
});

test("record hashes the IP and carries header presence, not values", async () => {
  const r = req("/agents.md", { ...browser, "cf-connecting-ip": "203.0.113.7" });
  const rec = await buildRecord(r, new Response("ok", { status: 200 }), { IP_SALT: "salt" }, 1759600000000);
  assert.equal(rec.path, "/agents.md");
  assert.equal(rec.status, 200);
  assert.equal(rec.accept_language, true);
  assert.equal(rec.ip_hash.length, 32);
  assert.ok(!JSON.stringify(rec).includes("203.0.113.7"));
  const rec2 = await buildRecord(r, null, { IP_SALT: "salt" }, 1759600000000 + 86400000);
  assert.notEqual(rec.ip_hash, rec2.ip_hash, "salt rotates daily");
});

test("tracker cookie and Cloudflare verified category are carried", async () => {
  assert.equal(trackerSession("foo=1; _at_sid=s_abc123_xyz; bar=2"), "s_abc123_xyz");
  assert.equal(trackerSession("_at_sid=bad value"), null);
  assert.equal(trackerSession(null), null);
  const r = new Request("https://store.example/products/x", {
    headers: { ...browser, "signature-agent": '"https://chatgpt.com"', cookie: "_at_sid=s_k1_abc; cart=zzz" } });
  const rec = await buildRecord(r, new Response("ok"), { IP_SALT: "s" });
  assert.equal(rec.tracker_session, "s_k1_abc");
  assert.equal(rec.verified_category, null);
  assert.ok(!JSON.stringify(rec).includes("cart=zzz")); // no other cookie leaves Cloudflare
});

test("store agent API calls are forwarded with the tool name only", async () => {
  assert.equal(forwardReason(req("/api/ucp/mcp", browser, "POST")), "agent_api");
  assert.equal(forwardReason(req("/api/mcp", {}, "POST")), "agent_api");
  const body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call",
    params: { name: "update_cart", arguments: { address: "1 George St", email: "a@b.com" } } });
  const r = new Request("https://store.example/api/ucp/mcp", { method: "POST", body,
    headers: { "user-agent": "ChatGPT-User/1.0", "content-type": "application/json" } });
  const rec = await buildRecord(r, new Response("ok"), { IP_SALT: "s" }, Date.now(), r.clone());
  assert.equal(rec.tool, "update_cart");
  assert.ok(!JSON.stringify(rec).includes("George")); // arguments never leave Cloudflare
  assert.equal(await toolName(new Request("https://x/api/mcp", { method: "POST", body: '{"method":"tools/list"}' })), "tools/list");
});
