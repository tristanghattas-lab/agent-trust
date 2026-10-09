import type { ActionFunctionArgs, HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useFetcher, useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { shelfLatest, shelfStart } from "../agent-trust.server";
import { money, when } from "../components/ui";

/**
 * Agent shelf test: realistic shopper requests sent to this store's own
 * agent search (the one ChatGPT and other agents use), scored found /
 * partial / missed, with the in-stock products agents didn't get shown.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  return { d: await shelfLatest(session.shop) };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  return shelfStart(session.shop).catch((e) => ({ error: String(e) }));
};

const VERDICT: Record<string, { tone: "success" | "warning" | "critical" | "neutral"; label: string }> = {
  found: { tone: "success", label: "Found" }, partial: { tone: "warning", label: "Partial" },
  missed: { tone: "critical", label: "Missed" }, error: { tone: "neutral", label: "Error" },
};

export default function Shelf() {
  const { d } = useLoaderData<typeof loader>();
  const fetcher = useFetcher();
  const run = d?.run;
  const running = d?.status === "running" || fetcher.state !== "idle";
  return (
    <s-page heading="Agent shelf test">
      <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>
      <s-button slot="primary-action" loading={running || undefined}
        onClick={() => fetcher.submit({}, { method: "post" })}>Run again</s-button>
      {running && (
        <s-banner tone="info" heading="Test running">
          Sending shopper requests to your store's agent search. It takes a few minutes; reload to see results.
        </s-banner>
      )}
      {!run ? (
        <s-section>
          <s-text color="subdued">No results yet. The first test starts automatically and takes a few minutes.</s-text>
        </s-section>
      ) : (
        <>
          <s-section heading={`Score ${run.score} / 100`}>
            <s-stack direction="block" gap="small">
              <s-text>
                {run.found} found, {run.partial} partial and {run.missed} missed, from {run.scored} shopper requests
                sent to your store's agent search (the one ChatGPT, Gemini and other agents use). Tested {when(run.finished_at)}.
              </s-text>
              <s-text color="subdued">
                Found: a suitable, in-stock product in the top three. Partial: suitable products, but a detail the
                shopper asked for isn't in the data agents get. Missed: nothing suitable in the top three.
              </s-text>
            </s-stack>
          </s-section>
          <s-section heading="Every request" padding="none">
            <s-table>
              <s-table-header-row>
                <s-table-header listSlot="primary">Shopper request</s-table-header>
                <s-table-header listSlot="inline">Result</s-table-header>
                <s-table-header>What the agent got</s-table-header>
                <s-table-header>In stock but not shown</s-table-header>
              </s-table-header-row>
              <s-table-body>
                {run.results.map((r: any) => (
                  <s-table-row key={r.id}>
                    <s-table-cell>{r.query}</s-table-cell>
                    <s-table-cell>
                      <s-badge tone={VERDICT[r.verdict]?.tone}>{VERDICT[r.verdict]?.label}</s-badge>
                    </s-table-cell>
                    <s-table-cell>
                      {(r.top || []).length
                        ? r.top.map((t: any) => `${t.title} ${money(t.price, 2)}${t.suits ? "" : " ✗"}`).join(" · ")
                        : r.error || "Nothing"}
                      {(r.unmet || []).length ? ` (${r.unmet.join("; ")})` : ""}
                    </s-table-cell>
                    <s-table-cell>
                      {(r.missed_products || []).map((m: any) => `${m.title} ${money(m.price, 2)}`).join(" · ") || "—"}
                    </s-table-cell>
                  </s-table-row>
                ))}
              </s-table-body>
            </s-table>
          </s-section>
          <s-section slot="aside" heading="Catalogue health">
            <s-stack direction="block" gap="small-300">
              <s-text>{run.health.products.toLocaleString()} products, {run.health.in_stock.toLocaleString()} in stock</s-text>
              <s-text>{run.health.no_product_type.toLocaleString()} with no product type</s-text>
              <s-text>{run.health.scores_in_description.toLocaleString()} with a critic score agents can read</s-text>
              <s-link href="/app/fixes">Fix these</s-link>
            </s-stack>
          </s-section>
          {(d?.history || []).length > 1 && (
            <s-section slot="aside" heading="Score over time">
              <s-stack direction="block" gap="small-300">
                {d.history.slice().reverse().map((h: any) => (
                  <s-stack key={h.at} direction="inline" justifyContent="space-between">
                    <s-text color="subdued">{when(h.at)}</s-text>
                    <s-text type="strong">{h.score}</s-text>
                  </s-stack>
                ))}
              </s-stack>
            </s-section>
          )}
        </>
      )}
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
