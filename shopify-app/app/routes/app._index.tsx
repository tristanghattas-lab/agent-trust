import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { authenticate } from "../shopify.server";

/**
 * Agent Trust home inside Shopify admin.
 *
 * On every load it makes sure the checkout pixel is switched on for this
 * store (webPixelCreate/Update), then shows a short summary from the Agent
 * Trust API and links to switch on the tracker embed and open the full
 * dashboard.
 */

type Summary = {
  sessions: number;
  agentSessions: number;
  agentShare: number | null;
  agentOrders: number;
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
    return {
      sessions: d.kpis?.sessions ?? 0,
      agentSessions: d.kpis?.agent_sessions ?? 0,
      agentShare: d.kpis?.agent_share ?? null,
      agentOrders: d.kpis?.ai_influenced_orders ?? 0,
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
  const [pixel, summary] = await Promise.all([ensurePixel(admin, shop), fetchSummary(shop)]);
  const embedLink =
    `https://${shop}/admin/themes/current/editor?context=apps&template=index` +
    `&activateAppId=${process.env.SHOPIFY_API_KEY}/tracker`;
  return { shop, pixel, summary, embedLink, dashboard: `${DASHBOARD}/?shop=${shop}` };
};

const SOURCE_NAMES: Record<string, string> = {
  tracker: "Browser tracker",
  orders: "Order feed",
  edge: "Edge Worker",
  cloudflare: "Cloudflare analytics",
  pixel: "Checkout pixel",
};

export default function Index() {
  const { shop, pixel, summary, embedLink, dashboard } = useLoaderData<typeof loader>();
  const pct = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

  return (
    <s-page heading="Agent Trust">
      <s-button slot="primary-action" href={dashboard} target="_blank">
        Open dashboard
      </s-button>

      <s-section heading="What AI agents are doing on your store">
        {summary ? (
          <s-stack direction="block" gap="base">
            <s-paragraph>
              Last 30 days: <s-text type="strong">{summary.sessions.toLocaleString()}</s-text> sessions,{" "}
              <s-text type="strong">{summary.agentSessions.toLocaleString()}</s-text> from agents (
              {pct(summary.agentShare)}). AI-influenced orders:{" "}
              <s-text type="strong">{summary.agentOrders}</s-text>.
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
        ) : (
          <s-paragraph>
            No data yet for {shop}. Switch on the tracker below, then browse your store or place a test
            order: results appear within a few minutes.
          </s-paragraph>
        )}
      </s-section>

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
          times), never what customers type, and never card details. IP addresses are hashed.
        </s-paragraph>
      </s-section>
    </s-page>
  );
}

export const headers: HeadersFunction = (headersArgs) => boundary.headers(headersArgs);
