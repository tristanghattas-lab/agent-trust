/**
 * Agent Trust capture snippet — v0.
 *
 * Drop-in <script> tag, DataFast-style. Generates a session key, tracks
 * whether JS actually executed (a scripted client that never runs this
 * file is itself a signal — logged server-side by its absence), and posts
 * lightweight events to the ingestion API.
 *
 * Usage:
 *   <script async src="https://<ingest-host>/tracker.js"
 *           data-api="https://<ingest-host>/ingest"></script>
 *
 * Optional attributes:
 *   data-sample="0.25"  record only this share of sessions (default 1).
 *                       Decided once per session and remembered, so a
 *                       sampled session is recorded on every page.
 *
 * Privacy: never reads what is typed, pasted or selected. Only counts and
 * timings (how many clicks, how many keystrokes, whether a field changed
 * without any keys being pressed) leave the browser.
 *
 * Deliberately does not try to do fingerprinting Cloudflare/AWS already
 * do — it captures what's only visible inside the commerce flow itself:
 * cart value, checkout timing, age-gate interaction.
 */
(function () {
  if (window.__agentTrustLoaded) return; // installed twice (theme + GTM) — run once
  window.__agentTrustLoaded = true;

  var script = document.currentScript;
  var API = (script && script.getAttribute("data-api")) || "/ingest";
  var SAMPLE = parseFloat((script && script.getAttribute("data-sample")) || "1");
  if (!(SAMPLE >= 0 && SAMPLE <= 1)) SAMPLE = 1;

  // Per-session sampling decision, remembered so a session is either
  // recorded on every page or on none (half-recorded sessions would skew
  // every funnel number).
  function inSample() {
    if (SAMPLE >= 1) return true;
    var decision = null;
    try {
      decision = localStorage.getItem("_at_sampled");
    } catch (e) {
      /* storage blocked */
    }
    if (decision === null) {
      decision = Math.random() < SAMPLE ? "1" : "0";
      try {
        localStorage.setItem("_at_sampled", decision);
      } catch (e) {
        /* ignore */
      }
    }
    return decision === "1";
  }
  if (!inSample()) return;

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
  var sessionStartAt = Date.now();

  // Automation-detection signal, not just session metadata: a real hand
  // physically moves a cursor across the screen before it clicks; a
  // script's element.click() (Playwright, browser-use, Selenium, etc.)
  // fires the click via a programmatic DOM call with no hardware pointer
  // trail leading up to it. That structural gap — a "teleport" straight
  // to the click target — survives even an agent that's been fed a
  // recorded human trajectory to replay, because the click event itself
  // is still dispatched without a preceding raw pointermove stream.
  // Mouse-only: on a touch-primary device there's no mousemove trail for
  // *anyone*, human or agent (a tap doesn't hover first), so this would
  // misfire constantly on mobile — pointerEnv gates it off there instead
  // of guessing. (This is exactly the gap the iPhone ChatGPT test would
  // have fallen into if we didn't gate it.)
  var pointerEnv = "unknown";
  if (window.matchMedia) {
    if (matchMedia("(pointer: fine)").matches) pointerEnv = "fine";
    else if (matchMedia("(pointer: coarse)").matches) pointerEnv = "coarse";
  }
  //
  // Measured against real traffic, the teleport check alone misses
  // browser-use: it dispatches one synthetic mousemove to the target
  // ~130ms before each click, landing inside TELEPORT_WINDOW_MS. What it
  // can't cheaply fake is the *trail* — a hand-driven cursor emits dozens
  // of mousemoves travelling to a target; browser-use emits exactly one.
  // So also count "sparse-trail" clicks: ≤ SPARSE_TRAIL_MAX_MOVES moves
  // since the previous click (or page load). Sent as deltas so the server
  // can sum them across every page of the session.
  var TELEPORT_WINDOW_MS = 300;
  var SPARSE_TRAIL_MAX_MOVES = 2;
  var mouseMoveCount = 0;
  var clickCount = 0;
  var teleportClickCount = 0;
  var sparseTrailClickCount = 0;
  var movesSinceLastClick = 0;
  var lastMouseMoveAt = 0;
  var sentClicks = 0;
  var sentSparseTrailClicks = 0;

  if (pointerEnv === "fine") {
    document.addEventListener(
      "mousemove",
      function () {
        mouseMoveCount++;
        movesSinceLastClick++;
        lastMouseMoveAt = Date.now();
      },
      { passive: true, capture: true }
    );
    document.addEventListener(
      "click",
      function () {
        clickCount++;
        if (Date.now() - lastMouseMoveAt > TELEPORT_WINDOW_MS) {
          teleportClickCount++;
        }
        if (movesSinceLastClick <= SPARSE_TRAIL_MAX_MOVES) {
          sparseTrailClickCount++;
        }
        movesSinceLastClick = 0;
      },
      { passive: true, capture: true }
    );
  }

  // Timing/rhythm only, same as everything else this file sends — never
  // what was typed, read, or clicked on, just that movement happened.
  function behavioralMetrics() {
    if (pointerEnv !== "fine") {
      return { pointer_env: pointerEnv };
    }
    var ageSeconds = Math.max(1, (Date.now() - sessionStartAt) / 1000);
    var clicksDelta = clickCount - sentClicks;
    var sparseDelta = sparseTrailClickCount - sentSparseTrailClicks;
    sentClicks = clickCount;
    sentSparseTrailClicks = sparseTrailClickCount;
    return {
      pointer_env: pointerEnv,
      mouse_event_rate: +(mouseMoveCount / ageSeconds).toFixed(3),
      teleport_click_ratio:
        clickCount > 0 ? +(teleportClickCount / clickCount).toFixed(3) : null,
      clicks_delta: clicksDelta,
      sparse_trail_clicks_delta: sparseDelta,
    };
  }

  // Browser-automation fingerprints. Each is a property a normal browser
  // doesn't have but a driven one leaks unless the operator patches it out.
  // Cheap to fake for a careful operator, so these are corroborating
  // signals; the behavioural ones above are harder to fake.
  function automationTells() {
    var tells = [];
    try {
      if (navigator.webdriver === true) tells.push("webdriver");
      if (/HeadlessChrome/.test(navigator.userAgent)) tells.push("headless_ua");
      if (navigator.languages && navigator.languages.length === 0) tells.push("no_languages");
      if (window.outerWidth === 0 && window.outerHeight === 0) tells.push("zero_outer_window");
      if (window.__playwright__binding__ || window.__pwInitScripts) tells.push("playwright_globals");
      if (window._phantom || window.callPhantom) tells.push("phantom_globals");
      for (var k in window) {
        if (/^\$?cdc_|^\$wdc_/.test(k)) {
          tells.push("chromedriver_globals");
          break;
        }
      }
    } catch (e) {
      /* a hostile or locked-down environment — report what we have */
    }
    return tells.join(",");
  }

  // Typing cadence, counts only. A person changes a text field by pressing
  // keys (or pasting); a script often sets the value and fires an input
  // event with no key pressed at all. Autofill also changes fields without
  // keys, so this is only ever scored alongside other signals.
  var keydownCount = 0;
  var inputCount = 0;
  var keylessInputCount = 0;
  var lastKeydownAt = 0;
  var sentInputs = 0;
  var sentKeylessInputs = 0;
  var sentKeydowns = 0;
  document.addEventListener(
    "keydown",
    function () {
      keydownCount++;
      lastKeydownAt = Date.now();
    },
    { passive: true, capture: true }
  );
  document.addEventListener(
    "input",
    function (e) {
      var t = e.target;
      if (!t || !(t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      if (t.type === "checkbox" || t.type === "radio" || t.type === "range") return;
      inputCount++;
      var type = e.inputType || "";
      var pasted = type.indexOf("Paste") !== -1 || type.indexOf("Drop") !== -1;
      if (!pasted && Date.now() - lastKeydownAt > 200) keylessInputCount++;
    },
    { passive: true, capture: true }
  );

  function inputMetrics() {
    var out = {
      inputs_delta: inputCount - sentInputs,
      keyless_inputs_delta: keylessInputCount - sentKeylessInputs,
      keydowns_delta: keydownCount - sentKeydowns,
    };
    sentInputs = inputCount;
    sentKeylessInputs = keylessInputCount;
    sentKeydowns = keydownCount;
    return out;
  }

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
          shop: (window.Shopify && window.Shopify.shop) || location.hostname,
          user_agent: navigator.userAgent,
          referrer: document.referrer,
          landing_path: location.pathname,
          js_executed: true,
        },
        behavioralMetrics(),
        inputMetrics(),
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

  // Basic pageview on load, with the automation fingerprint (checked once
  // per page; the server keeps the union across pages).
  post({ event_count: 1, automation_tells: automationTells() });

  // Flush this page's behavioural metrics when the visitor leaves it.
  // Without this they only ever went out with the load-time pageview —
  // before any mouse movement or click could happen — so every page's
  // actual interaction was lost on navigation. event_count 0: a metrics
  // update, not a new event. Skipped when nothing moved or clicked since
  // the last send, so pagehide + visibilitychange don't double-post.
  var flushedMoves = 0;
  var flushedClicks = 0;
  function flushMetrics() {
    var inputsChanged = inputCount !== sentInputs || keydownCount !== sentKeydowns;
    var pointerChanged =
      pointerEnv === "fine" && (mouseMoveCount !== flushedMoves || clickCount !== flushedClicks);
    if (!inputsChanged && !pointerChanged) return;
    flushedMoves = mouseMoveCount;
    flushedClicks = clickCount;
    post({ event_count: 0 });
  }
  window.addEventListener("pagehide", flushMetrics);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") flushMetrics();
  });

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

  // Report the cart total. /cart/change.js-style responses carry the whole
  // cart (total_price); Dawn's /cart/add returns only the added line item,
  // so fall back to one /cart.js read for the real total.
  function reportCart(data) {
    if (data && typeof data.total_price === "number") {
      window.AgentTrust.cartUpdated(data.total_price / 100);
      return;
    }
    originalFetch("/cart.js", { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (cart) {
        window.AgentTrust.cartUpdated(
          cart && typeof cart.total_price === "number" ? cart.total_price / 100 : undefined
        );
      })
      .catch(function () {
        window.AgentTrust.cartUpdated();
      });
  }

  // Shopify-only: auto-detect the theme's own AJAX cart calls
  // (/cart/add, /cart/change, /cart/update, /cart/clear — with or without
  // .js, and under a locale prefix like /en-au/cart/add) and treat a
  // successful one as a cartUpdated(). The .js was optional all along:
  // Dawn posts to routes.cart_add_url, which is plain "/cart/add", so the
  // old .js-only pattern never matched a single add-to-cart on this store.
  // Sites that don't use fetch for their cart (rare) still work via the
  // explicit window.AgentTrust.cartUpdated() call documented above.
  if (window.Shopify && originalFetch) {
    window.fetch = function (input, init) {
      var url =
        typeof input === "string"
          ? input
          : (input && (input.url || input.href)) || "";
      var isCartCall = /\/cart\/(add|change|update|clear)(\.js)?(\?|#|$)/.test(url);
      var result = originalFetch(input, init);
      if (isCartCall) {
        result
          .then(function (response) {
            if (!response || !response.ok) return;
            try {
              response
                .clone()
                .json()
                .then(reportCart)
                .catch(function () {
                  reportCart(null);
                });
            } catch (e) {
              reportCart(null);
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
