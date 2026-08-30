---
name: render-dashboard
description: >
  Render a dashboard spec into one beautiful, interactive, self-contained HTML
  dashboard — header with the accuracy-tier stamp, KPI cards, a responsive grid
  of charts, tables, and verbatim caveats — that you can open offline, host on
  any static surface, or attach to a wiki page. A dashboard's chart blocks
  ARE render-chart specs (single_line, multi_series_line, ranked_bar, funnel),
  drawn by the same shared governed option-builders, so honest axes, the tier
  pill, the MOCK DATA watermark, and hollow still-maturing markers all come for
  free. Pure Python standard library: no venv, no pip, no node. Faithful and
  governed — draws only the points in the embedded spec and ships that spec for
  audit. Any study can emit one.
---

# Skill: render-dashboard

## Purpose

`render-chart` renders **one** governed chart. This skill renders a whole
governed **surface**: the multi-panel dashboard a PM or stakeholder actually
wants — a stamped header, KPI cards, several charts in a responsive grid,
supporting tables, and verbatim caveats — as **one self-contained `.html`** that
opens offline and travels as a single file.

```
query-metric / funnel-dropoff / segment-compare  →  governed numbers
        ↘ chart_spec.py → chart specs ┐
                                      ├─→  dashboard spec  →  render-dashboard  →  one interactive HTML
        KPIs + tables + caveats ──────┘
```

The one decision that makes this safe and reusable: **a dashboard's chart blocks
are ordinary `render-chart` specs.** The dashboard draws them with the *same*
shared option-builders `render-chart` uses
(`render-chart/theme/chart-builders.js`), so there is exactly **one** chart
renderer in this plugin. Every governance guarantee — rate axes anchored at 0,
the accuracy-tier pill, the MOCK DATA watermark, hollow still-maturing markers,
baselines — is inherited, not re-implemented.

It is a **rendering/composition backend**, not a data step. It never queries the
warehouse, never re-fetches, never smooths or extrapolates.

## Why HTML (same easy path as render-chart)

- **Zero install.** `render_dashboard.py` is pure standard library — no venv, no
  pip, no node. Run it and get a file.
- **Self-contained by default.** Apache ECharts is inlined from `render-chart`'s
  vendored copy, so the dashboard opens offline and is a single ~1 MB artifact.
  Pass `--cdn` for a ~30 KB file that loads ECharts from a CDN.
- **One look.** All styling comes from `render-chart/theme/tokens.json`, so the
  dashboard, its charts, and standalone charts share one design system.

## Invocation

```
/render-dashboard [spec.dashboard.json] [--theme auto|light|dark] [--cdn]
```

Examples:
```
/render-dashboard outputs/kattfot/<study>/artifacts/<study>.dashboard.json
/render-dashboard examples/example.dashboard.json --theme dark
```

## First-run setup

**None.** No dependencies (`scripts/requirements.txt` is empty on purpose).
ECharts, the theme tokens, and the shared chart-builders are all reused from the
sibling `render-chart` skill. If ECharts is ever missing there, re-vendor it
(see render-chart) or render with `--cdn`.

## Skill root resolution

Resolve a logical `RD_ROOT` once per session and substitute it below. **First
match wins:**

1. If `CLAUDE_PLUGIN_ROOT` is set and
   `${CLAUDE_PLUGIN_ROOT}/skills/render-dashboard/SKILL.md` exists → use it.
2. Else if `<workspace>/.cursor/skills/render-dashboard/SKILL.md` exists → use it.
3. Else fall back to the directory containing the `SKILL.md` you loaded.

`render-chart` is resolved automatically as a sibling of `render-dashboard`
(for ECharts + tokens + chart-builders); override with `--echarts`,
`--theme-tokens`, `--chart-builders` if your layout differs.

## The dashboard spec contract

A dashboard spec is a JSON object. Top-level fields:

| Field | Meaning |
|---|---|
| `title` | dashboard heading (required) |
| `subtitle` | one-line description under the title |
| `accuracy_tier` | `T1`/`T2`/`T3` → rendered as the themed pill |
| `source` | governed source string, shown in the stamp as `code` |
| `window` | cohort/time window line in the stamp |
| `published_by` | free-text identity/run line (e.g. "Live warehouse via ADC") |
| `generated_at` | date, shown in the stamp |
| `mock` | `true` → page-wide MOCK DATA watermark (also tripped if any chart block is mock) |
| `exploratory` | `true` → page-wide EXPLORATORY (T3) banner (also tripped if any chart block is exploratory) |
| `theme` | optional default `auto`/`light`/`dark` (CLI `--theme` wins) |
| `kpis` | array of `{label, value, detail, emphasis}` cards |
| `sections` | ordered array of `charts` and `table` sections |
| `caveats` | array of verbatim caveat strings (never trimmed) |
| `footer_note` | optional override of the faithful-and-reproducible footnote |

**KPI card** — `value` and `detail` are strings you format (the cards are
descriptive chrome over the same governed numbers). `emphasis` is one of
`accent` / `good` / `bad` / `none`.

**`charts` section** — `{type:"charts", title, layout, blocks}`. `layout` is
`two-col` (default) or `one-col`. Each block is
`{caption, height?, title?, spec}` where **`spec` is a render-chart spec**
(`chart_type` ∈ `single_line` | `multi_series_line` | `ranked_bar` | `funnel`).
The block heading defaults to `spec.title`; `height` overrides the panel height
(px). A block whose spec carries `flag_last_n` automatically gets the
"○ hollow = still maturing" note.

**`table` section** — `{type:"table", title, caption, columns, rows, highlight}`.
`columns` is `[{label, align, format}]` where `format` ∈ `int` / `pct`
(value already in %, one decimal) / `pct2` / `rate` (0..1 fraction) / `currency`
/ `num1` / `str`. `rows` is an array of arrays aligned to `columns`. Optional
`highlight` = `{column, when: lt|gt|lte|gte, value, class: bad|good}` conditionally
emphasizes a numeric cell.

See `examples/example.dashboard.json` for a complete, minimal spec.

## Flow

### Step 1 — Build chart specs (governed)

Build each chart with the sibling helper from a governed artifact, exactly as
for `render-chart`:
```
<python> "$VM_ROOT/scripts/chart_spec.py" --artifact <artifact.md> [--intent …] \
  > <charts_dir>/<date>-<slug>.spec.json
```
(`$VM_ROOT` is the `visualize-metric` skill root.) Anything derived from
warehouse data must pass through `chart_spec.py` so tier/source/mock/axis policy
is preserved. You may reuse specs `visualize-metric` / `render-chart` already
wrote.

### Step 2 — Compose the dashboard spec

Assemble the dashboard spec: drop each chart spec into a `charts` block, add KPI
cards and tables derived from the same governed numbers, and copy the source
artifacts' **caveats verbatim**. Set `accuracy_tier` to the lowest (most
conservative) tier among the inputs; set `mock: true` if any input is mock.
Write it next to the study's artifacts, e.g.
`outputs/kattfot/<study>/artifacts/<study>.dashboard.json`.

For a one-off composition, a small script that reads the study's governed
aggregates and emits the spec is the cleanest path.

### Step 3 — Render the HTML

```
python3 "$RD_ROOT/scripts/render_dashboard.py" \
  --spec outputs/kattfot/<study>/artifacts/<study>.dashboard.json \
  --out  outputs/kattfot/<study>/<study>-dashboard.html \
  [--theme auto|light|dark]  [--cdn]
```

Prints `{"html": …, "bytes": …, "charts": …, "inlined": …}`. The renderer
**refuses** any chart block whose `chart_type` is not an allow-listed
`render-chart` type.

### Step 4 — Report (don't paste the dashboard into chat)

Tell the user the `.html` path and how to open it (double-click, or
`open <file>`). If the spec is mock, remind them it is watermarked. Mention the
sharing options: open locally, host on any static surface (it is a single file),
or attach it to a wiki page as the interactive companion to a governed,
script-free inline summary.

## Options

| Flag | Effect |
|---|---|
| `--theme auto` | follow the viewer's OS (default); `light`/`dark` to force |
| `--cdn` | reference ECharts from a CDN (~30 KB file) instead of inlining (~1 MB) |
| `--echarts <path>` | inline a specific `echarts.min.js` |
| `--chart-builders <path>` | use a specific `chart-builders.js` (default: render-chart's) |
| `--theme-tokens <path>` | use a different design-token file |

## Governance — what keeps a dashboard defensible

- **One chart renderer.** Charts are drawn by `render-chart`'s shared
  `chart-builders.js`, so honest axes (rate axes anchored at 0), the tier pill,
  the MOCK DATA watermark, hollow still-maturing markers, and baselines behave
  identically to a standalone chart. The dashboard cannot "draw it differently."
- **Allow-listed.** Only `single_line` / `multi_series_line` / `ranked_bar` /
  `funnel` chart blocks render; anything else is refused.
- **Faithful only.** Draws only the points in the embedded spec — no re-query,
  no smoothing, no extrapolation.
- **Carries the stamp.** Accuracy-tier pill + source/window/generated stamp on
  the header. Mock specs trip the page-wide MOCK DATA watermark.
- **Caveats verbatim.** The caveats panel is never trimmed.
- **Reproducible.** The full dashboard spec is embedded in the page (in a
  `<script type="application/json">` and a visible audit `<details>`), so a
  reviewer can audit every number and re-render — the visual analogue of the
  parameterized SQL.

## Out of scope

- Generating numbers or fetching data — this skill only renders an existing
  spec. Build governed chart specs with `chart_spec.py`.
- Publishing to a wiki, chat, or drive — that is `publish`. This emits a file;
  attaching/hosting it is a downstream choice.
- New chart types — add them to `render-chart`'s `chart-builders.js` (and
  `ALLOWED_CHART_TYPES` in both renderers) so both surfaces gain them at once.

## Status in this template

This skill is complete and runnable as shipped. Of the governed upstream it composes,
`query-metric` and `chart_spec.py` now ship in this repo; `funnel-dropoff` and
`segment-compare` are still part of the skill set this template builds toward. The
renderer only needs a conforming spec, so a dashboard can already be assembled from
`query-metric` output today — start from `examples/example.dashboard.json`.
