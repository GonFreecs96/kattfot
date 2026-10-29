---
name: order_confirmed_to_shipped_rate
display_name: Order Confirmed to Shipped rate
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
numerator_column: orders_shipped
denominator_column: orders_confirmed
maturity_stage: shipped
value_format: percent
column_label: Confirmed → Shipped
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Order Confirmed to Shipped rate

## Definition

Of the confirmed orders, the share dispatched. The first fulfilment stage, and the
first place where the funnel stops being about the customer's behavior and starts
being about the warehouse's.

## Numerator

`orders_shipped` — orders with `shipped_flag = TRUE`.

## Denominator

`orders_confirmed` — orders with `order_confirmed_flag = TRUE`.

## Value formula

```
ship_rate = SAFE_DIVIDE(SUM(orders_shipped), SUM(orders_confirmed))
```

## Caveats

- **`category` is the dominant driver.** Electronics stocks out materially more often
  than beauty or apparel. A blended trend across a period where the category mix moved
  is confounded; slice by `category` first.
- **The shortfall is two causes, and this metric cannot separate them.** An order that
  did not ship was either cancelled after confirmation or is stuck out of stock. See
  `confirmed_not_shipped_rate` for the stuck half; the difference is the cancelled
  half. The spine-backed drop-off skill splits them directly via `terminal_branch`
  (`5x Cancelled after Confirmation` vs `5 Confirmed not Shipped (Out of Stock)`).
- **Reads low on recent cohorts if the cutoff is skipped.** The `shipped` lag is 7
  days, covering warehouse pick-and-pack plus weekend spillover. Query the raw view
  without the maturity cutoff on last week's cohort and you will see a ship rate near
  zero — correctly, and misleadingly.
- **Weekend seasonality within a cohort week.** An order confirmed on a Friday has a
  different observed ship latency than one confirmed on a Monday. The weekly grain
  averages that away; the timing percentiles in the spine-backed skill do not.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** The fulfilment team's primary funnel metric.

## SMEs

- `fulfilment-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Confirmed-to-shipped rate by category over the last 12 weeks."

Maps to `dimensions=[category]`, `time-range=last_12_weeks`.
