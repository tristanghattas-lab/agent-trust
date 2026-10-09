import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { apiCall } from "../agent-trust.server";
import { money, Timeline, when } from "../components/ui";

/** One visit: who it was, why it was classed that way, and what it did. */
export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  const key = String(params.key || "");
  const v: any = await apiCall(`/metrics/sessions/${encodeURIComponent(key)}?shop=${encodeURIComponent(session.shop)}`)
    .catch(() => null);
  return { v };
};

export default function Visit() {
  const { v } = useLoaderData<typeof loader>();
  if (!v?.session_key) {
    return (
      <s-page heading="Visit">
        <s-link slot="breadcrumb-actions" href="/app/behaviour">Behaviour</s-link>
        <s-section><s-text color="subdued">Agent Trust has no record of this visit.</s-text></s-section>
      </s-page>
    );
  }
  const sd = v.signals_detail || {};
  const pair = (a: number | null, b: number | null) => (a == null ? "—" : `${b ?? 0} of ${a}`);
  const facts: [string, string][] = [
    ["Started", when(v.first_seen)],
    ["Landing page", v.landing_path || "—"],
    ["Came from", v.ai_source ? `${v.ai_source} (AI)` : v.referrer || "Direct"],
    ["Confidence", v.confidence != null ? v.confidence.toFixed(2) : "—"],
    ["Clicks with no mouse movement", pair(sd.clicks, sd.clicks_without_mouse_trail)],
    ["Fields filled without typing", pair(sd.fields_filled, sd.fields_filled_without_keys)],
    ["Automation fingerprints", (sd.automation_tells || []).join(", ") || "none"],
    ["Cart", money(v.cart_value, 2)],
  ];
  const isAgent = v.class !== "human";
  return (
    <s-page heading={isAgent ? v.agent : "Visitor"}>
      <s-link slot="breadcrumb-actions" href="/app/behaviour">Behaviour</s-link>
      <s-section heading="Verdict">
        <s-stack direction="block" gap="small">
          <s-stack direction="inline" gap="small">
            <s-badge tone={isAgent ? "warning" : "neutral"}>{v.class_label}</s-badge>
            {v.ordered && <s-badge tone="success">Placed an order</s-badge>}
            {v.behaviour_only && <s-badge tone="info">Caught on behaviour, not what it claimed</s-badge>}
          </s-stack>
          {(v.reasons || []).length > 0 ? (
            <s-unordered-list>
              {v.reasons.map((r: any) => <s-list-item key={r.code}>{r.text}</s-list-item>)}
            </s-unordered-list>
          ) : (
            <s-text color="subdued">Nothing unusual about how this visit behaved.</s-text>
          )}
        </s-stack>
      </s-section>
      <s-section heading="What it did">
        <Timeline steps={v.journey || []} />
      </s-section>
      <s-section slot="aside" heading="Details">
        <s-stack direction="block" gap="small-300">
          {facts.map(([k, val]) => (
            <s-stack key={k} direction="block" gap="none">
              <s-text color="subdued">{k}</s-text>
              <s-text>{val}</s-text>
            </s-stack>
          ))}
        </s-stack>
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
