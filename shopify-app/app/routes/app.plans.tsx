import type { ActionFunctionArgs, HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useFetcher, useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate, BILLING_ON, BILLING_TEST, PAID_PLANS } from "../shopify.server";
import { activeSubscriptions, changePlan, getPlan, metricsView, syncBilling } from "../agent-trust.server";
import { money, PLANS } from "../components/ui";

/**
 * Plans. Free shows what AI is doing and what it's costing; Growth shows how
 * to win those sales; Trust adds the risk tools.
 *
 * BILLING=shopify (public app): paid plans go through Shopify's approval
 * page and land on the merchant's Shopify invoice; the 14-day trial needs no
 * card, and anyone choosing a paid plan mid-trial isn't charged until it ends.
 * Otherwise (custom app pilot) switching is free and recorded in the API.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session, admin } = await authenticate.admin(request);
  const [plan, opp] = await Promise.all([
    BILLING_ON ? syncBilling(admin.graphql, session.shop, BILLING_TEST) : getPlan(session.shop),
    metricsView("opportunities", session.shop, { days: 30 }),
  ]);
  return { plan, missed: opp?.missed_total ?? null, billing: BILLING_ON };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const { session, admin, billing } = await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  if (intent === "trial") return changePlan(session.shop, { start_trial: true }).catch(() => getPlan(session.shop));
  if (intent !== "plan") return getPlan(session.shop);

  const key = String(form.get("plan"));
  if (!BILLING_ON) return changePlan(session.shop, { plan: key });

  if (key === "free") {
    for (const sub of await activeSubscriptions(admin.graphql, BILLING_TEST)) {
      await billing.cancel({ subscriptionId: sub.id, isTest: BILLING_TEST, prorate: true });
    }
    return changePlan(session.shop, { plan: "free" });
  }
  const name = PAID_PLANS[key as keyof typeof PAID_PLANS];
  if (!name) return getPlan(session.shop);
  const current = await getPlan(session.shop);
  // Throws a redirect to Shopify's approval page; the app syncs the plan when
  // the merchant comes back (app.tsx loader) and on app_subscriptions/update.
  return billing.request({
    plan: name,
    isTest: BILLING_TEST,
    trialDays: current.on_trial ? current.trial_days_left : 0,
  });
};

export default function Plans() {
  const { plan, missed, billing } = useLoaderData<typeof loader>();
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
          {billing
            ? "Prices in USD, billed every 30 days on your Shopify invoice. Change or cancel any time from this page."
            : "Pilot pricing in USD. No charge during the pilot; billing moves to your Shopify invoice when Agent Trust is listed on the Shopify App Store."}
        </s-text>
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
