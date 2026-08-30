---
name: delivered_to_return_window_complete_rate
display_name: Delivered to Return Window Complete rate
owner: analytics-owner
module: checkout
entity: shipment
relationships: [order]
business_rules: [per_stage_maturity, return_window_is_thirty_days, delivery_starts_the_return_clock]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: return_windows_complete
denominator_column: orders_delivered
maturity_stage: return_window_complete
value_format: percent
column_label: Delivered → Kept
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 26
status: live
---

# Delivered to Return Window Complete rate

## Definition

Of the delivered orders, the share whose 30-day return window closed **without a
return** — i.e. the customer kept it. The final stage of the ladder, and the only one
that describes a settled outcome.

## Numerator

`return_windows_complete` — orders with `return_window_complete_flag = TRUE`:
delivered, the window has closed, and no return was initiated.

## Denominator

`orders_delivered` — orders with `delivered_flag = TRUE`.

## Value formula

```
kept_rate = SAFE_DIVIDE(SUM(return_windows_complete), SUM(orders_delivered))
```

## Caveats

- **This is the metric the whole maturity apparatus exists for.** The numerator is
  unknowable until 30 days after delivery, which is roughly 44 days after the cart was
  created. Read the raw view on the last six weeks and this rate reads near zero —
  correctly, and it will be misread as a collapse in satisfaction. The 44-day
  `return_window_complete` cutoff is not caution; it is the difference between a
  number and an artefact of when you asked.
- **Asymmetric observability, and it biases the wrong way.** A *return* becomes visible
  as soon as it is initiated, part-way through the window; a *keep* is only confirmed
  once the window shuts. So a partially-mature cohort has visible returns and invisible
  keeps — the rate is biased **downward**, not merely noisy. Averaging across a window
  that includes immature cohorts does not cancel this out.
- **Not a satisfaction measure.** A customer who kept an item they were unhappy with
  because returning it was inconvenient is in the numerator. Read alongside
  `delivered_to_returned_rate` and whatever direct feedback exists.
- **`category` moves this by nearly 15 points.** Apparel returns roughly three times as
  often as home goods, so the blended rate is substantially a statement about category
  mix. Slice before comparing periods.
- **Long default lookback (26 weeks)** because the mature window is already six weeks
  behind; a shorter one would leave too few settled cohorts to read a trend from.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** Not a revenue-recognition figure; that is T1 and belongs to
finance.

## SMEs

- `analytics-owner` (definition)
- `fulfilment-pm` (returns operations)

## Example queries

> "Kept rate by category over the last 26 weeks."

Maps to `dimensions=[category]`, default lookback.
