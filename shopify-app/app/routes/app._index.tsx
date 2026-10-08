import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate, unauthenticated } from "../shopify.server";
import { ensureOrderScan, scanSummary, SCAN_DAYS } from "../agent-trust.server";

/**
 * Agent Trust home inside Shopify admin.
 *
 * On every load it makes sure the checkout pixel is switched on for this
 * store (webPixelCreate/Update), then shows a short summary from the Agent
 * Trust API and links to switch on the tracker embed and open the full
 * dashboard.
 */

type Scan = {
  orders: number;
  test_orders: number;
  revenue: number | null;
  ai_orders: number;
  ai_revenue: number | null;
  ai_revenue_share: number | null;
  ai_aov: number | null;
  other_aov: number | null;
  by_source: { source: string; orders: number; revenue: number | null }[];
} | null;

type Attention = { severity: "high" | "medium" | "info"; title: string; detail: string; page?: string };

type Summary = {
  sessions: number;
  agentSessions: number;
  agentShare: number | null;
  aiRevenue: number;
  aiRevenueShare: number | null;
  aiOrders: number;
  aiTestOrders: number;
  agentOrders: number;
  agentRevenue: number;
  agentConversion: number | null;
  humanConversion: number | null;
  needsReview: number;
  attention: Attention[];
  sources: Record<string, boolean>;
} | null;

const API = process.env.AGENT_TRUST_API_URL || "https://agent-trust-api-o7u9.onrender.com";
const DASHBOARD = process.env.AGENT_TRUST_DASHBOARD_URL || "https://agent-trust-dashboard-mwnm.onrender.com";

async function ensurePixel(admin: any, shop: string): Promise<string> {
  const settings = JSON.stringify({ shop });
  try {
    const found = await admin.graphql(`#graphql
      query { webPixel { id settings } }`);
    const json = await found.json();
    const pixel = json?.data?.webPixel;
    if (pixel?.id) {
      if (pixel.settings !== settings) {
        await admin.graphql(`#graphql
          mutation update($id: ID!, $webPixel: WebPixelInput!) {
            webPixelUpdate(id: $id, webPixel: $webPixel) { userErrors { message } }
          }`, { variables: { id: pixel.id, webPixel: { settings } } });
      }
      return "on";
    }
  } catch (e) {
    // No pixel yet: the query errors rather than returning null.
  }
  const created = await admin.graphql(`#graphql
    mutation create($webPixel: WebPixelInput!) {
      webPixelCreate(webPixel: $webPixel) { userErrors { message } webPixel { id } }
    }`, { variables: { webPixel: { settings } } });
  const json = await created.json();
  const errors = json?.data?.webPixelCreate?.userErrors || [];
  return errors.length ? `error: ${errors[0].message}` : "on";
}

async function fetchSummary(shop: string): Promise<Summary> {
  const key = process.env.METRICS_API_KEY;
  if (!key) return null;
  try {
    const r = await fetch(`${API}/metrics/overview?shop=${encodeURIComponent(shop)}&days=30`, {
      headers: { Authorization: `Bearer ${key}` },
    });
    if (!r.ok) return null;
    const d = await r.json();
    const ao = d.ai_orders ?? {};
    return {
      sessions: d.kpis?.sessions ?? 0,
      agentSessions: d.kpis?.agent_sessions ?? 0,
      agentShare: d.kpis?.agent_share ?? null,
      aiRevenue: ao.ai_revenue ?? d.kpis?.ai_influenced_revenue ?? 0,
      aiRevenueShare: ao.ai_revenue_share ?? null,
      aiOrders: ao.ai_orders ?? d.kpis?.ai_influenced_orders ?? 0,
      aiTestOrders: ao.ai_test_orders ?? 0,
      agentOrders: ao.agent_orders ?? 0,
      agentRevenue: ao.agent_revenue ?? 0,
      agentConversion: ao.agent_conversion ?? null,
      humanConversion: ao.human_conversion ?? null,
      needsReview: ao.needs_review ?? d.kpis?.flagged_orders ?? 0,
      attention: ao.attention ?? [],
      sources: Object.fromEntries(
        (d.coverage?.sources ?? []).map((x: { key: string; connected: boolean }) => [x.key, x.connected]),
      ),
    };
  } catch (e) {
    return null;
  }
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { admin, session } = await authenticate.admin(request);
  const shop = session.shop;
  const [pixel, summary, scanState] = await Promise.all([
    ensurePixel(admin, shop),
    fetchSummary(shop),
    // Past-order scan: starts in the background on first open, then shows its result.
    ensureOrderScan(shop, async () => (await unauthenticated.admin(shop)).admin.graphql),
  ]);
  let scan: Scan = null;
  if (scanState.status === "done") {
    try {
      scan = await scanSummary(shop);
    } catch {
      scan = null;
    }
  }
  const embedLink =
    `https://${shop}/admin/themes/current/editor?context=apps&template=index` +
    `&activateAppId=${process.env.SHOPIFY_API_KEY}/tracker`;
  return { shop, pixel, summary, scan, scanStatus: scanState.status, scanDays: SCAN_DAYS, embedLink, dashboard: `${DASHBOARD}/?shop=${shop}` };
};

const SOURCE_NAMES: Record<string, string> = {
  tracker: "Browser tracker",
  orders: "Order feed",
  edge: "Edge Worker",
  cloudflare: "Cloudflare analytics",
  pixel: "Checkout pixel",
};

const TONE = { high: "critical", medium: "warning", info: "info" } as const;
const LABEL = { high: "Urgent", medium: "Review", info: "Tip" } as const;

const pct = (v: number | null, digits = 1) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
const money = (v: number | null) =>
  v == null ? "—" : `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

function Kpi({ label, value, note, tone }: { label: string; value: string; note: string; tone?: "critical" }) {
  return (
    <s-box padding="base" border="base" borderRadius="base" background="base">
      <s-stack direction="block" gap="small-300">
        <s-text color="subdued">{label}</s-text>
        <s-heading>{value}</s-heading>
        <s-text color="subdued" tone={tone}>
          {note}
        </s-text>
      </s-stack>
    </s-box>
  );
}

export default function Index() {
  const { shop, pixel, summary, scan, scanStatus, scanDays, embedLink, dashboard } = useLoaderData<typeof loader>();

  return (
    <s-page heading="Agent Trust">
      <s-button slot="primary-action" href={dashboard} target="_blank">
        Open dashboard
      </s-button>

      <s-section heading={`AI in your last ${scanDays} days of orders`}>
        {scan ? (
          <s-stack direction="block" gap="base">
            {scan.ai_orders ? (
              <s-heading>
                {scan.ai_orders} order{scan.ai_orders === 1 ? "" : "s"} worth {money(scan.ai_revenue)} came from AI
                {scan.ai_revenue_share != null ? ` (${pct(scan.ai_revenue_share)} of revenue)` : ""}
              </s-heading>
            ) : (
              <s-heading>No AI orders found in your last {scanDays} days</s-heading>
            )}
            {scan.ai_aov != null && scan.other_aov != null && (
              <s-text color="subdued">
                Average AI order {money(scan.ai_aov)}, against {money(scan.other_aov)} for everything else.
              </s-text>
            )}
            {scan.by_source.length > 0 && (
              <s-stack direction="block" gap="small-300">
                {scan.by_source.map((b) => (
                  <s-stack key={b.source} direction="inline" gap="small-200">
                    <s-text type="strong">{b.source}:</s-text>
                    <s-text>
                      {b.orders} order{b.orders === 1 ? "" : "s"}, {money(b.revenue)}
                    </s-text>
                  </s-stack>
                ))}
              </s-stack>
            )}
            <s-text color="subdued">
              From {scan.orders.toLocaleString()} orders
              {scan.test_orders ? ` (${scan.test_orders} test orders not counted)` : ""}, using Shopify's own
              referral and sales-channel data. Agents that hide what they are don't show up here: the tracker
              catches those from now on.
            </s-text>
          </s-stack>
        ) : (
          <s-paragraph>
            {scanStatus === "never"
              ? "Couldn't start the scan of your past orders. Reload this page to try again."
              : `Scanning your last ${scanDays} days of orders for AI referrals and AI checkouts. Reload this page in a minute.`}
          </s-paragraph>
        )}
      </s-section>

      {summary ? (
        <>
          <s-section heading="Last 30 days">
            <s-query-container>
              <s-grid
                gridTemplateColumns="@container (inline-size > 640px) repeat(4, 1fr), repeat(2, 1fr)"
                gap="base"
              >
                <Kpi
                  label="Revenue from AI"
                  value={money(summary.aiRevenue)}
                  note={
                    (summary.aiRevenueShare != null ? `${pct(summary.aiRevenueShare)} of revenue · ` : "") +
                    `${summary.aiOrders} orders` +
                    (summary.aiTestOrders ? ` (${summary.aiTestOrders} test, not counted)` : "")
                  }
                />
                <Kpi
                  label="Agent orders"
                  value={summary.agentOrders.toLocaleString()}
                  note={`${money(summary.agentRevenue)} placed or assisted by agents`}
                />
                <Kpi
                  label="Agent conversion"
                  value={pct(summary.agentConversion, 2)}
                  note={summary.humanConversion != null ? `people convert at ${pct(summary.humanConversion, 2)}` : ""}
                />
                <Kpi
                  label="Needs review"
                  value={String(summary.needsReview)}
                  note="orders to check before fulfilling"
                  tone={summary.needsReview ? "critical" : undefined}
                />
              </s-grid>
            </s-query-container>
          </s-section>

          <s-section heading="Needs attention">
            {summary.attention.length ? (
              <s-stack direction="block" gap="base">
                {summary.attention.map((a, i) => (
                  <s-stack key={i} direction="inline" gap="small" alignItems="start">
                    <s-badge tone={TONE[a.severity]}>{LABEL[a.severity]}</s-badge>
                    <s-stack direction="block" gap="none">
                      <s-text type="strong">{a.title}</s-text>
                      <s-text color="subdued">{a.detail}</s-text>
                    </s-stack>
                  </s-stack>
                ))}
              </s-stack>
            ) : (
              <s-paragraph>Nothing needs you right now.</s-paragraph>
            )}
          </s-section>

          <s-section heading="Data sources">
            <s-stack direction="block" gap="small">
              <s-paragraph>
                {summary.sessions.toLocaleString()} sessions, {summary.agentSessions.toLocaleString()} from agents (
                {pct(summary.agentShare)}).
              </s-paragraph>
              <s-stack direction="inline" gap="small-200">
                {Object.entries(SOURCE_NAMES).map(([k, label]) => (
                  <s-badge key={k} tone={summary.sources[k] ? "success" : "neutral"}>
                    {summary.sources[k] ? "● " : "○ "}
                    {label}
                  </s-badge>
                ))}
              </s-stack>
            </s-stack>
          </s-section>
        </>
      ) : (
        <s-section heading="What AI agents are doing on your store">
          <s-paragraph>
            No data yet for {shop}. Switch on the tracker below, then browse your store or place a test order:
            results appear within a few minutes.
          </s-paragraph>
        </s-section>
      )}

      <s-section heading="Setup">
        <s-stack direction="block" gap="base">
          <s-paragraph>
            <s-text type="strong">1. Order, cart and checkout events:</s-text> on. Shopify sends them to
            Agent Trust automatically, including carts that agents build through Shopify's agent API.
          </s-paragraph>
          <s-paragraph>
            <s-text type="strong">2. Checkout pixel:</s-text> {pixel === "on" ? "on" : pixel}. Records when
            each checkout step happens (never what's typed), to tell agent checkouts from people.
          </s-paragraph>
          <s-paragraph>
            <s-text type="strong">3. Browser tracker:</s-text> switch on the Agent Trust app embed in your
            theme, then click Save.
          </s-paragraph>
          <s-button href={embedLink} target="_blank">
            Switch on the tracker
          </s-button>
          <s-paragraph>
            <s-text type="strong">Optional: edge visibility.</s-text> If your domain runs on Cloudflare,
            connect it in the dashboard's Connections page to see crawlers and agents that never run
            JavaScript.
          </s-paragraph>
        </s-stack>
      </s-section>

      <s-section slot="aside" heading="Privacy">
        <s-paragraph>
          Agent Trust records behaviour (mouse movement, clicks, typing cadence as counts, checkout step
          times), the pages and products viewed, adds to cart, and site search terms (shortened, with emails
          and long numbers removed). Never what customers type into forms, and never card details. IP
          addresses are hashed.
        </s-paragraph>
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
