import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { getPlan, metricsView } from "../agent-trust.server";
import { AgentFunnel, BehaviourMap, Locked, money, PathChips, when } from "../components/ui";
import type { MapPoint } from "../components/ui";

/**
 * Behaviour: the map of every visit by how it behaved, the paths agents take
 * through the store, and the latest agent visits step by step.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  const plan = await getPlan(session.shop);
  if (!plan.features.includes("behaviour")) return { d: null, opp: null, plan };
  const [d, opp] = await Promise.all([
    metricsView("behaviour", session.shop, { days: 30 }),
    metricsView("opportunities", session.shop, { days: 30 }),
  ]);
  return { d, opp, plan };
};

export default function Behaviour() {
  const { d, opp, plan } = useLoaderData<typeof loader>();
  if (!plan.features.includes("behaviour")) {
    return (
      <s-page heading="Behaviour">
        <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>
        <s-section>
          <Locked feature="behaviour" title="See how every visit behaves, and where agents drop out" trialAvailable={plan.trial_available}>
            The behaviour map places every visit by how it behaves, so agents stand out from people even when they
            hide what they are. See where agents drop out of your funnel, what it costs, and each agent visit step by step.
          </Locked>
        </s-section>
      </s-page>
    );
  }
  const funnel = opp?.funnel;
  const points: MapPoint[] = d?.points ?? [];
  const recent: any[] = d?.recent ?? [];
  const look = d?.lookalikes ?? {};
  return (
    <s-page heading="Behaviour">
      <s-link slot="breadcrumb-actions" href="/app">Agent Trust</s-link>

      <s-section heading="Behaviour map">
        <s-stack direction="block" gap="base">
          <s-text color="subdued">
            Every dot is a visit in the last 30 days, placed by how it behaved: how the pointer moved before clicks,
            whether fields were filled without typing, how fast it moved between pages, how much it read and scrolled,
            and whether the tab was hidden. Visits that behave alike sit together, so agents separate from people
            without relying on what they claim to be. Hover a dot for details; click it to see the visit.
          </s-text>
          {points.length >= 3 ? (
            <BehaviourMap points={points} />
          ) : (
            <s-banner tone="info" heading="Not enough visits to map yet">
              The map fills in as the storefront tracker records visits with pointer and page-by-page behaviour.
            </s-banner>
          )}
          {(look.people_like_agents > 0 || look.agents_like_people > 0) && (
            <s-banner tone="warning" heading="Visits worth a second look">
              {look.people_like_agents > 0 && `${look.people_like_agents} visit${look.people_like_agents === 1 ? "" : "s"} classed as people behave like agents. `}
              {look.agents_like_people > 0 && `${look.agents_like_people} agent visit${look.agents_like_people === 1 ? "" : "s"} blend in with people.`}
              {" "}They're ringed in red on the map.
            </s-banner>
          )}
        </s-stack>
      </s-section>

      <s-section heading="Where agents drop out">
        {funnel?.steps?.length && funnel.agent_visits ? (
          <s-stack direction="block" gap="base">
            <AgentFunnel steps={funnel.steps} lostCarts={funnel.lost_carts} lostCheckouts={funnel.lost_checkouts} />
            <s-link href="/app">See what to fix on Home</s-link>
          </s-stack>
        ) : (
          <s-text color="subdued">No agent visits yet.</s-text>
        )}
      </s-section>

      <s-section heading="Latest agent visits">
        {recent.length ? (
          <s-stack direction="block" gap="base">
            {recent.map((r) => (
              <s-box key={r.session_key} padding="base" border="base" borderRadius="base">
                <s-stack direction="block" gap="small">
                  <s-stack direction="inline" justifyContent="space-between" alignItems="center" gap="base">
                    <s-stack direction="inline" gap="small" alignItems="center">
                      <s-link href={`/app/visits/${encodeURIComponent(r.session_key)}`}>{r.agent}</s-link>
                      <s-badge tone={r.class === "automation" ? "warning" : "info"}>{r.class_label}</s-badge>
                      {r.ordered && <s-badge tone="success">Ordered{r.cart_value ? ` ${money(r.cart_value)}` : ""}</s-badge>}
                    </s-stack>
                    <s-text color="subdued">{when(r.first_seen)}</s-text>
                  </s-stack>
                  <PathChips path={r.path} />
                  {r.summary && (
                    <s-text color="subdued">
                      {r.summary.steps} steps in {Math.round(r.summary.duration_seconds)}s
                      {r.summary.searches?.length ? ` · searched "${r.summary.searches[0]}"` : ""}
                      {r.summary.median_seconds_between_steps != null ? ` · ${Math.round(r.summary.median_seconds_between_steps)}s between steps` : ""}
                      {r.confidence != null ? ` · confidence ${r.confidence.toFixed(2)}` : ""}
                    </s-text>
                  )}
                </s-stack>
              </s-box>
            ))}
          </s-stack>
        ) : (
          <s-text color="subdued">No agent visits with page-by-page steps yet.</s-text>
        )}
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
