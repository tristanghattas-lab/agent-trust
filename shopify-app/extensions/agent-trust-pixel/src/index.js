/**
 * Agent Trust checkout pixel.
 *
 * Shopify's checkout doesn't run theme scripts, so the tracker goes blind at
 * checkout. This pixel runs inside checkout and records WHEN each step
 * happened (contact, address, shipping, payment, completed), joined to the
 * tracker's session through its first-party _at_sid cookie.
 *
 * Step timing separates an agent filling checkout in a couple of seconds
 * from a person typing, and shows whether a person took over to pay (the
 * hand-off pattern). Only step names, times and the checkout/order ids are
 * sent: never names, addresses, emails or card details.
 */
import { register } from "@shopify/web-pixels-extension";

const INGEST = "https://agent-trust-api-o7u9.onrender.com/pixel/events";
const STEPS = [
  "checkout_started",
  "checkout_contact_info_submitted",
  "checkout_address_info_submitted",
  "checkout_shipping_info_submitted",
  "payment_info_submitted",
  "checkout_completed",
];

register(({ analytics, browser, settings }) => {
  STEPS.forEach((name) => {
    analytics.subscribe(name, async (event) => {
      try {
        const checkout = (event.data && event.data.checkout) || {};
        let sessionKey = null;
        try {
          sessionKey = (await browser.cookie.get("_at_sid")) || null;
        } catch (e) {
          sessionKey = null;
        }
        const body = JSON.stringify({
          shop: settings.shop,
          event: name,
          ts: event.timestamp,
          session_key: sessionKey,
          checkout_token: checkout.token || null,
          order_id: (checkout.order && checkout.order.id) || null,
        });
        // text/plain keeps this a simple request: no CORS preflight needed.
        fetch(INGEST, { method: "POST", body, keepalive: true,
                        headers: { "content-type": "text/plain" } });
      } catch (e) {
        /* never affect checkout */
      }
    });
  });
});
