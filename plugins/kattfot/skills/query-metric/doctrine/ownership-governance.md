# Ownership and review cadence

Every governed definition in this catalog names an owner. Not for blame — for
**addressability**. The failure mode this prevents is the one every analytics
layer eventually hits: a metric that everybody uses, nobody maintains, and no
reader can question, because there is no one to ask.

## What an owner owns

The **definition**, not the number and not the pipeline.

- Is the numerator still measuring what the name says?
- Have the caveats gone stale — did a fixed bug make one obsolete, or a shipped
  feature add a new one?
- Is the allow-list still the right shape, or has a dimension become dangerous
  (high cardinality, newly PII-adjacent) since it was added?
- When someone reconciles this metric against another number and they disagree,
  who explains why?

Distinct from two other roles that get conflated with it:

| Role | Owns | Named in |
|---|---|---|
| **Definition owner** | What the metric means; its caveats and allow-list | `owner:` in the metric / entity frontmatter |
| **Pipeline owner** | That the source view lands, on time, correctly | your orchestration tool, not here |
| **Blocker owner** | Unblocking an instrumentation gap that stops a metric existing at all | the coverage register's `blocker:` prose |

## Placeholders in this template

Every `owner:` and `smes:` value in the bundled catalog is a **neutral
placeholder** (`analytics-owner`, `payments-pm`, `fulfilment-pm`). They are
deliberately not names or email addresses: a public template that shipped real
contact details would be leaking, and one that shipped plausible-looking fake
names would invite somebody to route a real question to nobody.

Replace them with real people when you fork this. A definition owned by
`analytics-owner` is a definition owned by nobody, and the whole point of the
field is that it resolves to a human.

## Review cadence

The cadence a definition needs follows how fast the thing underneath it changes,
not how important it is:

| Metric characteristic | Suggested review |
|---|---|
| Sits on a stage the product team is actively changing | Each release that touches the stage |
| Depends on an external provider's behavior (a payment processor, a carrier) | Quarterly, plus on any provider change |
| Stable definition over a stable pipeline | Annually, to confirm it is still used |
| Carries a caveat that says "pending investigation" | Whenever that investigation closes — a stale open question is worse than a closed one |

## Freshness signalling

The intent is that a definition whose review is overdue is *visibly* overdue —
a `STALE:` marker surfaced next to the metric wherever it is listed, naming the
owner and the date it was last reviewed. Nothing in this template computes that
yet: doing it properly needs a `last_reviewed:` field on every definition and a
lister that reads it, and shipping half of that would be worse than shipping
none, because a freshness signal that silently fails to fire reads as "fresh".

If you want it, the pieces are: add `last_reviewed:` to the metric frontmatter
schema, extend `tests/test_metric_catalog.py` to require it, and have whatever
lists your catalog compare it against the cadence above.
