import type { LoaderFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import { applyTags, DASHBOARD, fetchOrderVerdict } from "../agent-trust.server";

/**
 * GET /api/order/:id  (called by the order-page block, with Shopify's
 * session token). Returns the order's verdict and refreshes its "AI: " tags,
 * so tags stay current whenever a merchant opens the order.
 */
export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  const { admin, session, cors } = await authenticate.admin(request);
  const id = String(params.id || "").split("/").pop() || "";
  try {
    const v = await fetchOrderVerdict(session.shop, id);
    if (!v) return cors(Response.json({ found: false }));
    let tags: string[] | null = null;
    try {
      tags = await applyTags(admin.graphql, id, v);
    } catch (e) {
      console.warn("Tagging failed", id, e);
    }
    return cors(Response.json({
      found: true, tags, dashboard: `${DASHBOARD}/?shop=${session.shop}`,
      verdict: {
        origin: v.origin, origin_label: v.origin_label, agent: v.agent, identity_label: v.identity_label,
        confidence: v.confidence, ai_source: v.ai_source, handoff_seconds: v.handoff_seconds,
        evidence_score: v.evidence_score, action: v.action, action_reason: v.action_reason,
        is_test: v.is_test, chain: v.chain,
      },
    }));
  } catch (e) {
    return cors(Response.json({ found: false, error: String(e) }, { status: 502 }));
  }
};
