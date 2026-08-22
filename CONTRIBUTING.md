# Contributing to assay

`assay` is a public template for **governed, defensible analytics** over a data
warehouse. It carries the architecture of a real internal system with zero
proprietary content, so contributions here fall into two very different
categories:

1. **Improving the template itself** — the governance engine, the hooks, the
   sixteen functional skills, the docs, the test suite.
2. **Forking it for your own domain** — swapping the `checkout` module for
   your own business, which is a local exercise on your fork and does not
   come back here. See [`docs/adding-a-module.md`](docs/adding-a-module.md)
   for that path.

This file is about (1). If you are doing (2), you still want
[Local setup](#local-setup) and [Running the checks](#running-the-checks)
below, but you are not bound by [What stays out of a PR](#what-stays-out-of-a-pr).

> **Before anything else: run the scrub gate.** `assay` was scrubbed out of a
> private internal repository before it was made public. `python3
> scripts/scrub_check.py` — a fixed, 40-token deny-list (the private repo's
> own name, carrier/partner names, internal warehouse identifiers,
> issue-tracker project keys, contact addresses) — is the **only** thing
> standing between a contribution and one of those tokens re-entering this
> public repo. **This project has no CI.** The gate is wired into `pytest`
> (see [Running the checks](#running-the-checks)) so it cannot be skipped by
> skipping a separate CI job that doesn't exist — but nothing forces you to
> run `pytest` either. Keeping this repo clean is a **manual discipline**,
> not an automated backstop: run the gate yourself, every time, especially
> if you're porting language in from somewhere else.

## Repository layout

```
plugins/assay/                         # the shipped plugin
  hooks/
    hooks.json                         # Claude Code PreToolUse — see "The access-path hook"
    scripts/bq_path_matcher.py         # shared matcher (both runtimes)
    scripts/enforce_bq_path.py         # Claude Code adapter
  skills/query-metric/                 # governed metric runner + the bundled metric catalog
    metrics/checkout/*.md              # one file per metric (YAML frontmatter = the contract)
    doctrine/accuracy-tiers.md         # tiers (T1/T2/T3) + refusal rules R1–R6
    doctrine/ownership-governance.md   # owner / review-cadence doctrine
    entities/                          # the named entity model (one file per entity)
    templates/                         # example-metric.md, MODULE-ONBOARDING.md
    AUTHORING.md                       # how to author a metric — read this first
  skills/_shared/                      # identity / PII / manifest / spine / dialect / adapters
  skills/<other-skill>/                # one SKILL.md + scripts/ per skill
  config/                              # modules.yaml, funnel specs, funnels.yaml, coverage, guardrails
  contracts/consumer-api.md            # the cross-plugin response contract
.cursor/hooks/                         # the Cursor mirror of the Claude Code hook
scripts/                               # repo tooling — NOT part of the shipped plugin
  generate_fixtures.py                 #   the deterministic fixture generator
  demo.py                              #   the compose chain, end to end, offline
  demo_skills.py                       #   all sixteen functional skills, offline, as a gate
  scrub_check.py                       #   the deny-token gate
data/assay/sample/                     # the two committed fixture CSVs + FIXTURES.md
tests/                                 # governance tests + catalog linter (run by pytest)
docs/                                  # this documentation suite
outputs/                               # generated artifacts — git-ignored (reproducible)
examples/                              # committed, real generated output — what a fresh
                                        #   clone actually produces, see examples/README.md
```

## Local setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt                                     # to run tests
.venv/bin/pip install -r plugins/assay/skills/query-metric/scripts/requirements.txt  # to run metrics live
.venv/bin/pip install -r plugins/assay/skills/visualize-metric/scripts/requirements.txt  # charts
.venv/bin/pip install -r plugins/assay/skills/export-report/scripts/requirements.txt     # PDFs
```

The default `ASSAY_ENGINE=sqlite` needs only `pyyaml` and runs entirely
offline against the committed fixtures. Live queries (`ASSAY_ENGINE=bigquery`)
authenticate as **you** via ADC (`gcloud auth application-default login`).
Service-account credentials are refused by design — never wire one in.

## Running the checks

```bash
.venv/bin/python -m pytest        # the governance suite + catalog linter
python3 scripts/scrub_check.py    # the deny-token gate
.venv/bin/python scripts/demo_skills.py   # every functional skill, offline
```

All three should be green before you open a PR.

`pytest` enforces, among other things:

- **Identity** — service-account / impersonated credentials are refused
  (`tests/test_identity.py`), and the sqlite path's deliberate exception to
  that (no identity to assert with no warehouse involved) is pinned
  (`tests/test_adapters.py`).
- **Refusal rules R1–R3 + SQL safety** — unregistered metric, off-allow-list
  dimension/filter, and filter values are bound parameters, never inlined
  (`tests/test_query_metric_governance.py`).
- **R6 / PII** — the code-level denylist refuses personal columns even when a
  metric's own frontmatter would have allowed them (`tests/test_pii_refusal.py`,
  `tests/test_view_ddl_pii.py` — the latter scans view DDL directly).
- **Catalog lint** — every metric has the required frontmatter, valid enums,
  a unique name, and is tier **T2** (the only served tier)
  (`tests/test_metric_catalog.py`).
- **Module registry rules** — every spine lists each spine-backed skill under
  exactly one of `skills:` / `excluded:` (never both, never neither, and an
  `excluded:` entry needs a real reason), exactly one default spine per
  module, and a `declared` spine cannot list a skill
  (`tests/test_module_registry.py`).
- **Maturity honesty** — `illustrative: true` and `calibrated_at: null` stay
  set on every hand-authored `stage_maturity` block, so this repo cannot
  quietly start implying its lags were measured (`tests/test_maturity.py`).
- **The scrub gate itself** — `tests/test_scrub_check.py::test_this_repo_is_clean`
  runs `scripts/scrub_check.py` as a test, so a leak fails `pytest`, not just
  a separate CI step you might skip.

`scripts/scrub_check.py` walks every git-tracked file for a fixed list of
deny-tokens (the private repo this template was derived from, carrier/partner
names, internal warehouse identifiers, issue-tracker project keys, and
contact addresses) and exits non-zero on any hit. **Every contribution —
prose, code, comments, examples — must keep this clean.** If you are porting
language from somewhere else, run the gate before you commit, not after.

## The access-path hook

`plugins/assay/hooks/hooks.json` registers a Claude Code `PreToolUse` hook on
the `Bash` tool. `.cursor/hooks.json` registers the same policy for Cursor via
`beforeShellExecution`. Both call into the shared matcher at
`plugins/assay/hooks/scripts/bq_path_matcher.py` and deny commands that reach
BigQuery/GCP outside the vetted skill scripts under
`plugins/assay/skills/*/scripts/` (bare `bq` CLI, `gcloud` referencing
BigQuery, inline python using `google-cloud-bigquery` directly, curl/wget to
`googleapis.com`). This is a technical backstop for the sanctioned-path rule
documented in every `SKILL.md` — it runs outside the model's control.

Scope and limits, worth knowing before you touch it:

- **Dual runtime.** Claude Code uses `PreToolUse`/Bash with
  `${CLAUDE_PLUGIN_ROOT}`; Cursor uses `beforeShellExecution` and
  `.cursor/hooks/enforce_bq_path.py` as a thin adapter over the same matcher.
  Neither replaces reading the relevant `SKILL.md` — both are command-string
  blocklists.
- **It's a blocklist, not a sandbox.** It cannot see into a script file's
  contents — `python3 some_file.py` bypasses it if "bigquery" never appears
  in the Bash command text itself.
- **Edit regexes with care and re-run the tests.**
  `tests/test_bq_bypass_hook.py` pins both directions (real vetted script
  invocations must stay allowed; bypass vectors must stay denied).
- **This does not replace warehouse-side policy tags.** See
  `plugins/assay/skills/query-metric/doctrine/accuracy-tiers.md` (R6) and
  `plugins/assay/skills/_shared/pii.py`.

## Adding or changing a metric

1. Read **`plugins/assay/skills/query-metric/AUTHORING.md`** — it documents
   the full YAML contract, the required and optional frontmatter fields, and
   how to write a caveat that earns its place.
2. Add/edit the metric file under
   `plugins/assay/skills/query-metric/metrics/<module>/`. The directory name
   must equal the metric's own `module:` field — the linter enforces it.
3. Run `pytest` — the catalog linter validates your frontmatter, and the
   maturity contract checks your `maturity_stage` (if any) resolves against
   the module's funnel spec.
4. If the metric belongs to a funnel, add it to `config/funnels.yaml`'s
   ordered `stages:` list and name an `end_to_end` reference metric.
5. Open a PR.

For a whole new business domain rather than one metric, see
[`docs/adding-a-module.md`](docs/adding-a-module.md), which points at the
full checklist in
`plugins/assay/skills/query-metric/templates/MODULE-ONBOARDING.md`.

## Documentation ownership

`README.md`, `CONTRIBUTING.md`, and everything under `docs/` are written to be
read on their own — they should not need the underlying code open beside
them to make sense. If you change behavior that a doc describes (a flag, a
default, an exit code, a file path), update the doc in the same PR. A stale
doc in a template is worse than no doc, because a fork carries it forward
without noticing.

## Branch & PR flow

- Branch off `main`; never commit directly to `main`.
- One logical change per PR. Keep the diff reviewable.
- Run all three checks in [Running the checks](#running-the-checks) before
  opening the PR, and say in the description which of them you ran.

## What stays out of a PR

- **Anything the scrub gate would flag.** Real warehouse project IDs, real
  company/partner/carrier names, real internal ticket prefixes, real contact
  addresses. If you need an example of one of these, invent a fictional one
  in the style already used by the `checkout` module.
- **Credentials of any kind**, query outputs (`outputs/`), and rendered chart
  HTML — all already git-ignored; do not force-add them.
- **A local registry with real values.** Config that defines the module
  registry, funnel specs, coverage, and guardrails is tracked because it *is*
  the substance of the plugin — but a personal or workspace-specific copy
  carrying a real warehouse project ID or a live wiki page ID uses the
  `.local.yaml` / `*-registry.yaml` convention in `.gitignore`, not a tracked
  file.
