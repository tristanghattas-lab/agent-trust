/**
 * Agent Trust capture snippet — v0.
 *
 * Drop-in <script> tag, DataFast-style. Generates a session key, tracks
 * whether JS actually executed (a scripted client that never runs this
 * file is itself a signal — logged server-side by its absence), and posts
 * lightweight events to the ingestion API.
 *
 * Usage:
 *   <script src="https://<ingest-host>/tracker.js"
 *           data-api="https://<ingest-host>/ingest"></script>
 *
 * Deliberately does not try to do fingerprinting Cloudflare/AWS already
 * do — it captures what's only visible inside the commerce flow itself:
 * cart value, checkout timing, age-gate interaction.
 */
(function () {
  var script = document.currentScript;
  var API = (script && script.getAttribute("data-api")) || "/ingest";

  function getSessionKey() {
    var key = "";
    try {
      key = localStorage.getItem("_at_session");
    } catch (e) {
      /* private mode / blocked storage — fall through to a per-load key */
    }
    if (!key) {
      key =
        "s_" +
        Date.now().toString(36) +
        "_" +
        Math.random().toString(36).slice(2, 10);
      try {
        localStorage.setItem("_at_session", key);
      } catch (e) {
        /* ignore */
      }
    }
    return key;
  }

  var sessionKey = getSessionKey();

  // Captured before anything below patches window.fetch, so our own
  // outgoing calls (here and in the auto-detect patch further down) never
  // re-trigger the patch and loop.
  var originalFetch = window.fetch ? window.fetch.bind(window) : null;

  // Shopify-only: write the session key onto the cart itself, via Shopify's
  // own AJAX Cart API. This is what lets the order webhook (server-side,
  // never sees this script) match a completed order back to the session
  // that produced it — the webhook reads this same attribute name back out
  // of the order payload (see app/shopify_webhooks.py, SESSION_ATTRIBUTE_NAME).
  // Same-origin relative to the storefront page, so no CORS setup needed.
  // Silently a no-op on any site that isn't Shopify (the fetch 404s and is
  // ignored) — tracker.js is also used on non-Shopify site types.
  function syncShopifyCartAttribute() {
    if (!window.Shopify || !originalFetch) return;
    try {
      originalFetch("/cart/update.js", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          attributes: { agent_trust_session: sessionKey },
        }),
        keepalive: true,
      }).catch(function () {
        /* no cart yet, or not actually Shopify — ignore either way */
      });
    } catch (e) {
      /* never let tracking break the page */
    }
  }

  function post(payload) {
    var body = JSON.stringify(
      Object.assign(
        {
          session_key: sessionKey,
          user_agent: navigator.userAgent,
          referrer: document.referrer,
          landing_path: location.pathname,
          js_executed: true,
        },
        payload
      )
    );
    // Not sendBeacon: it always sends credentials, and a credentialed
    // cross-origin JSON POST needs Access-Control-Allow-Credentials on the
    // preflight — without it the browser silently drops the POST after a
    // 200 OPTIONS. The API doesn't use cookies, so omit them; keepalive
    // gives the same survive-page-unload behaviour sendBeacon did.
    if (!originalFetch) return;
    try {
      originalFetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: true,
        credentials: "omit",
      }).catch(function () {
        /* never let tracking break the page */
      });
    } catch (e) {
      /* never let tracking break the page */
    }
  }

  // Basic pageview on load.
  post({ event_count: 1 });

  // A cart may already exist from an earlier page view (Shopify carts
  // persist across the visit), so try to tag it immediately too — not
  // just on the next cartUpdated() call.
  syncShopifyCartAttribute();

  // Public API the site's own checkout/age-gate code calls directly —
  // this is the part generic bot-detection tools can't see, because it
  // requires knowing what "checkout" and "age gate" mean on this site.
  window.AgentTrust = {
    cartUpdated: function (value) {
      post({ event_count: 1, cart_value: value });
      syncShopifyCartAttribute();
    },
    checkoutStarted: function () {
      post({ event_count: 1, checkout_started: true });
    },
    checkoutCompleted: function () {
      post({ event_count: 1, checkout_completed: true });
    },
    ageGateShown: function () {
      post({ event_count: 1, age_gate_shown: true });
    },
    ageGateResult: function (passed) {
      post({ event_count: 1, age_gate_shown: true, age_gate_passed: !!passed });
    },
  };

  // Shopify-only: auto-detect the theme's own AJAX cart calls
  // (/cart/add.js, /cart/change.js, /cart/update.js, /cart/clear.js) and
  // treat a successful one as a cartUpdated() — covers Dawn and most
  // modern themes with zero theme-code edits beyond the <script> tag.
  // Sites that don't use fetch for their cart (rare) still work via the
  // explicit window.AgentTrust.cartUpdated() call documented above.
  if (window.Shopify && originalFetch) {
    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var isCartCall = /\/cart\/(add|change|update|clear)\.js(\?|$)/.test(url);
      var result = originalFetch(input, init);
      if (isCartCall) {
        result
          .then(function (response) {
            if (!response || !response.ok) return;
            try {
              response
                .clone()
                .json()
                .then(function (data) {
                  var val =
                    data && typeof data.total_price === "number"
                      ? data.total_price / 100
                      : undefined;
                  window.AgentTrust.cartUpdated(val);
                })
                .catch(function () {
                  window.AgentTrust.cartUpdated();
                });
            } catch (e) {
              window.AgentTrust.cartUpdated();
            }
          })
          .catch(function () {
            /* the cart request itself failed — nothing to report */
          });
      }
      return result;
    };
  }
})();
