---
name: cart_to_return_window_complete_end_to_end
display_name: Cart to Return Window Complete conversion (settled, end to end)
owner: analytics-owner
version: 1
last_reviewed: 2026-10-28
module: checkout
entity: checkout_session
relationships: [order, shipment]
business_rules: [cohort_vs_activity_time, per_stage_maturity, return_window_is_thirty_days, mix_confounds_unsliced_trends]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: return_windows_complete
denominator_column: carts_created
maturity_stage: return_window_complete
value_format: percent
column_label: Cart → Settled
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 26
status: live
---

# Cart to Return Window Complete conversion (settled, end to end)

## Definition

The share of created carts that became an order the customer **kept** — delivered,
return window closed, not returned. The only genuinely settled conversion number in
the module, and the reference metric for the full `checkout` funnel composition.

## Numerator

`return_windows_complete` — sessions with `return_window_complete_flag = TRUE`.

## Denominator

`carts_created` — every cart in the cohort cell.

## Value formula

```
settled_conversion = SAFE_DIVIDE(SUM(return_windows_complete), SUM(carts_created))
```

## Caveats

- **Six weeks behind, structurally.** The numerator needs delivery plus a 30-day
  return window, so the newest readable cohort is roughly 44 days old. That is not a
  pipeline delay to be fixed — it is how long it takes to find out. Anyone who wants a
  fresher end-to-end number wants `cart_to_order_confirmed_end_to_end`, which stops at
  confirmation and says so.
- **Reads catastrophically low without the cutoff.** On the most recent cohorts the
  numerator is genuinely zero: nothing has settled. If a chart of this metric appears
  to fall off a cliff at the right-hand edge, the cutoff was not applied. The
  still-maturing markers in the rendered chart exist to make that visible rather than
  plausible.
- **The lower bound on a satisfying answer, not the answer.** A returned order is not
  necessarily lost revenue (it may be exchanged), and a kept order is not necessarily a
  happy customer. This metric is deliberately the strictest reading available.
- **Mix-sensitive at both ends.** `channel` drives cart-to-checkout; `category` drives
  return rate by nearly three-fold. A blended movement can be entirely mix.
- **Composed stage rates will not multiply to this exactly** — see the same caveat on
  `cart_to_order_confirmed_end_to_end`. Eight stages each gated on their own cutoff
  compound the divergence further than five do.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** The most defensible conversion figure here, and still not a
finance number.

## SMEs

- `analytics-owner` (definition)
- `checkout-pm`, `fulfilment-pm` (product)

## Example queries

> "Settled cart-to-kept conversion for the last 26 weeks."

Maps to `dimensions=[]`, default lookback.

> "Draw the whole checkout funnel."

Composes the `checkout` funnel from `config/funnels.yaml`, with this as
`end_to_end`.
