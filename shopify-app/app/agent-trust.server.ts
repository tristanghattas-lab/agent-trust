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
