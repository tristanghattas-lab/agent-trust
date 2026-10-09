/**
 * Shared display pieces for the Agent Trust pages in Shopify admin. Polaris
 * web components for everything Shopify already has; small inline SVG/HTML
 * for the two charts (Polaris has none). Colours follow the dashboard: one
 * hue per order origin, agent shades share the orange family.
 */
import type { ReactNode } from "react";
import { useFetcher } from "react-router";

export const money = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : `$${v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
export const pct = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const when = (iso: string | null | undefined) =>
  iso
    ? new Date(iso).toLocaleString("en-AU", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })
    : "—";

export const ORIGIN_COLOURS: Record<string, string> = {
  agent_placed: "#d9572b", agent_assisted: "#f0a07a", agent: "#d9572b",
  ai_channel: "#17a673", ai_referred: "#5b4bc4", human: "#b5b4ad", unmatched: "#dcdbd5",
};

type Tone = "critical" | "warning" | "info" | "success" | "neutral" | "caution";
const ORIGIN_TONE: Record<string, Tone> = {
  agent_placed: "warning", agent_assisted: "info", agent: "warning", ai_channel: "success",
  ai_referred: "info", human: "neutral", unmatched: "neutral",
};

export function OriginBadge({ origin, label }: { origin: string; label: string }) {
  return <s-badge tone={ORIGIN_TONE[origin] || "neutral"}>{label}</s-badge>;
}

export function ActionBadge({ action }: { action: "accept" | "review" }) {
  return action === "review" ? <s-badge tone="critical">Review</s-badge> : <s-badge tone="success">OK</s-badge>;
}

export function Kpi({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "critical" }) {
  return (
    <s-box padding="base" border="base" borderRadius="base" background="base">
      <s-stack direction="block" gap="small-300">
        <s-text color="subdued">{label}</s-text>
        <s-heading>{value}</s-heading>
        {note ? (
          <s-text color="subdued" tone={tone}>
            {note}
          </s-text>
        ) : null}
      </s-stack>
    </s-box>
  );
}

export function KpiGrid({ children }: { children: ReactNode }) {
  // Plain CSS grid: as many cards per row as fit (4 on a normal admin width).
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
      {children}
    </div>
  );
}

/** Daily bars, one series. Hover a day for its value. */
export function DailyBars({ days, colour = "#2a78d6", format = money }: {
  days: { date: string; value: number }[];
  colour?: string;
  format?: (v: number) => string;
}) {
  const W = 640, H = 160, PAD_L = 0, PAD_B = 22, PAD_T = 18;
  const max = Math.max(...days.map((d) => d.value), 0);
  if (!days.length || max <= 0) return null;
  const slot = (W - PAD_L) / days.length;
  const bw = Math.max(2, slot - 2);
  const y = (v: number) => PAD_T + (H - PAD_T - PAD_B) * (1 - v / max);
  const label = (iso: string) => new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short" });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Daily AI revenue" style={{ display: "block" }}>
      <text x={0} y={11} fontSize="11" fill="#6d7175">{format(max)}</text>
      <line x1={0} x2={W} y1={PAD_T} y2={PAD_T} stroke="#ebebeb" />
      {days.map((d, i) => {
        const x = PAD_L + i * slot + 1;
        const top = y(d.value);
        const h = H - PAD_B - top;
        return (
          <g key={d.date}>
            <title>{`${label(d.date)}: ${format(d.value)}`}</title>
            <rect x={x - 1} y={PAD_T} width={slot} height={H - PAD_B - PAD_T} fill="transparent" />
            {d.value > 0 && (
              <path
                d={`M${x},${H - PAD_B} V${top + Math.min(4, h)} q0,-${Math.min(4, h)} ${Math.min(4, bw / 2)},-${Math.min(4, h)} H${x + bw - Math.min(4, bw / 2)} q${Math.min(4, bw / 2)},0 ${Math.min(4, bw / 2)},${Math.min(4, h)} V${H - PAD_B} Z`}
                fill={colour}
              />
            )}
          </g>
        );
      })}
      <line x1={0} x2={W} y1={H - PAD_B} y2={H - PAD_B} stroke="#c9cccf" />
      <text x={0} y={H - 6} fontSize="11" fill="#6d7175">{label(days[0].date)}</text>
      <text x={W} y={H - 6} fontSize="11" fill="#6d7175" textAnchor="end">{label(days[days.length - 1].date)}</text>
    </svg>
  );
}

/** Horizontal bars with the label and value as text (never colour alone). */
export function HBars({ rows }: { rows: { key: string; label: string; value: number; display: string; colour: string }[] }) {
  const max = Math.max(...rows.map((r) => r.value), 0) || 1;
  return (
    <s-stack direction="block" gap="small">
      {rows.map((r) => (
        <s-stack key={r.key} direction="block" gap="small-400">
          <s-stack direction="inline" justifyContent="space-between">
            <s-text>{r.label}</s-text>
            <s-text type="strong">{r.display}</s-text>
          </s-stack>
          <div style={{ height: 8, background: "#f1f1f1", borderRadius: 4 }}>
            <div style={{ width: `${Math.max(2, (r.value / max) * 100)}%`, height: 8, background: r.colour, borderRadius: 4 }} />
          </div>
        </s-stack>
      ))}
    </s-stack>
  );
}

const STEP_COLOURS: Record<string, string> = {
  page: "#6a9ef0", search: "#5b4bc4", cart_add: "#17a673", cart_change: "#17a673", dead_end: "#d72c0d", leave: "#c9cccf",
};

export function Timeline({ steps }: { steps: { t: number; kind: string; text: string }[] }) {
  if (!steps?.length) return <s-text color="subdued">No page-by-page steps recorded for this visit.</s-text>;
  return (
    <s-stack direction="block" gap="small-300">
      {steps.slice(0, 40).map((s, i) => (
        <s-stack key={i} direction="inline" gap="small" alignItems="center">
          <span style={{ width: 44, textAlign: "right", color: "#6d7175", fontSize: 12, fontVariantNumeric: "tabular-nums" }}>
            +{Math.round(s.t)}s
          </span>
          <span style={{ width: 8, height: 8, borderRadius: 4, background: STEP_COLOURS[s.kind] || "#c9cccf", flex: "none" }} />
          <s-text color={s.kind === "leave" ? "subdued" : undefined}>{s.text}</s-text>
        </s-stack>
      ))}
    </s-stack>
  );
}

// ---------------------------------------------------------------------------
// Flashier analytics: visits chart, behaviour map, agent paths, live strip
// ---------------------------------------------------------------------------
export const CLASS_COLOURS: Record<string, string> = {
  human: "#b5b4ad", ai_referred: "#5b4bc4", assistant: "#2a78d6", automation: "#d9572b", agents: "#d9572b",
};

export function Legend({ items }: { items: { label: string; colour: string; ring?: boolean }[] }) {
  return (
    <s-stack direction="inline" gap="base">
      {items.map((i) => (
        <s-stack key={i.label} direction="inline" gap="small-300" alignItems="center">
          <span style={{
            width: 10, height: 10, borderRadius: 5, display: "inline-block",
            background: i.ring ? "transparent" : i.colour, border: i.ring ? `2px solid ${i.colour}` : "none",
          }} />
          <s-text color="subdued">{i.label}</s-text>
        </s-stack>
      ))}
    </s-stack>
  );
}

/** Stacked daily visits: people, people sent by AI, agents. Hover a day for the split. */
export function VisitsChart({ days }: { days: { date: string; people: number; ai: number; agents: number }[] }) {
  const W = 640, H = 170, PAD_B = 22, PAD_T = 18;
  const max = Math.max(...days.map((d) => d.people + d.ai + d.agents), 0);
  if (!days.length || max <= 0) return null;
  const slot = W / days.length, bw = Math.max(2, slot - 2);
  const h = (v: number) => ((H - PAD_T - PAD_B) * v) / max;
  const label = (iso: string) => new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short" });
  return (
    <s-stack direction="block" gap="small">
      <Legend items={[
        { label: "People", colour: CLASS_COLOURS.human },
        { label: "Sent by AI assistants", colour: CLASS_COLOURS.ai_referred },
        { label: "AI agents", colour: CLASS_COLOURS.agents },
      ]} />
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Visits per day" style={{ display: "block" }}>
        <text x={0} y={11} fontSize="11" fill="#6d7175">{max.toLocaleString()}</text>
        <line x1={0} x2={W} y1={PAD_T} y2={PAD_T} stroke="#ebebeb" />
        {days.map((d, i) => {
          const x = i * slot + 1;
          let y = H - PAD_B;
          const segs = [["people", d.people, CLASS_COLOURS.human], ["ai", d.ai, CLASS_COLOURS.ai_referred],
                        ["agents", d.agents, CLASS_COLOURS.agents]] as const;
          return (
            <g key={d.date}>
              <title>{`${label(d.date)}: ${d.people} people, ${d.ai} sent by AI, ${d.agents} agents`}</title>
              <rect x={x - 1} y={PAD_T} width={slot} height={H - PAD_B - PAD_T} fill="transparent" />
              {segs.map(([k, v, c]) => {
                if (!v) return null;
                const hh = h(v);
                y -= hh;
                return <rect key={k} x={x} y={y} width={bw} height={Math.max(0, hh - 1)} fill={c} rx={1.5} />;
              })}
            </g>
          );
        })}
        <line x1={0} x2={W} y1={H - PAD_B} y2={H - PAD_B} stroke="#c9cccf" />
        <text x={0} y={H - 6} fontSize="11" fill="#6d7175">{label(days[0].date)}</text>
        <text x={W} y={H - 6} fontSize="11" fill="#6d7175" textAnchor="end">{label(days[days.length - 1].date)}</text>
      </svg>
    </s-stack>
  );
}

export type MapPoint = {
  x: number; y: number; session_key: string; class: string; class_label: string; agent: string | null;
  confidence: number | null; ordered: boolean; lookalike: boolean;
};

/**
 * The behaviour map: each dot is a visit, placed by how it behaved (pointer,
 * typing, pace, reading, tab use). Visits that behave alike sit together.
 * Bigger dots placed an order; a red ring marks a visit that sits with the
 * other group (a person behaving like an agent, or an agent blending in).
 */
export function BehaviourMap({ points }: { points: MapPoint[] }) {
  const W = 640, H = 400, P = 18;
  const sx = (x: number) => P + ((x + 1) / 2) * (W - 2 * P);
  const sy = (y: number) => P + ((1 - y) / 2) * (H - 2 * P);
  const groups = [
    { key: "human", label: "People", colour: CLASS_COLOURS.human },
    { key: "assistant", label: "AI assistants", colour: CLASS_COLOURS.assistant },
    { key: "automation", label: "Browser agents", colour: CLASS_COLOURS.automation },
  ];
  // Labels sit at each group's median, so a few stragglers don't drag them off the cluster.
  const median = (xs: number[]) => { const v = [...xs].sort((a, b) => a - b); return v[Math.floor(v.length / 2)]; };
  const centre = (g: MapPoint[]) => (g.length ? { x: median(g.map((p) => p.x)), y: median(g.map((p) => p.y)) } : null);
  const hc = centre(points.filter((p) => p.class === "human"));
  const ac = centre(points.filter((p) => p.class !== "human"));
  // People first (background), agents on top, order-placing visits last.
  const order = (p: MapPoint) => (p.class === "human" ? 0 : 1) + (p.ordered ? 2 : 0) + (p.lookalike ? 4 : 0);
  const sorted = [...points].sort((a, b) => order(a) - order(b));
  return (
    <s-stack direction="block" gap="small">
      <Legend items={[
        ...groups.map((g) => ({ label: `${g.label} (${points.filter((p) => p.class === g.key).length})`, colour: g.colour })),
        { label: "Bigger dot: placed an order", colour: "#4a4a4a", ring: true },
        { label: "Behaves like the other group", colour: "#d72c0d", ring: true },
      ]} />
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Behaviour map of visits"
        style={{ display: "block", background: "#fafafa", borderRadius: 8 }}>
        <line x1={W / 2} x2={W / 2} y1={P} y2={H - P} stroke="#efefef" />
        <line x1={P} x2={W - P} y1={H / 2} y2={H / 2} stroke="#efefef" />
        {sorted.map((p) => {
          const col = CLASS_COLOURS[p.class] || CLASS_COLOURS.human;
          const r = p.ordered ? 6 : 3.5;
          return (
            <a key={p.session_key} href={`/app/visits/${encodeURIComponent(p.session_key)}`}>
              <title>{`${p.agent || "Person"} · ${p.class_label}${p.confidence != null ? ` · confidence ${p.confidence.toFixed(2)}` : ""}${p.ordered ? " · placed an order" : ""}${p.lookalike ? " · behaves like the other group" : ""}`}</title>
              <circle cx={sx(p.x)} cy={sy(p.y)} r={r + 4} fill="transparent" />
              <circle cx={sx(p.x)} cy={sy(p.y)} r={r} fill={col} fillOpacity={p.class === "human" ? 0.65 : 0.85}
                stroke={p.ordered ? "#4a4a4a" : "#ffffff"} strokeWidth={p.ordered ? 0.9 : 0.8} />
              {p.lookalike && <circle cx={sx(p.x)} cy={sy(p.y)} r={r + 3.5} fill="none" stroke="#d72c0d" strokeWidth={1.6} />}
            </a>
          );
        })}
        {hc && (
          <text x={sx(hc.x)} y={sy(hc.y) - 14} textAnchor="middle" fontSize="13" fontWeight={600} fill="#4a4a4a"
            stroke="#fafafa" strokeWidth={4} paintOrder="stroke">People</text>
        )}
        {ac && (
          <text x={sx(ac.x)} y={sy(ac.y) - 14} textAnchor="middle" fontSize="13" fontWeight={600} fill="#a8401c"
            stroke="#fafafa" strokeWidth={4} paintOrder="stroke">Agents</text>
        )}
      </svg>
    </s-stack>
  );
}

const STAGE_COLOURS: Record<string, string> = {
  Home: "#e3e3e3", Collection: "#e3e3e3", Search: "#e6e1fb", Product: "#dbe9fd", Page: "#efefef",
  "Add to cart": "#d3f2e3", Checkout: "#ffe7c2", Order: "#c8eecf", "Dead end": "#fde0dc", Cart: "#e3e3e3",
};

export function PathChips({ path }: { path: string[] }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4 }}>
      {path.map((st, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          {i > 0 && <span style={{ color: "#8c9196", fontSize: 12 }}>→</span>}
          <span style={{
            background: STAGE_COLOURS[st] || "#efefef", borderRadius: 6, padding: "2px 8px", fontSize: 12,
            fontWeight: st === "Order" ? 650 : 500, color: "#202223", whiteSpace: "nowrap",
          }}>{st}</span>
        </span>
      ))}
    </div>
  );
}

export function LiveDot() {
  return (
    <span style={{ position: "relative", width: 10, height: 10, display: "inline-block" }}>
      <style>{`@keyframes atPulse{0%{transform:scale(1);opacity:.6}100%{transform:scale(2.6);opacity:0}}`}</style>
      <span style={{ position: "absolute", inset: 0, borderRadius: 5, background: "#29845a", animation: "atPulse 1.6s ease-out infinite" }} />
      <span style={{ position: "absolute", inset: 0, borderRadius: 5, background: "#29845a" }} />
    </span>
  );
}

export const ago = (s: number) => (s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`);

// ---------------------------------------------------------------------------
// Plans and locks
// ---------------------------------------------------------------------------
export const PLANS = [
  {
    key: "free", name: "Free", price: "$0",
    pitch: "See what AI is doing on your store.",
    features: ["AI orders in your last 60 days", "AI revenue and agent orders", "Agent sales you're missing, in dollars",
               "Every order tagged by origin"],
  },
  {
    key: "growth", name: "Growth", price: "$49/month",
    pitch: "Win more sales from AI shoppers.",
    features: ["How to fix each missed sale", "Behaviour map and agent journeys", "What agents look at, search for and skip",
               "Revenue by source, live view", "Weekly AI revenue email"],
  },
  {
    key: "trust", name: "Trust", price: "$149/month",
    pitch: "Sell to agents without the risk.",
    features: ["Everything in Growth", "Evidence for every agent order", "Review queue for unverified agent orders",
               "Order page panel and AI tags", "Chargeback evidence packs and alerts"],
  },
] as const;

export const PLAN_FOR_FEATURE: Record<string, "growth" | "trust"> = {
  fixes: "growth", behaviour: "growth", journeys: "growth", products: "growth", live: "growth", channels: "growth",
  evidence: "trust", review: "trust", tags: "trust",
};

/** Start the free trial from anywhere (posts to the Plans page's action). */
export function TrialButton({ label = "Start 14-day free trial", variant = "primary" }: {
  label?: string; variant?: "primary" | "secondary";
}) {
  const fetcher = useFetcher();
  return (
    <s-button variant={variant} loading={fetcher.state !== "idle" || undefined}
      onClick={() => fetcher.submit({ intent: "trial" }, { method: "post", action: "/app/plans" })}>
      {label}
    </s-button>
  );
}

/** A locked feature: what it does, and a way in (trial or plans). */
export function Locked({ feature, title, children, trialAvailable }: {
  feature: string; title: string; children?: ReactNode; trialAvailable: boolean;
}) {
  const plan = PLANS.find((p) => p.key === PLAN_FOR_FEATURE[feature]);
  return (
    <s-box padding="base" border="base" borderRadius="base" background="subdued">
      <s-stack direction="block" gap="small">
        <s-stack direction="inline" gap="small" alignItems="center">
          <s-icon type="lock" />
          <s-text type="strong">{title}</s-text>
          {plan && <s-badge tone="info">{plan.name}</s-badge>}
        </s-stack>
        {children ? <s-text color="subdued">{children}</s-text> : null}
        <s-stack direction="inline" gap="small">
          {trialAvailable ? <TrialButton /> : null}
          <s-button href="/app/plans" variant={trialAvailable ? "secondary" : "primary"}>See plans</s-button>
        </s-stack>
      </s-stack>
    </s-box>
  );
}

/** Agent drop-off: share of agent visits reaching each step, and the cart value lost between steps. */
export function AgentFunnel({ steps, lostCarts, lostCheckouts }: {
  steps: { step: string; visits: number; share: number | null }[]; lostCarts: number | null; lostCheckouts: number | null;
}) {
  const lossAfter: Record<string, number | null> = { "Added to cart": lostCarts, "Started checkout": lostCheckouts };
  return (
    <s-stack direction="block" gap="small">
      {steps.map((s) => (
        <s-stack key={s.step} direction="block" gap="small-400">
          <s-stack direction="inline" justifyContent="space-between">
            <s-text>{s.step}</s-text>
            <s-text type="strong">{s.visits.toLocaleString()} <s-text color="subdued">({pct(s.share, 0)})</s-text></s-text>
          </s-stack>
          <div style={{ height: 14, background: "#f1f1f1", borderRadius: 4 }}>
            <div style={{ width: `${Math.max(1.5, (s.share ?? 0) * 100)}%`, height: 14, borderRadius: 4,
                          background: s.step === "Ordered" ? "#17a673" : "#d9572b", opacity: s.step === "Ordered" ? 1 : 0.85 }} />
          </div>
          {lossAfter[s.step] ? (
            <s-text tone="critical">↓ {money(lossAfter[s.step])} in agent carts dropped after this step</s-text>
          ) : null}
        </s-stack>
      ))}
    </s-stack>
  );
}
