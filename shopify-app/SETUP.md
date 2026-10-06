# Installing the Agent Trust Shopify app

What the app does once installed on a store:

- **Order, cart and checkout events** go straight from Shopify to the Agent
  Trust API, including carts that agents build through Shopify's agent API
  (which never touch the storefront or Cloudflare).
- **Checkout pixel**: records when each checkout step happens (never what's
  typed), joined to the tracker session, to tell agent checkouts from people
  and show hand-offs.
- **Tracker as an app embed**: switched on in the theme editor, no theme code.
- **Admin page** inside Shopify with a summary and setup links.

It replaces the manual setup: the `<script>` tag in `theme.liquid` and the
order notification webhook with its per-store secret.

## 1. Render (creates the app's server)

1. dashboard.render.com → **Blueprints** → agent-trust → **Sync**. This creates
   the `agent-trust-shopify` service. Its first deploy will run but can't
   authenticate yet; that's expected.
2. Copy the new service's URL (like `https://agent-trust-shopify-xxxx.onrender.com`)
   and send it to Claude, who puts it in `shopify.app.toml`.

## 2. Your laptop (registers the app with Shopify, about 10 minutes)

Needs Node.js 22.15 or newer (`node -v`; install the LTS from nodejs.org).

```bash
# Get the code (or GitHub → Code → Download ZIP, then unzip)
git clone https://github.com/tristanghattas-lab/agent-trust.git
cd agent-trust/shopify-app
npm install

# Log in to your Partner account in the browser window that opens,
# choose "Create a new app", name it "Agent Trust".
npx shopify app config link

# Push the app config, webhooks, tracker embed and checkout pixel to Shopify.
# Answer yes to release a new version.
npx shopify app deploy
```

## 3. Keys (Partner dashboard → Render)

1. partners.shopify.com → **Apps** → Agent Trust → **Client credentials**.
2. In Render → agent-trust-shopify → **Environment**, set:
   - `SHOPIFY_API_KEY` = Client ID
   - `SHOPIFY_API_SECRET` = Client secret
3. Save, then **Manual Deploy** agent-trust-shopify, then agent-trust-api
   (the API uses the same secret to check webhook signatures).
4. Also set `SHOPIFY_API_SECRET` (same Client secret) on **agent-trust-api**.

## 4. Allow order data

Partner dashboard → Agent Trust → **API access** → **Protected customer data
access** → request access to order data. Reason: detecting AI-agent orders and
keeping evidence for disputes. Order webhooks need this.

## 5. Install on the store

1. Partner dashboard → Agent Trust → **Distribution** → **Custom distribution**
   → store `73ee52.myshopify.com` → generate the install link.
2. Open the link, install, approve the permissions.
3. In Shopify admin → Apps → **Agent Trust** → **Switch on the tracker** →
   toggle **Agent Trust tracker** on in the theme editor → **Save**.

## 6. Remove the manual setup (avoids double counting)

- Online Store → Themes → Edit code → `theme.liquid`: delete the
  `agent-trust-api…/tracker.js` script tag.
- Settings → Notifications → Webhooks: delete the manual Order creation webhook.
  (Duplicate orders are ignored anyway; this just keeps things tidy.)

## Check it works

Place a test order with the test card. In the dashboard's **Run report**:
the order, its checkout steps (pixel) and the cart/checkout events should
appear, and the **Checkout pixel** source turns ●.
