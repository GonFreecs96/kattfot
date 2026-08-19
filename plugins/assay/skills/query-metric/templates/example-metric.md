---
name: example_checkout_completion_rate
display_name: Checkout Completion Rate (Example)
owner: analytics-owner
module: checkout
entity: checkout_session       # REQUIRED: the registered entity this metric measures
# relationships: [order]       # optional: other registered entities it touches
# business_rules: [per_stage_maturity]   # optional: rule ids from the entity registry
accuracy_tier: T2
source_view: analytics_sandbox.example_view
source_project: ${ASSAY_BIGQUERY_PROJECT}
time_grain: week
time_column: week_start
time_column_type: DATE
numerator_column: sessions_completed
denominator_column: sessions_started
value_format: percent
column_label: Completion Rate
allowed_dimensions: [segment]
allowed_filters: [week_start, segment]
default_lookback_weeks: 4
status: dummy
---

# Checkout Completion Rate (Example)

> The starter metric. It is wired to a tiny synthetic CSV next to this file
> (`example_view.csv`), so you can run the whole chain — query, artifact, chart,
> PDF — before provisioning anything in a warehouse. Copy it, rename it, and
> rewrite both the frontmatter and the prose. `AUTHORING.md` in this skill's
> directory is the full walkthrough.

## Definition

The share of started checkout sessions that completed, within a period, sliced by
`segment` and bucketed to ISO week.

## Numerator

`sessions_completed` — sessions that reached the confirmation step. Pre-aggregated
at the weekly × `segment` grain in the source view.

## Denominator

`sessions_started` — sessions that entered checkout in the period. Same grain as the
numerator.

## Value formula

```
completion_rate = SAFE_DIVIDE(SUM(sessions_completed), SUM(sessions_started))
```

`SAFE_DIVIDE` returns NULL on a zero denominator; the skill renders NULL as `n/a`,
not as 0% — an absent rate and a zero rate are different findings.

## Caveats

- **`status: dummy` is what routes this to the CSV.** The helper reads
  `<ASSAY_SAMPLE_DIR>/example_view.csv` and aggregates it in Python rather than
  running SQL. That path is a deliberate second opinion on the arithmetic — a
  reference implementation sharing no code with the SQL path — which is why the
  test suite uses it as a numeric oracle. It is not the offline path for real
  metrics; those run their actual SQL against sqlite (`ASSAY_ENGINE=sqlite`).
- **Sample dates are pinned to 2026-Q2.** Pass `--today 2026-05-26` when running
  this example, or refresh the CSV's dates when you adapt it.
- **Replace before use.** Rename `name`, repoint `source_view`, and rewrite this
  body. A metric that still says "Example" in a stakeholder's PDF is a governance
  failure with a very short paper trail.

## Accuracy tier

**T2 — operational.** Example only. Not for any real decision.

## SMEs

- `analytics-owner` (definition)
