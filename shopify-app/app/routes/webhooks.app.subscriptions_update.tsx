import type { ActionFunctionArgs } from "react-router";
import { authenticate, BILLING_ON, BILLING_TEST } from "../shopify.server";
import { syncBilling } from "../agent-trust.server";

/**
 * app_subscriptions/update: a plan was approved, cancelled, declined, frozen
 * (unpaid) or expired. Re-read the store's active subscriptions rather than
 * trusting this one event, because an upgrade sends the new plan's ACTIVE
 * and the old plan's CANCELLED in either order.
 */
export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, admin } = await authenticate.webhook(request);
  if (BILLING_ON && admin) await syncBilling(admin.graphql, shop, BILLING_TEST);
  return new Response();
};
