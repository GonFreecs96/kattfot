#!/usr/bin/env python3
"""render.py — render a chart spec JSON to SVG + PNG.

A pure, deterministic renderer: it reads a spec produced by `chart_spec.py`
(chart type, data points, governance stamp) and writes a faithful image. No data
fetching, no smoothing, no extrapolation — it draws exactly the points in the spec.

Governance-preserving rendering, which is the whole reason this is a separate,
dumb step rather than something the agent improvises:

  - **rate axes anchor at 0.** A truncated axis turns a two-point move into a
    cliff. It is the easiest way to mislead with a chart containing only true
    numbers, so the option is not exposed;
  - the **accuracy tier, source view, and generated-at** are stamped as a subtitle
    on every image, so a screenshot cannot lose its provenance;
  - **fixture-backed specs get a diagonal MOCK DATA watermark** — a synthetic
    number cannot travel as a measured one;
  - **trailing still-maturing points are drawn hollow** with a note, because a deep
    funnel stage's most recent cohorts genuinely have not settled and a solid marker
    would assert that they had;
  - a **baseline**, if given, is drawn as a labelled reference rather than implied
    by the axis.

Usage:
  render.py --spec spec.json --out-base outputs/kattfot/charts/2026-08-17-foo
  chart_spec.py ... | render.py --out-base <base>      # spec on stdin

Writes <base>.svg and <base>.png. Prints {"svg":..., "png":..., "renderer":...}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless; no display required
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

# Colorblind-friendly (Okabe–Ito), indexed by series order so colors are stable
# across re-renders of the same chart.
PALETTE = [
    "#0072B2", "#D55E00", "#009E73", "#CC79A7",
    "#E69F00", "#56B4E9", "#F0E442", "#000000",
]
GRID = "#DDDDDD"
BASELINE_C = "#888888"
#: The T3 ink from the shared theme tokens (render-chart/theme/tokens.json) —
#: the same amber every HTML surface uses for the exploratory tier.
EXPLORE_INK = "#8A5A00"
MATURING_NOTE = "hollow markers = still maturing (see Caveats)"
FAITHFUL_NOTE = "Faithful to the source artifact's Results table — see its Caveats."
EXPLORE_NOTE = (
    "EXPLORATORY (T3): ad-hoc result, no reviewed definition — not decision-grade"
)


def fmt_value(y, value_format: str) -> str:
    if y is None:
        return "—"
    if value_format == "percent":
        return f"{y * 100:.1f}%"
    if value_format == "currency":
        return f"${y:,.0f}"
    if value_format == "number":
        # Raw fractional values (an unformatted ad-hoc ratio): significant
        # digits, never rounded to an integer.
        return f"{y:.4g}"
    return f"{y:,.0f}"


def _apply_value_axis(ax, spec: dict):
    if spec.get("value_format") == "percent":
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    if spec.get("y_axis_min") is not None:
        ax.set_ylim(bottom=spec["y_axis_min"])


#: Left/right margins for figure-level text, in figure coordinates.
_STAMP_L, _STAMP_R = 0.012, 0.988


def _stamp(fig, ax, spec: dict):
    """Title, provenance subtitle, footnotes, and the fixture watermark.

    Placed in FIGURE coordinates, not axes coordinates. A funnel or ranked bar has
    long categorical labels that push the axes far to the right, and a left-aligned
    axes title then starts a third of the way across and runs off the canvas — the
    title is the one element that must never be clipped, since it is what names the
    metric.
    """
    title = spec.get("title", "")
    if title:
        fig.text(
            _STAMP_L, 0.972, title, fontsize=13, fontweight="bold",
            ha="left", va="top",
        )
    sub = spec.get("subtitle", "")
    if sub:
        fig.text(
            _STAMP_L, 0.930, sub, fontsize=8, color="#555555", ha="left", va="top",
        )
    notes = []
    if spec.get("flag_last_n"):
        notes.append(MATURING_NOTE)
    if spec.get("exploratory"):
        notes.append(EXPLORE_NOTE)
    notes.append(FAITHFUL_NOTE)
    fig.text(0.01, 0.01, "  ·  ".join(notes), fontsize=7, color="#777777", ha="left")

    if spec.get("mock"):
        fig.text(
            0.5, 0.5, "MOCK DATA", fontsize=64, color="red", alpha=0.12,
            rotation=28, ha="center", va="center", zorder=1000, fontweight="bold",
        )

    # The exploratory stamp is a loud header line, not a second diagonal — it
    # must stay legible when it coexists with the MOCK watermark (a
    # fixture-backed exploration carries both). Right-aligned on the subtitle
    # row rather than the title row: an explore title is the question, which
    # can run the full figure width.
    if spec.get("exploratory"):
        fig.text(
            _STAMP_R, 0.930, "EXPLORATORY (T3) — NOT DECISION-GRADE",
            fontsize=10, color=EXPLORE_INK, fontweight="bold",
            ha="right", va="top",
        )


def _draw_baseline(ax, spec: dict):
    b = spec.get("baseline")
    if b is None:
        return
    ax.axhline(b, color=BASELINE_C, linestyle="--", linewidth=1, zorder=2)
    ax.text(
        0.995, b, f" baseline {fmt_value(b, spec.get('value_format'))}",
        transform=ax.get_yaxis_transform(), fontsize=8, color=BASELINE_C,
        va="bottom", ha="right",
    )


# ---------------------------------------------------------------------------
# Chart types
# ---------------------------------------------------------------------------


def render_line(spec: dict, fig, ax):
    vf = spec.get("value_format")
    flag_n = spec.get("flag_last_n", 0)
    series = spec.get("series", [])
    multi = len(series) > 1

    for i, s in enumerate(series):
        color = PALETTE[i % len(PALETTE)]
        xs = [p["x"] for p in s["points"]]
        ys = [p["y"] for p in s["points"]]
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=5,
                label=s["name"] if multi else None, zorder=5)

        # Hollow the trailing still-maturing points.
        if flag_n:
            for p in s["points"][-flag_n:]:
                if p["y"] is None:
                    continue
                ax.plot(p["x"], p["y"], marker="o", markersize=7,
                        markerfacecolor="white", markeredgecolor=color,
                        markeredgewidth=1.5, zorder=6)

        last = next((p for p in reversed(s["points"]) if p["y"] is not None), None)
        if last is not None:
            ax.annotate(
                fmt_value(last["y"], vf), (last["x"], last["y"]),
                textcoords="offset points", xytext=(6, 4), fontsize=8,
                color=color, fontweight="bold",
            )

    _draw_baseline(ax, spec)
    _apply_value_axis(ax, spec)
    ax.set_xlabel(spec.get("x_label", ""), fontsize=9)
    ax.set_ylabel(spec.get("y_label", ""), fontsize=9)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    _rotate_xticks(ax)
    if multi:
        ax.legend(fontsize=8, frameon=False, loc="best")


def render_ranked_bar(spec: dict, fig, ax):
    vf = spec.get("value_format")
    bars = spec.get("bars", [])
    labels = [b["label"] for b in bars]
    values = [b["y"] if b["y"] is not None else 0 for b in bars]
    ypos = range(len(bars))

    ax.barh(list(ypos), values, color=PALETTE[0], zorder=3)
    ax.set_yticks(list(ypos))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()  # largest on top

    for y, b in zip(ypos, bars):
        ax.text(
            (b["y"] or 0), y, "  " + fmt_value(b["y"], vf),
            va="center", ha="left", fontsize=8, fontweight="bold",
        )

    base = spec.get("baseline")
    if base is not None:
        ax.axvline(base, color=BASELINE_C, linestyle="--", linewidth=1)

    if vf == "percent":
        ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    ax.set_xlim(left=0)
    if spec.get("as_of"):
        ax.set_xlabel(f"{spec.get('y_label','')}  (as of {spec['as_of']})", fontsize=9)
    ax.grid(True, axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def render_funnel(spec: dict, fig, ax):
    stages = spec.get("stages", [])
    n = len(stages)
    labels = [s["label"] for s in stages]
    rates = [s["rate"] if s["rate"] is not None else 0 for s in stages]
    ypos = list(range(n))

    ax.barh(ypos, rates, color=[PALETTE[i % len(PALETTE)] for i in range(n)], zorder=3)
    ax.set_yticks(ypos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()  # first stage on top

    for y, s in zip(ypos, stages):
        detail = ""
        if s.get("num") is not None and s.get("den"):
            detail = f"  ({s['num']:,}/{s['den']:,})"
        ax.text(
            (s["rate"] or 0), y, "  " + fmt_value(s["rate"], "percent") + detail,
            va="center", ha="left", fontsize=8, fontweight="bold",
        )

    ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    ax.set_xlim(0, 1)
    ax.set_xlabel("stage conversion rate", fontsize=9)
    ax.grid(True, axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)

    cum = spec.get("cumulative_passthrough")
    if cum is not None:
        # Figure coordinates, right-aligned on the same baseline as the provenance
        # subtitle — see _stamp() for why nothing here hangs off the axes.
        fig.text(
            _STAMP_R, 0.930,
            f"end-to-end pass-through ≈ {fmt_value(cum, 'percent')}",
            fontsize=9, color="#333333", ha="right", va="top", fontweight="bold",
        )


def _rotate_xticks(ax):
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_ha("right")
        lbl.set_fontsize(8)


RENDERERS = {
    "single_line": render_line,
    "multi_series_line": render_line,
    "ranked_bar": render_ranked_bar,
    "funnel": render_funnel,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def render(spec: dict, out_base: Path) -> dict:
    chart_type = spec.get("chart_type")
    if chart_type not in RENDERERS:
        raise SystemExit(
            f"ERROR: unknown chart_type '{chart_type}'. "
            f"Known: {sorted(RENDERERS)}"
        )

    fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
    RENDERERS[chart_type](spec, fig, ax)
    _stamp(fig, ax, spec)
    # Reserve the top strip for the title/subtitle stamp and the bottom for the
    # footnotes, so neither can collide with the plot however long the labels are.
    fig.tight_layout(rect=(0, 0.035, 1, 0.905))

    out_base.parent.mkdir(parents=True, exist_ok=True)
    svg = out_base.with_suffix(".svg")
    png = out_base.with_suffix(".png")
    fig.savefig(svg, format="svg")
    fig.savefig(png, format="png")
    plt.close(fig)
    return {
        "svg": str(svg),
        "png": str(png),
        "renderer": f"matplotlib {matplotlib.__version__}",
        "chart_type": chart_type,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--spec", help="path to a chart spec JSON (default: stdin)")
    p.add_argument("--out-base", required=True, help="output path without extension")
    args = p.parse_args()

    raw = Path(args.spec).read_text() if args.spec else sys.stdin.read()
    result = render(json.loads(raw), Path(args.out_base))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
