---
entity: browse_session
display_name: Browse Session
module: browse
status: declared
metric_backed: false
owner: analytics-owner
grain: one row per visit to the catalog, whether or not it produced a cart
keys:
  primary: browse_session_id
  foreign: [checkout_session_id, visitor_token, customer_token]
source_views:
  - analytics.browse_session_spine
dimensions:
  - name: device
    governed_column: device
    anchor: [browse_session]
    values: [desktop, mobile_web, tablet, app]
    note: "the client the visit came from; `app` overlaps the mobile_app channel but is not the same cut"
  - name: landing_surface
    governed_column: landing_surface
    anchor: [browse_session]
    values: [home, category, product_detail, search_results, promotion]
    note: "the first page of the visit — a deep link into product detail behaves very differently from a home-page entry"
relationships:
  - to: checkout_session
    kind: one_to_zero_or_one
    via: checkout_session_id
    note: >
      the handoff edge, declared in config/modules.yaml. NULL on every visit that
      never produced a cart, which is the majority — that is what makes the edge's
      coverage a conversion rate rather than a join-quality figure
  - to: customer
    kind: many_to_one
    via: customer_token
    note: "NULL for a signed-out visit; `visitor_token` is the weaker device-scoped key that is usually present instead"
  - to: channel
    kind: many_to_one
    via: channel
    note: "shared vocabulary with checkout_session, so a browse-side and a cart-side channel split are comparable"
  - to: market
    kind: many_to_one
    via: market
    note: "shared vocabulary with checkout_session"
business_rules:
  - id: browse_grain_is_not_a_filter_on_checkout
    source: config/modules.yaml
    summary: "a visit that produced no cart is absent from the checkout spine entirely, so browse-to-cart conversion cannot be computed by filtering the checkout funnel — it is a different population, not a narrower one"
  - id: handoff_coverage_is_a_conversion_rate
    source: config/modules.yaml
    summary: "the handoff edge's `coverage` is the share of ALL browse sessions carrying a checkout_session_id; a low value means shoppers left, not that rows failed to match"
  - id: visitor_token_is_not_a_person
    source: config/guardrails.yaml
    summary: "visitor_token is device-scoped and rotates, so a visitor rollup over a long window splits one person into several and inflates any per-visitor denominator"
---

# Browse Session

The catalog-side grain: **one visit, cart or no cart.** Registered as `declared` —
the contract is authored and linted, and nothing is provisioned behind it.

## Why it is declared rather than live

No `analytics.browse_session_spine` exists in this template and no browse fixture
is committed, so no query can be run at this grain. The reason is stated plainly
in `config/modules.yaml` and in `coverage.yaml#browse_to_cart_handoff`: a browse
spine over the same 19 cohort weeks as the committed checkout spine is roughly
2.4x as many rows, because most visits never produce a cart, and carrying both
would put ~16 MB of synthetic CSV into a repository whose selling point is that a
fresh clone runs offline with no setup.

`metric_backed: false` and the empty `skills:` list on the registry entry are the
machine-readable form of that, and the linter holds both in place — a `declared`
spine cannot list a skill, and a `declared` entity cannot back a metric.

## Why the grain is worth declaring anyway

Because it is where the **denominator** lives, and the denominator is the part
that is expensive to get wrong later.

"What share of carts convert?" is a checkout-spine question. "What share of visits
produce a cart?" is not the same question with a different filter — it cannot be
asked on the checkout spine at all, because a visit that produced no cart leaves
no row there. The population is different, and the missing rows are exactly the
ones the question is about.

That is also why the handoff edge carries a coverage figure rather than being a
bare join declaration. `checkout_session_id` is NULL on most browse sessions, so
the share of source rows that reach the target is a genuine measurement of shopper
behaviour — a conversion rate — and not, as a "coverage" field usually implies, a
statement about how well two tables match. Reading it as data quality would turn a
successful measurement of a leaky funnel into a false alarm about the pipeline.

## What would have to land

1. The browse event stream sessionized to one row per visit, with the four stage
   flags and their timestamps.
2. `checkout_session_id` stamped on the visit that created the cart — the join
   key, and the only field the handoff edge needs.
3. A measured coverage: `COUNTIF(checkout_session_id IS NOT NULL) / COUNT(*)` over
   the browse spine, written back to the registry with
   `coverage_basis: measured_source_share` and a `coverage_measured_at` date.

Steps 1 and 2 are the instrumentation ask (`PLAT-112` in the coverage register).
Step 3 is the moment the edge's number stops being an estimate, and the registry
linter is what stops it claiming to be one before then.
