/**
 * Calls to the Agent Trust API, and order tagging, shared by app routes.
 */
export const API = process.env.AGENT_TRUST_API_URL || "https://agent-trust-api-o7u9.onrender.com";
export const DASHBOARD = process.env.AGENT_TRUST_DASHBOARD_URL || "https://agent-trust-dashboard-mwnm.onrender.com";

export type OrderVerdict = {
  shopify_order_id: string;
  origin: string;
  origin_label: string;
  agent: string | null;
  identity: string | null;
  identity_label: string | null;
  confidence: number | null;
  ai_source: string | null;
  handoff_seconds: number | null;
  evidence_score: string;
  needs_review: boolean;
  action: "accept" | "review";
  action_reason: string;
  is_test: boolean;
  flags: string[];
  chain: { step: string; ok: boolean; text: string }[];
};

/** One order's verdict from the API, or null if Agent Trust has no record of it. */
export async function fetchOrderVerdict(shop: string, orderId: string): Promise<OrderVerdict | null> {
  const key = process.env.METRICS_API_KEY;
  if (!key) throw new Error("METRICS_API_KEY not set");
  const r = await fetch(
    `${API}/metrics/orders/${encodeURIComponent(orderId)}?shop=${encodeURIComponent(shop)}&days=365`,
    { headers: { Authorization: `Bearer ${key}` } },
  );
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`Agent Trust API ${r.status}`);
  return (await r.json()) as OrderVerdict;
}

// Every tag this app writes starts with "AI: ", so stale ones can be removed.
const TAG_PREFIX = "AI: ";
const ORIGIN_TAGS: Record<string, string> = {
  agent_placed: "AI: agent-placed",
  agent_assisted: "AI: agent-assisted",
  agent: "AI: agent order",
  ai_channel: "AI: in-app order",
  ai_referred: "AI: referred",
};
const REVIEW_TAG = "AI: needs review";

export function tagsFor(v: OrderVerdict): string[] {
  const tags = ORIGIN_TAGS[v.origin] ? [ORIGIN_TAGS[v.origin]] : [];
  if (v.action === "review") tags.push(REVIEW_TAG);
  return tags;
}

type GraphQL = (query: string, opts?: { variables?: Record<string, unknown> }) => Promise<Response>;

/** Make the order's "AI: " tags match the verdict. Returns the tags set. */
export async function applyTags(graphql: GraphQL, orderId: string, v: OrderVerdict): Promise<string[]> {
  const id = `gid://shopify/Order/${orderId}`;
  const want = tagsFor(v);
  const res = await graphql(`#graphql
    query orderTags($id: ID!) { order(id: $id) { tags } }`, { variables: { id } });
  const have: string[] = ((await res.json())?.data?.order?.tags ?? []).filter((t: string) =>
    t.startsWith(TAG_PREFIX),
  );
  const remove = have.filter((t) => !want.includes(t));
  const add = want.filter((t) => !have.includes(t));
  if (remove.length) {
    await graphql(`#graphql
      mutation rm($id: ID!, $tags: [String!]!) { tagsRemove(id: $id, tags: $tags) { userErrors { message } } }`,
      { variables: { id, tags: remove } });
  }
  if (add.length) {
    const r = await graphql(`#graphql
      mutation add($id: ID!, $tags: [String!]!) { tagsAdd(id: $id, tags: $tags) { userErrors { message } } }`,
      { variables: { id, tags: add } });
    const errs = (await r.json())?.data?.tagsAdd?.userErrors ?? [];
    if (errs.length) throw new Error(errs[0].message);
  }
  return want;
}

// ---------------------------------------------------------------------------
// Past-order scan: on install, page through the store's recent orders and
// send their attribution (sales channel, app, landing page, referrer) to the
// API, so the merchant sees AI orders from before Agent Trust was installed.
// Shopify allows about 60 days of orders without extra permission.
// ---------------------------------------------------------------------------
export const SCAN_DAYS = 60;
const SCAN_MAX_ORDERS = 5000;

export async function apiCall(path: string, init?: RequestInit) {
  const key = process.env.METRICS_API_KEY;
  if (!key) throw new Error("METRICS_API_KEY not set");
  const r = await fetch(`${API}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!r.ok) throw new Error(`Agent Trust API ${r.status} on ${path}`);
  return r.json();
}

export type ScanStatus = { status: "never" | "running" | "done"; started_at?: string; orders_added?: number };

export const scanStatus = (shop: string): Promise<ScanStatus> =>
  apiCall(`/backfill/status?shop=${encodeURIComponent(shop)}`);

export const scanSummary = (shop: string) =>
  apiCall(`/metrics/scan?shop=${encodeURIComponent(shop)}&days=${SCAN_DAYS}`);

const ORDERS_QUERY = `#graphql
  query pastOrders($first: Int!, $after: String, $query: String!) {
    orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        createdAt
        test
        sourceName
        app { id name }
        totalPriceSet { shopMoney { amount currencyCode } }
        customerJourneySummary {
          firstVisit { landingPage referrerUrl utmParameters { source } }
          lastVisit { landingPage referrerUrl utmParameters { source } }
        }
      }
    }
  }`;

type Visit = { landingPage?: string | null; referrerUrl?: string | null; utmParameters?: { source?: string | null } | null } | null;

function visitText(v: Visit): { landing: string | null; referrer: string | null } {
  if (!v) return { landing: null, referrer: null };
  let landing = v.landingPage || null;
  const utm = v.utmParameters?.source;
  if (utm && !(landing || "").includes("utm_source")) landing = `${landing || ""}?utm_source=${utm}`;
  return { landing, referrer: v.referrerUrl || null };
}

/** Scan the store's recent orders into Agent Trust. Safe to run more than once. */
export async function runOrderScan(graphql: GraphQL, shop: string): Promise<number> {
  const since = new Date(Date.now() - SCAN_DAYS * 86400_000).toISOString().slice(0, 10);
  let after: string | null = null;
  let runId: string | null = null;
  let sent = 0;
  for (;;) {
    const res = await graphql(ORDERS_QUERY, { variables: { first: 100, after, query: `created_at:>=${since}` } });
    const json: any = await res.json();
    const page = json?.data?.orders;
    if (!page) throw new Error(`orders query failed: ${JSON.stringify(json?.errors ?? json).slice(0, 300)}`);
    const orders = page.nodes.map((n: any) => {
      // Last visit first: it's the one that led to the purchase.
      const last = visitText(n.customerJourneySummary?.lastVisit);
      const first = visitText(n.customerJourneySummary?.firstVisit);
      return {
        id: n.id,
        created_at: n.createdAt,
        total: Number(n.totalPriceSet?.shopMoney?.amount ?? 0),
        currency: n.totalPriceSet?.shopMoney?.currencyCode ?? null,
        test: !!n.test,
        source_name: n.sourceName ?? null,
        app: n.app ? `${String(n.app.id).split("/").pop()} ${n.app.name}` : null,
        landing_site: last.landing || first.landing,
        referring_site: last.referrer || first.referrer,
      };
    });
    sent += orders.length;
    const done = !page.pageInfo.hasNextPage || sent >= SCAN_MAX_ORDERS;
    const r: any = await apiCall("/backfill/orders", {
      method: "POST",
      body: JSON.stringify({ shop, run_id: runId, orders, done }),
    });
    runId = r.run_id;
    if (done) return sent;
    after = page.pageInfo.endCursor;
  }
}

const scanning = new Set<string>();

/** Start a scan in the background unless one is running or already done. */
export async function ensureOrderScan(shop: string, makeGraphql: () => Promise<GraphQL>): Promise<ScanStatus> {
  let st: ScanStatus;
  try {
    st = await scanStatus(shop);
  } catch {
    return { status: "never" };
  }
  const stale = st.status === "running" && st.started_at && Date.now() - Date.parse(st.started_at) > 15 * 60_000;
  if ((st.status === "never" || stale) && !scanning.has(shop)) {
    scanning.add(shop);
    makeGraphql()
      .then((g) => runOrderScan(g, shop))
      .catch((e) => console.warn("Order scan failed", shop, e))
      .finally(() => scanning.delete(shop));
    return { status: "running" };
  }
  return st;
}


/** GET a metrics view for a store, or null if the API can't be reached. */
export async function metricsView<T = any>(view: string, shop: string, params: Record<string, string | number> = {}): Promise<T | null> {
  const q = new URLSearchParams({ shop, ...Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])) });
  try {
    return (await apiCall(`/metrics/${view}?${q}`)) as T;
  } catch (e) {
    console.warn("metrics view failed", view, shop, e);
    return null;
  }
}

/** Make sure the checkout pixel is switched on for this store. Returns "on" or an error. */
export async function ensurePixel(admin: { graphql: GraphQL }, shop: string): Promise<string> {
  const settings = JSON.stringify({ shop });
  try {
    const found = await admin.graphql(`#graphql
      query { webPixel { id settings } }`);
    const pixel = (await found.json())?.data?.webPixel;
    if (pixel?.id) {
      if (pixel.settings !== settings) {
        await admin.graphql(`#graphql
          mutation update($id: ID!, $webPixel: WebPixelInput!) {
            webPixelUpdate(id: $id, webPixel: $webPixel) { userErrors { message } }
          }`, { variables: { id: pixel.id, webPixel: { settings } } });
      }
      return "on";
    }
  } catch {
    // No pixel yet: the query errors rather than returning null.
  }
  const created = await admin.graphql(`#graphql
    mutation create($webPixel: WebPixelInput!) {
      webPixelCreate(webPixel: $webPixel) { userErrors { message } webPixel { id } }
    }`, { variables: { webPixel: { settings } } });
  const errors = (await created.json())?.data?.webPixelCreate?.userErrors || [];
  return errors.length ? `error: ${errors[0].message}` : "on";
}

// ---------------------------------------------------------------------------
// Plans
// ---------------------------------------------------------------------------
export type Plan = {
  plan: "free" | "growth" | "trust";
  effective_plan: "free" | "growth" | "trust";
  on_trial: boolean;
  trial_days_left: number;
  trial_available: boolean;
  features: string[];
};

const FREE: Plan = {
  plan: "free", effective_plan: "free", on_trial: false, trial_days_left: 0, trial_available: true,
  features: ["overview", "scan", "missed_sales", "orders_list"],
};

export async function getPlan(shop: string): Promise<Plan> {
  try {
    return (await apiCall(`/plans?shop=${encodeURIComponent(shop)}`)) as Plan;
  } catch {
    return FREE;
  }
}

export async function changePlan(shop: string, body: { plan?: string; start_trial?: boolean }): Promise<Plan> {
  return (await apiCall("/plans", { method: "POST", body: JSON.stringify({ shop, ...body }) })) as Plan;
}

// ---------------------------------------------------------------------------
// Agent shelf test (API runs it; see app/shelf.py)
// ---------------------------------------------------------------------------
export const shelfLatest = (shop: string) =>
  apiCall(`/shelf?shop=${encodeURIComponent(shop)}`).catch(() => null);

export const shelfStart = (shop: string) =>
  apiCall("/shelf/run", { method: "POST", body: JSON.stringify({ shop }) });

// ---------------------------------------------------------------------------
// Fix queue: product types from the store's own collections
// ---------------------------------------------------------------------------
// First matching collection handle decides the type.
const TYPE_RULES: [RegExp, string][] = [
  [/^(non-alcohol|non-alcoholic|alcohol-free)$/, "Non-Alcoholic Wine"],
  [/^(champagne|sparkling|prosecco|cava)$/, "Sparkling Wine"],
  [/^(rose-wine|rose)$/, "Rosé"],
  [/^(fortified|port|sherry)$/, "Fortified Wine"],
  [/^(dessert|sweet-wine|sauternes)$/, "Dessert Wine"],
  [/^(red-wine|shiraz|pinot-noir|cabernet-sauvignon|cabernet-blends|merlot|grenache|nebbiolo|sangiovese|tempranillo)$/, "Red Wine"],
  [/^(white-wine|chardonnay|riesling|sauvignon-blanc|pinot-gris|semillon|chenin-blanc)$/, "White Wine"],
  [/^(whisky|whiskey|gin|rum|vodka|tequila|brandy|cognac|spirits|liqueur)$/, "Spirits"],
  [/^(accessories|riedel|glassware|gifts-accessories)$/, "Accessories"],
];

export type TypeProposal = { id: string; title: string; proposed: string; via: string };

/**
 * Products with no product type, and the type their collection suggests
 * (rosé collection -> "Rosé"). Walks the matching collections only, so it
 * stays fast on big catalogues. First matching rule wins.
 */
export async function untypedProducts(graphql: GraphQL): Promise<TypeProposal[]> {
  const colRes = await graphql(`#graphql
    query cols { collections(first: 250) { nodes { handle } } }`);
  const handles: string[] = ((await colRes.json())?.data?.collections?.nodes ?? []).map((c: any) => c.handle);
  const seen = new Set<string>();
  const out: TypeProposal[] = [];
  for (const [re, type] of TYPE_RULES) {
    for (const handle of handles.filter((h) => re.test(h))) {
      let after: string | null = null;
      for (let i = 0; i < 8; i++) {
        const res = await graphql(`#graphql
          query members($handle: String!, $after: String) {
            collectionByIdentifier(identifier: { handle: $handle }) {
              products(first: 250, after: $after) { pageInfo { hasNextPage endCursor } nodes { id title productType } }
            }
          }`, { variables: { handle, after } });
        const page: any = (await res.json())?.data?.collectionByIdentifier?.products;
        if (!page) break;
        for (const n of page.nodes) {
          if (seen.has(n.id)) continue;
          seen.add(n.id);
          if (!(n.productType || "").trim()) out.push({ id: n.id, title: n.title, proposed: type, via: handle });
        }
        if (!page.pageInfo.hasNextPage) break;
        after = page.pageInfo.endCursor;
      }
    }
  }
  return out;
}

/** Set product types, a batch at a time. Returns how many were updated. */
export async function applyTypes(graphql: GraphQL, items: { id: string; type: string }[]): Promise<{ done: number; errors: string[] }> {
  let done = 0;
  const errors: string[] = [];
  for (const it of items) {
    const r = await graphql(`#graphql
      mutation setType($product: ProductUpdateInput!) {
        productUpdate(product: $product) { userErrors { message } }
      }`, { variables: { product: { id: it.id, productType: it.type } } });
    const errs = (await r.json())?.data?.productUpdate?.userErrors ?? [];
    if (errs.length) errors.push(`${it.id}: ${errs[0].message}`);
    else done++;
  }
  return { done, errors };
}

/** Metafields that look like critic scores or cellaring advice, from a sample of products. */
export async function scoreFields(graphql: GraphQL): Promise<{ key: string; examples: string[]; products: number }[]> {
  const res = await graphql(`#graphql
    query sample { products(first: 60, sortKey: UPDATED_AT, reverse: true) {
      nodes { title metafields(first: 40) { nodes { namespace key value } } } } }`);
  const nodes: any[] = (await res.json())?.data?.products?.nodes ?? [];
  const found: Record<string, { examples: string[]; products: number }> = {};
  for (const p of nodes) {
    for (const m of p.metafields.nodes) {
      const k = `${m.namespace}.${m.key}`;
      if (!/score|point|critic|rating|review|cellar|drink|window|aging|ageing/i.test(k)) continue;
      const f = (found[k] ??= { examples: [], products: 0 });
      f.products++;
      if (f.examples.length < 3) f.examples.push(`${p.title}: ${String(m.value).slice(0, 120)}`);
    }
  }
  return Object.entries(found).map(([key, v]) => ({ key, ...v })).sort((a, b) => b.products - a.products);
}
