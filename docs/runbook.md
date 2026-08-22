# assay — Runbook

*No-fluff how-to. Pair with the [glossary](glossary.md) (what the terms
mean), the [skill cheat sheet](skill-cheatsheet.md) (every flag + example),
and [`what-is-assay.md`](what-is-assay.md) (why this exists). Want to see
real output before running anything? [`examples/`](../examples/) has it.*

Everything here is invoked in chat with Claude Code / Cursor. Skills are
`assay:<skill>`. When in doubt, start at `assay:catalog`.

---

## 0. Why this runbook is organized this way

A governance layer only earns its keep if the people using it actually reach
for it instead of routing around it. So this runbook is organized around
three jobs, in the order a new user actually needs them:

1. **Get a real answer fast** — the three first questions (§2), the decision
   guide (§3), and the compose chains (§4).
2. **Extend it without breaking the guarantees** — authoring a metric (§5),
   the maintenance tasks that keep the registers honest (§6).
3. **Know what you cannot skip** — the governance you don't get to opt out
   of (§8), and the traps that bite people who skip it anyway (§9).

---

## 1. One-time setup

Nothing is required to start. `ASSAY_ENGINE` defaults to `sqlite`, which
reads the committed fixtures — no cloud account, no credentials, no network.
Everything below is only needed to point at a **real** warehouse.

```
assay:setup
```

It runs an offline doctor (gcloud, service-account env, ADC, Python deps),
then a `SELECT 1` probe. Manual equivalent:

```bash
# 1. deps (the sqlite path needs only pyyaml; the bigquery path also needs
#    google-cloud-bigquery)
python3 -m venv .venv
.venv/bin/pip install -r plugins/assay/skills/query-metric/scripts/requirements.txt
.venv/bin/pip install -r tests/requirements.txt          # to run the test suite

# 2. point at your project and auth as YOURSELF (never a service account)
export ASSAY_ENGINE=bigquery
export ASSAY_BIGQUERY_PROJECT=<your-gcp-project>
gcloud auth application-default login

# 3. if a query is refused with a service-account error, this is usually why:
unset GOOGLE_APPLICATION_CREDENTIALS
```

**Rules that bite people:**
- Use `.venv/bin/python`, not bare `python3`, or you will hit missing-deps
  (exit 3).
- Missing/stale ADC → exit 2, re-run `gcloud auth application-default login`.
- Service-account / impersonated creds → exit 4 (refused by design — there is
  no fallback).
- macOS/Homebrew global `pip install` hits PEP 668; the venv avoids it.
- There is **no default project baked in.** Shipping one would hand every
  fork somebody else's billing account — `ASSAY_BIGQUERY_PROJECT` must be set
  explicitly before the live probe means anything.

---

## 2. The three first questions (the daily driver)

This is the muscle memory to teach a new user (and yourself):

1. **What can I ask?** → `assay:catalog` — every governed metric and latency
   KPI, by module.
2. **Can I measure ⟨X⟩ yet?** → `assay:coverage cart abandon reason` — is it
   instrumented; if not, who owns the blocker and what to watch.
3. **Ask a real question** → `assay:query-metric cart to checkout-started
   conversion by week for the last 8 weeks` — a governed, tier-stamped
   number, run as you.

There is no open-ended exploratory lane in this template (a T3-style ad hoc
SQL surface is out of scope — see [`governance-model.md`](governance-model.md#5-accuracy-tiers--r4)).
A recurring question that has no registered metric belongs in
[`adding-a-module.md`](adding-a-module.md), not in a workaround.

---

## 3. Which skill for which question (decision guide)

| You want to… | Use | Notes |
|---|---|---|
| Get one governed number / rate | `assay:query-metric` | Plain-English question; it picks the metric, validates the allow-list, writes an artifact. |
| See what's available to ask | `assay:catalog` | Or `assay:load-context catalog` for the machine form. |
| Know if something is instrumented | `assay:coverage <thing>` | Returns live / partial / empty / blocked / declined / flag_gated + blocker + owner. |
| Find **where** a funnel leaks | `assay:funnel-dropoff` | Stage drop-off + terminal-branch attribution for a segment. |
| Find **how slow** a stage is | `assay:stage-timing` | p50/p90/p99 per transition, with n. |
| Compare two segments or before/after | `assay:segment-compare` | Shown as `A` · `B` · `Δ`; filter-compare or pre/post. Descriptive only. |
| Catch a regression across all groups | `assay:funnel-pulse` | Scheduled, mature-cohort sweep; tells you *which* group to dig into. |
| Explain why our number ≠ their number | `assay:reconcile` | Names the cause (window, maturity, timezone, filter, grain). |
| Check an old artifact is still valid | `assay:verify` | Offline definition/query drift check via the manifest. |
| Pull a governed **definition** into another tool | `assay:load-context metric/{slug}` | No warehouse access; the cross-plugin API. |
| Project where an immature cohort is heading | `assay:funnel-nowcast` | Mack chain ladder over the spine; FORECAST-lane P10/P50/P90, capped at cohort size, with a self-scoring backtest. |
| Measure a shipped release causally | *(not implemented — see below)* | Outlined as `assay:release-scorecard`. |
| Answer a question about **people**, not sessions | *(not implemented — see below)* | Outlined as `assay:customer-journey`. |
| Publish a governed artifact to a wiki | *(not implemented — see below)* | Outlined as `assay:publish`. |

**Rule of thumb:** `query-metric` to put a figure in front of someone;
`load-context` to ground reasoning in the canonical definition.

**On the three outlined skills** (`release-scorecard`, `customer-journey`,
`publish`): their names, intended behavior, and the reason they are not
implemented in this template all appear in the module registry
(`config/modules.yaml`, each module's `excluded:` block) and in the
[skill cheat sheet](skill-cheatsheet.md#outlined-skills-documented-not-implemented).
Do not invoke them expecting real output — there is no script behind them
yet. Forking this template and building one is exactly what
[`adding-a-module.md`](adding-a-module.md) points you toward.

---

## 4. Core workflows (the compose chains)

### 4a. Answer → chart → share

```
assay:query-metric payment approval rate by payment_method, last 12 weeks
assay:visualize-metric          # newest artifact; infers chart type, injects it back
assay:export-report             # → self-contained PDF
#   …or…
assay:render-chart               # → interactive self-contained HTML
```

- `query-metric` writes `outputs/assay/{date}-{slug}.md` and prints the path
  — it does **not** paste the artifact into chat.
- `visualize-metric` and `render-chart` read the **same chart-spec JSON**, so
  the PNG (for the PDF) and the HTML (for sharing) are the same governed
  data.
- Funnel chart in one shot: `assay:visualize-metric show the checkout
  funnel` (composes the stage metrics from `funnels.yaml`).
- Want a whole multi-panel view instead of one chart?
  `assay:render-dashboard` composes several chart specs, KPI cards, and
  tables into one governed HTML.

### 4b. Investigate a regression (the deep-dive loop)

```
assay:funnel-pulse                          # broad sweep → which group moved?
assay:funnel-dropoff <that segment>         # where in the funnel it leaks (terminal branches)
assay:stage-timing <that segment>           # did a stage just get slower?
assay:segment-compare before vs after <ship date>   # describe the shift (no causal claim)
```

This is the recurring shape of a real investigation, turned into governed
one-liners instead of half a day of trap-laden hand-querying. Note the last
step: `segment-compare` is explicitly descriptive — it will not, and should
not, claim the change *caused* the shift. A pre-registered causal read is
exactly what `release-scorecard` would add, and it is one of the four
outlined skills — not available in this template.

> **Maturity caveat:** `funnel-pulse` is **stage-aware** — each stage is
> monitored over its own mature window (checkout-started near-current ~0d →
> return-window-complete ~44d), so a fresh-week abandonment regression
> surfaces within days while deep stages stay settled. `--maturity flag`
> reverts to a legacy single-window gate.

### 4c. "My dashboard says X, assay says Y"

```
assay:reconcile
```

Gather first (it asks if you don't give them): their value, the metric, our
window, **their** window, their filters/segment, their grain. It re-runs one
tweaked convention at a time until one reproduces their number and reports
the named `cause` — or `UNKNOWN` (it never invents a cause). The most common
answer by a wide margin is `MATURITY_CUTOFF`: on a deep-stage metric like
`delivered_to_return_window_complete_rate`, the difference between a settled
read and one that reaches into recent, still-immature cohorts is enormous
(roughly 88% vs. 37% in this template's fixtures), not marginal.

### 4d. "Can we even measure this?"

```
assay:coverage cart abandonment
```

If `blocked`/`empty`/`flag_gated`, it gives the blocker, the owner, and the
ref to watch — so the ask stops bouncing back days later as "sorry, no." If
it's not in the register, it routes you to `assay:catalog` rather than
guessing at a verdict.

### 4e. Hand a governed number to another tool

Consumers call the read APIs and parse the contract
(`plugins/assay/contracts/consumer-api.md`):

- `assay:load-context metric/cart_to_checkout_start_conversion` → the
  canonical definition.
- `assay:query-metric …` → a live number + manifest.

Watch for the sentinels (`TIER_REFUSED`, `OFF_ALLOWLIST`, `AUTH_REQUIRED`, …)
and the `STALE:` / `MATURITY:` suffixes — they are stable, prefix-matchable
lines. See the [glossary](glossary.md#error-sentinels-the-consumer-api-contract).

---

## 5. Authoring & maintaining metrics

Full detail in `plugins/assay/skills/query-metric/AUTHORING.md` — read it
before writing a fifth caveat that says the same thing three ways.

### Add a metric (dummy → live lifecycle)

```bash
# 1. copy the annotated template
cp plugins/assay/skills/query-metric/templates/example-metric.md \
   plugins/assay/skills/query-metric/metrics/checkout/<your-metric>.md

# 2. edit YAML: name, display_name, accuracy_tier: T2, source_view,
#    source_project, time_column, numerator_column, denominator_column,
#    value_format, allowed_dimensions, allowed_filters,
#    default_lookback_weeks, entity, module, status: dummy

# 3. (dummy mode) drop a sample CSV with the referenced columns
#    data/assay/sample/<view_slug>.csv, or reuse the shipped example_view.csv

# 4. smoke-test in dummy mode — inspect the artifact + the SQL in Provenance
assay:query-metric <question that maps to your metric>

# 5. validate frontmatter
.venv/bin/python -m pytest

# 6. provision the BigQuery view; spot-check vs. an external reference

# 7. flip ONE line and re-run — routes to the real engine, no code change
#    status: dummy  →  status: live
```

**Why `dummy` is opt-in only:** the CSV-stub mock path triggers *only* on
`status: dummy`. Missing ADC/deps/service-account detection are hard errors,
never a silent fallback to synthetic numbers. If you are looking at real
numbers, you are authed as yourself and the metric is `status: live`.
Always.

**Pre-flight before `status: live`:** the view exists; every referenced
column exists; `time_column_type` matches; grain matches `time_grain`; the
metric's `maturity_stage` (if set) resolves against the module's funnel spec;
at least one query spot-checked against an external reference.

**Bundled vs. consumer metrics:** the helper walks the consumer dir
(`$ASSAY_METRICS_DIR`, default `context/assay/metrics/`) first, then the
plugin-bundled catalog — **consumer wins on a `name:` collision**, so a team
can override one metric without forking the catalog. Adding to the
*bundled* catalog is a PR against this repository (see
[`CONTRIBUTING.md`](../CONTRIBUTING.md)).

---

## 6. Recurring maintenance tasks

| Task | Command | When |
|---|---|---|
| Run the governance suite | `.venv/bin/python -m pytest` | Before every PR. |
| Run the scrub gate | `python3 scripts/scrub_check.py` | Before every commit — also runs inside `pytest`. |
| Run every functional skill offline | `.venv/bin/python scripts/demo_skills.py` | Before every PR that touches a skill script. |
| Regenerate fixtures after a spec change | `python3 scripts/generate_fixtures.py` | When you change the funnel spec's stage ladder, terminal branches, or dimension values in a way the generator needs to reflect. |
| Schedule the funnel monitor | wire `funnel-pulse` via whatever scheduler you already use, leaving `--today` unset | Weekly sustained-health check, once you have a real warehouse. The skill does not self-schedule. |

---

## 7. Publishing

This template does **not** ship a publishing path. `assay:publish` is one of
the three outlined skills — the intended shape (a read-only wiki page,
resolved through a page registry, composed with a tier stamp and verbatim
caveats, gated on explicit human approval before any write) is described in
the [skill cheat sheet](skill-cheatsheet.md#outlined-skills-documented-not-implemented),
but there is no script behind it in this repository. Distribution today
means `assay:export-report` (a local PDF) or `assay:render-chart` /
`assay:render-dashboard` (a local HTML file) — both of which a human then
sends, hosts, or attaches themselves.

---

## 8. Governance you can't skip

These are what make output safe to hand to someone who was not in the room
when the query ran. Do not route around them. Full detail (and honest
limits) in [`governance-model.md`](governance-model.md).

- **Runs as you (ADC), never a service account**, on the warehouse path. No
  shared key; a refusal here is the system working, not a bug to work
  around.
- **Tiers are load-bearing.** T2 is served and stamped; T1 and T3 are
  refused outright. A consumer can never silently embed a non-governed
  number — that is the `TIER_REFUSED` guarantee.
- **Refusals are clean (R1–R6).** Name the rule + the offending input, give
  the allow-list, no apology, no workaround, no artifact written.
  **R6/PII is a hard refuse enforced in code** — no override.
- **Warn-level guardrails surface, don't block.** Feed-cutover
  `DISCONTINUITY:` and column/view `CAVEAT (...):` notes auto-attach.
  Surface them; don't suppress them.
- **Every number carries a receipt.** The manifest
  (`metric_def_sha` / `query_sha` / `result_sha`) plus the append-only
  audit log make a number replayable and attributable. Run `assay:verify`
  on any artifact you're about to reuse.
- **One way to the warehouse.** The access-path hook denies every route
  except the vetted skill scripts — never satisfy "just check something
  quickly" with a raw `bq`/`gcloud`/`curl` call, even under pressure.

---

## 9. Traps & gotchas (hard-won)

- **The cart is the denominator, not the order.** `order_id` is `NULL` on
  every session that never confirmed, so an order-grain read is structurally
  blind to the abandoned cart — the single largest exit in this ladder.
  Every end-to-end metric here is a share of `carts_created`.
- **Maturity is per (cohort × stage), everywhere.** Both `query-metric` and
  the spine-backed skills gate each stage/transition to the cohorts mature
  *for that stage* — checkout-start settles in minutes, return-window in
  ~44 days. The spine skills default to `--maturity stage`;
  `--maturity flag` restores a legacy whole-funnel gate, and
  `funnel-pulse` refuses `--no-mature` outright rather than running
  ungated silently.
- **The stage-maturity lags are illustrative, not measured, in this
  template.** `illustrative: true` / `calibrated_at: null` in the funnel
  spec. Do not treat a fork's fixture-derived lag as calibrated until you
  have actually measured it against real settle time.
- **`payment_decisioned` needs a per-method override.** Instant card
  authorization dominates volume and decides in seconds; a bank transfer
  routed through review takes days. A blended submit-to-decision number is
  badly wrong for the slow method unless you split by `payment_method`.
- **`terminal_branch` is a curated heuristic**, evaluated deepest-stage-first
  — not a field a source system emits. The boundary cases (stale vs.
  abandoned, cancelled vs. out-of-stock) are the view's judgment.
- **Zero rows ≠ a result.** If a query returns nothing, the skill reports
  the empty/typo'd-filter condition — it will not narrate a fake answer.
- **The `browse` module has no data behind it.** Every spine-backed skill on
  `browse/session` is excluded in the registry, with a stated reason. Don't
  expect `--module browse --spine session` to return real numbers.
- **On the sqlite engine, no identity is asserted.** That is a documented,
  tested exception (see [`governance-model.md`](governance-model.md#1-per-user-identity)), not an oversight — but it is
  worth knowing before assuming "assay always runs as a real person" holds
  on every path.

---

## 10. Where things live (fast map)

```
plugins/assay/skills/<skill>/SKILL.md         # how each skill behaves (the source of truth)
plugins/assay/skills/query-metric/
  AUTHORING.md                                # how to add/change a metric
  doctrine/accuracy-tiers.md                  # tiers + refusal rules R1–R6
  doctrine/ownership-governance.md            # owner / review-cadence doctrine
  metrics/checkout/*.md                       # the 15 registered metrics (+ LINEAGE.md)
  entities/                                   # the named entity model
  views/checkout-views.md                     # spine/weekly data dictionary + caveats
  templates/MODULE-ONBOARDING.md              # the "make this yours" checklist
plugins/assay/config/                         # modules.yaml, funnel specs, funnels, coverage, guardrails
plugins/assay/contracts/consumer-api.md       # the cross-plugin response contract
scripts/                                      # generate_fixtures, demo, demo_skills, scrub_check
tests/                                        # governance tests + catalog linter
outputs/                                      # generated artifacts (git-ignored, reproducible)
docs/                                         # this documentation suite
```

When a behavior question isn't answered here, read the relevant `SKILL.md` —
each is self-contained and authoritative.
