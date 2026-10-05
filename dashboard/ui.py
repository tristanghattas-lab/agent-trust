"""
Visual building blocks for the dashboard: page CSS, KPI tiles with
sparklines, card headers, pills and the activity feed. Plain HTML injected
through st.markdown, so the look doesn't depend on Streamlit's defaults.
"""
from __future__ import annotations

import html
from typing import Iterable

import pandas as pd
import streamlit as st

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
ACCENT = "#2a78d6"

CSS = """
<style>
  /* --- chrome --- */
  header[data-testid="stHeader"], footer, #MainMenu,
  [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none !important;}
  .stApp {background: #f6f5f2;}
  .block-container {padding: 1.6rem 2.2rem 3rem; max-width: 1360px;}
  html, body, [class*="css"] {font-family: system-ui, -apple-system, "Segoe UI", sans-serif;}

  /* --- sidebar --- */
  section[data-testid="stSidebar"] {background: #fcfcfb; border-right: 1px solid rgba(11,11,11,0.07);}
  section[data-testid="stSidebar"] .block-container {padding-top: 1.2rem;}
  .at-brand {display:flex; align-items:center; gap:10px; margin: 2px 0 2px;}
  .at-brand-mark {width:28px; height:28px; border-radius:8px; background:#0b0b0b; color:#fff;
                  display:flex; align-items:center; justify-content:center; font-size:14px;}
  .at-brand-name {font-weight:650; font-size:1.02rem; color:#0b0b0b; letter-spacing:-0.01em;}
  .at-brand-sub {color:#898781; font-size:0.78rem; margin: 0 0 18px 38px;}
  .at-side-label {color:#898781; font-size:0.72rem; font-weight:600; letter-spacing:0.06em;
                  text-transform:uppercase; margin: 18px 0 6px;}
  /* nav: radio styled as a menu */
  section[data-testid="stSidebar"] div[role="radiogroup"].st-key-nav label,
  section[data-testid="stSidebar"] .st-key-nav div[role="radiogroup"] label {
      width:100%; padding: 7px 10px; border-radius: 8px; margin: 1px 0; cursor:pointer;}
  section[data-testid="stSidebar"] .st-key-nav div[role="radiogroup"] label:hover {background:#f0efec;}
  section[data-testid="stSidebar"] .st-key-nav div[role="radiogroup"] label > div:first-child {display:none;}
  section[data-testid="stSidebar"] .st-key-nav div[role="radiogroup"] label:has(input:checked) {
      background:#eef4fc;}
  section[data-testid="stSidebar"] .st-key-nav div[role="radiogroup"] label:has(input:checked) p {
      color:#1c5cab; font-weight:600;}
  section[data-testid="stSidebar"] .st-key-nav p {font-size:0.92rem; color:#0b0b0b;}

  /* --- page header --- */
  .at-head {display:flex; justify-content:space-between; align-items:flex-end; margin-bottom: 4px;}
  .at-title {font-size:1.7rem; font-weight:680; letter-spacing:-0.02em; color:#0b0b0b; line-height:1.2;}
  .at-crumb {color:#52514e; font-size:0.88rem; margin-top:4px;}
  .at-pill {display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px;
            font-size:0.75rem; font-weight:650; letter-spacing:0.03em; white-space:nowrap;}
  .at-pill.demo {background:#fff4d6; color:#5c4400; border:1px solid #f3d27a;}
  .at-pill.live {background:#e9f7e9; color:#0b4d0b; border:1px solid #9fd89f;}
  .at-dot {width:7px; height:7px; border-radius:50%; display:inline-block;}
  .at-note {color:#6b5a1e; font-size:0.8rem; background:#fffaeb; border:1px solid #f6e3a8;
            border-radius:8px; padding:7px 12px; margin: 10px 0 16px;}

  /* --- cards (bordered st.container) --- */
  div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[data-testid="stVerticalBlock"]) {
      background:#fcfcfb; border:1px solid rgba(11,11,11,0.08) !important; border-radius:14px;
      box-shadow: 0 1px 2px rgba(11,11,11,0.03);}
  .at-card-title {font-size:0.98rem; font-weight:640; color:#0b0b0b; margin:0;}
  .at-card-sub {font-size:0.8rem; color:#898781; margin: 2px 0 6px;}

  /* --- KPI tiles --- */
  .at-kpi {background:#fcfcfb; border:1px solid rgba(11,11,11,0.08); border-radius:14px;
           padding:14px 16px 10px; box-shadow: 0 1px 2px rgba(11,11,11,0.03); height:100%;}
  .at-kpi-label {font-size:0.78rem; color:#52514e; font-weight:550; display:flex; gap:6px; align-items:center;}
  .at-kpi-value {font-size:1.75rem; font-weight:680; color:#0b0b0b; letter-spacing:-0.02em; margin-top:4px;
                 line-height:1.15;}
  .at-kpi-delta {font-size:0.78rem; margin-top:3px; color:#898781;}
  .at-kpi-delta.up {color:#006300;} .at-kpi-delta.down {color:#b42b2b;}
  .at-kpi svg {display:block; margin-top:6px;}

  /* --- activity feed --- */
  .at-feed-row {display:flex; gap:12px; padding:9px 0; border-bottom:1px solid #efeee9;}
  .at-feed-row:last-child {border-bottom:none;}
  .at-feed-dot {width:9px; height:9px; border-radius:50%; margin-top:6px; flex:none;}
  .at-feed-text {font-size:0.87rem; color:#0b0b0b; line-height:1.35;}
  .at-feed-meta {font-size:0.75rem; color:#898781; margin-top:1px;}

  /* --- findings / signals --- */
  .at-sev {display:inline-block; padding:2px 8px; border-radius:5px; font-size:0.72rem; font-weight:650;
           margin-right:8px; vertical-align:1px;}
  .at-finding {padding: 12px 0; border-bottom:1px solid #efeee9;}
  .at-finding:last-child {border-bottom:none;}
  .at-finding-title {font-weight:620; color:#0b0b0b; font-size:0.93rem;}
  .at-finding-meta {color:#898781; font-size:0.78rem;}
  .at-finding-body {color:#0b0b0b; font-size:0.88rem; margin-top:6px;}
  .at-fix {color:#52514e; font-size:0.82rem; margin-top:6px; padding-left:10px; border-left:2px solid #c3c2b7;}
  .at-signal {display:flex; gap:10px; padding:8px 0; border-bottom:1px solid #efeee9; font-size:0.88rem;}
  .at-signal:last-child {border-bottom:none;}
  .at-kv {display:flex; justify-content:space-between; padding:7px 0; border-bottom:1px solid #efeee9;
          font-size:0.85rem;}
  .at-kv:last-child {border-bottom:none;}
  .at-kv span:first-child {color:#52514e;} .at-kv span:last-child {color:#0b0b0b; text-align:right;}

  /* --- widgets --- */
  div[data-testid="stDataFrame"] {border-radius:10px; overflow:hidden;}
  div[data-testid="stExpander"] details {border-radius:10px; background:#fcfcfb;}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(text) -> str:
    return html.escape(str(text))


def sparkline(values: Iterable[float], colour: str = ACCENT, height: int = 34) -> str:
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    w = 100.0
    step = w / (len(vals) - 1)
    pts = [(i * step, height - 3 - (v - lo) / span * (height - 8)) for i, v in enumerate(vals)]
    line = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    area = f"0,{height} " + line + f" {w},{height}"
    return (
        f'<svg viewBox="0 0 {w} {height}" preserveAspectRatio="none" width="100%" height="{height}">'
        f'<polygon points="{area}" fill="{colour}" fill-opacity="0.08"/>'
        f'<polyline points="{line}" fill="none" stroke="{colour}" stroke-width="1.6" '
        f'vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def kpi(label: str, value: str, delta: str | None = None, tone: str = "neutral",
        spark: Iterable[float] | None = None, colour: str = ACCENT, help: str | None = None) -> str:
    """tone: 'up' (good), 'down' (bad) or 'neutral'."""
    arrow = {"up": "▲ ", "down": "▼ "}.get(tone, "")
    title = f' title="{esc(help)}"' if help else ""
    info = ' <span style="color:#c3c2b7">ⓘ</span>' if help else ""
    return (
        f'<div class="at-kpi"{title}>'
        f'<div class="at-kpi-label">{esc(label)}{info}</div>'
        f'<div class="at-kpi-value">{esc(value)}</div>'
        + (f'<div class="at-kpi-delta {tone}">{arrow}{esc(delta)}</div>' if delta else
           '<div class="at-kpi-delta">&nbsp;</div>')
        + (sparkline(spark, colour) if spark is not None else "")
        + "</div>"
    )


def kpi_row(tiles: list[str]) -> None:
    cols = st.columns(len(tiles))
    for col, tile in zip(cols, tiles):
        col.markdown(tile, unsafe_allow_html=True)


def card_title(title: str, sub: str | None = None) -> None:
    st.markdown(
        f'<div class="at-card-title">{esc(title)}</div>'
        + (f'<div class="at-card-sub">{esc(sub)}</div>' if sub else ""),
        unsafe_allow_html=True,
    )


def page_header(title: str, crumb: str, demo: bool, note: str = "") -> None:
    pill = (
        '<span class="at-pill demo"><span class="at-dot" style="background:#e0a400"></span>TEST DATA</span>'
        if demo else
        '<span class="at-pill live"><span class="at-dot" style="background:#0ca30c"></span>LIVE</span>'
    )
    st.markdown(
        f'<div class="at-head"><div><div class="at-title">{esc(title)}</div>'
        f'<div class="at-crumb">{esc(crumb)}</div></div>{pill}</div>',
        unsafe_allow_html=True,
    )
    if demo:
        st.markdown(
            '<div class="at-note"><b>Synthetic data.</b> Traffic, orders and outcomes are generated '
            "to show how the dashboard reads. Agent behaviour is modelled on real test runs against "
            f"a Shopify dev store; volumes and rates are invented. {esc(note)}</div>",
            unsafe_allow_html=True,
        )


def relative_time(ts: pd.Timestamp, now: pd.Timestamp) -> str:
    secs = max(0, (now - ts).total_seconds())
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def feed(items: list[dict], now: pd.Timestamp) -> None:
    """items: {ts, colour, text, meta}"""
    if not items:
        st.markdown('<div class="at-card-sub">Nothing notable in this window.</div>',
                    unsafe_allow_html=True)
        return
    rows = "".join(
        f'<div class="at-feed-row"><span class="at-feed-dot" style="background:{i["colour"]}"></span>'
        f'<div><div class="at-feed-text">{i["text"]}</div>'
        f'<div class="at-feed-meta">{esc(relative_time(i["ts"], now))} · {esc(i["meta"])}</div></div></div>'
        for i in items
    )
    st.markdown(rows, unsafe_allow_html=True)


def kv_list(pairs: dict[str, str]) -> None:
    st.markdown(
        "".join(f'<div class="at-kv"><span>{esc(k)}</span><span>{esc(v)}</span></div>'
                for k, v in pairs.items()),
        unsafe_allow_html=True,
    )
