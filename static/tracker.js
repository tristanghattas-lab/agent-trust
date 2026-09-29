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
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(
          API,
          new Blob([body], { type: "application/json" })
        );
      } else {
        fetch(API, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body,
          keepalive: true,
        });
      }
    } catch (e) {
      /* never let tracking break the page */
    }
  }

  // Basic pageview on load.
  post({ event_count: 1 });

  // Public API the site's own checkout/age-gate code calls directly —
  // this is the part generic bot-detection tools can't see, because it
  // requires knowing what "checkout" and "age gate" mean on this site.
  window.AgentTrust = {
    cartUpdated: function (value) {
      post({ event_count: 1, cart_value: value });
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
})();
