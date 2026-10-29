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

A definition whose review is overdue is *visibly* overdue. Every metric carries
two keys the catalog linter (`tests/test_metric_catalog.py`) requires:

| Key | Meaning |
|---|---|
| `version` | Integer, starts at 1. Bump it when the definition's *meaning* changes — numerator, denominator, grain, a caveat that changes how the number reads. A wording fix does not bump it. |
| `last_reviewed` | ISO date the owner last confirmed the definition still holds. Set it on every review, including the ones that change nothing. |

`kattfot:catalog` (and `kattfot:load-context catalog`) compares `last_reviewed`
against a review window — 180 days by default, `KATTFOT_REVIEW_WINDOW_DAYS` to
change it per deployment — and lists each metric as `fresh` or `stale`. In the
markdown table a stale definition carries the marker
`STALE: last reviewed {date}, owner: {owner}` next to its name. A definition with
no `last_reviewed` at all (a consumer override that never set one) lists as
`STALE: never reviewed`, because a freshness signal that silently fails to fire
would read as "fresh" — the one failure mode worse than having no signal.

Two things the marker deliberately is not:

- **Not a refusal.** A stale metric still serves. The marker is a prompt for its
  owner, surfaced where the metric is listed, not a gate on the number.
- **Not the same signal as load-context's `STALE:` line.** That one keys on the
  file's modification time (`contracts/consumer-api.md`); this one keys on the
  owner's explicit `last_reviewed`. A file can be edited without being reviewed,
  and reviewed without being edited.

The window is one number for the whole catalog. The cadence table above is the
per-metric judgement; a deployment that wants per-metric windows needs a
`review_every_days:` key and a one-line change in `catalog.py`.
