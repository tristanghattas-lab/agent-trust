import "@shopify/shopify-app-react-router/adapters/node";
import {
  ApiVersion,
  AppDistribution,
  BillingInterval,
  BillingReplacementBehavior,
  shopifyApp,
} from "@shopify/shopify-app-react-router/server";
import { PrismaSessionStorage } from "@shopify/shopify-app-session-storage-prisma";
import prisma from "./db.server";

// Paid plans, charged on the merchant's Shopify invoice. Only works on a
// public (App Store) app: custom-distribution apps can't use the Billing API,
// so it's off unless BILLING=shopify. BILLING_TEST stays on (no real charges)
// until it's set to "false" for launch.
export const BILLING_ON = process.env.BILLING === "shopify";
export const BILLING_TEST = process.env.BILLING_TEST !== "false";
export const PAID_PLANS = { growth: "Growth", trust: "Trust" } as const;

const billing = {
  [PAID_PLANS.growth]: {
    lineItems: [{ amount: 49, currencyCode: "USD", interval: BillingInterval.Every30Days as const }],
    replacementBehavior: BillingReplacementBehavior.ApplyImmediately,
  },
  [PAID_PLANS.trust]: {
    lineItems: [{ amount: 149, currencyCode: "USD", interval: BillingInterval.Every30Days as const }],
    replacementBehavior: BillingReplacementBehavior.ApplyImmediately,
  },
};

const shopify = shopifyApp({
  billing,
  apiKey: process.env.SHOPIFY_API_KEY,
  apiSecretKey: process.env.SHOPIFY_API_SECRET || "",
  apiVersion: ApiVersion.October25,
  scopes: process.env.SCOPES?.split(","),
  // Render sets RENDER_EXTERNAL_URL to the service's own URL.
  appUrl: process.env.SHOPIFY_APP_URL || process.env.RENDER_EXTERNAL_URL || "",
  authPathPrefix: "/auth",
  sessionStorage: new PrismaSessionStorage(prisma),
  distribution: AppDistribution.AppStore,
  future: {
    expiringOfflineAccessTokens: true,
  },
  ...(process.env.SHOP_CUSTOM_DOMAIN
    ? { customShopDomains: [process.env.SHOP_CUSTOM_DOMAIN] }
    : {}),
});

export default shopify;
export const apiVersion = ApiVersion.October25;
export const addDocumentResponseHeaders = shopify.addDocumentResponseHeaders;
export const authenticate = shopify.authenticate;
export const unauthenticated = shopify.unauthenticated;
export const login = shopify.login;
export const registerWebhooks = shopify.registerWebhooks;
export const sessionStorage = shopify.sessionStorage;
