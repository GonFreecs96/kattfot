# Adding a module — making this template yours

*This is the single most important doc in a template, because a template
that nobody successfully forks was never worth writing. The full,
step-by-step checklist lives in*
**[`plugins/kattfot/skills/query-metric/templates/MODULE-ONBOARDING.md`](../plugins/kattfot/skills/query-metric/templates/MODULE-ONBOARDING.md)**
*— read it, not a paraphrase of it. This page is the map, not the
territory: what a module actually is, the two or three steps where people
lose real time, and why the checklist orders things the way it does.*

## What you are actually replacing

Everything domain-specific in this repository — the `checkout` module, its
stage ladder, its fifteen metrics, its fixtures — lives behind exactly two
seams:

1. **The module registry + funnel spec** (`config/modules.yaml`,
   `config/checkout-funnel-spec.yaml`) — declares the stage ladder, the
   terminal branches, the allow-lists, and the maturity lags for one spine.
2. **The metric definitions** (`skills/query-metric/metrics/checkout/*.md`) —
   fifteen markdown files, each a `SUM/SUM` ratio over the pre-aggregated
   weekly view.

Neither seam touches `_shared/` (identity, PII, manifest, dialect, the
adapters) or any skill's Python. Swap what is behind these two seams for
your own business and the governance engine — everything this documentation
suite calls "the product" — comes along unmodified. That is the whole
design: **the domain is data, the governance is code**, and only one of
those two should change when you fork this.

## The path, in the order that actually matters

The full checklist (linked above) is authoritative; this is why it is
ordered the way it is, and where people actually lose time.

**0. Decide whether you need a spine at all.** Be honest here — it saves a
lot of work. You need only **metrics** if every question is a ratio over a
governed grain ("what share of X did Y, by week, sliced by Z"). You need a
**spine** only if a question is about *where* things ended up (drop-off
attribution), *how long* a transition takes at a percentile (a `SUM/SUM`
metric structurally cannot express one), or whether a stage's conversion
moved against its own baseline. The `browse` module in this repo registers
with `spines: {}` — a legitimate shape — to show that "no spine" is a real
option, not a placeholder for "not done yet."

**1–2. Register the module, then author the entities.** Cheap, and
`tests/test_module_registry.py` starts holding you to the registry's rules
immediately, so run `pytest` after this step rather than saving it for the
end.

**3. Author the funnel spec — this is where the two expensive mistakes
live:**

- **`legacy_exclude_column` + `legacy_exclude_value` are both-or-nothing.**
  Set only the column and the exclusion clause is omitted *entirely* —
  `col != NULL` evaluates to `NULL`, not `TRUE`, so a half-configured legacy
  exclusion silently excludes nothing and the output looks completely
  normal. This exact failure mode existed in the private repository this
  template was derived from, invisibly, until someone happened to check.
- **`stage_maturity` should be measured, not guessed.** This template's
  lags are hand-authored and marked `illustrative: true` /
  `calibrated_at: null`, and `tests/test_maturity.py` holds those flags in
  place. When you point this at your own warehouse, *generate* the block by
  measuring open-anchored settle time on your own spine (the p95 of
  `DATE_DIFF(stage_date, open_date, DAY)`), then clear the flags. A
  hand-picked lag is an assumption wearing a measurement's clothes — the
  entire point of the mechanism is that the number was observed, and
  skipping this step means every maturity cutoff downstream is
  theatre.

**4. Write the view DDL.** `tests/test_view_ddl_pii.py` scans it for
identifiers matching the R6 (PII) denylist and fails on a hit — a defense at
the data layer, because an allow-list above a view that already exposes a
personal column is moot.

**5. Author the metrics.** Every metric is T2 (the catalog linter enforces
it), and the `## Caveats` section you write is reproduced **verbatim** into
every artifact this metric will ever produce — it is the highest-leverage
prose in the repository, which is also exactly why an unflattering caveat
has to go in rather than get smoothed over.

**6. Generate fixtures.** Non-negotiable: deterministic (fixed seed),
ISO-8601 dates as text (sqlite has no date type, and `BETWEEN` on a
non-ISO-8601 string fails *silently*, not loudly), and a genuine maturation
tail — recent cohorts should visibly not have reached their deep stages yet,
or the maturity machinery has nothing real to withhold and you are testing a
mock of your own logic rather than the logic itself.

**7. Add the funnel composition** (`config/funnels.yaml`) with an explicit
`end_to_end` reference metric — the product of the stage rates will not
exactly match it, because each stage carries its own maturity cutoff, and a
reader needs to see both numbers to understand why.

**8. Wire the spine skills.** Flip a skill from `excluded:` to `skills:` in
the registry *only* once the spec actually carries what that skill reads —
`tests/test_module_registry.py` checks this, so an inclusion can never be a
false promise. Keep a real reason on every exclusion you leave in place.

## The checklist

Copy this into your PR description; the full version (with the *why* behind
each line) is in `MODULE-ONBOARDING.md`:

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

## What you are not forking

Nothing under `plugins/kattfot/skills/_shared/`, the hooks, or the skill
scripts themselves needs to change for a new domain. If you find yourself
editing `_shared/pii.py`, `_shared/identity.py`, or a skill's Python to make
your domain work, stop — that is very likely a sign the domain belongs in
the funnel spec or the metric definitions instead, not in the governance
engine. The one exception worth knowing: `_shared/pii.py`'s `SAFE_ALLOWLIST`
is where you add a new opaque join key or geography column your domain
introduces (a new tokenized id, a new aggregate-geography column) — that is
additive configuration, reviewed as governance, not a code change to the
denylist logic itself.
