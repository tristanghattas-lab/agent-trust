import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate, unauthenticated } from "../shopify.server";
import {
  DASHBOARD, ensureOrderScan, ensurePixel, metricsView, scanSummary, SCAN_DAYS,
} from "../agent-trust.server";
import {
  ActionBadge, DailyBars, HBars, Kpi, KpiGrid, money, ORIGIN_COLOURS, OriginBadge, pct, when,
} from "../components/ui";

/**
 * Agent Trust home in Shopify admin: AI orders found in past orders, this
 * month's numbers, what needs attention, AI revenue by day, where AI orders
 * come from, the latest AI orders, and setup status.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { admin, session } = await authenticate.admin(request);
  const shop = session.shop;
  const [pixel, overview, scanState] = await Promise.all([
    ensurePixel(admin, shop),
    metricsView("overview", shop, { days: 30 }),
    ensureOrderScan(shop, async () => (await unauthenticated.admin(shop)).admin.graphql),
  ]);
  const scan = scanState.status === "done" ? await scanSummary(shop).catch(() => null) : null;
  const embedLink =
    `https://${shop}/admin/themes/current/editor?context=apps&template=index` +
    `&activateAppId=${process.env.SHOPIFY_API_KEY}/tracker`;
  return {
    shop, pixel, overview, scan, scanStatus: scanState.status, scanDays: SCAN_DAYS, embedLink,
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
  const { overview, scan, scanStatus, scanDays, pixel, embedLink, dashboard } = useLoaderData<typeof loader>();
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
    <s-page heading="Agent Trust">
      <s-button slot="primary-action" href="/app/orders">View orders</s-button>
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
