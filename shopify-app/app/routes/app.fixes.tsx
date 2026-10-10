import type { ActionFunctionArgs, HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useFetcher, useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { applyAlt, applyTypes, getPlan, imagesWithoutAlt, scoreFields, shelfLatest, untypedProducts } from "../agent-trust.server";
import { Locked } from "../components/ui";

/**
 * Fix queue: changes that help agents find and choose the store's products.
 *   1. Product types, from the store's own collections (preview, then apply).
 *   2. Critic scores and cellaring advice: where they're stored, so they can
 *      be put where agents read them.
 *   3. Image alt text from product names (preview, then apply).
 *   4. Readiness fixes the app can't make (robots.txt, CDN bot blocks,
 *      barcodes, reviews): step-by-step, from the readiness check.
 *   5. Internal tags and collections agents see (review only: tags often
 *      drive collections and discounts, so nothing is removed automatically).
 */
const BATCH = 100;

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { admin, session } = await authenticate.admin(request);
  const plan = await getPlan(session.shop);
  const [shelf, untyped, scores, alt] = await Promise.all([
    shelfLatest(session.shop),
    untypedProducts(admin.graphql).catch(() => []),
    scoreFields(admin.graphql).catch(() => []),
    imagesWithoutAlt(admin.graphql).catch(() => ({ items: [], images: 0, checked: 0 })),
  ]);
  const byType: Record<string, { count: number; examples: string[] }> = {};
  for (const u of untyped) {
    const t = (byType[u.proposed] ??= { count: 0, examples: [] });
    t.count++;
    if (t.examples.length < 3) t.examples.push(u.title);
  }
  const altImages = alt.items.reduce((n, i) => n + i.media.length, 0);
  const guides = (shelf?.run?.readiness?.checks ?? []).filter((c: any) => (c.status === "fail" || c.status === "warn") && c.fix_kind === "guide");
  return {
    plan, health: shelf?.run?.health ?? null, byType, untypedTotal: untyped.length, scores, guides,
    alt: { images: alt.images, missing: altImages, checked: alt.checked, examples: alt.items.slice(0, 3).map((i) => i.media[0].alt) },
  };
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
  if (form.get("intent") === "alt") {
    const { items } = await imagesWithoutAlt(admin.graphql);
    const r = await applyAlt(admin.graphql, items.slice(0, BATCH));
    return { altApplied: r.done, errors: r.errors.slice(0, 5) };
  }
  return { error: "Unknown fix" };
};

export default function Fixes() {
  const { plan, health, byType, untypedTotal, scores, alt, guides } = useLoaderData<typeof loader>();
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
      {fetcher.data?.altApplied != null && (
        <s-banner tone="success" heading={`Added alt text to ${fetcher.data.altApplied} images`}>
          {fetcher.data.errors?.length ? `Some failed: ${fetcher.data.errors.join("; ")}` : "Agents and screen readers can now tell what each image shows."}
        </s-banner>
      )}
      {fetcher.data?.error && <s-banner tone="critical" heading={fetcher.data.error} />}

      <s-section heading="1. Set product types">
        <s-stack direction="block" gap="base">
          <s-text>
            {health ? `${health.no_product_type.toLocaleString()} of ${health.products.toLocaleString()} products have no product type. ` : ""}
            Agents use the product type to tell what something is (a rain jacket from a fleece, a rosé from a white).
            We can set it from the collection each product is already in. {untypedTotal.toLocaleString()} can be set this
            way. Skip any type that doesn't look right.
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
            <s-text color="subdued">Nothing to set: every product in one of your product collections already has a type.</s-text>
          )}
        </s-stack>
      </s-section>

      <s-section heading="2. Put ratings and key details where agents can read them">
        <s-stack direction="block" gap="small">
          <s-text>
            {health ? `${health.scores_in_description.toLocaleString()} products have a critic score in their description. ` : ""}
            Ratings, scores and key details (size, materials, compatibility, cellaring) shown on your pages but stored in
            separate fields don't reach agents, so an agent can't confirm them when a shopper asks.
          </s-text>
          {scores.length ? (
            <>
              <s-text type="strong">Where these details are stored:</s-text>
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

      <s-section heading="3. Describe your product images">
        <s-stack direction="block" gap="base">
          <s-text>
            {alt.missing
              ? `${alt.missing.toLocaleString()} of ${alt.images.toLocaleString()} images on your active products have no alt text. `
              : `Every image on your active products has alt text. `}
            Agents and Google read alt text to know what an image shows. We fill it from the brand and product name;
            you can refine any of them later in Shopify.
          </s-text>
          {alt.missing > 0 && (
            <>
              <s-text color="subdued">For example: {alt.examples.join(" · ")}</s-text>
              <s-stack direction="inline" gap="small">
                <s-button disabled={!canApply || busy || undefined} loading={busy || undefined}
                  onClick={() => fetcher.submit({ intent: "alt" }, { method: "post" })}>
                  Add alt text{alt.missing > BATCH ? " (next batch)" : ""}
                </s-button>
              </s-stack>
            </>
          )}
        </s-stack>
      </s-section>

      {guides.length > 0 && (
        <s-section heading="4. Fixes to make in Shopify or your CDN">
          <s-stack direction="block" gap="base">
            <s-text>These came from the readiness check. The app can't change them for you, so here's how.</s-text>
            {guides.map((c: any) => (
              <s-box key={c.id} padding="base" border="base" borderRadius="base">
                <s-stack direction="block" gap="small-300">
                  <s-stack direction="inline" gap="small" alignItems="center">
                    <s-badge tone={c.status === "fail" ? "critical" : "warning"}>{c.status === "fail" ? "Fix" : "Improve"}</s-badge>
                    <s-text type="strong">{c.title}</s-text>
                  </s-stack>
                  <s-text>{c.detail}</s-text>
                  <s-text color="subdued">{c.fix}</s-text>
                </s-stack>
              </s-box>
            ))}
          </s-stack>
        </s-section>
      )}

      <s-section heading={guides.length ? "5. Review what agents see that shoppers don't" : "4. Review what agents see that shoppers don't"}>
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
