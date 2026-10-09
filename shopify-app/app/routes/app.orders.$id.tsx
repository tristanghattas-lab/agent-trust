import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { applyTags, fetchOrderVerdict } from "../agent-trust.server";
import { ActionBadge, money, OriginBadge, Timeline, when } from "../components/ui";

/** One order: verdict, evidence chain, what the visitor did, items, checkout timing. */
export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  const { admin, session } = await authenticate.admin(request);
  const o: any = await fetchOrderVerdict(session.shop, String(params.id)).catch(() => null);
  if (o?.shopify_order_id) {
    await applyTags(admin.graphql, o.shopify_order_id, o).catch(() => null);  // keep tags current
  }
  return { o };
};

const STEP_NAMES: Record<string, string> = {
  checkout_started: "Started checkout", checkout_contact_info_submitted: "Contact details",
  checkout_address_info_submitted: "Address", checkout_shipping_info_submitted: "Shipping method",
  payment_info_submitted: "Payment", checkout_completed: "Completed",
};

export default function OrderDetail() {
  const { o } = useLoaderData<typeof loader>();
  if (!o) {
    return (
      <s-page heading="Order">
        <s-link slot="breadcrumb-actions" href="/app/orders">Orders</s-link>
        <s-section><s-text color="subdued">Agent Trust has no record of this order.</s-text></s-section>
      </s-page>
    );
  }
  const facts: [string, string][] = [
    ["Placed", when(o.created_at)],
    ["Value", `${money(o.order_value, 2)}${o.is_test ? " (test order)" : ""}`],
    ["Agent", o.agent || "—"],
    ["Identity", o.identity_label || "—"],
    ["Confidence", o.confidence != null ? o.confidence.toFixed(2) : "—"],
    ["AI source", o.ai_source || "—"],
    ["Pause before payment", o.handoff_seconds != null ? `${Math.round(o.handoff_seconds)}s` : "—"],
    ["Evidence", o.evidence_score],
  ];
  return (
    <s-page heading={`Order #${o.shopify_order_id}`} inlineSize="large">
      <s-link slot="breadcrumb-actions" href="/app/orders">Orders</s-link>
      <s-button slot="secondary-actions" href={`shopify://admin/orders/${o.shopify_order_id}`}>Open in Shopify</s-button>

      <s-section heading="Verdict">
        <s-stack direction="block" gap="base">
          <s-stack direction="inline" gap="small">
            <OriginBadge origin={o.origin} label={o.origin_label} />
            <ActionBadge action={o.action} />
            {(o.flags || []).map((f: string) => <s-badge key={f} tone="critical">{f}</s-badge>)}
          </s-stack>
          <s-text>{o.action_reason}</s-text>
        </s-stack>
      </s-section>

      <s-section heading="Evidence">
        <s-stack direction="block" gap="base">
          {(o.chain || []).map((c: any) => (
            <s-stack key={c.step} direction="inline" gap="small" alignItems="start">
              <s-icon type={c.ok ? "check-circle" : "circle-dashed"} tone={c.ok ? "success" : "caution"} />
              <s-stack direction="block" gap="none">
                <s-text type="strong">{c.step}</s-text>
                <s-text color="subdued">{c.text}</s-text>
              </s-stack>
            </s-stack>
          ))}
        </s-stack>
      </s-section>

      <s-section heading="What the visitor did">
        <Timeline steps={o.journey || []} />
      </s-section>

      {(o.session?.reasons || []).length > 0 && (
        <s-section heading="Why it was flagged as an agent">
          <s-unordered-list>
            {o.session.reasons.map((r: any) => <s-list-item key={r.code}>{r.text}</s-list-item>)}
          </s-unordered-list>
        </s-section>
      )}

      <s-section slot="aside" heading="Details">
        <s-stack direction="block" gap="small-300">
          {facts.map(([k, v]) => (
            <s-stack key={k} direction="inline" justifyContent="space-between" gap="base">
              <s-text color="subdued">{k}</s-text>
              <s-text>{v}</s-text>
            </s-stack>
          ))}
        </s-stack>
      </s-section>

      {(o.items || []).length > 0 && (
        <s-section slot="aside" heading="Items">
          <s-stack direction="block" gap="small-300">
            {o.items.map((i: any, n: number) => (
              <s-stack key={n} direction="inline" justifyContent="space-between" gap="base">
                <s-text>{i.quantity} × {i.title || i.variant_id}</s-text>
                <s-text>{money((i.price || 0) * (i.quantity || 1), 2)}</s-text>
              </s-stack>
            ))}
          </s-stack>
        </s-section>
      )}

      {(o.checkout_steps || []).length > 0 && (
        <s-section slot="aside" heading="Checkout timing">
          <s-stack direction="block" gap="small-300">
            {o.checkout_steps.map((c: any) => (
              <s-stack key={c.step} direction="inline" justifyContent="space-between" gap="base">
                <s-text>{STEP_NAMES[c.step] || c.step}</s-text>
                <s-text color="subdued">+{Math.round(c.seconds)}s</s-text>
              </s-stack>
            ))}
          </s-stack>
        </s-section>
      )}
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
