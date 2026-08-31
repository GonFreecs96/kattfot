---
name: confirmed_to_returned_rate
display_name: Confirmed To Returned Rate (Draft)
owner: NEEDS-OWNER
module: checkout
entity: NEEDS-DECISION
accuracy_tier: T3
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: orders_returned
denominator_column: orders_confirmed
value_format: percent
allowed_dimensions:
- channel
allowed_filters:
- channel
- cohort_week
default_lookback_weeks: 8
status: draft
promoted_from:
  question: Of confirmed orders, what share is ultimately returned, by channel?
  artifact: examples/explore-artifact.md
  query_sha: eb7ee243c191e874
  register_sha: 9ad622eaf6017db3
  promoted_at: '2026-08-30'
---

# Confirmed To Returned Rate (Draft)

> **DRAFT — NOT SERVED.** Scaffolded by `kattfot:promote` from an
> exploratory artifact. `accuracy_tier: T3` keeps the governed runtime
> refusing this definition (R4) until a human review completes the
> checklist below and edits the tier to T2.

## Definition

Of confirmed orders, what share is ultimately returned, by channel?

_(Rewrite as a definition, not a question: what the number claims,
at what grain, over what population. See AUTHORING.md.)_

## Numerator

`orders_returned` — describe what qualifies a row, and at what grain.

## Denominator

`orders_confirmed` — describe the population, and why it is the honest base.

## Caveats

- **Draft from exploration.** The definition below descends from an
  ad-hoc T3 query; nothing about it has been reviewed. The caveats a
  reviewer adds here are reproduced verbatim in every artifact —
  including the unflattering ones.

## Promotion checklist

- [ ] `owner:` — a named owner, not a team alias that resolves to nobody
- [ ] `entity:` — the registered entity this measures (see `load-context entities`)
- [ ] `maturity_stage:` — which stage settles this number; without it the
      metric has NO maturity gate and reports immature cohorts as final
- [ ] allow-lists reviewed: every dimension and filter is deliberate
- [ ] caveats written, including the unflattering ones
- [ ] definition prose rewritten (Definition / Numerator / Denominator)
- [ ] `accuracy_tier: T2` and `status: live` — the LAST edit, after all
      of the above; this is the review gate

## Provenance

The exploratory query this draft descends from:

```sql
SELECT cohort_week, channel, SAFE_DIVIDE(SUM(orders_returned), SUM(orders_confirmed)) AS confirmed_to_returned_rate, SUM(orders_returned) AS numerator, SUM(orders_confirmed) AS denominator FROM `${KATTFOT_BIGQUERY_PROJECT}.analytics.cart_checkout_funnel_weekly` WHERE cohort_week >= @start_week GROUP BY cohort_week, channel ORDER BY cohort_week, channel
```
