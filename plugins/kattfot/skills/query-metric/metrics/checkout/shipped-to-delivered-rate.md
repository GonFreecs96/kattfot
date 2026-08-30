---
name: shipped_to_delivered_rate
display_name: Shipped to Delivered rate
owner: fulfilment-pm
module: checkout
entity: shipment
relationships: [market, order]
business_rules: [per_stage_maturity, delivery_starts_the_return_clock]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: orders_delivered
denominator_column: orders_shipped
maturity_stage: delivered
value_format: percent
column_label: Shipped → Delivered
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Shipped to Delivered rate

## Definition

Of the dispatched orders, the share confirmed delivered. Carrier performance, and
the gate on everything after it — because delivery starts the 30-day return clock.

## Numerator

`orders_delivered` — orders with `delivered_flag = TRUE`.

## Denominator

`orders_shipped` — orders with `shipped_flag = TRUE`.

## Value formula

```
delivery_rate = SAFE_DIVIDE(SUM(orders_delivered), SUM(orders_shipped))
```

## Caveats

- **Measures a delivery *scan*, not a customer receiving something.** A carrier
  marking an order delivered is the best signal available and is not the same event.
  Misdelivery shows up later as a return or a support contact, not here.
- **Market changes the settle point, not just the rate.** Transit takes materially
  longer in some markets, so a recent-window read will show a slower market
  "converting" worse when its tail simply has not landed. The `delivered` lag (14d)
  is set to cover the slowest market's tail, which means it is conservative for the
  fastest. This is a genuine limitation of one book-wide lag per stage — the
  per-group override mechanism in the funnel spec exists for exactly this shape of
  problem, and is currently only configured for `payment_decisioned`.
- **Split shipments count as delivered on the LAST leg.** An order arriving in two
  boxes is delivered when the second lands. Conservative on purpose — it never reports
  an order settled before all of it can be returned — at the cost of reading
  pessimistic for multi-item orders.
- **Near-ceiling metric.** At ~98%, weekly movement on a sliced cell is mostly noise.
  Check the denominator before reacting.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.**

## SMEs

- `fulfilment-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Shipped-to-delivered rate by market, last 16 weeks."

Maps to `dimensions=[market]`, `time-range=last_16_weeks`.
