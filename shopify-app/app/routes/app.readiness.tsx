import type { ActionFunctionArgs, HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useFetcher, useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { shelfLatest, shelfStart } from "../agent-trust.server";
import { when } from "../components/ui";

/**
 * Agent readiness: can AI agents reach the store (robots.txt, firewall/CDN
 * blocks) and is the product data they read complete (structured data,
 * barcodes, brand, ratings, descriptions)? Runs with each shelf test.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  return { d: await shelfLatest(session.shop) };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  return shelfStart(session.shop).catch((e) => ({ error: String(e) }));
};

const STATUS: Record<string, { tone: "success" | "warning" | "critical" | "info"; label: string }> = {
  pass: { tone: "success", label: "OK" }, warn: { tone: "warning", label: "Improve" },
  fail: { tone: "critical", label: "Fix" }, info: { tone: "info", label: "Note" },
};
const AREAS = ["Access", "Product data", "Catalogue"];

export default function Readiness() {
  const { d } = useLoaderData<typeof loader>();
  const fetcher = useFetcher();
  const r = d?.run?.readiness;
  const running = d?.status === "running" || fetcher.state !== "idle";
  const toFix = (r?.checks || []).filter((c: any) => c.status === "fail" || c.status === "warn");
  return (
    <s-page heading="Agent readiness">
      <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>
      <s-button slot="primary-action" loading={running || undefined}
        onClick={() => fetcher.submit({}, { method: "post" })}>Check again</s-button>
      {!r ? (
        <s-section>
          <s-text color="subdued">
            {running ? "Checking your store now. Reload in a few minutes." :
              "No readiness check yet. It runs with the shelf test: press Check again."}
          </s-text>
        </s-section>
      ) : (
        <>
          <s-section>
            <s-stack direction="inline" gap="base" alignItems="center">
              <span style={{ fontSize: 34, fontWeight: 650, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
                {r.score ?? "–"}<span style={{ fontSize: 16, color: "#6d7175", fontWeight: 500 }}> / 100</span>
              </span>
              <s-text color="subdued">
                {toFix.length ? `${toFix.length} thing${toFix.length === 1 ? "" : "s"} to fix so AI agents can reach and understand ${r.domain}.`
                  : `AI agents can reach ${r.domain} and read your product data.`} Checked {when(d.run.finished_at)}.
              </s-text>
            </s-stack>
          </s-section>
          {AREAS.map((area) => {
            const checks = r.checks.filter((c: any) => c.area === area);
            if (!checks.length) return null;
            return (
              <s-section key={area} heading={area}>
                <s-stack direction="block" gap="base">
                  {checks.map((c: any) => (
                    <s-box key={c.id} padding="base" border="base" borderRadius="base">
                      <s-stack direction="block" gap="small-300">
                        <s-stack direction="inline" gap="small" alignItems="center">
                          <s-badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</s-badge>
                          <s-text type="strong">{c.title}</s-text>
                        </s-stack>
                        <s-text>{c.detail}</s-text>
                        {(c.examples || []).length > 0 && c.status !== "pass" && (
                          <s-text color="subdued">For example: {c.examples.map((e: any) => e.title).join(" · ")}</s-text>
                        )}
                        {c.fix && c.status !== "pass" && (
                          <s-text color="subdued">
                            {c.fix_kind === "app" ? "Fix in the app: " : "How to fix: "}{c.fix}
                            {c.fix_kind === "app" ? <> <s-link href="/app/fixes">Open Fixes</s-link></> : null}
                          </s-text>
                        )}
                      </s-stack>
                    </s-box>
                  ))}
                </s-stack>
              </s-section>
            );
          })}
          {r.access && !r.access.usable && (
            <s-section>
              <s-text color="subdued">
                We couldn't load your store from our servers, so firewall and CDN blocks weren't checked this time.
              </s-text>
            </s-section>
          )}
          {(d?.history || []).filter((h: any) => h.readiness != null).length > 1 && (
            <s-section slot="aside" heading="Score over time">
              <s-stack direction="block" gap="small-300">
                {d.history.filter((h: any) => h.readiness != null).reverse().map((h: any) => (
                  <s-stack key={h.at} direction="inline" justifyContent="space-between">
                    <s-text color="subdued">{when(h.at)}</s-text>
                    <s-text type="strong">{h.readiness}</s-text>
                  </s-stack>
                ))}
              </s-stack>
            </s-section>
          )}
          <s-section slot="aside" heading="What this checks">
            <s-text color="subdued">
              Whether ChatGPT, Perplexity, Claude, Google and Bing are allowed in and can load your pages; whether your
              product pages publish the price, stock, brand, barcode and ratings agents read; and whether descriptions
              give agents enough to match a shopper's request. Shelf test measures the result: whether agents find
              your products.
            </s-text>
          </s-section>
        </>
      )}
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
