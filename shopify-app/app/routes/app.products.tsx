import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { getPlan, metricsView } from "../agent-trust.server";
import { Kpi, KpiGrid, Locked, pct } from "../components/ui";

/** What agents look at, add to cart, search for and get stuck on, vs people. */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  const plan = await getPlan(session.shop);
  if (!plan.features.includes("products")) return { d: null, plan };
  return { d: await metricsView("products", session.shop, { days: 30 }), plan };
};

const secs = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v)}s`);

export default function Products() {
  const { d, plan } = useLoaderData<typeof loader>();
  if (!plan.features.includes("products")) {
    return (
      <s-page heading="How agents shop your store">
        <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>
        <s-section>
          <Locked feature="products" title="What agents look at, search for and skip" trialAvailable={plan.trial_available}>
            See the products agents view and add, what they search for, which items they leave without adding, and the
            out-of-stock and missing pages that turn them away.
          </Locked>
        </s-section>
      </s-page>
    );
  }
  const ag = d?.agent_behaviour?.agents;
  const pp = d?.agent_behaviour?.people;
  const products: any[] = d?.products ?? [];
  return (
    <s-page heading="How agents shop your store">
      <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>
      {!ag ? (
        <s-section>
          <s-text color="subdued">
            Nothing yet. This fills in as agents browse your store, from the storefront tracker's page-by-page steps.
          </s-text>
        </s-section>
      ) : (
        <>
          <s-section heading="Last 30 days, agents vs people">
            <KpiGrid>
              <Kpi label="Products viewed per visit" value={(ag.products_per_session ?? 0).toFixed(1)}
                note={pp ? `People: ${(pp.products_per_session ?? 0).toFixed(1)}` : undefined} />
              <Kpi label="Visits that search" value={pct(ag.search_share, 0)}
                note={pp ? `People: ${pct(pp.search_share, 0)}` : undefined} />
              <Kpi label="Time on a page" value={secs(ag.avg_seconds_on_page)}
                note={pp ? `People: ${secs(pp.avg_seconds_on_page)}` : undefined} />
              <Kpi label="Dead ends hit" value={String(ag.dead_ends ?? 0)} note="Missing pages and out-of-stock products"
                tone={ag.dead_ends ? "critical" : undefined} />
            </KpiGrid>
          </s-section>

          <s-section heading="Products agents pay attention to" padding="none">
            <s-table>
              <s-table-header-row>
                <s-table-header listSlot="primary">Product</s-table-header>
                <s-table-header format="numeric">Agent views</s-table-header>
                <s-table-header format="numeric">Agent adds</s-table-header>
                <s-table-header format="numeric">Agent add rate</s-table-header>
                <s-table-header format="numeric">People views</s-table-header>
                <s-table-header format="numeric">People add rate</s-table-header>
                <s-table-header format="numeric">Out of stock hits</s-table-header>
              </s-table-header-row>
              <s-table-body>
                {products.slice(0, 50).map((p) => (
                  <s-table-row key={p.product}>
                    <s-table-cell>{p.title}</s-table-cell>
                    <s-table-cell>{p.agent_views}</s-table-cell>
                    <s-table-cell>{p.agent_adds}</s-table-cell>
                    <s-table-cell>{pct(p.agent_add_rate, 0)}</s-table-cell>
                    <s-table-cell>{p.human_views}</s-table-cell>
                    <s-table-cell>{pct(p.human_add_rate, 0)}</s-table-cell>
                    <s-table-cell>{p.out_of_stock_hits || "—"}</s-table-cell>
                  </s-table-row>
                ))}
              </s-table-body>
            </s-table>
          </s-section>

          <s-section slot="aside" heading="What agents search for">
            {(d.top_agent_searches ?? []).length ? (
              <s-stack direction="block" gap="small-300">
                {d.top_agent_searches.map((q: any) => (
                  <s-stack key={q.query} direction="inline" justifyContent="space-between">
                    <s-text>"{q.query}"</s-text>
                    <s-text color="subdued">{q.count}×</s-text>
                  </s-stack>
                ))}
              </s-stack>
            ) : (
              <s-text color="subdued">No agent searches yet.</s-text>
            )}
          </s-section>

          <s-section slot="aside" heading="Where agents get stuck">
            {(d.dead_ends ?? []).length ? (
              <s-stack direction="block" gap="small-300">
                {d.dead_ends.map((x: any) => (
                  <s-stack key={`${x.path}-${x.detail}`} direction="block" gap="none">
                    <s-text>{x.path}</s-text>
                    <s-text color="subdued">
                      {x.detail === "out_of_stock" ? "Out of stock" : "Missing page (404)"} · {x.agent_hits}×
                    </s-text>
                  </s-stack>
                ))}
              </s-stack>
            ) : (
              <s-text color="subdued">No dead ends hit by agents.</s-text>
            )}
          </s-section>
        </>
      )}
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
