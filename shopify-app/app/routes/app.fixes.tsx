import type { ActionFunctionArgs, HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useFetcher, useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { applyTypes, getPlan, scoreFields, shelfLatest, untypedProducts } from "../agent-trust.server";
import { Locked } from "../components/ui";

/**
 * Fix queue: changes that help agents find and choose the store's products.
 *   1. Product types, from the store's own collections (preview, then apply).
 *   2. Critic scores and cellaring advice: where they're stored, so they can
 *      be put where agents read them.
 *   3. Internal tags and collections agents see (review only: tags often
 *      drive collections and discounts, so nothing is removed automatically).
 */
const BATCH = 100;

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { admin, session } = await authenticate.admin(request);
  const plan = await getPlan(session.shop);
  const [shelf, untyped, scores] = await Promise.all([
    shelfLatest(session.shop),
    untypedProducts(admin.graphql).catch(() => []),
    scoreFields(admin.graphql).catch(() => []),
  ]);
  const byType: Record<string, { count: number; examples: string[] }> = {};
  for (const u of untyped) {
    const t = (byType[u.proposed] ??= { count: 0, examples: [] });
    t.count++;
    if (t.examples.length < 3) t.examples.push(u.title);
  }
  return { plan, health: shelf?.run?.health ?? null, byType, untypedTotal: untyped.length, scores };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const { admin, session } = await authenticate.admin(request);
  const plan = await getPlan(session.shop);
  if (!plan.features.includes("fixes")) return { error: "Applying fixes is part of Growth." };
  const form = await request.formData();
  if (form.get("intent") === "types") {
    const type = String(form.get("type") || "");
    const items = (await untypedProducts(admin.graphql)).filter((u) => !type || u.proposed === type).slice(0, BATCH);
    const r = await applyTypes(admin.graphql, items.map((i) => ({ id: i.id, type: i.proposed })));
    return { applied: r.done, errors: r.errors.slice(0, 5), type };
  }
  return { error: "Unknown fix" };
};

export default function Fixes() {
  const { plan, health, byType, untypedTotal, scores } = useLoaderData<typeof loader>();
  const fetcher = useFetcher<any>();
  const canApply = plan.features.includes("fixes");
  const busy = fetcher.state !== "idle";
  const types = Object.entries(byType).sort((a, b) => b[1].count - a[1].count);
  return (
    <s-page heading="Fixes">
      <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>
      {fetcher.data?.applied != null && (
        <s-banner tone="success" heading={`Set the product type on ${fetcher.data.applied} products`}>
          {fetcher.data.errors?.length ? `Some failed: ${fetcher.data.errors.join("; ")}` : "Run the shelf test again to see the effect."}
        </s-banner>
      )}
      {fetcher.data?.error && <s-banner tone="critical" heading={fetcher.data.error} />}

      <s-section heading="1. Set product types">
        <s-stack direction="block" gap="base">
          <s-text>
            {health ? `${health.no_product_type.toLocaleString()} of ${health.products.toLocaleString()} products have no product type. ` : ""}
            Agents use the type to tell a rosé from a Fiano and a Champagne from a spirit. We can set it from the
            collection each product is already in. {untypedTotal.toLocaleString()} can be set this way.
          </s-text>
          {!canApply && (
            <Locked feature="fixes" title="Apply fixes in one click" trialAvailable={plan.trial_available}>
              Growth sets product types for you, in batches you approve.
            </Locked>
          )}
          {types.length ? (
            <s-table>
              <s-table-header-row>
                <s-table-header listSlot="primary">Set type to</s-table-header>
                <s-table-header format="numeric">Products</s-table-header>
                <s-table-header>For example</s-table-header>
                <s-table-header listSlot="inline">Apply</s-table-header>
              </s-table-header-row>
              <s-table-body>
                {types.map(([type, v]) => (
                  <s-table-row key={type}>
                    <s-table-cell>{type}</s-table-cell>
                    <s-table-cell>{v.count}</s-table-cell>
                    <s-table-cell>{v.examples.join(" · ")}</s-table-cell>
                    <s-table-cell>
                      <s-button disabled={!canApply || busy || undefined} loading={busy || undefined}
                        onClick={() => fetcher.submit({ intent: "types", type }, { method: "post" })}>
                        {v.count > BATCH ? `Apply next ${BATCH}` : "Apply"}
                      </s-button>
                    </s-table-cell>
                  </s-table-row>
                ))}
              </s-table-body>
            </s-table>
          ) : (
            <s-text color="subdued">Nothing to set: every product in a wine, spirits or accessories collection has a type.</s-text>
          )}
        </s-stack>
      </s-section>

      <s-section heading="2. Put critic scores where agents can read them">
        <s-stack direction="block" gap="small">
          <s-text>
            {health ? `${health.scores_in_description.toLocaleString()} products have a critic score in their description. ` : ""}
            Scores shown on your product pages but stored elsewhere never reach agents, so an agent asked for "a 95-point
            Cabernet" can't confirm yours.
          </s-text>
          {scores.length ? (
            <>
              <s-text type="strong">Where your scores and cellaring notes are stored:</s-text>
              <s-unordered-list>
                {scores.map((f: any) => (
                  <s-list-item key={f.key}>
                    {f.key} ({f.products} of the last 60 products updated) — e.g. {f.examples[0]}
                  </s-list-item>
                ))}
              </s-unordered-list>
              <s-text color="subdued">
                Next: confirm which field holds the scores, and we'll add a "Critic scores: …" line to each description
                for you to approve.
              </s-text>
            </>
          ) : (
            <s-text color="subdued">
              We couldn't find a score field. If your theme shows scores, tell us where they're entered and we'll add them
              to descriptions for you to approve.
            </s-text>
          )}
        </s-stack>
      </s-section>

      <s-section heading="3. Review what agents see that shoppers don't">
        <s-stack direction="block" gap="small">
          <s-text>
            Internal tags and collections are passed to agents with your products. They can't use them, and they make
            your products harder to match. Tags often power collections and discounts, so review these with whoever
            manages them rather than deleting.
          </s-text>
          {health?.internal_tags?.length ? (
            <s-text>Tags: {health.internal_tags.slice(0, 15).map((t: any) => `${t.tag} (${t.products})`).join(", ")}</s-text>
          ) : null}
          {health?.internal_collections?.length ? (
            <s-text>Collections: {health.internal_collections.slice(0, 15).join(", ")}</s-text>
          ) : null}
          {!health && <s-text color="subdued">Run the shelf test to see these.</s-text>}
        </s-stack>
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
