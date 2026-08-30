---
name: cart_to_order_confirmed_end_to_end
display_name: Cart to Order Confirmed conversion (end to end)
owner: analytics-owner
module: checkout
entity: checkout_session
relationships: [order, payment]
business_rules: [cohort_vs_activity_time, per_stage_maturity, confirmation_creates_the_order, mix_confounds_unsliced_trends]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: orders_confirmed
denominator_column: carts_created
maturity_stage: order_confirmed
value_format: percent
column_label: Cart → Order
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Cart to Order Confirmed conversion (end to end)

## Definition

The share of created carts that became a confirmed order. The headline checkout
conversion number, and the reference metric for the `checkout-payment` funnel
composition.

## Numerator

`orders_confirmed` — sessions with `order_confirmed_flag = TRUE`.

## Denominator

`carts_created` — every cart in the cohort cell.

## Value formula

```
conversion_rate = SAFE_DIVIDE(SUM(orders_confirmed), SUM(carts_created))
```

## Caveats

- **Stops at confirmation, which is not the outcome.** A confirmed order can fail to
  ship, fail to arrive, or come straight back. Use
  `cart_to_return_window_complete_end_to_end` for the settled version. This metric is
  the right one for checkout work specifically because it excludes fulfilment noise —
  just do not call it "conversion" without saying where it stops.
- **The product of the stage rates will not exactly equal this.** Composing the five
  `checkout-payment` funnel stages gives a close but non-identical figure: each stage
  is gated on its *own* maturity cutoff, so the stages are computed over slightly
  different cohort windows. That divergence is a feature — it is the visible cost of
  per-stage freshness — but it means the funnel chart's cumulative pass-through and
  this metric are two different, both-correct numbers. When they diverge a lot, the
  usual cause is a stage whose cutoff is much deeper than its neighbours.
- **Mix-sensitive in two directions.** `channel` drives the top of the funnel;
  `payment_method` drives the middle. A blended trend can move with neither stage
  changing.
- **Gated on `order_confirmed` (lag 3d).**
- **Includes the retired `v1-legacy` checkout** in the early weeks of the window.
  Filter `checkout_version=v2` for a like-for-like trend.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** The standard number for checkout conversion conversations. Not
a revenue figure.

## SMEs

- `analytics-owner` (definition)
- `checkout-pm` (product)

## Example queries

> "End-to-end cart-to-order conversion by week for the last 20 weeks."

Maps to `dimensions=[]`, default lookback.

> "Show me the checkout payment funnel for the mobile app."

Composes the `checkout-payment` funnel with `filters=[channel=mobile_app]`.
