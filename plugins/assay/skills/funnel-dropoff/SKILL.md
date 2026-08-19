---
name: funnel-dropoff
description: >
  Spine-backed checkout funnel drop-off analysis. For a segment (channel /
  payment_method / market / category), returns the ordered stage funnel
  (Cart Created → Checkout Started → Payment Submitted → Payment Decisioned →
  Order Confirmed → Shipped → Delivered → Return Window Complete) with
  stage-to-stage and cumulative conversion, PLUS the terminal-branch
  decomposition — of all carts created, the share that ended in each branch
  (Payment Declined, Confirmed not Shipped, Cart Abandoned, …). This is the leak
  attribution the governed SUM/SUM metrics structurally cannot express.
  Aggregate-only, allow-listed, per-user identity on the warehouse path. T2.
version: 0.1.0
---

# Funnel drop-off

You are running the `funnel-dropoff` skill. The question is **"where does this
funnel leak?"** — and specifically the half of it that `assay:query-metric` cannot
answer.

A governed metric is a ratio of two pre-aggregated columns. It can tell you that
78% of carts that entered checkout submitted a payment; it cannot tell you where
the other 22% went, because that information does not survive the aggregation. So
this skill reads the **session-grain spine** — one row per cart, with every stage
flag and a curated terminal-branch classification — and returns two things:

1. **The ordered stage funnel** — counts, stage-to-stage conversion, and
   cumulative pass-through from the first stage.
2. **The terminal-branch decomposition** — of all carts created, the share that
   *ended* in each mutually exclusive branch.

The second is the leak attribution. Use `assay:query-metric` for a governed point
conversion, `assay:stage-timing` for latency, and this when the question is about
the funnel's shape and where carts exit.

## Two honest readings of one ladder, and why they differ

This is the thing to get right when you narrate the output, because it looks like
an inconsistency and is not.

- **The table** gives each stage its own mature cohort window. `Checkout Started`
  settles in minutes, so it is read through last week; `Return Window Complete`
  needs ~44 days, so it is read through a much older cohort. Each row's rates are
  population-consistent *within the row* (`stage_conversion` = stage ÷ previous
  over one window; `cumulative` = stage ÷ first stage over that same window), but
  the rows are **not one cohort**, so `cumulative` need not fall monotonically
  down the table. A deep stage's older, different cohort can read higher than a
  shallower stage's fresher one.
- **The chart** (`chart_spec`) is built from `deep_*` — one single fully-settled
  cohort — so it narrows monotonically from 100% and reads as one coherent funnel.

State each row's *Mature through* week in the table and point at the chart for the
single-cohort shape. Never average the two.

## Branch coherence is checked, not assumed

The branches are mutually exclusive and exhaustive, so under a single cohort cutoff
they must sum to exactly the carts created in that window. The helper computes that
(`branch_coherence`, and the top-level `branch_coherent`) and warns loudly when it
fails. Read the flag before you read the shares: if it is false, the branch table
is not a decomposition of anything and the two real causes are the branch query and
the stage query disagreeing about the cohort window, or the view's deepest-first
`CASE` leaving rows unclassified.

## Auth model

On `ASSAY_ENGINE=bigquery` the query runs as the individual human user via
Application Default Credentials, and a service-account credential is refused in
code (`_shared/identity.py`, exit 4). On the default `sqlite` engine it reads the
committed fixtures, asserts no identity — there is nothing to attribute — and
stamps `fixture_backed: true`, which becomes the MOCK DATA watermark on everything
downstream. Both facts are in the output; put the relevant one in the artifact.

Remediation on a refusal: `unset GOOGLE_APPLICATION_CREDENTIALS`, then
`gcloud auth application-default login`.

## Flow

### Step 1 — Load the spec
The helper resolves the module's funnel spec through `config/modules.yaml`
(override: `ASSAY_FUNNEL_SPEC`). Treat its `allowed_dimensions` and
`allowed_filters` as the canonical allow-lists — they are what the helper
re-validates against, and it exits 5 on a breach.

### Step 2 — Parse the question
- **Segment filters** — "on mobile" → `channel=mobile_app`; "in the UK" →
  `market=uk`. One or more `col=value`.
- **Group-by** (optional, single dimension) — "by channel", "split by category".
- **Time range** — "last 12 weeks" → `last_12_weeks`; "January to March" →
  `2026-01-05:2026-03-30`. Default: the spec's lookback (26 weeks).
- **Maturity** — default is stage-aware (`--maturity stage`). Honour "the old
  whole-funnel gate" (`--maturity flag`), "include immature cohorts"
  (`--no-mature`), or "include the retired checkout version"
  (`--include-legacy`).

### Step 3 — Refuse before querying
- Group-by not in `allowed_dimensions` → **refuse (R2)**.
- Filter column not in `allowed_filters` → **refuse (R3)**.
- A request for individual carts, or a cart id → **refuse (R5)**.
- A PII column — name, email, address, phone, date of birth → **refuse (R6)**,
  with no override, even if it somehow appears in an allow-list.

### Step 4 — Run the helper

```bash
python3 plugins/assay/skills/funnel-dropoff/scripts/funnel_dropoff.py \
  [--group-by <dim>] [--filter <col>=<value> ...] \
  [--time-range last_N_weeks | YYYY-MM-DD:YYYY-MM-DD] \
  [--maturity stage|flag|off] [--no-mature] [--include-legacy] \
  [--today YYYY-MM-DD] [--engine sqlite|bigquery]
```

Reading the committed fixtures, pass `--today 2026-08-17` — their observation
instant, recorded in `data/assay/sample/FIXTURES.md`. Without it the maturity
cutoffs are computed from the real today and withhold more of the tail every week
until the whole book looks immature.

Returns JSON: `groups[]` each with `started` (the freshest lag-0 cohort),
`deep_started` (the single settled cohort behind the chart and the branches),
`stages[]` (`count`, `stage_conversion`, `cumulative`, `start_base`,
`mature_through`, `lag_days`, `deep_count`, `deep_cumulative`), `branches[]`
(`count`, `pct_of_started`), and `branch_coherence`; plus a top-level `maturity`
block, `maturity_cutoff`, `legacy`, a render-chart `chart_spec`, both
parameterized SQL strings, and `warnings`.

### Step 5 — Write the artifact
Render the markdown below and save it to
`<cwd>/outputs/assay/{YYYY-MM-DD}-funnel-dropoff-{slug}.md`. Tell the user the
path; do not paste the whole artifact into the chat.

Then offer, without doing it: "I can hand the `chart_spec` to
`assay:render-chart` for an interactive HTML funnel."

## Output format

```markdown
---
skill: funnel-dropoff
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
group_by: {dim or none}
branch_coherent: {true|false}
---

# Funnel drop-off — {segment} ({time_range_human})

**Accuracy tier:** {tier} (operational — decision-grade for product and process
discussions, not regulator-facing)
**Source view:** `{source_project}.{source_view}` (session-grain spine)
**Window:** {start} → {end} · maturity: {mode} · {legacy.detail}

## Stage funnel

Each row is over the cohorts mature for that stage (*Mature through*); rates are
population-consistent within a row, so this column is not one cohort. See the
chart for the single-cohort funnel.

| Stage | Count | Stage conv. | Cumulative | Mature through |
| --- | --- | --- | --- | --- |
| Cart Created | … | — | 100% | {week} |
| Checkout Started | … | …% | …% | {week} |
| … | … | …% | …% | {week} |
| Return Window Complete | … | …% | …% | {week} |

## Where it leaks (terminal branch)

Over the single fully-settled cohort (through {maturity.deep_cohort_through}).
Branches sum to {branch_coherence.carts_in_deep_window} carts created — coherent.

| Terminal branch | Count | % of carts |
| --- | --- | --- |
| … | … | …% |

## Narrative
[Two or three paragraphs: the biggest single stage drop, the dominant terminal
branches, and any per-group nuance. Distinguish a leak you can act on (out of
stock) from one you cannot (a genuine decline).]

## Caveats
[The standing caveats below, plus anything segment-specific.]

## Provenance
- **Stage SQL / Branch SQL** (parameterized, from the helper)
- **Spec:** `config/{spec file}`
- **Maturity:** per-stage cutoffs, deepest settled cohort {deep_cohort_through}
```

## Standing caveats (always include)

- **The table mixes cohort windows by design.** Each stage is gated to the cohorts
  mature for it, so `cumulative` need not decrease monotonically down the table.
  The chart and the branch mix use the single deepest settled cohort. State each
  row's *Mature through*.
- **Flag-based stages are not a strict prerequisite chain.** Each is an
  "ever reached" flag, so a stage-to-stage rate can read marginally above 100%.
  Read cumulative-from-start for the shape.
- **`terminal_branch` is a curated heuristic**, evaluated deepest-stage-first. It
  is exclusive and exhaustive by construction, but the boundary cases — stale
  versus abandoned, cancelled versus out-of-stock — are the view's judgement, not
  a field a source system emitted.
- **A branch share is a share of carts, not of exits.** "4d Payment Declined at
  3.8%" means 3.8% of all carts created, not 3.8% of the ones that failed.
- **The maturity lags are hand-authored and illustrative, not measured** (the spec
  says so in machine-readable form). A real deployment regenerates them by
  measuring settle time on its own spine.
- **Fixture-backed runs are synthetic.** When `fixture_backed` is true the numbers
  come from a seeded generator; treat the shape as illustrative and the values as
  meaningless.

## Refusals

Defer to `query-metric/doctrine/accuracy-tiers.md` for the R2/R3/R5/R6 voice.
State the rule and the offending input, list what *is* allowed, propose no
workaround that violates the same rule, and write no artifact on a refusal.

## Out of scope

- Individual or cart-level rows (R5) and PII columns (R6).
- Columns outside the spec's allow-lists.
- Any causal claim about why a leak moved — that is `assay:segment-compare` for a
  description, and nothing here for a verdict.
- Writing anywhere outside `outputs/`, or modifying the spine or the spec.
