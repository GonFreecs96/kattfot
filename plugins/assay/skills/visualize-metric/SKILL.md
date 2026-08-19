---
name: visualize-metric
description: >
  Render a governed query-metric artifact into a faithful, shareable chart (SVG +
  PNG) and inject a Visualization section back into the artifact. Infers the chart
  type from the result shape and the analytical intent in the prompt — trend lines,
  ranked bars, or a composed conversion funnel. Charts are derived only from the
  artifact's Results table: never re-queries, never smooths or extrapolates, rate
  axes anchor at 0, and the accuracy tier, source, fixture watermark, and
  still-maturing markers all travel with the image.
---

# Skill: visualize-metric

## Purpose

Turn a governed artifact into a chart that carries its governance with it.

The design constraint that shapes everything here: **the chart is derived from the
artifact, not from the warehouse.** It reads the rendered Results table — the table a
human actually saw — and draws exactly those points. It cannot re-query, cannot fill
a gap, cannot smooth a spike. That is a deliberate limitation, because a
visualization able to reach past its artifact would break the one property that makes
the artifact worth keeping: that the number, the query, the caveats, and the picture
all describe the same thing.

```
query-metric → artifact.md → chart_spec.py → spec.json → render.py       (SVG + PNG)
                                                       ↘ render-chart    (interactive HTML)
                                                       ↘ export-report   (PDF, embeds the PNG)
```

## Invocation

```
/visualize-metric [chart the last query | show the checkout funnel | compare channels]
```

Examples:
```
/visualize-metric                                  # chart the newest artifact
/visualize-metric outputs/assay/2026-08-17-payment-approval-rate.md
/visualize-metric compare the return rate across categories
/visualize-metric show the checkout funnel
```

## First-run setup

```
python3 -m venv .venv
.venv/bin/pip install -r "$VM_ROOT/scripts/requirements.txt"
```

`chart_spec.py` needs only `pyyaml`. `render.py` needs `matplotlib`. If matplotlib is
unavailable, the sibling **`render-chart`** skill consumes the same spec and is pure
standard library — so a chart is always reachable.

## Skill root resolution

Resolve `$VM_ROOT` once per session, first match wins:

1. `${CLAUDE_PLUGIN_ROOT}/skills/visualize-metric/SKILL.md`
2. `<workspace>/.cursor/skills/visualize-metric/SKILL.md`
3. The directory containing the `SKILL.md` you loaded.

## Flow

### Step 1 — Find the artifact

Explicit path if given. Otherwise the newest `*.md` in `<ASSAY_OUTPUTS_DIR>` (default
`outputs/assay/`).

The artifact **must** have frontmatter carrying `metric`, `accuracy_tier`,
`source_view`, and `generated_at`. Missing any of them, refuse: only governed
artifacts get visualized. A chart is the form a number travels in most easily and is
questioned least, so an ungoverned one becoming a chart is exactly backwards.

### Step 2 — Extract the analytical intent

From the user's words, not from the data:

| Phrasing | `--intent` |
|---|---|
| "over time", "trend", "by week" | `trend` |
| "compare", "which is best/worst", "rank" | `compare` |
| "break down", "split by" | `breakdown` |
| "share of", "proportion" | `share` |

Omit `--intent` when the prompt gives no signal. The shape rules below then decide,
deterministically.

### Step 3 — Decide whether to flag still-maturing points

Read the artifact's `maturity` block and its warnings. If the metric's stage settles
slowly — anything downstream of shipping, in the bundled checkout module — the newest
one or two cohort weeks in the table may be **partially** settled even after the
cutoff. Pass `--flag-last-n 1` (or 2) so those points render **hollow** with a
footnote.

This matters more than it sounds. A deep-stage metric's most recent points are
biased in a *specific direction* — downward, because a negative outcome (a return) is
observable as soon as it happens while a positive one (a keep) is only confirmed when
the window closes. A solid marker asserts a settled value. A hollow one says "not
yet", which is the truth.

### Step 4 — Build the spec

```
<python> "$VM_ROOT/scripts/chart_spec.py" \
  --artifact <path> \
  [--intent trend|compare|breakdown|share] \
  [--chart-type single_line|multi_series_line|ranked_bar] \
  [--baseline 0.35] \
  [--flag-last-n 1] \
  --out <charts_dir>/<date>-<slug>.spec.json
```

The spec is written as **provenance**, not as a temp file: it records the exact points
plotted, the chart type, the rationale for that type, and the governance stamp. A
reviewer can diff it against the artifact's table without rerunning anything.

#### Chart-type resolution

Deterministic given (intent × shape). The chosen rationale string is embedded in the
spec, so the inference can always explain itself.

| Condition | Chart |
|---|---|
| `--chart-type` given | that type |
| intent `compare` and ≥1 dimension | `ranked_bar` of the latest period |
| 0 dimensions, >1 time point | `single_line` |
| 0 dimensions, 1 time point | `ranked_bar` (one labelled bar) |
| ≥1 dimension, >1 time point, ≤6 series | `multi_series_line` |
| ≥1 dimension, >1 time point, >6 series | `ranked_bar` (spaghetti guard) |
| ≥1 dimension, 1 time point | `ranked_bar` |

### Step 5 — Render

```
<python> "$VM_ROOT/scripts/render.py" \
  --spec <charts_dir>/<date>-<slug>.spec.json \
  --out-base <charts_dir>/<date>-<slug>
```

Writes `.svg` and `.png`. For the interactive version, hand the **same spec** to
`render-chart` — the spec is the contract and the renderers are interchangeable.

### Step 6 — Inject the Visualization section

Insert into the artifact, immediately after `## Results`:

```markdown
## Visualization

![{title}]({relative_png_path})

**Chart type:** {chart_type} — {rationale}
**Spec:** `{relative_spec_path}` (the exact points plotted)
**Renderer:** {renderer}

> Drawn only from the Results table above. No re-query, no smoothing, no
> extrapolation. Rate axes anchor at 0.
> _(when flag_last_n > 0)_ Hollow markers mark cohort weeks that are still
> maturing — see Caveats.
> _(when fixture-backed)_ The MOCK DATA watermark is not decorative: these numbers
> come from committed synthetic fixtures.
```

Do not touch anything else in the artifact. The manifest fingerprints the metric
definition, the query, and the result — not the prose — so adding this section does not
invalidate the receipt, but rewriting the Results table would make the chart and the
receipt disagree.

### Step 7 — Report

Give the user the PNG path and the artifact path. Offer, without running:
"want this as a PDF? `/export-report`" or "want it interactive? `/render-chart`".

## Funnel mode

A funnel is a **named composition of governed metrics**, defined in
`config/funnels.yaml` — not a hard-coded pipeline. Each entry lists ordered stage
metrics plus an `end_to_end` reference metric.

1. Read `config/funnels.yaml` (consumer copy first, then `$ASSAY_FUNNELS_CONFIG`,
   then the plugin-bundled default).
2. Run `query-metric` once per stage metric, over a **shared time range and filter
   set**, writing each JSON to a temp file.
3. Compose:

```
<python> "$VM_ROOT/scripts/chart_spec.py" \
  --funnel checkout \
  --stage-json stage1.json stage2.json ... \
  --out <charts_dir>/<date>-checkout-funnel.spec.json
```

4. Render as above.

Each stage rate is `sum(numerator) / sum(denominator)` across the window — summed,
not averaged, because averaging weekly rates weights a thin week like a fat one.

### The pass-through gap is a feature

The product of the stage rates will **not** exactly equal the named `end_to_end`
metric, and the spec records both plus the span of maturity cutoffs involved
(`maturity_cutoff_span`).

The reason: every stage carries its **own** maturity cutoff, so the stages are
computed over slightly different cohort windows. That is the visible cost of
per-stage freshness, and it is the right trade — the alternative is gating every
stage on the slowest one and discarding six weeks of readable early-funnel data.

Say this when the two numbers diverge noticeably, and name the likely cause: a stage
whose cutoff is far deeper than its neighbours. Do not quietly present one number and
hide the other.

## Faithfulness rules

Non-negotiable, in the sense that breaking one makes the chart worse than no chart:

- **Only points from the artifact's Results table.** No re-query, no interpolation
  across a gap, no derived series.
- **NULL is not zero.** A `—` cell is a missing rate, and plotting it as 0% invents a
  data point that reads as a catastrophe.
- **Rate axes anchor at 0.** Not configurable. A truncated axis is the easiest way to
  mislead with entirely true numbers.
- **No smoothing, no trend line, no forecast.** If a forecast is wanted, that is a
  different skill with intervals and a stated model — not a line drawn through
  history.
- **Every image is stamped** with tier, source, and generated-at, so a screenshot
  cannot shed its provenance.
- **Fixture-backed data is watermarked.** Checked from `fixture_backed`, falling back
  to `source_mode` — erring toward watermarking, because a spurious watermark is an
  annoyance and a missing one is a synthetic number passing as measured.
- **Still-maturing points are hollow**, with the note, whenever `--flag-last-n` > 0.

## Out of scope

- Querying anything. This skill has no warehouse access and needs none.
- Editing the Results table, or any part of the artifact other than adding
  `## Visualization`.
- Forecasting, smoothing, or annotation the artifact does not support.
- Multi-metric charts outside a `funnels.yaml` composition.

## Status in this template

Complete and runnable. With the default `ASSAY_ENGINE=sqlite` the whole chain works
offline against the committed fixtures — `scripts/demo.py` runs exactly this path end
to end and produces a chart plus a PDF with no credentials.

The bundled `config/funnels.yaml` defines two funnels over the `checkout` module: the
full eight-stage ladder to Return Window Complete, and a four-stage payment leg. The
second exists to make the point that a funnel is a named composition rather than a
fixed pipeline.
