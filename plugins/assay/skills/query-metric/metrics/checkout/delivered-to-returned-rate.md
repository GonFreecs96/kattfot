---
name: delivered_to_returned_rate
display_name: Return rate (delivered orders sent back)
owner: fulfilment-pm
module: checkout
entity: shipment
relationships: [order]
business_rules: [per_stage_maturity, return_window_is_thirty_days]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${ASSAY_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: orders_returned
denominator_column: orders_delivered
maturity_stage: returned
value_format: percent
column_label: Return Rate
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 26
status: live
---

# Return rate (delivered orders sent back)

## Definition

Of the delivered orders, the share for which a return was initiated inside the
30-day window.

## Numerator

`orders_returned` — orders with `returned_flag = TRUE`.

## Denominator

`orders_delivered` — orders with `delivered_flag = TRUE`.

## Value formula

```
return_rate = SAFE_DIVIDE(SUM(orders_returned), SUM(orders_delivered))
```

## Caveats

- **Return initiated, not return received.** The numerator fires when the customer
  starts a return, not when the goods arrive back or a refund is issued. A started
  return that never ships back is counted here and should not be counted as lost
  revenue — the two are different questions and this view answers only the first.
- **Gated on the `returned` stage (44d), the same lag as the kept rate.** A cohort
  whose window is still open has accumulated only part of its returns, so an immature
  read is biased **downward** — it looks like returns improved. This is the mirror
  image of the bias on `delivered_to_return_window_complete_rate` and, being the
  flattering direction, is the more dangerous of the two to read carelessly.
- **This and the kept rate do not sum to 1 on an immature cohort**, even though they
  do once settled. If they disagree, the cohort is not mature — which makes the pair a
  useful consistency check on whether a cutoff was applied.
- **Category dominates.** Apparel around 18%, home goods around 5%. A blended return
  rate is close to a restatement of the category mix, so comparing two periods without
  slicing mostly compares what was on promotion.
- **Free-return policy is a confounder this view cannot see.** A policy change moves
  this metric with no change in product quality or fit. Correlate with the policy
  timeline before reading a trend as a product signal.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** Not a returns-cost figure.

## SMEs

- `fulfilment-pm` (returns operations)
- `analytics-owner` (definition)

## Example queries

> "Return rate by category, last 26 weeks."

Maps to `dimensions=[category]`, default lookback.
