---
name: coupon_redemption_rate
display_name: Coupon redemption rate (exploratory draft)
owner: growth-pm
version: 1
last_reviewed: 2026-10-28
module: checkout
entity: checkout_session
accuracy_tier: T3
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_column: cohort_week
numerator_column: coupons_redeemed
denominator_column: carts_created
value_format: percent
aliases: [coupon redemption rate, coupon redemption, redemption rate, coupons redeemed]
allowed_dimensions: [channel]
allowed_filters: [channel]
default_lookback_weeks: 8
status: live
---

# Golden fixture — a T3 draft

The shape `kattfot:promote` scaffolds: an exploratory-lane finding written up as a
definition but not yet reviewed into T2. The golden suite pins that the governed
path refuses it under **R4** and refers to the exploratory lane, rather than
serving an unreviewed number.
