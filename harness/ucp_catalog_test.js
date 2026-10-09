// Round 1 agent shopping test: send shopper requests to a Shopify store's own
// agent search (UCP over MCP, the endpoint ChatGPT and other agents call) and
// score the top three results. Read-only: searches only, no carts or orders.
//
// Run in the browser console on the store's own domain (same-origin), e.g.
// https://unitedcellars.com.au, then read window.__ucp.
// The agent profile is served by the Agent Trust API (/ucp-agent.json).
const PROFILE = 'https://agent-trust-api-o7u9.onrender.com/ucp-agent.json';
const Q = [
  ["q01", "Shiraz under $60 for a dinner party", { max: 60, must: /shiraz/i }],
  ["q02", "Penfolds Grange", { must: /grange/i }],
  ["q03", "Champagne for a wedding toast around $80 a bottle", { max: 100, must: /champagne/i }],
  ["q04", "Wine from 1996 for a 30th birthday gift", { must: /1996/ }],
  ["q05", "Organic red wine under $40", { max: 40, must: /organic/i }],
  ["q06", "Non-alcoholic sparkling wine", { must: /non.?alc|alcohol.?free|0\.0|zero/i }],
  ["q07", "Central Otago Pinot Noir", { must: /pinot/i, also: /otago/i }],
  ["q08", "Margaret River Cabernet with a 95 point score", { must: /cabernet/i, also: /margaret/i }],
  ["q09", "Mixed dozen of red wines under $300", { must: /mixed|dozen|case/i }],
  ["q10", "Red wine to cellar for 10 years under $150", { max: 150 }],
  ["q11", "Burgundy Chardonnay", { must: /chardonnay|chablis|meursault|puligny|montrachet|bourgogne blanc/i }],
  ["q12", "Whisky gift around $300", { max: 400, must: /whisk/i }],
  ["q13", "Riesling to drink with Thai food", { must: /riesling/i }],
  ["q14", "Bordeaux en primeur 2023", { must: /bordeaux|ch[aâ]teau|primeur/i }],
  ["q15", "Low alcohol wine", { must: /low|light|alcohol|healthy/i }],
  ["q16", "Rose for summer under $35", { max: 35, must: /ros[eé]/i }],
  ["q17", "Barossa Shiraz under $40", { max: 40, must: /shiraz/i, also: /barossa/i }],
  ["q18", "Riedel wine glasses", { must: /riedel|glass/i }],
  ["q19", "Tuscan red under $100", { max: 100, must: /chianti|brunello|tuscan|toscana|bolgheri|sangiovese|montalcino/i }],
  ["q20", "Best value Champagne under $60", { max: 60, must: /champagne/i }],
];
async function search(query) {
  const body = { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'search_catalog', arguments: {
    meta: { 'ucp-agent': { profile: PROFILE } },
    catalog: { query, context: { address_country: 'AU', address_region: 'NSW', currency: 'AUD' }, pagination: { limit: 5 } } } } };
  const r = await fetch('/api/ucp/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json();
  if (j.error) return { error: j.error.message };
  const t = JSON.parse(j.result.content[0].text);
  return { products: (t.products || []).map(p => ({ title: p.title, price: p.price_range?.min?.amount / 100,
    avail: p.variants?.some(v => v.availability?.available), desc: (p.description?.html || '').replace(/<[^>]+>/g, '') })) };
}
window.__ucp = [];
for (const [id, q, rule] of Q) {
  const r = await search(q);
  if (r.error) { window.__ucp.push({ id, q, error: r.error }); continue; }
  const top = r.products.slice(0, 3);
  const ok = p => p.avail && (!rule.must || rule.must.test(p.title + ' ' + p.desc))
    && (!rule.also || rule.also.test(p.title + ' ' + p.desc)) && (!rule.max || p.price <= rule.max);
  window.__ucp.push({ id, q, found: top.some(ok), top: top.map(p => `${p.title} $${p.price}${p.avail ? '' : ' (OOS)'}`) });
}
console.table(window.__ucp.map(x => ({ id: x.id, q: x.q, found: x.found, first: x.top?.[0] })));
