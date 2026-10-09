import type { LoaderFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import { metricsView } from "../agent-trust.server";

/** Resource route polled by the home page's live strip. */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { session } = await authenticate.admin(request);
  return (await metricsView("live", session.shop, { minutes: 30 })) ?? { visitors: 0, agents: 0, people: 0, feed: [] };
};
