import type { ActionFunctionArgs, HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useFetcher, useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { changePlan, getPlan, metricsView } from "../agent-trust.server";
import { money, PLANS } from "../components/ui";

/**
 * Plans. Free shows what AI is doing and what it's costing; Growth shows how
 * to win those sales; Trust adds the risk tools. Billing runs through Shopify
 * once the app is publicly listed; during the pilot, switching is free.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  const [plan, opp] = await Promise.all([getPlan(session.shop), metricsView("opportunities", session.shop, { days: 30 })]);
  return { plan, missed: opp?.missed_total ?? null };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  if (intent === "trial") return changePlan(session.shop, { start_trial: true }).catch(() => getPlan(session.shop));
  if (intent === "plan") return changePlan(session.shop, { plan: String(form.get("plan")) });
  return getPlan(session.shop);
};

export default function Plans() {
  const { plan, missed } = useLoaderData<typeof loader>();
  const fetcher = useFetcher();
  const choose = (key: string) => fetcher.submit({ intent: "plan", plan: key }, { method: "post" });
  return (
    <s-page heading="Plans">
      <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>
      {plan.on_trial ? (
        <s-banner tone="success" heading={`Free trial: every feature for ${plan.trial_days_left} more day${plan.trial_days_left === 1 ? "" : "s"}`}>
          Pick a plan to keep what you need when the trial ends.
        </s-banner>
      ) : missed ? (
        <s-banner tone="warning" heading={`AI shoppers left ${money(missed)} on the table in the last 30 days`}>
          Growth shows why each sale was lost and how to fix it.
        </s-banner>
      ) : null}
      <s-section>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
          {PLANS.map((p) => {
            const current = plan.plan === p.key;
            return (
              <s-box key={p.key} padding="base" border="base" borderRadius="base"
                background={p.key === "growth" ? "subdued" : "base"}>
                <s-stack direction="block" gap="base">
                  <s-stack direction="inline" gap="small" alignItems="center">
                    <s-heading>{p.name}</s-heading>
                    {current && <s-badge tone="success">Current</s-badge>}
                    {p.key === "growth" && !current && <s-badge tone="info">Most popular</s-badge>}
                  </s-stack>
                  <s-text type="strong">{p.price}</s-text>
                  <s-text color="subdued">{p.pitch}</s-text>
                  <s-unordered-list>
                    {p.features.map((f) => <s-list-item key={f}>{f}</s-list-item>)}
                  </s-unordered-list>
                  {current ? (
                    <s-button disabled>Your plan</s-button>
                  ) : (
                    <s-button variant={p.key === "growth" ? "primary" : "secondary"} onClick={() => choose(p.key)}
                      loading={fetcher.state !== "idle" || undefined}>
                      {p.key === "free" ? "Switch to Free" : `Choose ${p.name}`}
                    </s-button>
                  )}
                </s-stack>
              </s-box>
            );
          })}
        </div>
      </s-section>
      {plan.trial_available && !plan.on_trial && (
        <s-section>
          <s-stack direction="inline" gap="base" alignItems="center">
            <s-text>Not sure yet? Try every feature free for 14 days.</s-text>
            <s-button variant="primary" onClick={() => fetcher.submit({ intent: "trial" }, { method: "post" })}>
              Start free trial
            </s-button>
          </s-stack>
        </s-section>
      )}
      <s-section>
        <s-text color="subdued">
          Pilot pricing in USD. No charge during the pilot; billing moves to your Shopify invoice when Agent Trust is
          listed on the Shopify App Store.
        </s-text>
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
