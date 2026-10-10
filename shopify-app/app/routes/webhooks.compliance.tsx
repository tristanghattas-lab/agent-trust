import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";
import { apiCall } from "../agent-trust.server";

/**
 * Shopify's mandatory privacy webhooks (every App Store app must handle them):
 *   customers/data_request  -> what we hold about a customer (sent to the API, logged)
 *   customers/redact        -> remove a customer's email and order notes
 *   shop/redact             -> 48h after uninstall: delete everything for the store
 * authenticate.webhook verifies Shopify's signature and rejects anything else.
 */
export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, payload } = await authenticate.webhook(request);
  const t = String(topic).toLowerCase().replace(/_/g, "/").replace("data/request", "data_request");
  const body = JSON.stringify({ shop, ...(payload as object) });
  try {
    if (t.startsWith("customers/data")) await apiCall("/privacy/customers-data-request", { method: "POST", body });
    else if (t.startsWith("customers/redact")) await apiCall("/privacy/customers-redact", { method: "POST", body });
    else if (t.startsWith("shop/redact")) {
      await apiCall("/privacy/shop-redact", { method: "POST", body });
      await db.session.deleteMany({ where: { shop } });
    }
  } catch (e) {
    console.error("Compliance webhook failed", topic, shop, e);
    return new Response(null, { status: 500 }); // Shopify retries
  }
  return new Response();
};
