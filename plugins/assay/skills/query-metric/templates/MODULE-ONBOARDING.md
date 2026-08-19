# Onboarding a new module

A **module** is a governed business domain: a set of metrics, optionally a spine
(an instance-grain view with a stage ladder), and the registry entry that ties them
together. This is the checklist for adding one, in dependency order, with the
reason for each step — because several of them look skippable and are not.

The `checkout` module in this repo is the worked example. Read it alongside this.

## 0. Decide whether you need a spine at all

Be honest here; it saves a lot of work.

You need only **metrics** if the questions are ratios over a governed grain: "what
share of X did Y, by week, sliced by Z". Register the module with `spines: {}` — a
legitimate shape, and `browse` in `config/modules.yaml` is there to show it.

You need a **spine** if any question is one of:

- *where* did the instances that failed end up (drop-off attribution),
- *how long* did a transition take at the 90th percentile (a SUM/SUM metric
  structurally cannot express a percentile),
- did a stage's conversion move recently versus its own baseline.

A spine costs a curated instance-grain view plus a funnel spec. Do not build one to
serve questions the weekly aggregate already answers.

## 1. Register the module

`plugins/assay/config/modules.yaml`:

```yaml
modules:
  your-module:
    label: "Human-Readable Name"
    scoping_doc: null
    spines: {}          # or a spine entry — see step 3
```

`tests/test_module_registry.py` will now hold you to the registry's rules, so run
`pytest` after this step rather than at the end.

## 2. Author the entities

`skills/query-metric/entities/`, one file per entity. Read that directory's
`README.md` first — particularly the entities-vs-dimensions rule, which is the
distinction people get wrong: a **noun with its own identity** is an entity;
a **categorical** is an enumerated `dimensions:` block on its parent entity.

Register only entities that back a metric or anchor a governed dimension. A registry
of every noun in the domain is a liability, not documentation.

## 3. Author the funnel spec (spine modules only)

Copy `config/checkout-funnel-spec.yaml` and work through it. Two things carry
disproportionate risk:

**`legacy_exclude_column` + `legacy_exclude_value` are both-or-nothing.** If you set
only the column, the clause is omitted entirely — deliberately, because
`col != NULL` evaluates to NULL rather than TRUE and would silently zero *every*
row. A spec with a half-configured legacy exclusion excludes nothing and looks
completely normal in the output. Set both, or neither.

**`stage_maturity` should be measured, not guessed.** The lags in this template are
hand-authored and labelled `illustrative: true` / `calibrated_at: null`, and the test
suite holds those flags in place. When you point this at real data, generate the
block by measuring open-anchored settle time on your own spine (the p95 of
`DATE_DIFF(stage_date, open_date, DAY)`) and drop the flags. A hand-picked lag is an
assumption wearing a measurement's clothes, and the whole value of the mechanism is
that the number was observed.

## 4. Write the view DDL

`skills/query-metric/views/`. Two views, as in the worked example: the instance-grain
spine, and a pre-aggregated weekly view for the ratio metrics.

`tests/test_view_ddl_pii.py` scans every DDL file for identifiers matching the R6
denylist and fails `pytest` on a hit — there is no CI, so this only catches you if
you run the suite. That is deliberate defense at the data layer: an
allow-list above a view that exposes a personal column is moot. Use tokenized keys
(`customer_token`) and aggregate geography (`market`, `postal_code`) — both are
explicitly allow-listed as non-PII.

## 5. Author the metrics

`skills/query-metric/metrics/<your-module>/`, one file per metric. The directory name
must equal the `module:` value in every metric's frontmatter; the linter enforces it.

Copy `templates/example-metric.md`. The required frontmatter fields are enumerated in
`AUTHORING.md` and checked by `tests/test_metric_catalog.py`.

Two rules worth internalising before you write fifteen of them:

- **Every metric is T2.** T1 comes from the system of record; T3 has no governed path.
  The linter enforces this.
- **Caveats are not a formality.** They are reproduced verbatim into every artifact,
  chart, and PDF, which makes them the highest-leverage prose in the repo — and it
  means an unflattering one has to go in. A caveat a reader discovers for themselves
  was a caveat you were hiding.

Put shared rules in a `LINEAGE.md` in the metrics directory (no `name:` key, so the
resolver and linter skip it) and reference them by id from `business_rules:`. Fifteen
copies of a paragraph become fifteen inconsistent paragraphs.

## 6. Generate fixtures

Extend `scripts/generate_fixtures.py`, or write a sibling. Non-negotiable properties:

- **Deterministic**, with a fixed seed. A fixture that changes between runs cannot be
  a test oracle.
- **ISO-8601 dates as text.** sqlite has no date type, so every offline date
  comparison is a string comparison, and ISO-8601 is the only common format whose
  lexicographic order matches chronological order. Epoch integers or `MM/DD/YYYY`
  fail `BETWEEN` *silently*.
- **A genuine maturation tail.** Set a fixed observation instant and let a stage flag
  fire only when its timestamp actually precedes it. Recent cohorts should be visibly
  incomplete, or the maturity machinery has nothing real to act on and you are testing
  a mock of your own logic.

## 7. Add the funnel composition

`config/funnels.yaml`: the ordered stage metrics plus the `end_to_end` reference
metric. `visualize-metric --funnel <key>` then composes them.

Name the end-to-end metric explicitly. The product of the stage rates will *not*
exactly equal it — each stage carries its own maturity cutoff, so they are computed
over slightly different cohort windows — and a reader needs to be able to see both
numbers to understand why.

## 8. Wire the spine skills (Phase 3)

Flip a skill from `excluded:` to `skills:` in the registry only once the spec actually
carries what it reads. `tests/test_module_registry.py` checks this, so an inclusion
cannot be a false promise. Keep a real reason on every remaining exclusion: "this grain
has no ordered stage ladder" is a reason, "TODO" is not.

## Checklist

- [ ] Module registered; `pytest` green
- [ ] Entities authored; each backs a metric or anchors a dimension
- [ ] Funnel spec authored (spine modules); both legacy-exclusion keys set or neither
- [ ] `stage_maturity` measured, or explicitly flagged illustrative
- [ ] View DDL written; PII linter green
- [ ] Metrics authored, all T2, directory name matches `module:`
- [ ] Shared rules in `LINEAGE.md`, referenced by id
- [ ] Fixtures deterministic, ISO dates, visible maturation tail
- [ ] `funnels.yaml` entry with an explicit `end_to_end`
- [ ] `pytest` and `python3 scripts/scrub_check.py` both green
