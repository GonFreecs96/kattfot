---
entity: order
display_name: Order
module: checkout
status: live
metric_backed: true
owner: analytics-owner
grain: one row per confirmed order
keys:
  primary: order_id
  foreign: [checkout_session_id]
source_views:
  - analytics.cart_checkout_session_spine
dimensions:
  - name: category
    governed_column: category
    anchor: [order]
    values: [apparel, electronics, home, beauty]
    note: >
      the order's primary product category. Drives both fulfilment speed and
      return rate far more than any other dimension, which is why it is a
      governed slice rather than a curiosity.
  - name: is_first_order
    governed_column: is_first_order
    anchor: [order, customer]
    values: ["true", "false"]
    note: >
      whether this is the customer's first order. Stored as a BOOL, so it is
      listed in the funnel spec's `string_cast` and grouped as CAST(... AS STRING).
relationships:
  - to: checkout_session
    kind: one_to_one
    via: checkout_session_id
    backs: [cart_to_order_confirmed_end_to_end]
  - to: shipment
    kind: one_to_zero_or_one
    via: order_id
    backs: [order_confirmed_to_shipped_rate, confirmed_not_shipped_rate]
    note: "a confirmed order that never ships is the out-of-stock leak"
  - to: customer
    kind: many_to_one
    via: customer_token
business_rules:
  - id: return_window_is_thirty_days
    source: config/checkout-funnel-spec.yaml
    summary: "a delivered order is not settled for 30 more days; until the window closes, 'kept' is unknown"
  - id: confirmation_creates_the_order
    source: metrics/checkout/LINEAGE.md
    summary: "order_id is NULL on every session that never confirmed, so orders cannot be the funnel denominator"
---

# Order

Created at confirmation, after a payment is approved. Everything downstream of
here — shipping, delivery, returns — is fulfilment rather than checkout, but it is
in the same funnel because a confirmed order that never arrives, or arrives and
comes straight back, was not revenue.

## The order is not the outcome

The instinct is to treat confirmation as the finish line. This funnel deliberately
does not, because three things happen after it that reverse the result:

1. **It may never ship.** Out of stock, or cancelled post-confirmation. Sized by
   `confirmed_not_shipped_rate`.
2. **It may never arrive.** Carrier loss, refused delivery.
3. **It may come back.** The return window is 30 days from delivery, and the
   return rate varies by nearly 3x across categories.

Stopping at confirmation makes an apparel funnel look identical to an electronics
one. They are not: apparel confirms readily and returns heavily.

## Why `category` matters more than it looks

`category` is the strongest single driver in the fulfilment half of this funnel —
it moves both ship rate (electronics stocks out more) and return rate (apparel
returns roughly three times as often as home goods). Any end-to-end comparison
across a period where the category mix shifted is confounded by that mix, which is
why `cart_to_return_window_complete_end_to_end` carries the caveat explicitly
rather than leaving a reader to discover it.
