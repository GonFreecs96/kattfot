---
entity: checkout_session
display_name: Checkout Session
module: checkout
status: live
metric_backed: true
owner: analytics-owner
grain: one row per cart created
keys:
  primary: checkout_session_id
  foreign: [customer_token]
source_views:
  - analytics.cart_checkout_session_spine
  - analytics.cart_checkout_funnel_weekly
dimensions:
  - name: channel
    governed_column: channel
    anchor: [channel]
    values: [web, mobile_app, marketplace, social]
    note: "the surface the cart was created on"
  - name: checkout_version
    governed_column: checkout_version
    anchor: [checkout_session]
    values: [v2, v1-legacy]
    note: >
      the checkout implementation serving the session. `v1-legacy` is the retired
      cohort the funnel spec excludes by default via legacy_exclude_column /
      legacy_exclude_value.
relationships:
  - to: customer
    kind: many_to_one
    via: customer_token
    note: "one person can create many carts; linkage is token-based and best-effort"
  - to: payment
    kind: one_to_zero_or_one
    via: checkout_session_id
    backs: [checkout_start_to_payment_submit_rate, payment_submit_to_decision_rate]
    note: "most carts never reach an authorization attempt at all"
  - to: order
    kind: one_to_zero_or_one
    via: checkout_session_id
    backs: [cart_to_order_confirmed_end_to_end, cart_to_return_window_complete_end_to_end]
    note: "a cart becomes an order only at confirmation, after an approved payment"
  - to: channel
    kind: many_to_one
    via: channel
  - to: market
    kind: many_to_one
    via: market
business_rules:
  - id: cohort_vs_activity_time
    source: metrics/checkout/LINEAGE.md
    summary: "cohort_week is the cart's CREATION week, tracked forward — not the week the outcome happened"
  - id: per_stage_maturity
    source: config/checkout-funnel-spec.yaml
    summary: "each stage is withheld only until THAT stage settles, not until the slowest one does"
---

# Checkout Session

The grain of the funnel: **one shopping cart**. Every stage rate in the checkout
module is ultimately a share of carts, which is why the denominator of the
end-to-end metrics is `carts_created` and not orders, customers, or visits.

## Why the cart, and not the order

An order-grain funnel can only describe orders that exist. It cannot see the
abandoned cart — which, in this book, is the single largest exit in the whole
ladder. Anchoring on the cart makes the biggest leak measurable rather than
invisible, at the cost of a denominator that includes a lot of browsing noise.
That trade is deliberate; the `1 Cart Abandoned` and `1z Stale (Pre-Checkout)`
terminal branches exist to size the noise.

## Identity

`checkout_session_id` is an opaque surrogate key. `customer_token` links sessions
to a person without exposing one: it is explicitly allow-listed in
`_shared/pii.py` as a tokenized join key, so customer-level analysis stays
possible while personal columns stay refused under R6.

## Cohort anchoring

A cart belongs to the ISO week it was **created**, forever. A cart created in week
*W* that confirms three weeks later still counts in *W*'s cohort. This is what
makes conversion comparable across weeks — and it is also what makes recent
cohorts genuinely incomplete, since their later stages have not happened yet.
That incompleteness is handled by per-stage maturity, not by pretending it away.
