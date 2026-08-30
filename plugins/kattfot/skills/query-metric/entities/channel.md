---
entity: channel
display_name: Channel
module: checkout
status: live
metric_backed: true
owner: analytics-owner
grain: one row per acquisition surface
keys:
  primary: channel
  foreign: []
source_views:
  - analytics.cart_checkout_session_spine
  - analytics.cart_checkout_funnel_weekly
dimensions:
  - name: channel
    governed_column: channel
    anchor: [channel, checkout_session]
    values: [web, mobile_app, marketplace, social]
    note: "the surface a cart was created on"
relationships:
  - to: checkout_session
    kind: one_to_many
    via: channel
    backs: [cart_to_checkout_start_conversion, cart_to_stale_rate]
business_rules:
  - id: channel_mix_confounds_trends
    source: metrics/checkout/LINEAGE.md
    summary: "channels convert at very different rates, so any un-sliced trend partly measures the channel mix rather than checkout performance"
---

# Channel

The surface a cart was created on. The default monitoring dimension for the funnel
regression monitor (`pulse.by: channel` in the funnel spec), and the most common
axis of a genuine misreading.

## Why it is the default slice

Because the channels are not small variations on each other — they are different
buying situations with intent baked in:

| Channel | Character | Effect on the funnel |
|---|---|---|
| `web` | The reference case; deliberate desktop shopping | Baseline |
| `mobile_app` | Installed users, higher intent, worse form ergonomics | Starts checkout less often, completes payment more often |
| `marketplace` | Arrives with an item already chosen | Highest start rate; carries most of the fraud signal |
| `social` | Impulse, lowest intent | Lowest start rate by a wide margin |

## The mix trap

An un-sliced conversion trend is partly a measure of channel mix. A marketing push
into `social` will lower the blended cart-to-checkout rate even if every individual
channel improved — the same arithmetic as Simpson's paradox, arriving as a Monday
morning surprise.

This is why `cart_to_checkout_start_conversion` carries a mix caveat rather than
leaving it to be discovered, and why the funnel pulse monitors per-channel rather
than in aggregate: a per-channel monitor cannot be fooled by a mix shift, because
the mix is the thing it holds constant.
