---
name: recognised_revenue
display_name: Recognised revenue (system of record)
owner: finance
version: 1
last_reviewed: 2026-10-28
module: checkout
entity: order
accuracy_tier: T1
source_view: finance.revenue_recognised_weekly
source_project: ${KATTFOT_BIGQUERY_PROJECT}
time_column: cohort_week
numerator_column: revenue_recognised
denominator_column: one
value_format: currency
aliases: [recognised revenue, recognized revenue, booked revenue, reported revenue, revenue for the quarter]
allowed_dimensions: [market]
allowed_filters: [market]
default_lookback_weeks: 13
status: live
---

# Golden fixture — a T1 metric

Not part of the bundled catalog (the catalog linter requires T2, which is the
point). The golden question suite mounts this directory as the consumer metrics
dir so a T1 ask resolves to a real definition and then refuses under **R4** in
`validate_tier`, the same way a consumer's own regulator-grade metric would.
