---
name: stage-timing
description: >
  Spine-backed percentile stage timing for the checkout funnel. For a segment
  (channel / payment_method / market / category), returns latency distributions —
  p50 / p90 / p99 and mean, with n — for each stage transition: cart to checkout
  start, checkout to payment, payment to decision, confirmation to ship, ship to
  deliver, delivery to return-window end. This is the percentile capability the
  governed SUM/SUM metrics structurally cannot express. Aggregate-only,
  allow-listed, per-user identity on the warehouse path. T2.
version: 0.1.0
---

# Stage timing

You are running the `stage-timing` skill. The question is **"how long does each
stage take for this segment?"**

`kattfot:query-metric`'s contract is `SUM(numerator) / SUM(denominator)`. A median is
not a ratio of two sums, so no metric definition can express one — this is a
structural gap, not a missing metric. This skill fills it by reading the
session-grain spine and computing per-transition latency distributions.

Pairs with `kattfot:funnel-dropoff` (where carts exit) and
`kattfot:segment-compare` (A vs B on both).

## Percentiles are an adapter capability, not translated SQL

The helper never writes `APPROX_QUANTILES`. It calls
`adapter.percentiles(...)`, which:

- on **BigQuery** pushes `APPROX_QUANTILES(x, 100)[OFFSET(p)]` into the warehouse
  — approximate at scale, no row transfer;
- on **sqlite** pulls the latency column and computes the percentile in Python —
  exact, because there is no percentile function in sqlite at all.

Both use the same *nearest-rank* definition, so the two engines agree on what is
being computed. `percentile_basis` in the output records which happened, and the
artifact must repeat it: "approximate, pushed down" and "exact, computed
client-side" are a difference a reader is entitled to see. `_shared/dialect.py`
refuses `APPROX_QUANTILES` outright rather than faking it in SQL, because a
correlated-subquery rank walk would be slow and subtly wrong on ties.

## The population per transition is not the funnel

Each transition's statistics are over the rows that reached **both** endpoints. A
row with either timestamp NULL contributes nothing — not a zero. So:

- `n` differs per transition, and comparing `n` down the table shows the surviving
  population;
- `n` is **not** the funnel start count, and quoting a p50 without its `n` hides
  that a deep transition's number may rest on a small tail.

Each transition is also gated to the cohorts mature for its **arrival** stage, not
its departure. That is the load-bearing choice: a recent cohort whose slow members
have not yet arrived would bias the percentiles *fast*, which reads as an
improvement.

## Auth model

Identical to the other spine skills: individual human identity via ADC on
`KATTFOT_ENGINE=bigquery` (service accounts refused in code, exit 4); the committed
fixtures with `fixture_backed: true` on the default `sqlite` engine.

## Flow

### Step 1 — Load the spec
The helper resolves `transitions`, the allow-lists, and the maturity table from the
module's funnel spec via `config/modules.yaml`. The spec's `kpis:` block names the
transitions that are pinned as citable KPIs; they come back under `kpis` keyed by
transition, so a named KPI and an ad-hoc read of the same transition cannot report
different things.

### Step 2 — Parse the question
- **Segment filters** — `col=value`, e.g. `payment_method=bank_transfer_review`.
- **Group-by** (optional, comma-separated) — "by market", "by market and
  category". More than one dimension is supported: the result is one row per group
  tuple with each dimension under its own key.
- **Transitions** (optional) — map phrasing to keys: "time to ship" →
  `confirm_to_ship`; "payment to decision" → `payment_to_decision`; "delivery
  time" → `ship_to_deliver`. Default: all of them.
- **Percentiles** (optional) — default `50,90,99`.
- **Time range / maturity** — as in `funnel-dropoff`.

If the user names a KPI (`time_to_ship_days`), resolve it to its transition through
the spec's `kpis:` block and say which KPI you read.

### Step 3 — Refuse before querying
Group-by not allow-listed → **R2**. Filter column not allow-listed → **R3**.
Unknown transition key → refuse, listing the available keys. Individual-level
request → **R5**. PII column → **R6**, no override.

### Step 4 — Run the helper

```bash
python3 plugins/kattfot/skills/stage-timing/scripts/stage_timing.py \
  [--group-by <dim>[,<dim>]] [--filter <col>=<value> ...] \
  [--transitions confirm_to_ship,payment_to_decision] \
  [--percentiles 50,90,99] \
  [--time-range last_N_weeks | YYYY-MM-DD:YYYY-MM-DD] \
  [--maturity stage|flag|off] [--no-mature] [--include-legacy] \
  [--today YYYY-MM-DD] [--engine sqlite|bigquery]
```

Against the committed fixtures pass `--today 2026-08-17` (their observation
instant, per `data/kattfot/sample/FIXTURES.md`).

Returns JSON: `groups[]` each with `group` (the dimension values) and
`transitions[]` carrying `n`, `mean`, `percentiles{p50,p90,p99}`, `unit`,
`maturity_stage`, `lag_days`, and `mature_through`; plus top-level
`percentile_basis`, `maturity`, `maturity_cutoff`, `legacy`, `kpis`, the value
expressions used, and `warnings`.

### Step 5 — Write the artifact
Render the markdown below and save it to
`<cwd>/outputs/kattfot/{YYYY-MM-DD}-stage-timing-{slug}.md`. Tell the user the path.

**Keep the unit visible in every cell or column header.** Some transitions are in
minutes and some in days; a table of bare numbers spanning both is a
misreading waiting to happen. Where a KPI declares a `report_unit` that differs
from the transition's own (time to ship is measured in minutes, reported in days),
convert and say you converted.

## Output format

```markdown
---
skill: stage-timing
accuracy_tier: {tier}
source_view: {source_project}.{source_view}
engine: {engine}
fixture_backed: {true|false}
generated_at: {iso}
time_range_start: {start}
time_range_end: {end}
maturity_mode: {stage|flag|off}
maturity_cutoff: {maturity.deep_cohort_through}
segment_filters: {filters}
group_by: {dims or none}
percentiles: {pcts}
percentile_basis: {basis}
---

# Stage timing — {segment} ({time_range_human})

**Accuracy tier:** {tier} (operational — not regulator-facing)
**Source view:** `{source_project}.{source_view}` (session-grain spine)
**Window:** {start} → {end} · maturity: {mode} · {legacy.detail}
**Percentiles:** {basis}

## Latency by transition{, per <dim>}

Each transition is over the rows that reached both endpoints, gated to the cohorts
mature for its arrival stage (*Mature through*).

| {dim?} | Transition | n | p50 | p90 | p99 | mean | unit | Mature through |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| … | Payment → Decision | … | … | … | … | … | min | {week} |
| … | Ship → Deliver | … | … | … | … | … | day | {week} |

## Narrative
[Two or three paragraphs: the slow stage, how the segment changes it, and which
groups are outliers. Lead with p50; mention the mean only alongside percentiles,
because a lognormal tail drags it somewhere no cart actually is.]

## Caveats
[The standing caveats below.]

## Provenance
- **Value expressions** (parameterized, from the helper) + the WHERE clause
- **Percentile basis:** {basis}
- **Spec:** `config/{spec file}`
```

## Standing caveats (always include)

- **The population differs per transition.** Each is over the rows that reached
  both endpoints, so `n` is not the funnel start count. Cite `n` beside every
  percentile.
- **Means are dragged by long tails.** These distributions are lognormal-ish and
  p99 can be orders of magnitude above p50. Lead with p50.
- **Percentile basis differs by engine.** BigQuery's `APPROX_QUANTILES` is
  approximate at scale; the offline path is exact nearest-rank. Same definition,
  different precision — state which one produced the number.
- **Arrival-stage gating is deliberate.** A transition is withheld until its
  arrival stage settles, so a recent cohort's slow members are not silently
  excluded in a way that would make timing look better than it is.
- **A zero `n` is usually correct withholding**, not missing data: it normally
  means the window holds no cohort mature for that transition's arrival stage.
- **The maturity lags are hand-authored and illustrative, not measured.**
- **Fixture-backed runs are synthetic** when `fixture_backed` is true.

## Refusals

Defer to `query-metric/doctrine/accuracy-tiers.md` for the R2/R3/R5/R6 voice.
State the rule and the offending input, give the allow-list, write no artifact.

## Out of scope

- Individual or cart-level rows (R5) and PII columns (R6).
- Transitions or columns outside the spec.
- Percentiles of anything that is not a declared transition — add it to the spec
  rather than hand-rolling a timestamp difference here.
- Timing *regression* monitoring — that is `kattfot:funnel-pulse` for conversion and
  `kattfot:segment-compare` for a two-sided latency read.
