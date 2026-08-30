---
name: payment_decline_rate
display_name: Payment decline rate
owner: payments-pm
module: checkout
entity: payment
relationships: [market]
business_rules: [per_stage_maturity, decision_is_not_approval]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: payments_declined
denominator_column: payments_decisioned
maturity_stage: payment_declined
value_format: percent
column_label: Decline Rate
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Payment decline rate

## Definition

Of the payments that received a decision, the share declined. The arithmetic
complement of `payment_approval_rate` over the same denominator.

## Numerator

`payments_declined` — sessions with `payment_declined_flag = TRUE`.

## Denominator

`payments_decisioned` — sessions with `payment_decisioned_flag = TRUE`.

## Value formula

```
decline_rate = SAFE_DIVIDE(SUM(payments_declined), SUM(payments_decisioned))
```

## Caveats

- **Registered despite being 1 − approval rate.** Not redundancy for its own sake:
  the two get asked by different people for different reasons, and a caveat attached
  only to the approval metric is invisible to someone who queried declines. Both
  carry the full caveat set, which is the point.
- **A decline is not a fraud stop.** A fraud/identity failure never reaches a
  decision, so it is absent from both this numerator and this denominator. See
  `fraud_check_failure_rate`; adding the two together double-counts nothing but also
  describes nothing, since they have different denominators.
- **A decline is not a lost customer.** A declined payment is frequently retried
  successfully on another method in the same session or the next day. This view
  cannot see the retry link, so this metric overstates permanent loss. Sizing the
  retry rescue needs the session spine and a customer-token join — genuinely not
  answerable here, and better said than implied.
- **Gated on the `payment_declined` stage** (lag 2d): a decline is known when the
  decision is.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.**

## SMEs

- `payments-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Decline rate by market, last 20 weeks."

Maps to `dimensions=[market]`.
