#!/usr/bin/env python3
"""render_dashboard.py — render a *dashboard spec* into one beautiful,
interactive, self-contained HTML dashboard.

Where `render-chart` renders ONE governed chart, this renders a whole governed
**surface**: a header with the accuracy-tier stamp, KPI cards, any number of
charts laid out in a responsive grid, tables, and verbatim caveats — all in a
single file you can open offline, host on any static surface, or attach to a
wiki page.

The key design decision: **a dashboard's chart blocks are just `render-chart`
specs** (`single_line` / `multi_series_line` / `ranked_bar` / `funnel`). The
charts are drawn by the *same* shared option-builders that `render-chart` uses
(`render-chart/theme/chart-builders.js`), so every governance guarantee comes
for free and identically: rate axes anchored at 0, the tier pill, the MOCK DATA
watermark, hollow still-maturing markers, baselines. There is one chart renderer
in this plugin, and the dashboard is a composition layer on top of it.

Design goals (shared with render-chart):
  * Zero install. Pure Python standard library — no venv, no pip, no node.
  * Self-contained by default. ECharts is inlined from render-chart's vendored
    copy so the file works offline. Pass `--cdn` to reference a CDN instead.
  * Faithful + governed. Charts draw only the points in their specs; the whole
    dashboard spec is embedded for audit. Mock specs trip the watermark.
  * One look. All styling comes from render-chart's `theme/tokens.json`, so the
    dashboard, its charts, and standalone charts share one design system.

Usage:
  render_dashboard.py --spec study.dashboard.json --out study-dashboard.html
  render_dashboard.py --spec s.json --out d.html --theme dark --cdn

Prints {"html": <path>, "bytes": <n>, "charts": <n>, "inlined": bool}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "theme" / "dashboard.html"

# render-chart is a sibling skill; reuse its vendored ECharts, theme tokens, and
# the single source-of-truth chart-builders so there is no second chart renderer.
RC_ROOT = SKILL_ROOT.parent / "render-chart"
RC_TOKENS = RC_ROOT / "theme" / "tokens.json"
RC_BUILDERS = RC_ROOT / "theme" / "chart-builders.js"
RC_ECHARTS = RC_ROOT / "vendor" / "echarts.min.js"
CDN_ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"

ALLOWED_CHART_TYPES = {"single_line", "multi_series_line", "ranked_bar", "funnel"}


def _echarts_block(use_cdn: bool, echarts_path: Path | None) -> tuple[str, bool]:
    if use_cdn:
        return f'<script src="{CDN_ECHARTS}"></script>', False
    path = echarts_path or RC_ECHARTS
    if not path.exists():
        raise SystemExit(
            f"ERROR: vendored ECharts not found at {path}. Re-vendor it in the "
            f"render-chart skill, or pass --cdn to reference {CDN_ECHARTS}."
        )
    js = path.read_text(encoding="utf-8").replace("</script>", "<\\/script>")
    return "<script>\n" + js + "\n</script>", True


def _read_or_die(path: Path, what: str) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: {what} not found at {path}.")
    return path.read_text(encoding="utf-8")


def _chart_builders(builders_path: Path | None) -> str:
    text = _read_or_die(builders_path or RC_BUILDERS, "shared chart builders")
    return text.replace("</script>", "<\\/script>")


def _validate(spec: dict) -> int:
    """Validate the dashboard spec; return the number of chart blocks."""
    if not isinstance(spec, dict):
        raise SystemExit("ERROR: dashboard spec must be a JSON object.")
    if not spec.get("title"):
        raise SystemExit("ERROR: dashboard spec needs a 'title'.")
    n_charts = 0
    for si, sec in enumerate(spec.get("sections", [])):
        stype = sec.get("type")
        if stype == "charts":
            for bi, block in enumerate(sec.get("blocks", [])):
                cs = block.get("spec")
                if not isinstance(cs, dict):
                    raise SystemExit(
                        f"ERROR: sections[{si}].blocks[{bi}] has no chart 'spec'."
                    )
                ct = cs.get("chart_type")
                if ct not in ALLOWED_CHART_TYPES:
                    raise SystemExit(
                        f"ERROR: sections[{si}].blocks[{bi}].spec.chart_type "
                        f"'{ct}' not one of {sorted(ALLOWED_CHART_TYPES)}. Chart "
                        f"blocks must be render-chart specs (chart_spec.py output)."
                    )
                n_charts += 1
        elif stype == "table":
            if not sec.get("columns"):
                raise SystemExit(f"ERROR: sections[{si}] (table) has no 'columns'.")
        else:
            raise SystemExit(
                f"ERROR: sections[{si}].type '{stype}' must be 'charts' or 'table'."
            )
    return n_charts


def _inject(template: str, mapping: dict[str, str]) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def render(spec: dict, tokens: dict, *, theme_mode: str, use_cdn: bool,
           echarts_path: Path | None, builders_path: Path | None) -> str:
    _validate(spec)
    template = _read_or_die(TEMPLATE, "dashboard template")
    echarts_html, _inlined = _echarts_block(use_cdn, echarts_path)
    return _inject(template, {
        "THEME_MODE": theme_mode,
        "DOC_TITLE": spec.get("title", "Dashboard"),
        # Embedded in <script type="application/json"> — escape the only
        # sequence that could close the tag early.
        "DASHBOARD_JSON": json.dumps(spec).replace("</", "<\\/"),
        "TOKENS_JSON": json.dumps(tokens).replace("</", "<\\/"),
        "CHART_BUILDERS": _chart_builders(builders_path),
        "ECHARTS": echarts_html,
    })


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--spec", help="path to a dashboard spec JSON (default: stdin)")
    p.add_argument("--out", required=True, help="output .html path")
    p.add_argument("--theme", default="auto", choices=["auto", "light", "dark"],
                   help="color mode (default auto: follow the viewer's OS)")
    p.add_argument("--cdn", action="store_true",
                   help="reference ECharts from a CDN instead of inlining it")
    p.add_argument("--echarts", default=None, help="path to a specific echarts.min.js")
    p.add_argument("--chart-builders", default=None,
                   help="path to a specific chart-builders.js (default: render-chart's)")
    p.add_argument("--theme-tokens", default=None,
                   help="path to a theme tokens JSON (default: render-chart's bundled)")
    args = p.parse_args()

    raw = Path(args.spec).read_text() if args.spec else sys.stdin.read()
    spec = json.loads(raw)
    tokens = json.loads(_read_or_die(
        Path(args.theme_tokens) if args.theme_tokens else RC_TOKENS, "theme tokens"))

    # A dashboard spec may declare a default theme; an explicit --theme wins.
    theme_mode = args.theme
    if theme_mode == "auto" and spec.get("theme") in {"light", "dark", "auto"}:
        theme_mode = spec["theme"]

    html = render(
        spec, tokens,
        theme_mode=theme_mode, use_cdn=args.cdn,
        echarts_path=Path(args.echarts) if args.echarts else None,
        builders_path=Path(args.chart_builders) if args.chart_builders else None,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(json.dumps({
        "html": str(out),
        "bytes": len(html.encode("utf-8")),
        "charts": _validate(spec),
        "inlined": not args.cdn,
    }, indent=2))


if __name__ == "__main__":
    main()
