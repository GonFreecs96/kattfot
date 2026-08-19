---
name: payment_submit_to_decision_rate
display_name: Payment Submit to Decision rate
owner: payments-pm
module: checkout
entity: payment
relationships: [checkout_session]
business_rules: [per_stage_maturity, decision_is_not_approval]
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
source_project: ${ASSAY_BIGQUERY_PROJECT}
time_grain: week
time_column: cohort_week
time_column_type: DATE
numerator_column: payments_decisioned
denominator_column: payments_submitted
maturity_stage: payment_decisioned
value_format: percent
column_label: Submit → Decision
allowed_dimensions: [channel, payment_method, market, category, checkout_version]
allowed_filters: [cohort_week, channel, payment_method, market, category, checkout_version]
default_lookback_weeks: 20
status: live
---

# Payment Submit to Decision rate

## Definition

Of the payments submitted, the share that ever received a **decision** — approved
or declined. Not the same as the approval rate: this measures whether the system
answered at all.

## Numerator

`payments_decisioned` — sessions with `payment_decisioned_flag = TRUE`.

## Denominator

`payments_submitted` — sessions with `payment_submitted_flag = TRUE`.

## Value formula

```
decision_rate = SAFE_DIVIDE(SUM(payments_decisioned), SUM(payments_submitted))
```

## Caveats

- **The shortfall is two different problems.** A submitted payment with no decision
  either failed a fraud/identity check (stopped before the decision) or is sitting
  in a review queue (may never resolve). The first is a control working; the second
  is a backlog. Size the first with `fraud_check_failure_rate`; the residual is the
  queue. The spine-backed drop-off skill separates them directly via
  `terminal_branch`.
- **`bank_transfer_review` dominates the gap.** It is ~6% of volume but carries most
  of the unresolved tail, and it decides an order of magnitude slower than card
  authorization. Slice by `payment_method` before drawing a conclusion about "the
  payment system".
- **This metric is where the maturity override earns its keep.** The book-wide
  `payment_decisioned` lag (2d) is set by the instant-card majority. The funnel spec
  carries a per-`payment_method` override (12d for `bank_transfer_review`) — without
  it, a recent cohort's bank-transfer payments look *undecided* when they are simply
  *unanswered so far*. Note that the override is applied by the spine-backed skills
  when grouping by that dimension; this metric uses the book-wide lag, so a
  `payment_method=bank_transfer_review` filter on a recent window will read low.
  That gap is real and is the honest limit of a pre-aggregated ratio metric.
- **Synthetic in this template.** See `LINEAGE.md`.

## Accuracy tier

**T2 — operational.** Read alongside `payment_approval_rate`; neither is
interpretable alone.

## SMEs

- `payments-pm` (product)
- `analytics-owner` (definition)

## Example queries

> "Submit-to-decision rate by payment method over the last 16 weeks."

Maps to `dimensions=[payment_method]`, `time-range=last_16_weeks`.
