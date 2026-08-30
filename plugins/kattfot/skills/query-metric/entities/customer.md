---
entity: customer
display_name: Customer
module: checkout
status: live
metric_backed: false
owner: analytics-owner
grain: one row per person, best-effort via customer_token
keys:
  primary: customer_token
  foreign: []
source_views:
  - analytics.cart_checkout_session_spine
relationships:
  - to: checkout_session
    kind: one_to_many
    via: customer_token
    note: "a person may create many carts; no metric is computed at this grain yet"
business_rules:
  - id: token_linkage_is_best_effort
    source: metrics/checkout/LINEAGE.md
    summary: "customer_token is absent on guest checkouts, so person-grain coverage is partial and every person-grain rate needs a stated denominator"
  - id: no_person_grain_spine
    source: config/modules.yaml
    summary: "the checkout module registers no customer spine, so 'did ANY of this person's carts convert' cannot be answered by the session-grain skills"
---

# Customer

Registered as **context, not as a metric grain**. `metric_backed: false` is
deliberate and is the honest state of this template: no metric in the catalog has a
customer denominator, and the module registry deliberately registers no
customer-grain spine.

## Why it is here at all

Because `customer_token` exists on the spine and is joinable, and an agent
reasoning about the data should be able to see that it is a **tokenized surrogate
key, not a person's identity**. It is explicitly allow-listed in `_shared/pii.py`
so that customer-level linkage remains possible while personal columns — names,
contact details, addresses — stay refused under R6 with no override.

## Why no metric uses it

A person-grain rate is a genuinely different question from a session-grain one, not
a regrouping of it. "What share of carts convert?" and "what share of people who
started a cart ever bought?" have different denominators and different answers, and
the second is systematically higher because one person's second attempt rescues
their first abandonment.

Answering it needs its own spine — one row per person, with "did ANY of their
sessions reach stage X" rollups — plus a stated position on the two things that
make the denominator arguable:

1. **Guest checkout.** No token, so those sessions cannot be attributed to a
   person. Are they each their own person, or excluded? Both choices bias the rate,
   in opposite directions.
2. **Token stability.** A token that rotates on device change splits one person
   into several, inflating the denominator and depressing the rate.

Shipping a customer-grain metric without answering those would produce a number
that looks like a conversion rate and is actually a measure of token coverage.
