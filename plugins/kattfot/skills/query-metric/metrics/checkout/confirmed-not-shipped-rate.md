---
name: confirmed_not_shipped_rate
display_name: Confirmed not Shipped (out-of-stock leak) rate
owner: fulfilment-pm
version: 1
last_reviewed: 2026-10-28
module: checkout
entity: order
relationships: [shipment]
business_rules: [per_stage_maturity]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: orders_confirmed_not_shipped
denominator_column: orders_confirmed
maturity_stage: shipped
value_format: percent
column_label: Confirmed not Shipped
aliases: [confirmed not shipped, out of stock leak, out of stock leak rate, confirmed but never shipped, unshipped order rate]
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Confirmed not Shipped (out-of-stock leak) rate

## Definition

Of the confirmed orders, the share that has been paid for and **still has not
shipped**. The single most controllable leak in the ladder: the customer has done
everything, the money has moved, and the goods have not left.

## Numerator

`orders_confirmed_not_shipped` — orders with `order_confirmed_flag = TRUE` and
`shipped_flag = FALSE`.

## Denominator

`orders_confirmed` — orders with `order_confirmed_flag = TRUE`.

## Value formula

```
leak_rate = SAFE_DIVIDE(SUM(orders_confirmed_not_shipped), SUM(orders_confirmed))
```

## Caveats

- **This metric is defined by an ABSENCE, which makes the maturity cutoff
  load-bearing rather than merely advisable.** An order confirmed yesterday has not
  shipped yet and is not a leak. Read this without the `shipped` cutoff and every
  recent cohort looks like a total stock-out. The 7-day lag is not conservatism here;
  it is what makes the number mean anything at all. This is the metric to point at
  when someone asks why the maturity machinery exists.
- **Not the same as the ship-rate complement.** `1 − order_confirmed_to_shipped_rate`
  also includes orders cancelled after confirmation, which are resolved rather than
  stuck. This metric is the *stuck* half only, which is the actionable one.
- **Still-stuck, not ever-stuck.** An order that was stuck for a week and then shipped
  is not in this numerator once it ships, so the metric understates how many orders
  *experienced* a stock-out delay. Measuring that needs the timing percentiles
  (`time_to_ship_days`), not a rate.
- **Electronics carries most of it.** Slice by `category`.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** Directly actionable for inventory and fulfilment planning.

## SMEs

- `fulfilment-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Which categories have the worst confirmed-not-shipped rate in the last 8 weeks?"

Maps to `dimensions=[category]`, `time-range=last_8_weeks`, intent `compare`.
