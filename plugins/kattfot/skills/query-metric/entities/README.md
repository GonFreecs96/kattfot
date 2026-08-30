# Entities registry

The **named entity model** for the governed analytics layer. It makes the entity
model that the metrics, funnel spec, and view DDL already encode *implicitly*
explicit and machine-readable, so an agent selects from named **entities,
relationships, and business rules** instead of inferring them from column names
and join logic.

Files without an `entity:` key — like this README — are documentation and are
skipped by the loader and the linter, mirroring how a `LINEAGE.md` (no `name:`) is
skipped by the metric resolver.

## Entities vs. dimensions

The distinction is load-bearing and worth stating before the table:

- A **noun with its own identity** is a first-class entity, one file per entity:
  `checkout_session`, `order`, `payment`, `shipment`, `customer`, `channel`,
  `market`.
- A **categorical** is an enumerated governed dimension anchored to a parent
  entity — a `dimensions:` block inside that entity's file, **not** a standalone
  entity. `payment_method` belongs to `payment`; `category` belongs to `order`.

Modelling `payment_method` as an entity would imply it has a lifecycle, a grain,
and relationships of its own. It has three values.

## Registered entities

| File | Entity | Module | Grain |
|---|---|---|---|
| `checkout-session.md` | `checkout_session` | checkout | one cart |
| `order.md` | `order` | checkout | one confirmed order |
| `payment.md` | `payment` | checkout | one authorization attempt |
| `shipment.md` | `shipment` | checkout | one dispatch → delivery leg |
| `customer.md` | `customer` | checkout | one person (best-effort, token-linked) |
| `channel.md` | `channel` | checkout | one acquisition surface |
| `market.md` | `market` | checkout | one selling region |

Seven, chosen because each one either backs a live metric or anchors a governed
dimension. That is the whole selection rule, and it is why there is no
`warehouse`, no `promotion`, no `session_device` — a template that registered
every noun a shop might have would be teaching the wrong lesson. Add an entity
when a metric needs it.

Only relationships that actually carry a metric or a join are modelled. An edge
that exists in the world but not in the data is not an edge here.

## Frontmatter schema

```yaml
entity: checkout_session       # snake_case slug; unique; the value metrics name
display_name: Checkout Session
module: checkout               # a module registered in config/modules.yaml
status: live                   # OPTIONAL live | declared | deprecated (default live)
metric_backed: true            # OPTIONAL bool; a declared entity sets this false
owner: analytics-owner         # OPTIONAL definition owner (see doctrine/)
grain: one row per cart
keys:
  primary: checkout_session_id
  foreign: [customer_token]
source_views:
  - analytics.cart_checkout_session_spine
dimensions:                    # OPTIONAL governed categoricals anchored here
  - name: payment_method
    governed_column: payment_method
    anchor: [payment]
    values: [card_instant, wallet, bank_transfer_review]
    note: "how the authorization was attempted"
relationships:                 # edges to OTHER registered entities
  - to: order
    kind: one_to_zero_or_one   # one_to_one | one_to_zero_or_one | one_to_many |
                               # many_to_one | many_to_many
    via: checkout_session_id
    backs: [cart_to_order_confirmed_end_to_end]   # OPTIONAL metric(s) it carries
    note: "a cart becomes an order only at confirmation"
business_rules:
  - id: cohort_vs_activity_time
    source: metrics/checkout/LINEAGE.md
    summary: "cohort_week is the cart's CREATION week, tracked forward"
```

`status: declared` marks a forward-looking entity that is named as context but is
not yet in a governed view. Such an entity must set `metric_backed: false`, must
not claim a `backs:` edge, and may not be a metric's primary `entity:`.
`tests/test_metric_catalog.py` enforces all of that, plus the shape above.

## How metrics reference entities

Each metric carries `entity: {slug}` (required) naming what its numerator and
denominator measure, and may carry `relationships:` (registered slugs it also
touches) and `business_rules:` (rule ids). For example, in
`approved_to_order_confirmed_rate`:

```yaml
entity: payment                # the denominator is approved payments
relationships: [order, checkout_session]
business_rules: [per_stage_maturity]
```

The funnel spec (`config/checkout-funnel-spec.yaml`) declares its own
`primary_entity:`, `entities:`, and `relationships:` blocks, kept consistent with
the stage ladder.
