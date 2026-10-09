/**
 * Shared display pieces for the Agent Trust pages in Shopify admin. Polaris
 * web components for everything Shopify already has; small inline SVG/HTML
 * for the two charts (Polaris has none). Colours follow the dashboard: one
 * hue per order origin, agent shades share the orange family.
 */
import type { ReactNode } from "react";

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
  return (
    <s-query-container>
      <s-grid gridTemplateColumns="@container (inline-size > 640px) repeat(4, 1fr), repeat(2, 1fr)" gap="base">
        {children}
      </s-grid>
    </s-query-container>
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
