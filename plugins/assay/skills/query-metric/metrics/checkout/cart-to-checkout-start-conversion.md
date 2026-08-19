---
name: cart_to_checkout_start_conversion
display_name: Cart to Checkout Start conversion rate
owner: analytics-owner
module: checkout
entity: checkout_session
relationships: [channel]
business_rules: [cohort_vs_activity_time, per_stage_maturity, mix_confounds_unsliced_trends]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${ASSAY_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: checkouts_started
denominator_column: carts_created
maturity_stage: checkout_started
value_format: percent
column_label: Cart → Checkout
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Cart to Checkout Start conversion rate

## Definition

The share of created carts that reached the checkout flow, within a
`cohort_week` × dimension slice. This is the **largest single drop-off in the
whole ladder** — roughly a third of carts never get here at all — which makes it
the first place to look and the easiest place to over-interpret.

## Numerator

`checkouts_started` — carts with `checkout_started_flag = TRUE`.

## Denominator

`carts_created` — every cart in the cohort cell. Not "every visit": a cart implies
at least one item was added.

## Value formula

```
conversion_rate = SAFE_DIVIDE(SUM(checkouts_started), SUM(carts_created))
```

`SAFE_DIVIDE` returns NULL on a zero denominator; the skill renders NULL as `n/a`
rather than as 0%, because an absent rate and a zero rate are different findings.

## Caveats

- **Channel mix moves this number without anything changing.** The channels
  converting between 55% and 76% means a shift in traffic mix alone will move the
  blended rate. Slice by `channel` before concluding the checkout got worse. This
  is the metric most often misread in the module.
- **A cart is not an intent to buy.** Add-to-cart is used as a bookmark, a price
  check, and a shipping-cost probe. A "low" rate here is partly a statement about
  how the cart is used, not only about checkout friction.
- **Cohort anchor is cart creation.** A cart created on a Sunday and returned to on
  Wednesday counts in Sunday's cohort week.
- **Settles almost immediately** (stage `checkout_started`, lag 0d). Mature as soon
  as the cohort week completes, so this is the one funnel metric that can be read
  fresh — do not apply the deep-stage lag to it out of caution, or you will withhold
  six weeks of readable data for no reason.
- **Includes the retired checkout.** The `v1-legacy` cohort sits in the first weeks
  of the window and converted measurably worse. Filter `checkout_version=v2`, or
  restrict the time range past the cutover, for a like-for-like trend.
- **Synthetic in this template.** See `LINEAGE.md`, `fixture_data_is_synthetic`.

## Accuracy tier

**T2 — operational.** Decision-grade for product discussions about checkout entry
friction. Not for external reporting.

## SMEs

- `analytics-owner` (definition)
- `checkout-pm` (product)

## Example queries

> "Cart to checkout conversion by week for the last 12 weeks, split by channel."

Maps to `dimensions=[channel]`, `time-range=last_12_weeks`.

> "What's the cart-to-checkout rate on mobile in the UK?"

Maps to `dimensions=[]`, `filters=[channel=mobile_app, market=uk]`.
