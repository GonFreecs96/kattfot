---
name: customer-journey
description: >
  OUTLINED, NOT IMPLEMENTED — see the status banner below. Would be a
  CUSTOMER-grain analysis for checkout — answering questions about people, not
  carts. For a segment, would return the customer set size, the
  cart:customer compression, customer-grain stage rates (decisioned / approved
  / delivered / return-window-complete — "did ANY of this person's carts reach
  it", the true rate the per-cart funnel understates), and journey shares (%
  multi-cart, % same-day repeat cart, % re-attempted after a decline).
  Linkage is customer_token (best-effort; absent on guest checkout).
version: 0.1.0
---

# Skill: customer-journey

## ⚠️ Status: outlined, not implemented

**This skill ships as a documented outline only.** There is no helper script
behind it — `scripts/customer_journey_stub.py` in this directory exits
immediately with a clear, non-zero-exit refusal (see [Status in this template](#status-in-this-template)).
No customer-grain query runs, and no artifact carrying the name
`customer-journey` is ever written.

**Renamed from the source skill this outline is ported from** (which used the
grain name `applicant-journey`, for a per-application financial-services
domain). This is a checkout book, and the corresponding grain is **the
customer**, not the applicant — `customer-journey` names what it actually is
rather than keeping a name that only made sense in a different domain. The
rename is complete: `plugins/assay/config/modules.yaml` and
`tests/test_module_registry.py` both refer to this skill as
`customer-journey` throughout.

**What implementing it for real would need:**
- **Fixtures at the customer grain.** Nothing else in this template's four
  outlines is blocked on statistics — this one is blocked on **data that does
  not exist yet**. `scripts/generate_fixtures.py` generates the session-grain
  spine (`cart_checkout_session_spine.csv`) and the weekly aggregate; neither
  is a customer-grain rollup, and no customer-grain CSV is committed. A real
  implementation needs a fixture (or a warehouse view) with one row per
  customer, carrying "did this person's cart set ever reach stage X" flags —
  which is a genuinely different table, not a `GROUP BY customer_token` over
  the existing spine (see [Why this needs its own rollup, not a GROUP BY](#why-this-needs-its-own-rollup-not-a-group-by)).
- **A registered customer spine.** `config/modules.yaml` would need a new
  spine entry (`checkout/customer` or similar) pointing at that rollup, with
  its own `allowed_dimensions` / `allowed_filters`, before this skill could
  be flipped from `excluded` to `skills:` for real — see
  `plugins/assay/skills/query-metric/entities/customer.md`, which already
  documents `metric_backed: false` and explains why no metric in the catalog
  uses this grain today.
- **An explicit, defensible position on guest checkout.** Not a coding task —
  a judgment call about what the denominator means. See
  [Guest checkout is a measurement decision, not a data gap](#guest-checkout-is-a-measurement-decision-not-a-data-gap).
- **No new statistical machinery.** Unlike `funnel-nowcast` and
  `release-scorecard`, this skill needs no `numpy`/`scipy`/`statsmodels` — it
  is aggregate rollups and ratios, the same shape of computation
  `funnel-dropoff` already does. The entire gap is the fixture and the
  registry entry above.

## Purpose (what this skill would answer)

Answer "what happens at the **person** level?" for checkout. It would query a
customer-grain rollup (one row per customer, best-effort via
`customer_token`, else a per-cart singleton) to produce:

1. **Customer set + compression** — how many customers a segment's carts
   collapse to (`carts_per_customer`).
2. **Customer-grain stage rates** — decisioned / approved / delivered /
   return-window-complete as a share of **people**, not carts. A person
   declined once and approved on a second attempt is **one** approved
   customer, so these rates would read **above** the per-cart rates
   `assay:funnel-dropoff` reports. This is the true customer-level conversion
   rate that a per-cart funnel can only lower-bound.
3. **Journey shares** — % with 2+ carts, % with 2+ carts the same day (the
   dual-cart / rapid-retry signal), % who re-attempted checkout after a
   Payment Declined or Cancelled branch.

It would complement `assay:funnel-dropoff` (per-cart leak attribution) and
`assay:query-metric` (governed point conversions). Use it — once
implemented — for questions about repeat customers, same-day retries, or
re-attempts after a decline; use the existing spine skills for everything
about a single cart's path.

## Why this needs its own rollup, not a `GROUP BY`

It is tempting to assume `SELECT customer_token, MAX(delivered_flag) FROM
cart_checkout_session_spine GROUP BY customer_token` gets most of the way
there. Two things make that read wrong rather than merely approximate:

1. **Guest checkout has no `customer_token` at all** — grouping by it
   silently drops every guest cart from the customer count instead of
   counting each as its own (singleton) customer, which understates the
   customer base and overstates the multi-cart share among what remains.
2. **Maturity has to be evaluated per stage, at the person level, before the
   aggregation** — "did ANY cart reach Delivered" is only answerable once
   each cart's own maturity window has been checked, and a naive `MAX()` over
   an unfiltered spine would silently count an immature cart's `false` as a
   real negative rather than "not yet knowable." The session-grain skills
   handle this per-stage maturity gate already; a customer rollup needs the
   same gate applied *before* the person-level `MAX`, not after.

Both are exactly the kind of thing a purpose-built rollup view encodes once
and a query written per-request gets subtly wrong every time.

## Guest checkout is a measurement decision, not a data gap

State this plainly in any artifact this skill would produce, because it
changes what the denominator *means*, not just its size:

- **No token, so no attribution.** A guest cart cannot be linked to any other
  cart by the same shopper. Counting each guest cart as its own singleton
  customer keeps the cart-level denominator whole, at the cost of an
  understated multi-cart share — some of those "singletons" are the same
  person who checked out as a guest twice.
- **The multi-cart rate is therefore a floor, not a point estimate.** It can
  only be revised upward as identity linkage improves; it is structurally
  unable to be too high.
- **This is a design decision with a real alternative** (excluding unlinkable
  carts from the customer denominator entirely), and the alternative biases
  in the opposite direction — it overstates the multi-cart share among the
  customers you can see, because it deletes exactly the population most
  likely to be a first-time or low-engagement shopper. Neither choice is
  free; state which one a given run made.

## Would-be invocation

```
assay:customer-journey what's the true customer-grain delivery rate for the last 12 weeks?
assay:customer-journey multi-cart and re-attempt rates by channel
assay:customer-journey same-day repeat-cart share for market=uk, linked customers only
```

Today, any of these returns the refusal in
[Status in this template](#status-in-this-template).

## Would-be auth model

Identical to the other spine-backed skills: individual human identity via ADC
on `ASSAY_ENGINE=bigquery`; the committed fixtures with `fixture_backed: true`
on the default `sqlite` engine — once a customer-grain fixture exists to read.

## Would-be flow (for the implementer)

1. **Load the (not-yet-registered) customer spine** via `config/modules.yaml`
   — its own `allowed_dimensions` / `allowed_filters`, treated as the
   canonical allow-list the same way every other spine skill treats its spec.
2. **Parse the question** — segment filters, an optional single group-by
   dimension, a time range keyed to the customer's **first-cart** week
   (not an arbitrary cart's week — this is what makes the window
   customer-coherent), and the usual maturity toggles (default stage-aware,
   `--maturity flag` for the legacy whole-window gate, `--no-mature`,
   `--include-legacy`, plus `--linked-only` to exclude guest singletons and
   see the linked-population-only rate).
3. **Refuse before querying** — group-by not allow-listed → R2; filter column
   not allow-listed → R3; a request for an individual customer, a
   `customer_token`, or a named person → **R5**, this skill is aggregate-only
   and never surfaces a person; any PII column → **R6**, no override.
   `customer_token` would be a grouping key inside the rollup view, never an
   output dimension or filter — the same discipline
   `query-metric/entities/customer.md` already states for the token today.
4. **Run the (not-yet-written) helper** over the customer-grain rollup.
5. **Write the artifact** to
   `<cwd>/outputs/assay/{YYYY-MM-DD}-customer-journey-{slug}.md`.

## Would-be output format

```markdown
---
skill: customer-journey
accuracy_tier: {tier}
source_view: {source_project}.{source_view}
generated_at: {iso}
time_range_start: {start}
time_range_end: {end}
segment_filters: {filters}
group_by: {dim or none}
---

# Customer journey — {segment} ({time_range_human})

**Accuracy tier:** {tier} (operational — decision-grade for product/process; not regulator-facing)
**Source view:** `{source_project}.{source_view}` (customer-grain rollup, customer_token linkage)
**Window:** {start} → {end} (by first-cart week) · maturity: {mode} · linked-only: {yes/no}

> Linkage is **customer_token** (best-effort, no canonical identity
> resolution). Guest checkouts carry no token and enter as singletons — the
> multi-cart rate is a floor, not a point estimate. {n} singletons in this
> scope.

## Customers

- **{customers}** customers from **{carts}** carts ({carts_per_customer} carts/customer)

## Customer-grain stage rates

| Stage | Customers | Base | Rate | Mature through |
| --- | --- | --- | --- | --- |
| Decisioned | … | … | …% | {week} |
| Approved (Order Confirmed) | … | … | …% | {week} |
| Delivered | … | … | …% | {week} |
| Return Window Complete | … | … | …% | {week} |

## Journey shape

| Pattern | Customers | Share |
| --- | --- | --- |
| 2+ carts | … | …% |
| 2+ carts same day (rapid retry) | … | …% |
| Re-attempted after a decline / cancellation | … | …% |

## Caveats
[The standing caveats below.]

## Provenance
- `sql`: {the parameterized query}
```

## Would-be standing caveats

- **Guest checkout makes the denominator a measurement, not a constant** — see
  above. State which convention (`singleton` vs `linked-only`) a given run
  used, every time.
- **`customer_token` is a surrogate key, not an identity resolution.** It is
  not stable across a re-registration or a token rotation on device change; a
  token that rotates splits one person into several, which inflates the
  customer count and depresses every customer-grain rate. This is the
  opposite bias from the guest-checkout floor, and the two do not cancel out
  in any knowable proportion.
- **Never a canonical person.** Best-effort linkage only — no cross-device,
  cross-email, or household resolution.
- **Fixture-backed runs are synthetic** when `fixture_backed` is true — and
  today there is no customer-grain fixture to be backed by at all.

## Refusals

Defer to `query-metric/doctrine/accuracy-tiers.md` for the R2/R3/R5/R6 voice.

## What this skill must NOT do, even once implemented

- Never surface an individual — no `customer_token` or cart id as output, no
  named person. Aggregate-only, R5/R6 enforced in the helper.
- Never present the multi-cart rate as exact — it is a floor, biased by
  guest-checkout singletons in one direction and by token instability in the
  other.
- Never re-query or smooth — the artifact would be the rollup's aggregates as
  returned, nothing recomputed client-side.

## Status in this template

There is no `customer_journey.py` and no customer-grain fixture behind this
skill. `scripts/customer_journey_stub.py` in this directory is the only thing
that runs:

```
$ python3 plugins/assay/skills/customer-journey/scripts/customer_journey_stub.py
customer-journey is not implemented in this template.
See SKILL.md in this directory for the method, its scope, and exactly
what implementing it would require.
$ echo $?
9
```
