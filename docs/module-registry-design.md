# The module registry + funnel-spec contract

*Descriptive, not a proposal: this is the design as actually implemented in
`plugins/assay/config/modules.yaml` and the funnel specs it points at. If
you are about to register a third module, read this doc, then read
`modules.yaml`'s own header comment (kept in sync with this doc) and
[`adding-a-module.md`](adding-a-module.md) for the step-by-step.*

## The problem this solves

A spine-backed skill (`funnel-dropoff`, `stage-timing`, `segment-compare`,
`funnel-pulse`) needs to know, for whatever module and grain it is asked
about: which curated view to read, what the stage ladder looks like, which
dimensions and filters are allowed, and how mature each stage's cohorts need
to be before they are readable. Two ways to give it that information are
both wrong:

- **Hardcoding one spec per skill.** Then a second module means forking
  every skill.
- **Letting each skill figure out its own module/spine resolution.** Then
  five skills disagree, subtly, about which spec backs "the funnel" the
  moment there is more than one.

The registry exists to make that resolution **one shared fact** every skill
reads the same way, and to make the *shape* of a funnel spec identical
regardless of which module it belongs to.

## The two files, and the difference between them

- **`config/modules.yaml`** — the registry. Which modules exist, which
  spines back each one, which spine-backed skills apply to which spine, and
  any cross-grain `handoff` edge.
- **`config/<module>-<grain>-funnel-spec.yaml`** — one **runtime contract**
  per spine, all the same shape. This repo ships two:
  `checkout-funnel-spec.yaml` (the `checkout` module's `session` spine,
  `live`) and `browse-funnel-spec.yaml` (the `browse` module's `session`
  spine, `declared`).

A **module** is a governed business domain. A **spine** is one analysable
grain within it — a curated view with one row per instance of that grain,
carrying every stage flag, stage timestamp, and terminal-branch
classification the spine-backed skills need. A module may register several
spines at different grains; exactly one is the default.

## The registry shape

```yaml
modules:
  checkout:
    label: "Checkout & Fulfilment"
    scoping_doc: null
    spines:
      session:
        spec: checkout-funnel-spec.yaml
        primary_entity: checkout_session
        default: true
        status: live
        skills:
          - funnel-dropoff
          - stage-timing
          - segment-compare
          - funnel-pulse
        excluded:
          funnel-nowcast: "Ships as a documented outline only. Chain-ladder now-casting needs a `nowcast:` block ... deliberately NOT authored here."
          release-scorecard: "..."
          customer-journey: "..."

  browse:
    label: "Catalog & Browse"
    scoping_doc: null
    spines:
      session:
        spec: browse-funnel-spec.yaml
        primary_entity: browse_session
        default: true
        status: declared
        skills: []
        excluded:
          funnel-dropoff: "Blocked on data, not on code: ..."
          # (every spine-backed skill excluded, same root cause)
        handoff:
          to: checkout/session
          "on": checkout_session_id
          coverage: 0.42
          coverage_basis: declared_estimate
          coverage_measured_at: null
          coverage_denominator: "all browse sessions opened in the window, whether or not they produced a cart"
          coverage_note: "..."
```

(Trimmed for readability — see `plugins/assay/config/modules.yaml` for the
full, heavily-commented file, including every `excluded:` reason verbatim.)

## Rules the registry enforces

All six are checked by `tests/test_module_registry.py`, not left to prose:

1. **`excluded:` is mandatory, not optional.** Every spine-backed skill
   listed under a spine must appear under **either** `skills:` or
   `excluded:` — never both, never neither. An `excluded:` entry needs a
   real reason ("this grain has no ordered stage ladder"), not a `TODO`.
   This is what turns "we didn't get to it" into a recorded decision, and
   it is exactly why the `browse` module's spine-backed exclusions each
   say something specific rather than reusing one blanket sentence.
2. **Exactly one `default: true` spine per module with a non-empty
   `spines:`.** So a bare `--module checkout` is unambiguous about which
   grain it means.
3. **A skill under `skills:` must have what it needs in the spec.**
   Inclusion is never a false promise — flip a skill from `excluded:` to
   `skills:` only once the spec actually carries the blocks that skill
   reads.
4. **`handoff` is declarative** (spine → spine, on a join key) rather than
   bespoke SQL buried inside whichever skill happens to need a cross-grain
   join.
5. **`scoping_doc` is inert at query time.** Recording a human-facing
   scoping document here (this template leaves it `null` for both modules)
   prevents the next person from wiring a skill to it by mistake.
6. **Every spine declares `status: live | declared`.** `live` means a
   queryable view exists on the default engine — a committed fixture here,
   a curated view in a real deployment. `declared` means the contract is
   authored and linted but nothing is provisioned behind it, and a
   `declared` spine must therefore list **no** skills — promising a skill on
   a spine with no data is the exact false promise rule 3 exists to prevent.

**Important:** `skills:` / `excluded:` are documentation plus a lint target,
**not** a runtime permission gate. `resolve_spine()` returns the spine's
dict as-is, and `spec_path()` reads only `cfg["spec"]`. A caller who passes
`--module browse --spine session` to a spine-backed skill this file marks
excluded will run it, then fail downstream — on a missing spec key, or (as
with `browse`) on a view that does not exist. That is intended: a clear
error on a real gap beats a silent wrong answer.

## How a spine-backed skill resolves its spec

Every spine-backed skill goes through `_shared/spine.py`'s
`load_registry()` / `resolve_spine()` / `spec_path()`. Resolution order:

1. **`ASSAY_FUNNEL_SPEC`** env var, if set — wins outright. Exists for tests
   and one-off overrides, not for normal use.
2. Otherwise, `--module` / `--spine` (defaulting to the module's
   `default: true` spine) resolved through `config/modules.yaml`.

This is one shared resolution path for four skills. A second module registered
tomorrow needs no skill-side change — only a new registry entry and a new
funnel-spec file in the same shape.

## The handoff edge, in full

Declarative cross-grain composition: spine → spine, on a join key, with a
**stated coverage**:

```yaml
handoff:
  to: checkout/session          # "module/spine" — both must be registered,
                                 # and `on` must be the target spine's primary key
  "on": checkout_session_id     # QUOTED — bare `on:` parses as the boolean
                                 # True under YAML 1.1 (PyYAML), a real bug this
                                 # exact file once had
  coverage: 0.42                # share of SOURCE rows that reach the target
  coverage_basis: declared_estimate | measured_source_share
  coverage_measured_at: null    # a date iff the basis is measured
  coverage_denominator: "..."   # what the share is OF, in words
  coverage_note: "..."          # what the number is NOT
```

`coverage` is declarative rather than SQL in a skill because the interesting
part of a cross-grain composition is not the join, it is the **denominator**
— and a join buried in a helper cannot state one. The registry linter is
strict about the field precisely because it invites self-deception:

- It must be strictly between 0 and 1.
- `coverage_basis` must match whether `coverage_measured_at` is present
  (`declared_estimate` ↔ `null`; `measured_source_share` ↔ a real date).
- A `declared` source spine may only ever carry a `declared_estimate` — you
  cannot report a *measurement* from a spine that has no data behind it.

### Why `browse → checkout` is the worked example

The `browse` module registers a second spine at **browse-session grain**
plus this handoff edge to `checkout/session` on `checkout_session_id`. It is
`declared`, not `live`: the stage ladder, terminal branches, allow-lists,
maturity lags, and join key are authored and linted, and no view is
provisioned behind it — a browse spine over the same cohort-week window as
the committed checkout spine would be roughly 2.4× as many rows, because
most visits never produce a cart, and carrying both would undermine the
template's whole promise that a fresh clone runs offline with nothing
provisioned.

The edge exists to make one thing concrete: **the hard part of a
cross-grain handoff is the denominator, not the schema.** A browse session
that never produces a cart carries no `checkout_session_id` at all, so the
edge's `coverage` is the browse-to-cart **conversion rate** over all browse
sessions — not a join-quality or data-quality figure, and a low value means
shoppers left, not that rows failed to match. It also **cannot** be derived
from the checkout spine, because the sessions it is about are absent from
it entirely: a different population, not a filter.

That number is therefore labelled `coverage_basis: declared_estimate` with a
null measurement date, and the registry linter enforces the pairing — a
spine with no data behind it can only ever carry an estimate, so an estimate
cannot wear a measurement's clothes. `config/coverage.yaml`'s
`browse_to_cart_handoff` entry says the same fact to a stakeholder, in the
instrumentation-coverage vocabulary rather than the registry's.

## Where this meets the entity model

The funnel spec carries its own `primary_entity:`, `entities:`, and
`relationships:` blocks (kept consistent with the stage ladder), which is a
*different* registry from `plugins/assay/skills/query-metric/entities/` — the
named entity model, one markdown file per entity, that metrics reference via
`entity:` / `relationships:` in their own frontmatter. The two describe the
same underlying business objects from two angles: the funnel spec anchors a
*spine* to the entities its stage ladder traverses; the entity registry
anchors a *metric* to the entity its numerator and denominator measure. See
`entities/README.md`'s "Entities vs. dimensions" for the rule that keeps a
categorical (`payment_method`) from being modelled as a standalone entity
when it is really a dimension anchored to one (`payment`).

## Acceptance gate for a change here

Because `resolve_spine()` and `spec_path()` sit upstream of every
spine-backed skill's SQL, a change to how the registry resolves a spec
changes the generated SQL for **all four** skills at once. The gate this
repository holds itself to is **identical generated SQL** for a canonical
request set across all four skills, offline
(`tests/test_metric_sql_regression.py`, `tests/test_spine_maturity.py`), plus
a spot-check against a live warehouse read if you have one.

**Byte-identical live output is not a testable gate**, deliberately: a
live artifact carries a run date and manifest fingerprints, and the
underlying counts genuinely move as cohorts mature between two runs minutes
apart. Test the *query construction*, not the *live value*.

**Blast radius:** if spec loading or normalization changes, `verify`'s
definition/query fingerprints shift and every artifact's manifest goes
stale at once. That is a real cost of touching this file — plan for it the
same way you would a schema migration, not a config tweak.
