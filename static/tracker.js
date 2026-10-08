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
 * Privacy: never reads what is typed, pasted or selected into forms. Only
 * counts and timings (how many clicks, how many keystrokes, whether a field
 * changed without any keys being pressed) leave the browser, plus the
 * journey: pages and products viewed, adds to cart, and site search terms
 * from the search URL (shortened; emails and long numbers redacted).
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

  // A session ends after 30 minutes without activity (the usual analytics
  // rule), so a later visit from the same browser starts a new session
  // instead of extending an old one.
  var SESSION_IDLE_MS = 30 * 60 * 1000;

  function getSessionKey() {
    var key = "";
    var now = Date.now();
    try {
      key = localStorage.getItem("_at_session");
      var seen = parseInt(localStorage.getItem("_at_seen") || "0", 10);
      if (key && (!seen || now - seen > SESSION_IDLE_MS)) key = "";
    } catch (e) {
      /* private mode / blocked storage — fall through to a per-load key */
    }
    if (!key) {
      key =
        "s_" +
        now.toString(36) +
        "_" +
        Math.random().toString(36).slice(2, 10);
      try {
        localStorage.setItem("_at_session", key);
      } catch (e) {
        /* ignore */
      }
    }
    touchSession();
    return key;
  }

  function touchSession() {
    try {
      localStorage.setItem("_at_seen", String(Date.now()));
    } catch (e) {
      /* ignore */
    }
  }

  var sessionKey = getSessionKey();
  // Same random key as a first-party cookie, so the store's edge Worker can
  // attach request-level evidence (Web Bot Auth signatures, Cloudflare's
  // verified-bot category) to this browser session. No personal data.
  try {
    document.cookie = "_at_sid=" + sessionKey + "; path=/; max-age=1800; SameSite=Lax; Secure";
  } catch (e) {
    /* ignore */
  }
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
  // The cart's token, sent once per session per cart, so Shopify's cart and
  // checkout webhooks (which don't carry cart attributes) join to this
  // session. The token is a random cart id, not personal data.
  function linkCart(cart) {
    if (!cart || !cart.token || typeof journeyEvent !== "function") return;
    var tok = String(cart.token).split("?")[0].slice(0, 100);
    try {
      if (localStorage.getItem("_at_cart") === sessionKey + ":" + tok) return;
      localStorage.setItem("_at_cart", sessionKey + ":" + tok);
    } catch (e) {}
    journeyEvent("cart_link", { cart_token: tok });
  }

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
      })
        .then(function (r) {
          return r.ok ? r.json() : null;
        })
        .then(linkCart)
        .catch(function () {
          /* no cart yet, or not actually Shopify — ignore either way */
        });
    } catch (e) {
      /* never let tracking break the page */
    }
  }

  // Path plus attribution tags only (utm_*, ref): enough to tell an AI
  // referral (ChatGPT adds utm_source=chatgpt.com) without collecting other
  // query parameters, which can hold emails or search terms.
  function landingPath() {
    try {
      var keep = [];
      new URLSearchParams(location.search).forEach(function (v, k) {
        if (/^utm_/i.test(k) || k === "ref") keep.push(encodeURIComponent(k) + "=" + encodeURIComponent(v.slice(0, 100)));
      });
      return location.pathname + (keep.length ? "?" + keep.slice(0, 6).join("&") : "");
    } catch (e) {
      return location.pathname;
    }
  }

  function post(payload) {
    touchSession();
    var body = JSON.stringify(
      Object.assign(
        {
          session_key: sessionKey,
          shop: (window.Shopify && window.Shopify.shop) || location.hostname,
          user_agent: navigator.userAgent,
          referrer: document.referrer,
          landing_path: landingPath(),
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


  // ---------------------------------------------------------------------
  // Journey: what the visitor did, step by step. Page views (page type and
  // product), searches, adds to cart, dead ends (404, out of stock) and,
  // when leaving a page, time on page, scroll depth and time the tab was
  // hidden. Batched to /journey as text/plain (no CORS preflight). Product
  // and page data only; search text is shortened and redacted if it looks
  // like an email or a long number; nothing typed into forms is sent.
  // ---------------------------------------------------------------------
  var JOURNEY_API = API.replace(/\/ingest\/?$/, "/journey");
  var SHOP = (window.Shopify && window.Shopify.shop) || location.hostname;
  var jQueue = [];
  var jTimer = null;

  function nextSeq() {
    var n = 0;
    try {
      var raw = localStorage.getItem("_at_seq") || "";
      var parts = raw.split(":");
      if (parts[0] === sessionKey) n = parseInt(parts[1] || "0", 10) || 0;
      localStorage.setItem("_at_seq", sessionKey + ":" + (n + 1));
    } catch (e) {
      /* storage blocked: seq stays 0, timestamps still order events */
    }
    return n + 1;
  }

  function flushJourney() {
    clearTimeout(jTimer);
    jTimer = null;
    if (!jQueue.length || !originalFetch) return;
    var body = JSON.stringify({ shop: SHOP, session_key: sessionKey, events: jQueue.splice(0, 60) });
    try {
      originalFetch(JOURNEY_API, {
        method: "POST",
        headers: { "Content-Type": "text/plain" },
        body: body,
        keepalive: true,
        credentials: "omit",
        mode: "no-cors",
      }).catch(function () {});
    } catch (e) {
      /* never let tracking break the page */
    }
  }

  function journeyEvent(kind, data) {
    var e = { kind: kind, ts: Date.now(), seq: nextSeq(), path: landingPath() };
    for (var k in data || {}) if (data[k] !== undefined && data[k] !== null) e[k] = data[k];
    jQueue.push(e);
    if (jQueue.length >= 20) flushJourney();
    else if (!jTimer) jTimer = setTimeout(flushJourney, 3000);
  }

  function cleanQuery(q) {
    q = String(q || "").trim().slice(0, 80);
    if (!q) return null;
    return /@|\d{6,}/.test(q) ? "[redacted]" : q;
  }

  var meta = (window.ShopifyAnalytics && window.ShopifyAnalytics.meta) || {};
  function guessPageType() {
    var p = location.pathname;
    if (/\/products\//.test(p)) return "product";
    if (/\/collections\//.test(p)) return "collection";
    if (/\/search/.test(p)) return "searchresults";
    if (/\/cart\/?$/.test(p)) return "cart";
    if (/\/(pages|blogs|policies)\//.test(p)) return "page";
    if (/^\/([a-z]{2}(-[a-z]{2})?\/?)?$/i.test(p)) return "home";
    return "other";
  }
  var pageType = (meta.page && meta.page.pageType) || guessPageType();
  var handleMatch = location.pathname.match(/\/products\/([^\/?#]+)/);
  var productHandle = handleMatch ? decodeURIComponent(handleMatch[1]) : null;
  function ogTitle() {
    var m = document.querySelector('meta[property="og:title"]');
    return m && m.content ? m.content.slice(0, 120) : null;
  }
  var is404 =
    pageType === "404" ||
    (document.body && /\btemplate-404\b/.test(document.body.className)) ||
    /^\s*(404|page not found)/i.test(document.title || "");

  function recordPage() {
    if (is404) {
      journeyEvent("dead_end", { page_type: "404", detail: "404" });
      return;
    }
    journeyEvent("page", { page_type: pageType, product: productHandle, title: productHandle ? ogTitle() : null });
    if (pageType === "searchresults" || /\/search/.test(location.pathname)) {
      var q = null;
      try {
        q = new URLSearchParams(location.search).get("q");
      } catch (e) {}
      if (q) journeyEvent("search", { query: cleanQuery(q), page_type: "searchresults" });
    }
    // Product availability, from Shopify's public product JSON (same origin).
    if (productHandle && originalFetch) {
      originalFetch(location.pathname.replace(/\/$/, "") + ".js", { credentials: "same-origin" })
        .then(function (r) {
          return r.ok ? r.json() : null;
        })
        .then(function (prod) {
          if (prod && prod.available === false) {
            journeyEvent("dead_end", {
              page_type: "product",
              product: productHandle,
              title: (prod.title || "").slice(0, 120),
              detail: "out_of_stock",
            });
          }
        })
        .catch(function () {});
    }
  }

  // Time on page, scroll depth, and how long the tab was hidden (agents
  // often work in a background tab).
  var pageStartAt = Date.now();
  var maxScroll = 0;
  var hiddenSince = document.visibilityState === "hidden" ? Date.now() : 0;
  var hiddenMs = 0;
  var leaveSent = false;
  window.addEventListener(
    "scroll",
    function () {
      var h = Math.max(document.documentElement.scrollHeight, 1);
      var pct = Math.round(((window.scrollY + window.innerHeight) / h) * 100);
      if (pct > maxScroll) maxScroll = Math.min(100, pct);
    },
    { passive: true }
  );
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      hiddenSince = Date.now();
      flushJourney();
    } else if (hiddenSince) {
      hiddenMs += Date.now() - hiddenSince;
      hiddenSince = 0;
    }
  });
  window.addEventListener("pagehide", function () {
    if (leaveSent) return;
    leaveSent = true;
    var hid = hiddenMs + (hiddenSince ? Date.now() - hiddenSince : 0);
    journeyEvent("leave", {
      page_type: is404 ? "404" : pageType,
      product: productHandle,
      dwell_ms: Date.now() - pageStartAt,
      scroll_pct: maxScroll,
      hidden_ms: hid,
    });
    flushJourney();
  });

  // Adds to cart through a plain form post (themes without AJAX carts).
  // AJAX themes (Dawn) cancel the submit and call /cart/add themselves; the
  // fetch hook below records those, so a cancelled submit is skipped here.
  document.addEventListener(
    "submit",
    function (ev) {
      var f = ev.target;
      if (!f || !f.action || !/\/cart\/add/.test(f.action)) return;
      setTimeout(function () {
        if (ev.defaultPrevented) return;
        try {
          var fd = new FormData(f);
          journeyEvent("cart_add", {
            product: productHandle,
            title: productHandle ? ogTitle() : null,
            variant_id: String(fd.get("id") || "").slice(0, 40) || null,
            quantity: parseInt(fd.get("quantity") || "1", 10) || 1,
            detail: "form",
          });
          flushJourney();
        } catch (e) {}
      }, 0);
    },
    true
  );

  function recordCartResponse(kind, data) {
    if (!data) return;
    if (data.token) linkCart(data);
    if (kind === "add") {
      var items = data.items && data.items.length ? data.items : data.variant_id ? [data] : [];
      for (var i = 0; i < items.length && i < 10; i++) {
        var it = items[i];
        journeyEvent("cart_add", {
          product: it.handle || null,
          title: String(it.product_title || it.title || "").slice(0, 120) || null,
          variant_id: it.variant_id ? String(it.variant_id) : null,
          quantity: it.quantity || 1,
          price: typeof it.final_price === "number" ? it.final_price / 100 : typeof it.price === "number" ? it.price / 100 : null,
        });
      }
    } else {
      journeyEvent("cart_change", {
        detail: kind,
        price: typeof data.total_price === "number" ? data.total_price / 100 : null,
        quantity: typeof data.item_count === "number" ? data.item_count : null,
      });
    }
  }

  recordPage();

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
      var cartMatch = url.match(/\/cart\/(add|change|update|clear)(\.js)?(\?|#|$)/);
      var isCartCall = !!cartMatch;
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
                  recordCartResponse(cartMatch[1], data);
                  reportCart(data);
                })
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
