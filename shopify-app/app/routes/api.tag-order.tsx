import type { ActionFunctionArgs } from "react-router";
import { timingSafeEqual } from "node:crypto";
import { unauthenticated } from "../shopify.server";
import { applyTags, fetchOrderVerdict } from "../agent-trust.server";

/**
 * POST /api/tag-order  {shop, order_id}
 * Called by the Agent Trust API after it records an order. Authenticated
 * with the shared METRICS_API_KEY; uses the store's offline token.
 */
function authorised(request: Request): boolean {
  const key = process.env.METRICS_API_KEY || "";
  const got = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!key || got.length !== key.length) return false;
  return timingSafeEqual(Buffer.from(got), Buffer.from(key));
}

export const action = async ({ request }: ActionFunctionArgs) => {
  if (request.method !== "POST") return new Response(null, { status: 405 });
  if (!authorised(request)) return Response.json({ error: "unauthorised" }, { status: 401 });
  const { shop, order_id } = (await request.json().catch(() => ({}))) as { shop?: string; order_id?: string };
  if (!shop || !order_id || !/^[a-z0-9-]+\.myshopify\.com$/.test(shop) || !/^\d+$/.test(order_id)) {
    return Response.json({ error: "shop and numeric order_id required" }, { status: 400 });
  }
  try {
    const v = await fetchOrderVerdict(shop, order_id);
    if (!v) return Response.json({ tagged: false, reason: "order not found" }, { status: 404 });
    const { admin } = await unauthenticated.admin(shop);
    const tags = await applyTags(admin.graphql, order_id, v);
    return Response.json({ tagged: true, tags });
  } catch (e) {
    return Response.json({ tagged: false, error: String(e) }, { status: 502 });
  }
};
