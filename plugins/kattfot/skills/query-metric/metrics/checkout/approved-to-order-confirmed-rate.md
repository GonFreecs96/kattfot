---
name: approved_to_order_confirmed_rate
display_name: Approved Payment to Order Confirmed rate
owner: analytics-owner
module: checkout
entity: payment
relationships: [order, checkout_session]
business_rules: [per_stage_maturity, confirmation_creates_the_order]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: orders_confirmed
denominator_column: payments_approved
maturity_stage: order_confirmed
value_format: percent
column_label: Approved → Confirmed
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Approved Payment to Order Confirmed rate

## Definition

Of the payments approved, the share that produced a confirmed order. Should be very
close to 100%; the interest is entirely in the residual.

## Numerator

`orders_confirmed` — sessions with `order_confirmed_flag = TRUE`.

## Denominator

`payments_approved` — sessions with `payment_approved_flag = TRUE`.

## Value formula

```
confirm_rate = SAFE_DIVIDE(SUM(orders_confirmed), SUM(payments_approved))
```

## Caveats

- **A near-100% metric is worth watching precisely because it is boring.** A money
  movement that never became an order is the worst kind of failure — the customer has
  been charged and has nothing. A drop of half a point here is a bigger incident than
  a five-point drop anywhere else in the ladder, so read it in absolute counts
  (which the artifact shows) rather than as a percentage.
- **This is a plumbing metric, not a behavioral one.** Nothing a customer does moves
  it. The residual is an inventory check failing after authorization, a confirmation
  write failing, or a timeout between the two.
- **Not a reconciliation.** Comparing this to a payment processor's settlement report
  will disagree, because the two count at different moments and this view has no
  authorization-reversal concept. Reconciling to the cent is a T1 exercise against the
  system of record, not something this tier claims.
- **Gated on `order_confirmed` (lag 3d).**
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** Use it to detect a plumbing failure, not to reconcile revenue.

## SMEs

- `analytics-owner` (definition)
- `checkout-pm` (product)

## Example queries

> "Approved-to-confirmed rate weekly for the last 20 weeks."

Maps to `dimensions=[]`, default lookback.
