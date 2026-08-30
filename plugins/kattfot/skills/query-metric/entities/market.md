---
entity: market
display_name: Market
module: checkout
status: live
metric_backed: true
owner: analytics-owner
grain: one row per selling region
keys:
  primary: market
  foreign: []
source_views:
  - analytics.cart_checkout_session_spine
  - analytics.cart_checkout_funnel_weekly
dimensions:
  - name: market
    governed_column: market
    anchor: [market, checkout_session]
    values: [us, ca, uk]
    note: >
      the selling region. Aggregate geography, explicitly allow-listed as non-PII
      in _shared/pii.py — coarse enough to group by without identifying anyone.
relationships:
  - to: checkout_session
    kind: one_to_many
    via: market
    backs: [payment_approval_rate]
  - to: shipment
    kind: one_to_many
    via: market
    backs: [shipped_to_delivered_rate]
business_rules:
  - id: market_changes_the_settle_point
    source: config/checkout-funnel-spec.yaml
    summary: "transit time differs by market, so the deepest stage settles later in slower markets — a maturity difference that reads as a conversion difference if ignored"
---

# Market

The selling region. Aggregate geography: `market` is explicitly on
`_shared/pii.py`'s safe allow-list alongside `region`, `city`, `postal_code`, and
`country`, because geography coarse enough to group by is not a personal
identifier. Street-level address is, and is refused.

## Two distinct effects, easily confused

Market moves the funnel in two places for two unrelated reasons, and separating them
is the point of registering it as an entity:

1. **Payment approval.** Non-domestic authorization carries slightly more friction,
   so approval rates differ by a point or two. This is a real conversion effect.
2. **Transit time.** Delivery takes longer in some markets, which pushes
   `delivered_at` — and therefore the whole 30-day return window — further out. This
   is a *maturity* effect, not a conversion one.

The second is the trap. A market-sliced end-to-end read over a recent window will
show the slower market converting worse, purely because more of its tail has not
happened yet. Per-stage maturity handles it if the cutoff is applied; it does not if
someone reads the raw view. That is what the caveat on
`cart_to_return_window_complete_end_to_end` is for.

## Cardinality

Three values here. Keeping the list short is deliberate: a `market` that meant
"country" across dozens of values would make every sliced cell too thin for a rate
to mean anything, and the honest fix in that case is a rollup dimension rather than
a wider allow-list.
