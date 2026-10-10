import type { MetaFunction } from "react-router";
import { useLoaderData } from "react-router";

/** Public support page (App Store listing links here). No login. */
export const meta: MetaFunction = () => [{ title: "Agent Trust: support" }];

export const loader = () => ({ email: process.env.SUPPORT_EMAIL || "support@example.com" });

const wrap = { maxWidth: 720, margin: "40px auto", padding: "0 16px", font: "16px/1.6 system-ui, sans-serif", color: "#222" };

export default function Support() {
  const { email } = useLoaderData<typeof loader>();
  return (
    <main style={wrap}>
      <h1>Agent Trust support</h1>
      <p>Email <a href={`mailto:${email}`}>{email}</a> with your store's myshopify.com address. We reply within one
        business day (Sydney time).</p>
      <h2>Common questions</h2>
      <p><strong>Why is my shelf score low?</strong> It means AI shopping agents searching your store for common
        requests didn't get your matching products back. Open <em>Fixes</em> in the app to see why and apply fixes.</p>
      <p><strong>Does the app change my products?</strong> Only when you approve a fix in the app.</p>
      <p><strong>How do I cancel?</strong> Choose <em>Free</em> on the Plans page, or uninstall the app. Paid plans
        are billed through Shopify and stop at once.</p>
      <p><a href="/privacy">Privacy policy</a></p>
    </main>
  );
}
