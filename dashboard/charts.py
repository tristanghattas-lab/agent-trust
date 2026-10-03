"""
Plotly figure builders. One visual system for every chart: fixed class
colours, thin marks, recessive grid, hover on every mark.

Palette: the dataviz skill's validated reference categorical order (slots
1-4 pass adjacent CVD checks in light mode). Human traffic is a neutral
grey so the agent classes carry the colour.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from dashboard.data import (
    ASSISTANT, AUTOMATION, CRAWLER, HUMAN, SCRAPER, SEVERITY_ORDER,
)

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURFACE = "#fcfcfb"

CLASS_COLOURS = {
    ASSISTANT: "#2a78d6",
    AUTOMATION: "#eb6834",
    CRAWLER: "#1baf7a",
    SCRAPER: "#eda100",
    HUMAN: "#c3c2b7",
    "Unmatched session": "#e1e0d9",
}
SEGMENT_COLOURS = {
    "All human visits": "#c3c2b7",
    "AI-referred humans": "#4a3aa7",
    ASSISTANT: CLASS_COLOURS[ASSISTANT],
    AUTOMATION: CLASS_COLOURS[AUTOMATION],
}
SOURCE_COLOURS = {
    "ChatGPT": "#2a78d6", "Perplexity": "#eb6834", "Gemini": "#1baf7a",
    "Copilot": "#eda100", "Claude": "#e87ba4",
}
STATUS = {"pass": "#0ca30c", "partial": "#fab219", "fail": "#d03b3b"}
SEVERITY_COLOURS = {
    "High": "#d03b3b", "Medium": "#ec835a", "Needs review": "#fab219",
    "Low": "#86b6ef", "Info": "#c3c2b7",
}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def _layout(fig: go.Figure, height: int = 320, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=4, r=12, t=8, b=4),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=INK_2),
        hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(family=FONT, color=INK)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(size=12, color=INK_2), title=None, traceorder="normal"),
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont=dict(color=MUTED), title=None,
                     ticks="", zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, linecolor=AXIS, showline=False,
                     tickfont=dict(color=MUTED), title=None, zeroline=False, automargin=True)
    return fig


def agent_sessions_by_day(s: pd.DataFrame) -> go.Figure:
    agents = s[s["traffic_class"] != HUMAN]
    days = pd.Index(sorted(s["date"].unique()), name="date")
    counts = agents.groupby(["date", "traffic_class"]).size().unstack(fill_value=0).reindex(days, fill_value=0)
    fig = go.Figure()
    for cls in [ASSISTANT, AUTOMATION, CRAWLER, SCRAPER]:
        if cls not in counts:
            continue
        fig.add_bar(
            x=counts.index, y=counts[cls], name=cls, marker_color=CLASS_COLOURS[cls],
            marker_line=dict(color=SURFACE, width=1),
            hovertemplate="%{x|%a %d %b}<br>" + cls + ": %{y}<extra></extra>",
        )
    fig.update_layout(barmode="stack")
    return _layout(fig)


def agent_share_by_day(s: pd.DataFrame) -> go.Figure:
    daily = s.groupby("date").agg(total=("session_key", "size"),
                                  agents=("is_agent", "sum"))
    daily["share"] = daily["agents"] / daily["total"]
    roll = daily["share"].rolling(7, min_periods=1).mean()
    fig = go.Figure()
    fig.add_scatter(x=daily.index, y=daily["share"], mode="markers", name="Daily",
                    marker=dict(size=6, color="#86b6ef"),
                    hovertemplate="%{x|%a %d %b}: %{y:.1%}<extra>Daily</extra>")
    fig.add_scatter(x=daily.index, y=roll, mode="lines", name="7-day average",
                    line=dict(width=2, color="#2a78d6"),
                    hovertemplate="%{x|%a %d %b}: %{y:.1%}<extra>7-day avg</extra>")
    fig.update_yaxes(tickformat=".0%", rangemode="tozero")
    return _layout(fig)


def funnel_comparison(funnel: pd.DataFrame) -> go.Figure:
    """funnel: index=segment, columns=stages (as share of segment's visits)."""
    fig = go.Figure()
    for seg in funnel.index:
        fig.add_bar(
            x=funnel.columns, y=funnel.loc[seg], name=seg,
            marker_color=SEGMENT_COLOURS.get(seg, MUTED),
            marker_line=dict(color=SURFACE, width=1),
            hovertemplate=seg + "<br>%{x}: %{y:.1%} of visits<extra></extra>",
        )
    fig.update_layout(barmode="group", bargroupgap=0.08)
    fig.update_yaxes(tickformat=".0%")
    return _layout(fig, height=340)


def ai_referrals_by_day(s: pd.DataFrame) -> go.Figure:
    ref = s[s["ai_source"].notna()]
    days = pd.Index(sorted(s["date"].unique()), name="date")
    counts = ref.groupby(["date", "ai_source"]).size().unstack(fill_value=0).reindex(days, fill_value=0)
    order = counts.sum().sort_values(ascending=False).index
    fig = go.Figure()
    for src in order:
        fig.add_bar(x=counts.index, y=counts[src], name=src,
                    marker_color=SOURCE_COLOURS.get(src, MUTED),
                    marker_line=dict(color=SURFACE, width=1),
                    hovertemplate="%{x|%a %d %b}<br>" + src + ": %{y}<extra></extra>")
    fig.update_layout(barmode="stack")
    return _layout(fig)


def horizontal_bars(labels: list[str], values: list[float], colours: list[str],
                    fmt: str = ",.0f", height: int | None = None) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(
        y=labels, x=values, orientation="h", marker_color=colours, width=0.62,
        text=[f"{v:{fmt}}" for v in values], textposition="outside",
        textfont=dict(color=INK_2), cliponaxis=False,
        hovertemplate="%{y}: %{x:" + fmt + "}<extra></extra>",
    )
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)", tickfont=dict(color=INK_2))
    top = max(values) if len(values) and max(values) > 0 else 1
    fig.update_xaxes(showticklabels=False, gridcolor="rgba(0,0,0,0)", range=[0, top * 1.22])
    return _layout(fig, height=height or max(140, 46 * len(labels)), legend=False)


def threat_matrix(runs: pd.DataFrame) -> go.Figure:
    """Agent surface x task grid. Cell colour = result status, cell text =
    the result word, so status is never colour-only."""
    latest = runs.sort_values("run_at").groupby(["agent_surface", "task_name"]).tail(1)
    surfaces = sorted(latest["agent_surface"].unique())
    tasks = list(dict.fromkeys(runs.sort_values("run_at")["task_name"]))
    code = {"pass": 0, "partial": 1, "fail": 2}
    z, txt, hover = [], [], []
    for sfc in surfaces:
        zr, tr, hr = [], [], []
        for t in tasks:
            row = latest[(latest.agent_surface == sfc) & (latest.task_name == t)]
            if row.empty:
                zr.append(None); tr.append(""); hr.append(f"{sfc} · {t}<br>Not run")
            else:
                r = row.iloc[0]
                zr.append(code.get(r.result, None))
                label = r.result.upper() + (" ⚠" if r.exploit_found else "")
                tr.append(label)
                hr.append(f"<b>{sfc} · {t}</b><br>{r.result} · {r.severity}<br>"
                          + "<br>".join(_wrap(r.friction_notes or "", 60)))
        z.append(zr); txt.append(tr); hover.append(hr)
    fig = go.Figure(go.Heatmap(
        z=z, x=[t.replace("_", " ") for t in tasks], y=surfaces, text=txt,
        texttemplate="%{text}", textfont=dict(color=INK, size=12),
        hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
        colorscale=[[0, STATUS["pass"]], [0.5, STATUS["partial"]], [1, STATUS["fail"]]],
        zmin=0, zmax=2, showscale=False, xgap=3, ygap=3,
    ))
    fig.update_xaxes(side="top", tickfont=dict(color=INK_2), showgrid=False, showline=False)
    fig.update_yaxes(tickfont=dict(color=INK_2), autorange="reversed", showgrid=False)
    return _layout(fig, height=90 + 56 * len(surfaces), legend=False)


def severity_bars(runs: pd.DataFrame) -> go.Figure:
    counts = runs["severity"].value_counts().reindex(SEVERITY_ORDER, fill_value=0)
    counts = counts[counts > 0]
    return horizontal_bars(list(counts.index), list(counts.values),
                           [SEVERITY_COLOURS[k] for k in counts.index], fmt=",d")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines
