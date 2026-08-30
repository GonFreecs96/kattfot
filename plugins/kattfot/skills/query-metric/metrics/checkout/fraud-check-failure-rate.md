---
name: fraud_check_failure_rate
display_name: Fraud / identity check failure rate
owner: payments-pm
module: checkout
entity: payment
relationships: [channel]
business_rules: [per_stage_maturity, decision_is_not_approval]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: fraud_failed
denominator_column: payments_submitted
maturity_stage: fraud_fail
value_format: percent
column_label: Fraud Fail Rate
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Fraud / identity check failure rate

## Definition

Of the payments submitted, the share stopped by a fraud or identity check **before
reaching a payment decision**. The denominator is submitted payments, because that
is the population the check is applied to.

## Numerator

`fraud_failed` — sessions with `fraud_failed_flag = TRUE`.

## Denominator

`payments_submitted` — sessions with `payment_submitted_flag = TRUE`.

## Value formula

```
fraud_fail_rate = SAFE_DIVIDE(SUM(fraud_failed), SUM(payments_submitted))
```

## Caveats

- **This measures the control firing, not fraud occurring.** A false positive and a
  caught fraudster are indistinguishable here. A rise could be a genuine attack, a
  rule change, or a vendor model update — and this view cannot tell you which.
  Correlate with a rule-deployment log before concluding anything about fraud
  volume.
- **`marketplace` and `social` carry most of it.** Marketplace traffic in particular
  fails these checks several times more often than `web`. An aggregate rate is a
  channel-mix statement; slice by `channel`.
- **Absent from the decision numerator and denominator.** A fraud-failed payment
  never reaches a decision, so it is not a decline and does not appear in
  `payment_approval_rate`'s denominator. The three metrics
  (`payment_submit_to_decision_rate`, `payment_approval_rate`, this one) partition
  the submitted population between them; none of them alone does.
- **The `fraud_fail` lag (3d) is one day longer than the decline lag** because a
  manual identity review can add a day. Both are hand-authored illustrative values
  in this template.
- **Rates are small — check the n.** At ~1–3% of submitted payments, a single-cell
  weekly rate sits on very few events. The artifact always shows the numerator and
  denominator; use them.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** Suitable for risk-and-product conversations about check
calibration. Not a fraud-loss figure.

## SMEs

- `payments-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Fraud check failure rate by channel over the last 12 weeks."

Maps to `dimensions=[channel]`, `time-range=last_12_weeks`.
