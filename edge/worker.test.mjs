// node --test edge/worker.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { forwardReason, buildRecord } from "./worker.js";

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
