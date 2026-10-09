import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useEffect } from "react";
import { useFetcher, useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate, unauthenticated } from "../shopify.server";
import {
  DASHBOARD, ensureOrderScan, ensurePixel, getPlan, metricsView, scanSummary, SCAN_DAYS, shelfLatest,
} from "../agent-trust.server";
import {
  ActionBadge, ago, DailyBars, HBars, Kpi, KpiGrid, LiveDot, Locked, money, ORIGIN_COLOURS, OriginBadge, pct,
  TrialButton, VisitsChart, when,
} from "../components/ui";

/**
 * Agent Trust home in Shopify admin: AI orders found in past orders, this
 * month's numbers, what needs attention, AI revenue by day, where AI orders
 * come from, the latest AI orders, and setup status.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { admin, session } = await authenticate.admin(request);
  const shop = session.shop;
  const [pixel, overview, scanState, live, channels, opp, plan, shelf] = await Promise.all([
    ensurePixel(admin, shop),
    metricsView("overview", shop, { days: 30 }),
    ensureOrderScan(shop, async () => (await unauthenticated.admin(shop)).admin.graphql),
    metricsView("live", shop, { minutes: 30 }),
    metricsView("channels", shop, { days: 30 }),
    metricsView("opportunities", shop, { days: 30 }),
    getPlan(shop),
    shelfLatest(shop),
  ]);
  const scan = scanState.status === "done" ? await scanSummary(shop).catch(() => null) : null;
  const embedLink =
    `https://${shop}/admin/themes/current/editor?context=apps&template=index` +
    `&activateAppId=${process.env.SHOPIFY_API_KEY}/tracker`;
  return {
    shop, pixel, overview, live, channels: channels?.channels ?? [], opp, plan, shelf, scan, scanStatus: scanState.status, scanDays: SCAN_DAYS, embedLink,
    dashboard: `${DASHBOARD}/?shop=${shop}`,
  };
};

const SOURCES: [string, string][] = [
  ["tracker", "Storefront tracker"], ["orders", "Orders"], ["pixel", "Checkout pixel"],
  ["edge", "Cloudflare (edge)"],
];
const SEVERITY: Record<string, { tone: "critical" | "warning" | "info"; label: string }> = {
  high: { tone: "critical", label: "Urgent" }, medium: { tone: "warning", label: "Review" }, info: { tone: "info", label: "Tip" },
};

export default function Home() {
  const { overview, live: liveInitial, channels, opp, plan, shelf, scan, scanStatus, scanDays, pixel, embedLink, dashboard } =
    useLoaderData<typeof loader>();
  const has = (f: string) => plan.features.includes(f);
  const items: any[] = opp?.items ?? [];
  // Live strip: refresh every 20 seconds without reloading the page.
  const fetcher = useFetcher<any>();
  useEffect(() => {
    const t = setInterval(() => { if (fetcher.state === "idle") fetcher.load("/app/live"); }, 20000);
    return () => clearInterval(t);
  }, [fetcher]);
  const live: any = fetcher.data ?? liveInitial ?? { visitors: 0, agents: 0, people: 0, feed: [] };
  const visitDays = (overview?.daily ?? []).map((d: any) => ({
    date: d.date, ai: d.ai_referred || 0, people: Math.max(0, (d.human || 0) - (d.ai_referred || 0)),
    agents: (d.assistant || 0) + (d.automation || 0),
  }));
  const ao = overview?.ai_orders ?? {};
  const k = overview?.kpis ?? null;
  const sources: Record<string, boolean> = Object.fromEntries(
    (overview?.coverage?.sources ?? []).map((s: { key: string; connected: boolean }) => [s.key, s.connected]),
  );
  const daily = (overview?.daily ?? []).map((d: any) => ({ date: d.date, value: d.ai_influenced_revenue || 0 }));
  const origins = (ao.by_origin ?? []).filter((b: any) => !["human", "unmatched"].includes(b.origin));
  const recent: any[] = ao.recent_ai_orders ?? [];
  const attention: any[] = ao.attention ?? [];
  const review = ao.needs_review ?? 0;

  return (
    <s-page heading={plan.on_trial ? `Agent Trust · trial, ${plan.trial_days_left} days left` : "Agent Trust"}>
      <s-button slot="primary-action" href={plan.effective_plan === "free" ? "/app/plans" : "/app/orders"}>
        {plan.effective_plan === "free" ? "Upgrade" : "View orders"}
      </s-button>
      <s-button slot="secondary-actions" href={dashboard} target="_blank">Full dashboard</s-button>

      {/* Past-order scan: the first thing a new merchant sees. */}
      {scan && scan.ai_orders > 0 && (
        <s-banner tone="success" heading={`${scan.ai_orders} orders worth ${money(scan.ai_revenue)} came from AI in your last ${scanDays} days`}>
          {scan.ai_revenue_share != null ? `${pct(scan.ai_revenue_share)} of revenue. ` : ""}
          {(scan.by_source ?? []).slice(0, 3).map((b: any) => `${b.source}: ${b.orders}`).join(" · ")}
          {scan.ai_aov != null && scan.other_aov != null
            ? `. Average AI order ${money(scan.ai_aov)} vs ${money(scan.other_aov)} for everything else.`
            : ""}
        </s-banner>
      )}
      {scan && scan.ai_orders === 0 && (
        <s-banner tone="info" heading={`No AI orders in your last ${scanDays} days of orders`}>
          {scan.test_orders ? `${scan.test_orders} test orders weren't counted. ` : ""}
          Shopify's referral data only shows AI that declares itself. The tracker catches the rest from now on.
        </s-banner>
      )}
      {!scan && scanStatus !== "never" && (
        <s-banner tone="info" heading={`Scanning your last ${scanDays} days of orders`}>
          Looking for orders that came from ChatGPT, Perplexity, Copilot and other AI. Reload in a minute.
        </s-banner>
      )}

      <s-section heading="Can AI agents find your products?">
        {shelf?.run ? (
          <s-stack direction="block" gap="base">
            <s-stack direction="inline" gap="base" alignItems="center">
              <span style={{ fontSize: 34, fontWeight: 650, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
                {shelf.run.score}<span style={{ fontSize: 16, color: "#6d7175", fontWeight: 500 }}> / 100</span>
              </span>
              <s-text color="subdued">
                {shelf.run.found} found · {shelf.run.partial} partial · {shelf.run.missed} missed, from {shelf.run.scored} shopper
                requests sent to your store's agent search
              </s-text>
            </s-stack>
            {shelf.run.results.filter((r: any) => r.verdict === "missed" || r.verdict === "partial").slice(0, 4).map((r: any) => (
              <s-box key={r.id} padding="small" border="base" borderRadius="base">
                <s-stack direction="block" gap="small-300">
                  <s-stack direction="inline" gap="small" alignItems="center">
                    <s-badge tone={r.verdict === "missed" ? "critical" : "warning"}>{r.verdict === "missed" ? "Missed" : "Partial"}</s-badge>
                    <s-text type="strong">"{r.query}"</s-text>
                  </s-stack>
                  {(r.missed_products || []).length > 0 ? (
                    <s-text color="subdued">
                      In stock but not shown: {r.missed_products.slice(0, 3).map((m: any) => `${m.title} (${money(m.price, 2)})`).join(", ")}
                    </s-text>
                  ) : r.unmet?.length ? (
                    <s-text color="subdued">{r.unmet.join("; ")}</s-text>
                  ) : null}
                </s-stack>
              </s-box>
            ))}
            <s-stack direction="inline" gap="small">
              <s-button variant="primary" href="/app/fixes">See fixes</s-button>
              <s-button href="/app/shelf">All requests</s-button>
            </s-stack>
          </s-stack>
        ) : (
          <s-text color="subdued">
            Testing your store now: we send realistic shopper requests to your store's agent search (the one ChatGPT and
            other agents use) and check what comes back. Reload in a few minutes.
          </s-text>
        )}
      </s-section>

      <s-section heading="Agent sales left on the table, last 30 days">
        {items.length ? (
          <s-stack direction="block" gap="base">
            <s-stack direction="block" gap="none">
              <span style={{ fontSize: 30, fontWeight: 650, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
                {money(opp?.missed_total ?? 0)}
              </span>
              <s-text color="subdued">
                {money(opp?.missed_measured ?? 0)} measured from abandoned agent carts and checkouts; the rest estimated
                from agents turned away by dead ends.
              </s-text>
            </s-stack>
            {items.map((i) => (
              <s-box key={i.key} padding="base" border="base" borderRadius="base">
                <s-stack direction="block" gap="small-200">
                  <s-stack direction="inline" justifyContent="space-between" gap="base" alignItems="center">
                    <s-stack direction="inline" gap="small" alignItems="center">
                      <s-badge tone={i.category === "protect" ? "critical" : i.category === "recover" ? "warning" : "info"}>
                        {i.category === "protect" ? "Protect" : i.category === "recover" ? "Recover" : "Improve"}
                      </s-badge>
                      <s-text type="strong">{i.title}</s-text>
                    </s-stack>
                    {i.value != null && (
                      <s-text type="strong">{i.estimate ? "~" : ""}{money(i.value)}</s-text>
                    )}
                  </s-stack>
                  <s-text color="subdued">{i.detail}</s-text>
                  {has(i.category === "protect" ? "review" : "fixes") ? (
                    <s-stack direction="inline" gap="small-200" alignItems="start">
                      <s-icon type="lightbulb" tone="info" />
                      <s-text>{i.fix}</s-text>
                    </s-stack>
                  ) : (
                    <s-stack direction="inline" gap="small" alignItems="center">
                      <s-icon type="lock" />
                      <s-text color="subdued">
                        {i.category === "protect" ? "Evidence and review queue on Trust." : "How to fix this is on Growth."}
                      </s-text>
                      {plan.trial_available ? <TrialButton label="Unlock free for 14 days" variant="secondary" /> : (
                        <s-link href="/app/plans">See plans</s-link>
                      )}
                    </s-stack>
                  )}
                </s-stack>
              </s-box>
            ))}
          </s-stack>
        ) : (
          <s-text color="subdued">
            Nothing missed yet. As agents shop your store, abandoned agent carts, checkouts and dead ends show up here
            with what they cost you.
          </s-text>
        )}
      </s-section>

      <s-section>
        <s-stack direction="block" gap="small">
          <s-stack direction="inline" gap="small" alignItems="center">
            <LiveDot />
            <s-text type="strong">
              {live.visitors} on your store now
            </s-text>
            <s-text color="subdued">
              · {live.agents} agent{live.agents === 1 ? "" : "s"} · {live.people} {live.people === 1 ? "person" : "people"} · last 30 minutes
            </s-text>
          </s-stack>
          {(live.feed ?? []).slice(0, 5).map((e: any, i: number) => (
            <s-stack key={i} direction="inline" gap="small" alignItems="center">
              <s-badge tone={e.is_agent ? "warning" : "neutral"}>{e.is_agent ? e.who : "Visitor"}</s-badge>
              <s-text>{e.text}</s-text>
              <s-text color="subdued">{ago(e.seconds_ago)}</s-text>
            </s-stack>
          ))}
        </s-stack>
      </s-section>

      <s-section heading="Last 30 days">
        <KpiGrid>
          <Kpi label="Revenue from AI" value={money(ao.ai_revenue ?? 0)}
            note={ao.ai_orders && ao.ai_test_orders === ao.ai_orders
              ? `${ao.ai_orders} test order${ao.ai_orders === 1 ? "" : "s"}, not counted`
              : `${ao.ai_orders ?? 0} orders${ao.ai_revenue_share != null ? ` · ${pct(ao.ai_revenue_share)} of revenue` : ""}${ao.ai_test_orders ? ` (${ao.ai_test_orders} test not counted)` : ""}`} />
          <Kpi label="Agent orders" value={String(ao.agent_orders ?? 0)}
            note={`${money(ao.agent_revenue ?? 0)} placed or assisted by agents`} />
          <Kpi label="Agent conversion" value={pct(ao.agent_conversion, 2)}
            note={ao.human_conversion != null ? `People convert at ${pct(ao.human_conversion, 2)}` : undefined} />
          <Kpi label="Needs review" value={String(review)} note="Orders to check before fulfilling"
            tone={review ? "critical" : undefined} />
        </KpiGrid>
      </s-section>

      <s-section heading="Needs attention">
        {attention.length ? (
          <s-stack direction="block" gap="base">
            {attention.map((a, i) => (
              <s-stack key={i} direction="inline" gap="base" alignItems="start" justifyContent="space-between">
                <s-stack direction="inline" gap="small" alignItems="start">
                  <s-badge tone={SEVERITY[a.severity]?.tone}>{SEVERITY[a.severity]?.label}</s-badge>
                  <s-stack direction="block" gap="none">
                    <s-text type="strong">{a.title}</s-text>
                    <s-text color="subdued">{a.detail}</s-text>
                  </s-stack>
                </s-stack>
                {a.page === "Orders" && <s-button variant="tertiary" href="/app/orders?filter=review">Review</s-button>}
              </s-stack>
            ))}
          </s-stack>
        ) : (
          <s-text color="subdued">Nothing needs you right now.</s-text>
        )}
      </s-section>

      <s-section heading="Visits">
        {visitDays.some((d: any) => d.people + d.ai + d.agents > 0) ? (
          <VisitsChart days={visitDays} />
        ) : (
          <s-text color="subdued">No visits recorded in the last 30 days.</s-text>
        )}
      </s-section>

      <s-section heading="Revenue by source" padding={has("channels") ? "none" : "base"}>
        {!has("channels") ? (
          <Locked feature="channels" title="Revenue and revenue per visitor for every source" trialAvailable={plan.trial_available}>
            See which AI assistants and agents bring the most valuable shoppers, next to Google, social and direct.
          </Locked>
        ) : channels.length ? (
          <s-table>
            <s-table-header-row>
              <s-table-header listSlot="primary">Source</s-table-header>
              <s-table-header format="numeric">Visitors</s-table-header>
              <s-table-header format="numeric">Conversion</s-table-header>
              <s-table-header format="currency">Revenue</s-table-header>
              <s-table-header format="currency">Per visitor</s-table-header>
            </s-table-header-row>
            <s-table-body>
              {channels.slice(0, 10).map((c: any) => (
                <s-table-row key={c.channel}>
                  <s-table-cell>
                    <s-stack direction="inline" gap="small-200" alignItems="center">
                      <s-text>{c.channel}</s-text>
                      {c.kind === "agent" && <s-badge tone="warning">Agent</s-badge>}
                      {c.kind === "ai" && <s-badge tone="info">AI</s-badge>}
                    </s-stack>
                  </s-table-cell>
                  <s-table-cell>{c.visitors ? c.visitors.toLocaleString() : "—"}</s-table-cell>
                  <s-table-cell>{c.visitors ? pct(c.conversion, 1) : "—"}</s-table-cell>
                  <s-table-cell>{money(c.revenue)}</s-table-cell>
                  <s-table-cell>{c.revenue_per_visitor != null ? money(c.revenue_per_visitor, 2) : "—"}</s-table-cell>
                </s-table-row>
              ))}
            </s-table-body>
          </s-table>
        ) : (
          <s-box padding="base"><s-text color="subdued">No visits recorded yet.</s-text></s-box>
        )}
      </s-section>

      <s-section heading="Revenue from AI, by day">
        {daily.some((d: any) => d.value > 0) ? (
          <DailyBars days={daily} />
        ) : (
          <s-text color="subdued">No AI revenue in the last 30 days{ao.ai_test_orders ? " (test orders aren't counted)" : ""}.</s-text>
        )}
      </s-section>

      <s-section heading="Latest AI orders" padding="none">
        {recent.length ? (
          <s-table>
            <s-table-header-row>
              <s-table-header listSlot="primary">Order</s-table-header>
              <s-table-header>Placed</s-table-header>
              <s-table-header listSlot="secondary">Origin</s-table-header>
              <s-table-header>Agent</s-table-header>
              <s-table-header format="currency">Value</s-table-header>
              <s-table-header listSlot="inline">Action</s-table-header>
            </s-table-header-row>
            <s-table-body>
              {recent.map((o) => (
                <s-table-row key={o.order_id} clickDelegate={`order-${o.order_id}`}>
                  <s-table-cell>
                    <s-link id={`order-${o.order_id}`} href={`/app/orders/${o.order_id}`}>
                      #{o.shopify_order_id}
                    </s-link>
                  </s-table-cell>
                  <s-table-cell>{when(o.created_at)}</s-table-cell>
                  <s-table-cell><OriginBadge origin={o.origin} label={o.origin_label} /></s-table-cell>
                  <s-table-cell>{o.agent || (o.ai_source ? `via ${o.ai_source}` : "—")}</s-table-cell>
                  <s-table-cell>{money(o.order_value, 2)}{o.is_test ? " (test)" : ""}</s-table-cell>
                  <s-table-cell><ActionBadge action={o.action} /></s-table-cell>
                </s-table-row>
              ))}
            </s-table-body>
          </s-table>
        ) : (
          <s-box padding="base"><s-text color="subdued">No AI orders yet.</s-text></s-box>
        )}
      </s-section>

      <s-section slot="aside" heading="Where AI orders come from">
        {origins.length ? (
          <HBars rows={origins.map((b: any) => ({
            key: b.origin, label: `${b.label} (${b.orders})`, value: b.revenue || b.orders,
            display: b.revenue ? money(b.revenue) : `${b.orders} test`, colour: ORIGIN_COLOURS[b.origin],
          }))} />
        ) : (
          <s-text color="subdued">No AI orders in the last 30 days.</s-text>
        )}
      </s-section>

      <s-section slot="aside" heading="Traffic">
        {k ? (
          <s-stack direction="block" gap="small-300">
            <s-text>{k.sessions.toLocaleString()} visits, {k.agent_sessions.toLocaleString()} from agents ({pct(k.agent_share)})</s-text>
            <s-text color="subdued">{k.ai_referred_visits.toLocaleString()} people sent by AI assistants</s-text>
            <s-link href="/app/behaviour">See the behaviour map</s-link>
            <s-link href="/app/products">How agents shop your store</s-link>
          </s-stack>
        ) : (
          <s-text color="subdued">No visits recorded yet.</s-text>
        )}
      </s-section>

      <s-section slot="aside" heading="Setup">
        <s-stack direction="block" gap="small">
          {SOURCES.map(([key, label]) => {
            const on = key === "pixel" ? pixel === "on" || sources.pixel : !!sources[key];
            return (
              <s-stack key={key} direction="inline" gap="small" alignItems="center">
                <s-icon type={on ? "check-circle" : "circle"} tone={on ? "success" : "neutral"} />
                <s-text>{label}</s-text>
                {!on && key === "tracker" && <s-link href={embedLink} target="_blank">Switch on</s-link>}
                {!on && key === "edge" && <s-text color="subdued">optional</s-text>}
              </s-stack>
            );
          })}
        </s-stack>
      </s-section>

      <s-section slot="aside" heading="Privacy">
        <s-text color="subdued">
          Records behaviour (clicks, mouse movement and typing as counts), pages and products viewed, adds to cart and
          site searches (emails and long numbers removed). Never what's typed into forms, never card details. IP
          addresses are hashed.
        </s-text>
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
