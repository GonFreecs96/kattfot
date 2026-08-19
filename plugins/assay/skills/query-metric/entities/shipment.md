---
entity: shipment
display_name: Shipment
module: checkout
status: live
metric_backed: true
owner: fulfilment-pm
grain: one row per dispatch-to-delivery leg on an order
keys:
  primary: order_id
  foreign: []
source_views:
  - analytics.cart_checkout_session_spine
relationships:
  - to: order
    kind: one_to_one
    via: order_id
    backs: [shipped_to_delivered_rate, delivered_to_return_window_complete_rate, delivered_to_returned_rate]
  - to: market
    kind: many_to_one
    via: market
    note: "transit time is market-dependent; the timing percentiles are grouped by it"
business_rules:
  - id: delivery_starts_the_return_clock
    source: config/checkout-funnel-spec.yaml
    summary: "return_window_ends_at = delivered_at + 30 days, so a late delivery pushes the whole settle point out"
  - id: one_leg_per_order
    source: metrics/checkout/LINEAGE.md
    summary: "split shipments are modelled as a single leg keyed on the LAST delivery, so an order is delivered only when all of it is"
---

# Shipment

The physical leg: dispatched, then delivered. Two stages, but the one that matters
for governance is delivery, because **delivery starts the return clock**.

## Why this is the maturity bottleneck

`return_window_ends_at` is derived from `delivered_at`, not from the order date. So
the settle point of the deepest funnel stage is a function of how fast the carrier
moved:

```
cart created -> ... -> shipped (~1d)  -> delivered (~3-5d)  -> window closes (+30d)
```

which is where the funnel spec's 44-day `return_window_complete` lag comes from —
roughly delivery (14d at the tail) plus the 30-day window. A slow market pushes its
own settle point out further than a fast one, which is a real reason a
market-sliced end-to-end comparison can look like a conversion difference when it
is a transit-time difference.

## Split shipments

Modelled as a single leg keyed on the **last** delivery. An order that arrives in
two boxes is delivered when the second box lands, not the first. This is the
conservative choice: it never reports an order as settled before all of it can be
returned. The cost is that a partially-delivered order looks entirely undelivered,
so `shipped_to_delivered_rate` runs slightly pessimistic for multi-item orders.
