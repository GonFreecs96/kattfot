---
entity: payment
display_name: Payment
module: checkout
status: live
metric_backed: true
owner: payments-pm
grain: one row per authorization attempt on a checkout session
keys:
  primary: checkout_session_id
  foreign: []
source_views:
  - analytics.cart_checkout_session_spine
dimensions:
  - name: payment_method
    governed_column: payment_method
    anchor: [payment]
    values: [card_instant, wallet, bank_transfer_review]
    note: >
      how the authorization was attempted, and therefore how long it takes to
      settle. `card_instant` decides in seconds; `bank_transfer_review` routes
      through a clearing cycle and a manual queue and decides in days. This is the
      dimension the per-group maturity override in the funnel spec keys on.
relationships:
  - to: checkout_session
    kind: one_to_one
    via: checkout_session_id
    backs: [payment_approval_rate, payment_decline_rate, fraud_check_failure_rate]
  - to: order
    kind: one_to_zero_or_one
    via: checkout_session_id
    backs: [approved_to_order_confirmed_rate]
    note: "an approved payment almost always confirms an order; the gap is the interesting part"
business_rules:
  - id: decision_is_not_approval
    source: metrics/checkout/LINEAGE.md
    summary: "decisioned means an answer arrived; approved means the answer was yes. Not every submitted payment is ever decisioned."
  - id: fraud_fail_precedes_decision
    source: metrics/checkout/LINEAGE.md
    summary: "a payment that fails the fraud/identity check never reaches a decision, so it is in the submitted denominator but not the decisioned one"
---

# Payment

The authorization attempt: submitted, then either decisioned (approved or
declined), stopped by a fraud/identity check, or left unresolved in a review
queue.

## The three outcomes people conflate

This entity exists mostly to keep three different things apart, because collapsing
them is the most common way a payment funnel misleads:

| Outcome | In `payments_submitted`? | In `payments_decisioned`? |
|---|---|---|
| Approved | yes | yes |
| Declined | yes | yes |
| Fraud / identity check failed | yes | **no** — it never reached a decision |
| Still in a review queue, unresolved | yes | **no** — it may never resolve |

So `payment_submit_to_decision_rate` is not 100% minus the decline rate: the gap
is fraud failures plus unresolved reviews. Reporting approval as
`approved / submitted` silently blames the risk engine for the queue's backlog,
which is why the catalog splits the two steps and defines approval against
`payments_decisioned`.

## Why `payment_method` drives maturity, not just conversion

`bank_transfer_review` is a small share of volume (~6%) but decides an order of
magnitude slower than card authorization. A single book-wide settle lag is
dominated by the instant majority and is badly wrong for this segment — recent
cohorts would look as though bank-transfer payments were being declined, when
they simply had not been answered yet. The funnel spec's
`stage_maturity.lag_days_overrides.payment_decisioned` exists for exactly this,
keyed on this dimension.
