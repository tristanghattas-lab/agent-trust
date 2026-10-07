"""
Plotly figures built from the metrics API's JSON. One visual system for
every chart: fixed class colours, thin marks, recessive grid, hover on
every mark, transparent background so charts sit on the card.

Palette: the dataviz skill's validated reference categorical order (slots
1-4 pass adjacent colour-vision checks in light mode). Human traffic is a
neutral grey so the agent classes carry the colour.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#ecebe6"
AXIS = "#c3c2b7"
SURFACE = "#fcfcfb"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

CLASS_COLOURS = {
    "assistant": "#2a78d6",
    "automation": "#eb6834",
    "crawler": "#1baf7a",
    "scraper": "#eda100",
    "human": "#c3c2b7",
}
CLASS_LABELS = {
    "assistant": "AI assistant (declared)",
    "automation": "Browser automation (undeclared)",
    "crawler": "AI crawler",
    "scraper": "Scraper / other bot",
    "human": "Human",
}
SEGMENT_COLOURS = {
    "human": "#c3c2b7",
    "ai_referred": "#4a3aa7",
    "assistant": CLASS_COLOURS["assistant"],
    "automation": CLASS_COLOURS["automation"],
    "ai_channel": "#1baf7a",
}
CLASS_COLOURS["ai_channel"] = SEGMENT_COLOURS["ai_channel"]
CLASS_LABELS["ai_channel"] = "AI channel (agentic checkout)"
# Order origins: agent shades share the automation hue (assisted lighter),
# so "agent" reads as one family everywhere.
ORIGIN_COLOURS = {
    "agent_placed": "#eb6834", "agent_assisted": "#f4ae8a", "agent": "#eb6834",
    "ai_channel": "#1baf7a", "ai_referred": "#4a3aa7", "human": "#c3c2b7", "unmatched": "#e4e3dd",
}
SOURCE_COLOURS = {
    "ChatGPT": "#2a78d6", "Perplexity": "#eb6834", "Gemini": "#1baf7a",
    "Copilot": "#eda100", "Claude": "#e87ba4",
}
STATUS = {"pass": "#0ca30c", "partial": "#fab219", "fail": "#d03b3b"}
SEVERITY_ORDER = ["High", "Medium", "Needs review", "Low", "Info"]
SEVERITY_COLOURS = {
    "High": "#d03b3b", "Medium": "#ec835a", "Needs review": "#fab219",
    "Low": "#86b6ef", "Info": "#c3c2b7",
}


def _layout(fig: go.Figure, height: int = 300, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=4, r=12, t=6, b=4),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color=INK_2),
        hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(family=FONT, color=INK, size=12)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    font=dict(size=11.5, color=INK_2), title=None, traceorder="normal"),
        bargap=0.3,
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont=dict(color=MUTED, size=11), title=None,
                     ticks="", zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, showline=False, tickfont=dict(color=MUTED, size=11),
                     title=None, zeroline=False, automargin=True)
    return fig


def agent_traffic(daily: list[dict], classes: list[str] | None = None) -> go.Figure:
    df = pd.DataFrame(daily)
    fig = go.Figure()
    for key in classes or ["assistant", "automation", "crawler", "scraper"]:
        fig.add_bar(
            x=pd.to_datetime(df["date"]), y=df[key], name=CLASS_LABELS[key],
            marker_color=CLASS_COLOURS[key], marker_line=dict(color=SURFACE, width=0.5),
            hovertemplate="%{x|%a %d %b}<br>" + CLASS_LABELS[key] + ": %{y}<extra></extra>",
        )
    fig.update_layout(barmode="stack", bargap=0.18)
    return _layout(fig, height=310)


def funnel(rows: list[dict]) -> go.Figure:
    stages = [("added_to_cart_rate", "Added to cart"), ("started_checkout_rate", "Started checkout"),
              ("ordered_rate", "Ordered")]
    fig = go.Figure()
    for r in rows:
        fig.add_bar(
            x=[label for _, label in stages], y=[r[k] or 0 for k, _ in stages],
            name=r["label"], marker_color=SEGMENT_COLOURS[r["segment"]],
            marker_line=dict(color=SURFACE, width=1),
            hovertemplate=r["label"] + "<br>%{x}: %{y:.1%} of visits<extra></extra>",
        )
    fig.update_layout(barmode="group", bargroupgap=0.1)
    top = max([r[k] or 0 for r in rows for k, _ in stages] + [0])
    # Fixed range from zero: an all-zero funnel otherwise gets a -100%..100% axis.
    fig.update_yaxes(tickformat=".0%", range=[0, top * 1.15 if top else 0.1])
    return _layout(fig, height=300)


def referrals_daily(daily: list[dict], sources: list[str]) -> go.Figure:
    df = pd.DataFrame(daily)
    fig = go.Figure()
    for src in sources:
        fig.add_bar(x=pd.to_datetime(df["date"]), y=df[src], name=src,
                    marker_color=SOURCE_COLOURS.get(src, MUTED),
                    marker_line=dict(color=SURFACE, width=0.5),
                    hovertemplate="%{x|%a %d %b}<br>" + src + ": %{y}<extra></extra>")
    fig.update_layout(barmode="stack", bargap=0.18)
    return _layout(fig, height=300)


def hbars(labels: list[str], values: list[float], colours: list[str], fmt: str = ",.0f",
          prefix: str = "", height: int | None = None) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(
        y=labels, x=values, orientation="h", marker_color=colours, width=0.6,
        text=[f"{prefix}{v:{fmt}}" for v in values], textposition="outside",
        textfont=dict(color=INK_2, size=11.5), cliponaxis=False,
        hovertemplate="%{y}: " + prefix + "%{x:" + fmt + "}<extra></extra>",
    )
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=INK_2, size=11.5))
    top = max(values) if values and max(values) > 0 else 1
    fig.update_xaxes(showticklabels=False, showgrid=False, range=[0, top * 1.25])
    return _layout(fig, height=height or max(120, 38 * len(labels) + 20), legend=False)


def threat_matrix(cells: list[dict], surfaces: list[str], tasks: list[str]) -> go.Figure:
    """Agent surface x task grid. Cell colour = result, cell text = the
    result word, so status never relies on colour alone."""
    code = {"pass": 0, "partial": 1, "fail": 2}
    lookup = {(c["agent_surface"], c["task"]): c for c in cells}
    z, txt, hover = [], [], []
    for sfc in surfaces:
        zr, tr, hr = [], [], []
        for t in tasks:
            c = lookup.get((sfc, t))
            if c is None:
                zr.append(None); tr.append(""); hr.append(f"{sfc} · {t}<br>Not run")
                continue
            zr.append(code.get(c["result"]))
            tr.append(c["result"].upper() + (" ⚠" if c["exploit"] else ""))
            hr.append(f"<b>{sfc} · {t.replace('_', ' ')}</b><br>{c['result']} · {c['severity']}<br>"
                      + "<br>".join(_wrap(c.get("notes") or "", 56)))
        z.append(zr); txt.append(tr); hover.append(hr)
    fig = go.Figure(go.Heatmap(
        z=z, x=[t.replace("_", " ") for t in tasks], y=surfaces, text=txt,
        texttemplate="%{text}", textfont=dict(color=INK, size=11.5),
        hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
        colorscale=[[0, STATUS["pass"]], [0.5, STATUS["partial"]], [1, STATUS["fail"]]],
        zmin=0, zmax=2, showscale=False, xgap=4, ygap=4,
    ))
    fig.update_xaxes(side="top", tickfont=dict(color=INK_2), showgrid=False, showline=False)
    fig.update_yaxes(tickfont=dict(color=INK_2), autorange="reversed", showgrid=False)
    return _layout(fig, height=80 + 54 * len(surfaces), legend=False)


def severity_bars(by_severity: dict[str, int]) -> go.Figure:
    keys = [k for k in SEVERITY_ORDER if by_severity.get(k)]
    return hbars(keys, [by_severity[k] for k in keys], [SEVERITY_COLOURS[k] for k in keys], fmt=",d")


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
