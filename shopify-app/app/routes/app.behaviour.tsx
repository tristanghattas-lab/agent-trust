import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";
import { metricsView } from "../agent-trust.server";
import { BehaviourMap, money, PathChips, pct, when } from "../components/ui";
import type { MapPoint } from "../components/ui";

/**
 * Behaviour: the map of every visit by how it behaved, the paths agents take
 * through the store, and the latest agent visits step by step.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  return { d: await metricsView("behaviour", session.shop, { days: 30 }) };
};

export default function Behaviour() {
  const { d } = useLoaderData<typeof loader>();
  const points: MapPoint[] = d?.points ?? [];
  const paths: any[] = d?.paths ?? [];
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

      <s-section heading="How agents move through your store" padding="none">
        {paths.length ? (
          <s-table>
            <s-table-header-row>
              <s-table-header listSlot="primary">Path</s-table-header>
              <s-table-header format="numeric">Visits</s-table-header>
              <s-table-header format="numeric">Orders</s-table-header>
              <s-table-header format="numeric">Typical time</s-table-header>
            </s-table-header-row>
            <s-table-body>
              {paths.map((p, i) => (
                <s-table-row key={i}>
                  <s-table-cell><PathChips path={p.path} /></s-table-cell>
                  <s-table-cell>{p.visits}</s-table-cell>
                  <s-table-cell>{p.orders ? `${p.orders} (${pct(p.conversion, 0)})` : "—"}</s-table-cell>
                  <s-table-cell>{p.median_seconds != null ? `${p.median_seconds}s` : "—"}</s-table-cell>
                </s-table-row>
              ))}
            </s-table-body>
          </s-table>
        ) : (
          <s-box padding="base"><s-text color="subdued">No agent visits with page-by-page steps yet.</s-text></s-box>
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
