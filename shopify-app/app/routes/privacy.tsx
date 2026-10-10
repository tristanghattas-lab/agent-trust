import type { MetaFunction } from "react-router";

/** Public privacy policy (App Store listing links here). No login. */
export const meta: MetaFunction = () => [{ title: "Agent Trust: privacy policy" }];

export const loader = () => ({
  email: process.env.SUPPORT_EMAIL || "support@example.com",
  updated: "10 October 2026",
});

import { useLoaderData } from "react-router";

const wrap = { maxWidth: 720, margin: "40px auto", padding: "0 16px", font: "16px/1.6 system-ui, sans-serif", color: "#222" };

export default function Privacy() {
  const { email, updated } = useLoaderData<typeof loader>();
  return (
    <main style={wrap}>
      <h1>Agent Trust privacy policy</h1>
      <p><em>Last updated {updated}</em></p>
      <p>
        Agent Trust is a Shopify app that shows merchants how AI shopping agents and assistants find, browse and buy
        from their store. This policy explains what the app collects when a merchant installs it, how it's used, and
        how it's deleted.
      </p>

      <h2>What we collect</h2>
      <ul>
        <li><strong>Store details:</strong> the store's domain, plan, and the access Shopify grants when the app is
          installed.</li>
        <li><strong>Orders:</strong> order ID, time, totals, line items, sales channel and Shopify's customer-journey
          summary (how the visitor arrived), plus the customer's email address and order note, used to link an order to
          the visit that produced it and to flag risky agent orders.</li>
        <li><strong>Carts and checkouts:</strong> cart and checkout events Shopify sends for the store, including
          carts built by AI agents through Shopify's agent APIs.</li>
        <li><strong>Store visits:</strong> if the merchant turns on the storefront script or checkout pixel, the pages
          viewed, searches (with emails, phone numbers and long numbers removed), add-to-cart events, browser user agent,
          IP address and interaction timing signals (such as scroll and pointer activity) used to tell agents from
          people. Visits are keyed to a random session ID, not to a named person.</li>
        <li><strong>Catalogue:</strong> public product and collection data, read to test whether AI agents can find
          the store's products and to suggest fixes.</li>
      </ul>

      <h2>How we use it</h2>
      <p>
        Only to provide the app to the merchant who installed it: classifying visits and orders by origin, reporting
        AI-driven revenue and missed sales, tagging orders, and suggesting catalogue fixes. Product changes are only
        made when the merchant approves them. We don't sell data, use it for advertising, or share one store's data with
        another store.
      </p>

      <h2>Who processes it</h2>
      <p>
        Data is stored and processed on Render (render.com), our hosting provider. If a merchant connects Cloudflare,
        traffic summaries are read from their Cloudflare account using a token they provide, stored encrypted.
      </p>

      <h2>Retention and deletion</h2>
      <ul>
        <li>When a merchant uninstalls the app, Shopify sends a shop deletion request 48 hours later and we delete all
          of that store's data.</li>
        <li>When a store's customer asks for their data to be deleted, Shopify forwards the request and we remove that
          customer's email address and order details from our records.</li>
        <li>Customer data requests are answered through the merchant, as Shopify requires.</li>
      </ul>

      <h2>Security</h2>
      <p>
        Data is encrypted in transit. Access is limited to the app's servers using separate, rotatable keys, with rate
        limits and audit logging. Merchants only ever see their own store's data.
      </p>

      <h2>Contact</h2>
      <p>Questions or requests: <a href={`mailto:${email}`}>{email}</a>.</p>
    </main>
  );
}
