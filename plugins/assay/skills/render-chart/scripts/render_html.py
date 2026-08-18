#!/usr/bin/env python3
"""render_html.py — render a chart spec JSON into one beautiful, interactive,
self-contained HTML file.

This is an alternative backend to visualize-metric's `render.py` (the
matplotlib SVG/PNG renderer). It consumes the **exact same chart spec** that
`chart_spec.py` emits — `single_line`, `multi_series_line`, `ranked_bar`, and
`funnel` — and produces a single `.html` you can open in any browser, hand to a
stakeholder, or attach to a message. Interactivity (tooltips, legend toggle,
hover focus) comes free from Apache ECharts.

Design goals:
  * Zero install. Pure Python standard library — no venv, no pip, no node.
    The agent runs `python3 render_html.py` and gets a file. (matplotlib needs
    a venv; this deliberately does not.)
  * Self-contained by default. The ECharts library is inlined from the vendored
    copy so the file works offline and travels as a single artifact. Pass
    `--cdn` to reference a CDN instead and keep the file ~30 KB.
  * Faithful + governed. It draws only the points in the spec — no re-query, no
    smoothing, no extrapolation. Rate axes anchor at 0 (honored via the spec's
    `y_axis_min` / `value_format`), the accuracy tier and source/generated
    stamp ride along as themed chrome, mock specs get a MOCK DATA watermark,
    and trailing still-maturing points render as hollow markers.
  * One look. All styling comes from `theme/tokens.json`, so every chart shares
    a single modern, light/dark-aware design system.

Usage:
  render_html.py --spec spec.json --out chart.html
  chart_spec.py ... | render_html.py --out chart.html        # spec on stdin
  render_html.py --spec spec.json --out chart.html \
      --caption "Cart-to-order conversion held near 28% across the window." \
      --theme light|dark|auto  --cdn

Prints {"html": <path>, "bytes": <n>, "chart_type": ..., "inlined": bool}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "theme" / "template.html"
TOKENS = SKILL_ROOT / "theme" / "tokens.json"
CHART_BUILDERS = SKILL_ROOT / "theme" / "chart-builders.js"
VENDOR_ECHARTS = SKILL_ROOT / "vendor" / "echarts.min.js"
CDN_ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"

ALLOWED_CHART_TYPES = {"single_line", "multi_series_line", "ranked_bar", "funnel"}


def _echarts_block(use_cdn: bool, echarts_path: Path | None) -> tuple[str, bool]:
    """Return (html_snippet, inlined?) for the ECharts <script>."""
    if use_cdn:
        return f'<script src="{CDN_ECHARTS}"></script>', False
    path = echarts_path or VENDOR_ECHARTS
    if not path.exists():
        raise SystemExit(
            f"ERROR: vendored ECharts not found at {path}. Re-vendor it, or pass "
            f"--cdn to reference {CDN_ECHARTS} instead."
        )
    # </script> inside the lib text would break out of our wrapper; guard it.
    js = path.read_text(encoding="utf-8").replace("</script>", "<\\/script>")
    return "<script>\n" + js + "\n</script>", True


def _load_tokens(theme_path: Path | None) -> dict:
    path = theme_path or TOKENS
    if not path.exists():
        raise SystemExit(f"ERROR: theme tokens not found at {path}.")
    return json.loads(path.read_text(encoding="utf-8"))


def _chart_builders() -> str:
    if not CHART_BUILDERS.exists():
        raise SystemExit(f"ERROR: shared chart builders not found at {CHART_BUILDERS}.")
    # Guard any </script> that could close our wrapper early.
    return CHART_BUILDERS.read_text(encoding="utf-8").replace("</script>", "<\\/script>")


def _validate(spec: dict) -> None:
    ct = spec.get("chart_type")
    if ct not in ALLOWED_CHART_TYPES:
        raise SystemExit(
            f"ERROR: chart_type '{ct}' not one of {sorted(ALLOWED_CHART_TYPES)}. "
            f"This renderer consumes chart_spec.py output."
        )
    needs = {
        "single_line": "series", "multi_series_line": "series",
        "ranked_bar": "bars", "funnel": "stages",
    }[ct]
    if not spec.get(needs):
        raise SystemExit(f"ERROR: spec for '{ct}' has no '{needs}' data to plot.")


def _inject(template: str, mapping: dict[str, str]) -> str:
    """Substitute {{KEY}} placeholders. Plain str.replace — no template engine,
    so the data (which may contain braces) can never trigger interpolation."""
    out = template
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def render(spec: dict, tokens: dict, *, caption: str | None, theme_mode: str,
           use_cdn: bool, echarts_path: Path | None, footer_note: str | None) -> str:
    _validate(spec)
    template = TEMPLATE.read_text(encoding="utf-8")
    echarts_html, _inlined = _echarts_block(use_cdn, echarts_path)

    doc_title = spec.get("title", "Chart")
    view = {
        "doc_title": doc_title,
        "caption": caption or "",
        "footer_note": footer_note
        or "Faithful to the source artifact's Results table — see its Caveats.",
        "theme_mode": theme_mode,
    }

    return _inject(template, {
        "THEME_MODE": theme_mode,
        "DOC_TITLE": doc_title,
        # Embedded in <script type="application/json"> — escape the only
        # sequence that could close the tag early.
        "SPEC_JSON": json.dumps(spec).replace("</", "<\\/"),
        "TOKENS_JSON": json.dumps(tokens).replace("</", "<\\/"),
        "VIEW_JSON": json.dumps(view).replace("</", "<\\/"),
        "CHART_BUILDERS": _chart_builders(),
        "ECHARTS": echarts_html,
    })


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--spec", help="path to a chart spec JSON (default: stdin)")
    p.add_argument("--out", required=True, help="output .html path")
    p.add_argument("--caption", default=None,
                   help="one plain-language sentence shown under the chart")
    p.add_argument("--theme", default="auto", choices=["auto", "light", "dark"],
                   help="color mode (default auto: follow the viewer's OS)")
    p.add_argument("--cdn", action="store_true",
                   help="reference ECharts from a CDN instead of inlining it "
                        "(smaller file, needs network to view)")
    p.add_argument("--echarts", default=None,
                   help="path to a specific echarts.min.js to inline")
    p.add_argument("--theme-tokens", default=None,
                   help="path to a theme tokens JSON (default: bundled)")
    p.add_argument("--footer-note", default=None,
                   help="override the faithful-to-artifact footnote")
    args = p.parse_args()

    raw = Path(args.spec).read_text() if args.spec else sys.stdin.read()
    spec = json.loads(raw)
    tokens = _load_tokens(Path(args.theme_tokens) if args.theme_tokens else None)

    html = render(
        spec, tokens,
        caption=args.caption, theme_mode=args.theme, use_cdn=args.cdn,
        echarts_path=Path(args.echarts) if args.echarts else None,
        footer_note=args.footer_note,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(json.dumps({
        "html": str(out),
        "bytes": len(html.encode("utf-8")),
        "chart_type": spec.get("chart_type"),
        "inlined": not args.cdn,
    }, indent=2))


if __name__ == "__main__":
    main()
