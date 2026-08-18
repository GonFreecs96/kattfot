---
name: render-chart
description: >
  Render a chart spec into one beautiful, interactive, self-contained HTML file
  you can open in any browser or hand to a stakeholder. Consumes the same chart
  spec that visualize-metric's chart_spec.py emits (single trend line,
  multi-series line, ranked bar, conversion funnel), so it is a drop-in
  alternative to the matplotlib SVG/PNG renderer — but with tooltips, legend
  toggling, hover focus, light/dark theming, and a shared modern design system.
  Pure Python standard library: no venv, no pip, no node. Faithful and governed
  — draws only the points in the spec, anchors rate axes at 0, carries the
  accuracy-tier + source stamp, and watermarks mock data.
---

# Skill: render-chart

## Purpose

`visualize-metric` already turns a governed artifact into a chart spec and a
static **SVG/PNG** (matplotlib). This skill adds the modern, interactive
surface: it takes the **same chart spec** and renders **one self-contained
`.html`** — a chart a stakeholder can hover, that looks designed, in light or
dark, that travels as a single file.

```
query-metric → chart_spec.py → render-chart (beautiful interactive HTML)
                            ↘ render.py     (static SVG/PNG for the PDF path)
```

It is a **rendering backend**, not a data step. It never queries the warehouse,
never re-fetches, never smooths or extrapolates — it draws exactly the points
in the spec, the same governance posture as `render.py`.

## Why HTML (and why this is the easy path)

- **Zero install.** `render_html.py` is pure standard library. The agent runs
  `python3 render_html.py …` and gets a file — no `.venv`, no `pip install`,
  no node toolchain. (Contrast `visualize-metric`, which needs matplotlib in a
  venv.) This is the lowest-friction way for Claude Code / Cursor to produce a
  visualization on the fly.
- **Self-contained by default.** Apache ECharts is inlined from the vendored
  copy, so the file opens offline and is a single shareable artifact (~1 MB).
  Pass `--cdn` for a ~20 KB file that references ECharts from a CDN instead.
- **One look.** All styling lives in `theme/tokens.json` — palette, type,
  spacing, light/dark — so every chart shares one modern design system.

## Invocation

```
/render-chart [spec.json] [intent/caption]
```

Examples:
```
/render-chart                                   # newest spec in the charts dir
/render-chart outputs/assay/charts/2026-06-01-cart_to_order_confirmed.spec.json
/render-chart show the checkout funnel          # build the spec, then render HTML
```

## First-run setup

**None.** No dependencies to install (`scripts/requirements.txt` is empty on
purpose). ECharts is already vendored at `vendor/echarts.min.js`. If that file
is ever missing, either re-vendor it:

```
curl -sSL -o vendor/echarts.min.js \
  https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js
```

…or render with `--cdn` (no local lib needed, but the viewer needs network).

## Skill root resolution

Resolve a logical `RC_ROOT` once per session and substitute it below. **First
match wins:**

1. If `CLAUDE_PLUGIN_ROOT` is set and
   `${CLAUDE_PLUGIN_ROOT}/skills/render-chart/SKILL.md` exists → use it.
2. Else if `<workspace>/.cursor/skills/render-chart/SKILL.md` exists → use it.
3. Else fall back to the directory containing the `SKILL.md` you loaded.

## The spec contract (shared with visualize-metric)

`render_html.py` consumes the JSON `chart_spec.py` emits. Common fields:

| Field | Meaning |
|---|---|
| `chart_type` | `single_line` \| `multi_series_line` \| `ranked_bar` \| `funnel` |
| `title`, `subtitle` | header + the source/tier/generated stamp |
| `value_format` | `percent` \| `currency` \| `count` (drives axis + tooltip formatting) |
| `y_axis_min` | `0` for rates — **honest axis, no truncation** |
| `baseline` | optional steady-state reference line |
| `accuracy_tier` | `T1`/`T2`/`T3` — rendered as a themed pill |
| `mock` | `true` → diagonal MOCK DATA watermark |
| `flag_last_n` | trailing N points drawn as hollow "still maturing" markers |
| `generated_at` | stamped in the footnote |

Per type: `single_line`/`multi_series_line` carry `series: [{name, points:[{x,y,num,den}]}]`;
`ranked_bar` carries `bars: [{label,y,num,den}]` + `as_of`; `funnel` carries
`stages: [{label,rate,num,den}]` + `cumulative_passthrough`.

Because the contract is shared, **the same spec renders to both** a governed
PNG (`render.py`) and a beautiful HTML (`render_html.py`) — pick the surface,
the data is identical.

See `examples/` for one complete spec per chart type.

## Flow

### Step 1 — Get a chart spec

Either:
- **Reuse one** already written by `visualize-metric` (in the charts dir,
  `*.spec.json`), or
- **Build one** with the sibling helper (governed artifacts only):
  ```
  <python> "$VM_ROOT/scripts/chart_spec.py" --artifact <artifact.md> [--intent …] \
    > <charts_dir>/<date>-<slug>.spec.json
  ```
  (`$VM_ROOT` is the sibling `visualize-metric` skill root.)

For a one-off chart from data you already have in hand, you may hand-write a
spec that matches the contract above — but anything derived from warehouse data
must go through `chart_spec.py` so governance (tier, source, mock, axis policy)
is preserved.

### Step 2 — Render the HTML

```
python3 "$RC_ROOT/scripts/render_html.py" \
  --spec <charts_dir>/<date>-<slug>.spec.json \
  --out  <charts_dir>/<date>-<slug>.html \
  --caption "<one plain-language sentence>" \
  [--theme auto|light|dark]   [--cdn]
```

Write the `--caption` yourself: one plain sentence a non-technical stakeholder
can read, never overstating beyond the spec's data (same rule as
`visualize-metric`'s Visualization caption). Prints
`{"html": …, "bytes": …, "chart_type": …, "inlined": …}`.

### Step 3 — Report (don't paste the chart into chat)

Tell the user the `.html` path and how to open it (double-click, or
`open <file>`). If the spec is mock, remind them the chart is watermarked. If
the artifact has a `## Visualization` section, you may add a line pointing to
the interactive HTML alongside the existing PNG — do not remove the PNG or edit
any other section.

## Use as a visualize-metric backend

`visualize-metric` can call this instead of (or in addition to) `render.py`:
after it builds the spec, run `render_html.py` with that spec to also emit an
interactive `.html` next to the `.svg`/`.png`. The PNG remains the form
`export-report` bakes into the PDF; the HTML is the "view / share interactively"
form. Nothing about the governed pipeline changes — same spec, extra surface.

## Options

| Flag | Effect |
|---|---|
| `--theme auto` | follow the viewer's OS (default); `light` / `dark` to force |
| `--cdn` | reference ECharts from a CDN (~20 KB file) instead of inlining (~1 MB) |
| `--caption` | the one-sentence plain-language caption under the chart |
| `--echarts <path>` | inline a specific `echarts.min.js` |
| `--theme-tokens <path>` | use a different design-token file |
| `--footer-note <text>` | override the faithful-to-artifact footnote |

## Governance — what keeps a chart defensible (unchanged from render.py)

- **Faithful only.** Draws only the points in the spec — no re-query, no
  smoothing, no extrapolation, no invented points.
- **Honest axes.** `value_format: percent` anchors the y-axis at 0 (via
  `y_axis_min`); no truncated axis that exaggerates a small movement.
- **Carries the stamp.** Accuracy-tier pill + source/generated-at subtitle on
  every chart. Mock specs get the diagonal MOCK DATA watermark.
- **Maturity flagged.** Trailing still-maturing points render as hollow markers
  with a footnote, so a maturity artifact isn't misread as a real decline. In
  the `checkout` module the deep stages are the slow ones — an order's return
  window does not close for ~30 days, so the most recent cohorts are always
  partially unresolved.
- **Reproducible.** The HTML embeds the exact spec it was built from (in a
  `<script type="application/json">`), so a reviewer can audit the points and
  re-render — the visual analogue of the parameterized SQL.

## Internals (for maintainers)

The per-chart ECharts option-building lives in **`theme/chart-builders.js`**
(`window.AssayCharts.buildOption(spec, T, tokens)`) — the **single source of
truth** for governed chart rendering. `render_html.py` inlines it into
`template.html`, and the sibling **`render-dashboard`** skill inlines the *same*
file for every chart it draws. Add or change a chart type here once and both
surfaces get it (also update `ALLOWED_CHART_TYPES` in `render_html.py` and
`render-dashboard/scripts/render_dashboard.py`). Keep the file framework-free
(plain ES5-ish JS, no imports) so it can be inlined verbatim.

## Out of scope

- Generating numbers or fetching data — this skill only renders an existing
  spec. Build governed specs with `chart_spec.py`.
- The static SVG/PNG and the PDF — those are `visualize-metric` / `export-report`.
- A whole multi-panel dashboard (KPIs + several charts + tables) — that is the
  sibling **`render-dashboard`** skill, which composes chart specs into one HTML.
- Publishing to a wiki, chat, or drive — a deferred publishing concern.
- Visualizing a non-governed warehouse number — route it through `chart_spec.py`,
  which enforces governed provenance.

## Status in this template

This skill is complete and runnable as shipped. `visualize-metric`, `chart_spec.py`,
and `query-metric` are referenced above as the governed upstream because that is
the intended pipeline; they are part of the skill set this template builds toward
rather than files you will find in this repo yet. Until they land, hand-write a
spec against the contract above (or use `examples/`) — the renderer does not care
where a conforming spec came from, which is exactly why it could be ported first.
