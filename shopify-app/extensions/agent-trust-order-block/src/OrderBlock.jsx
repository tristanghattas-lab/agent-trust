import "@shopify/ui-extensions/preact";
import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

/**
 * Agent Trust on the order page: where the order came from (agent-placed,
 * agent-assisted, AI-referred...), who the agent was and whether it proved
 * it, the evidence we hold, and a recommended action. Opening the block
 * also refreshes the order's "AI: " tags.
 */
export default async () => {
  render(<OrderBlock />, document.body);
};

const ORIGIN_TONE = {
  agent_placed: "warning", agent_assisted: "info", agent: "warning",
  ai_channel: "info", ai_referred: "info", human: "neutral", unmatched: "neutral",
};

function OrderBlock() {
  const gid = shopify.data.selected?.[0]?.id || "";
  const orderId = gid.split("/").pop();
  const [state, setState] = useState(/** @type {any} */ ({ loading: true }));

  useEffect(() => {
    let live = true;
    fetch(`/api/order/${orderId}`)
      .then((r) => r.json())
      .then((d) => live && setState({ loading: false, ...d }))
      .catch((e) => live && setState({ loading: false, found: false, error: String(e) }));
    return () => { live = false; };
  }, [orderId]);

  if (state.loading) {
    return (
      <s-admin-block heading="Agent Trust">
        <s-text color="subdued">Checking this order…</s-text>
      </s-admin-block>
    );
  }
  if (!state.found) {
    return (
      <s-admin-block heading="Agent Trust">
        <s-text color="subdued">
          {state.error ? "Couldn't reach Agent Trust. Try again shortly." : "No Agent Trust record for this order."}
        </s-text>
      </s-admin-block>
    );
  }

  const v = state.verdict;
  const review = v.action === "review";
  const facts = [
    v.agent && ["Agent", v.agent],
    v.identity_label && ["Identity", v.identity_label],
    v.confidence != null && ["Confidence", v.confidence.toFixed(2)],
    v.ai_source && ["AI source", v.ai_source],
    v.handoff_seconds != null && ["Pause before payment", `${Math.round(v.handoff_seconds)}s`],
    ["Evidence", v.evidence_score],
  ].filter(Boolean);

  return (
    <s-admin-block heading="Agent Trust">
      <s-stack direction="block" gap="base">
        <s-stack direction="inline" gap="small-200">
          <s-badge tone={ORIGIN_TONE[v.origin] || "neutral"}>{v.origin_label}</s-badge>
          <s-badge tone={review ? "critical" : "success"}>{review ? "Review" : "OK to fulfil"}</s-badge>
          {v.is_test && <s-badge>Test order</s-badge>}
        </s-stack>
        <s-text>{v.action_reason}</s-text>

        <s-stack direction="block" gap="small-300">
          {facts.map(([k, val]) => (
            <s-stack key={k} direction="inline" gap="small-200">
              <s-text color="subdued">{k}:</s-text>
              <s-text>{val}</s-text>
            </s-stack>
          ))}
        </s-stack>

        <s-divider />
        <s-stack direction="block" gap="small-300">
          {v.chain.map((c) => (
            <s-stack key={c.step} direction="inline" gap="small-200">
              <s-text type="strong" tone={c.ok ? "success" : "caution"}>
                {c.ok ? "✓" : "○"} {c.step}
              </s-text>
              <s-text color="subdued">{c.text}</s-text>
            </s-stack>
          ))}
        </s-stack>

        <s-link href={state.dashboard} target="_blank">
          Open in Agent Trust
        </s-link>
      </s-stack>
    </s-admin-block>
  );
}
