---
name: cart_to_stale_rate
display_name: Cart abandonment (stale) rate
owner: checkout-pm
version: 1
last_reviewed: 2026-10-28
module: checkout
entity: checkout_session
relationships: [channel]
business_rules: [cohort_vs_activity_time, per_stage_maturity, mix_confounds_unsliced_trends]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: carts_stale
denominator_column: carts_created
maturity_stage: stale_pre_checkout
value_format: percent
column_label: Stale Rate
aliases: [cart abandonment rate, abandonment rate, stale cart rate, stale rate, abandoned carts, cart abandonment]
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 26
status: live
---

# Cart abandonment (stale) rate

## Definition

The share of created carts that stopped making progress and have now aged past the
30-day abandonment threshold — permanently abandoned rather than merely idle. Sizes
the top-of-funnel leak that `cart_to_checkout_start_conversion` implies but does not
quantify as a settled outcome.

## Numerator

`carts_stale` — sessions with `cart_stale_flag = TRUE`: no forward progress, and
older than 30 days.

## Denominator

`carts_created` — every cart in the cohort cell.

## Value formula

```
stale_rate = SAFE_DIVIDE(SUM(carts_stale), SUM(carts_created))
```

## Caveats

- **Definitional, not observed.** There is no abandonment *event*: the flag is a rule
  that fires on the absence of progress after 30 days. Change the threshold and this
  metric changes with no change in customer behavior whatsoever. The 30 days is a
  convention documented in the funnel spec, and it is the reason the
  `stale_pre_checkout` maturity lag is exactly 30 — the flag literally cannot fire
  sooner.
- **Covers two distinct exits.** A cart that never reached checkout and a cart that
  submitted a payment which never resolved are both stale. The first is
  `1z Stale (Pre-Checkout)`, the second `3z Stale (Post-Submit)`; they need different
  responses (re-engagement vs. clearing a payment queue) and this rate blends them. The
  spine-backed drop-off skill separates them by `terminal_branch`.
- **Not the inverse of the checkout-start rate.** A cart can fail to reach checkout and
  still not be stale — it may be recent, or it may have been explicitly abandoned by the
  customer emptying it. The two metrics answer "did it progress?" and "is it finally
  dead?".
- **Mix-sensitive.** `social` traffic goes stale far more often than `marketplace`.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** Useful for sizing re-engagement opportunity. The threshold is a
convention, so do not compare it across organisations.

## SMEs

- `checkout-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Stale cart rate by channel for the last 26 weeks."

Maps to `dimensions=[channel]`, default lookback.
