import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { Outlet, useLoaderData, useRouteError } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { AppProvider } from "@shopify/shopify-app-react-router/react";

import { authenticate, BILLING_ON, BILLING_TEST } from "../shopify.server";
import { syncBilling } from "../agent-trust.server";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { admin, session } = await authenticate.admin(request);
  // Runs on each full load of the app, including the return from Shopify's
  // billing approval page, so a new plan shows straight away.
  if (BILLING_ON) await syncBilling(admin.graphql, session.shop, BILLING_TEST);

  // eslint-disable-next-line no-undef
  return { apiKey: process.env.SHOPIFY_API_KEY || "" };
};

export default function App() {
  const { apiKey } = useLoaderData<typeof loader>();

  return (
    <AppProvider apiKey={apiKey}>
      <s-app-nav>
        <s-link href="/app">Home</s-link>
        <s-link href="/app/shelf">Shelf test</s-link>
        <s-link href="/app/readiness">Readiness</s-link>
        <s-link href="/app/fixes">Fixes</s-link>
        <s-link href="/app/orders">Orders</s-link>
        <s-link href="/app/behaviour">Behaviour</s-link>
        <s-link href="/app/products">Products</s-link>
        <s-link href="/app/plans">Plans</s-link>
      </s-app-nav>
      <Outlet />
    </AppProvider>
  );
}

// Shopify needs React Router to catch some thrown responses, so that their headers are included in the response.
export function ErrorBoundary() {
  return boundary.error(useRouteError());
}

export const headers: HeadersFunction = (headersArgs) => {
  return boundary.headers(headersArgs);
};
