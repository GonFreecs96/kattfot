---
name: checkout_start_to_payment_submit_rate
display_name: Checkout Start to Payment Submit rate
owner: checkout-pm
module: checkout
entity: checkout_session
relationships: [payment]
business_rules: [cohort_vs_activity_time, per_stage_maturity]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: payments_submitted
denominator_column: checkouts_started
maturity_stage: payment_submitted
value_format: percent
column_label: Checkout → Payment
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Checkout Start to Payment Submit rate

## Definition

Of the carts that entered checkout, the share that submitted a payment. This is
the **form-completion** step: everything between arriving at checkout and pressing
pay — address entry, shipping selection, seeing the total.

## Numerator

`payments_submitted` — sessions with `payment_submitted_flag = TRUE`.

## Denominator

`checkouts_started` — sessions with `checkout_started_flag = TRUE`.

## Value formula

```
submit_rate = SAFE_DIVIDE(SUM(payments_submitted), SUM(checkouts_started))
```

## Caveats

- **Slicing by `payment_method` here is close to circular.** The method is only
  known once a payment is attempted, so every session in the numerator has one and
  sessions in the denominator that abandoned do not. Grouping by it answers "among
  people who paid, which method" — a useful question, but not the drop-off question
  this metric names. The dimension is allow-listed because that first question is
  legitimate; read the result carefully.
- **Shipping cost is the classic exit here** and is not measurable from this view:
  nothing distinguishes "saw the total and left" from "was interrupted". The
  spine-backed drop-off skill can at least separate an immediate exit from a
  returning-later one via stage timing.
- **Settles within a day** (stage `payment_submitted`, lag 1d): a saved cart can be
  returned to the following day, so the freshest complete week is one week back.
- **Mobile inverts the intuition.** `mobile_app` starts checkout *less* often but
  completes this step *more* often — installed users arrive with more intent and
  stored details. A blended read hides both halves.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** The primary metric for checkout-form work.

## SMEs

- `checkout-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Checkout-to-payment rate over the last 8 weeks by market."

Maps to `dimensions=[market]`, `time-range=last_8_weeks`.
