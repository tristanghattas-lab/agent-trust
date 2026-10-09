import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { metricsView } from "../agent-trust.server";
import { ActionBadge, money, OriginBadge, when } from "../components/ui";

/** Every order with where it came from, the agent, evidence and a recommended action. */
const FILTERS = [
  ["ai", "AI orders"], ["review", "Needs review"], ["all", "All orders"],
] as const;

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  const filter = new URL(request.url).searchParams.get("filter") || "ai";
  const data = await metricsView("orders/list", session.shop, { days: 90, limit: 500 });
  const all: any[] = data?.orders ?? [];
  const orders = all.filter((o) =>
    filter === "all" ? true : filter === "review" ? o.needs_review : !["human", "unmatched"].includes(o.origin));
  return { orders: orders.slice(0, 250), filter, total: all.length, reachable: !!data };
};

export default function Orders() {
  const { orders, filter, total, reachable } = useLoaderData<typeof loader>();
  return (
    <s-page heading="Orders">
      <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>
      <s-section padding="none">
        <s-box padding="base">
          <s-stack direction="inline" gap="small">
            {FILTERS.map(([key, label]) => (
              <s-button key={key} href={`/app/orders?filter=${key}`} variant={filter === key ? "primary" : "secondary"}>
                {label}
              </s-button>
            ))}
          </s-stack>
        </s-box>
        {orders.length ? (
          <s-table>
            <s-table-header-row>
              <s-table-header listSlot="primary">Order</s-table-header>
              <s-table-header>Placed</s-table-header>
              <s-table-header listSlot="secondary">Origin</s-table-header>
              <s-table-header>Agent</s-table-header>
              <s-table-header>Identity</s-table-header>
              <s-table-header format="currency">Value</s-table-header>
              <s-table-header format="numeric">Evidence</s-table-header>
              <s-table-header listSlot="inline">Action</s-table-header>
            </s-table-header-row>
            <s-table-body>
              {orders.map((o) => (
                <s-table-row key={o.order_id} clickDelegate={`o-${o.order_id}`}>
                  <s-table-cell>
                    <s-link id={`o-${o.order_id}`} href={`/app/orders/${o.order_id}`}>#{o.shopify_order_id}</s-link>
                  </s-table-cell>
                  <s-table-cell>{when(o.created_at)}</s-table-cell>
                  <s-table-cell><OriginBadge origin={o.origin} label={o.origin_label} /></s-table-cell>
                  <s-table-cell>{o.agent || (o.ai_source ? `via ${o.ai_source}` : "—")}</s-table-cell>
                  <s-table-cell>{o.identity_label || "—"}</s-table-cell>
                  <s-table-cell>{money(o.order_value, 2)}{o.is_test ? " (test)" : ""}</s-table-cell>
                  <s-table-cell>{o.evidence_score}</s-table-cell>
                  <s-table-cell><ActionBadge action={o.action} /></s-table-cell>
                </s-table-row>
              ))}
            </s-table-body>
          </s-table>
        ) : (
          <s-box padding="base">
            <s-text color="subdued">
              {!reachable ? "Couldn't reach Agent Trust. Try again shortly."
                : filter === "review" ? "No orders need review."
                : filter === "ai" ? `No AI orders in the last 90 days (${total} orders in total).` : "No orders yet."}
            </s-text>
          </s-box>
        )}
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
