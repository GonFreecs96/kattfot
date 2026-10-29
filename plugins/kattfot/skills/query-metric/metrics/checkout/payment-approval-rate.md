---
name: payment_approval_rate
display_name: Payment approval rate
owner: payments-pm
version: 1
last_reviewed: 2026-10-28
module: checkout
entity: payment
relationships: [market, order]
business_rules: [per_stage_maturity, decision_is_not_approval]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: payments_approved
denominator_column: payments_decisioned
maturity_stage: payment_decisioned
value_format: percent
column_label: Approval Rate
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Payment approval rate

## Definition

Of the payments that received a decision, the share approved. The denominator is
**decisioned**, not submitted — deliberately, and it is the most important
definitional choice in the module.

## Numerator

`payments_approved` — sessions with `payment_approved_flag = TRUE`.

## Denominator

`payments_decisioned` — sessions with `payment_decisioned_flag = TRUE`.

## Value formula

```
approval_rate = SAFE_DIVIDE(SUM(payments_approved), SUM(payments_decisioned))
```

## Caveats

- **Why decisioned and not submitted.** Defining approval over submitted payments
  folds fraud stops and review-queue backlogs into the approval rate, so an
  operations problem shows up as a risk-engine problem. If you want
  approved-over-submitted, compose it: `payment_submit_to_decision_rate` ×
  `payment_approval_rate`. Keeping them separate means each moves for one reason.
- **This is the complement of `payment_decline_rate` by construction.** They sum to
  1 across the same denominator. Both are registered because the question "how many
  did we approve" and the question "how many did we turn away" get asked by different
  people and answered from different directions; a caveat on one is not read by
  someone who queried the other.
- **Market differences are real but small** — a point or two, from non-domestic
  authorization friction. Do not over-read a one-point market gap on a thin cell:
  check the denominator the rate sits on, which the artifact always shows.
- **Approval is not a quality measure.** A rising approval rate can mean risk
  controls loosened. Read it against the return and chargeback picture, not alone.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.**

## SMEs

- `payments-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Approval rate by payment method and week, last 12 weeks."

Maps to `dimensions=[payment_method]`, `time-range=last_12_weeks`.
