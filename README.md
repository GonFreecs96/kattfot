# kattfot

> *Kattfoten* — "the cat's paw" — is the three-crown hallmark Sweden's assay
> office has stamped on verified silver since 1754. This repo stamps the
> equivalent on warehouse numbers. (Formerly published as `assay`.)

**kattfot** is a working template for **governed agentic analytics** — a Claude
Code / Cursor plugin marketplace whose subject is not charts but *governance*:
how an agent can be given real access to a data warehouse and still be unable
to produce a number nobody can defend.

It ships as a complete, runnable example rather than a framework you have to
imagine into being. Clone it, run the demo, and you are looking at real
governed artifacts — before you have configured a single credential.

## Sixty seconds

```bash
git clone https://github.com/GonFreecs96/kattfot.git && cd kattfot
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt \
  -r plugins/kattfot/skills/visualize-metric/scripts/requirements.txt
.venv/bin/python scripts/demo.py --skip-pdf
```

No cloud account, no credentials, no network: `KATTFOT_ENGINE` defaults to
`sqlite`, so the metric's real SQL runs against the committed fixture CSVs.
About two seconds later, `outputs/kattfot/` holds a governed artifact, its
chart spec, and the chart as SVG, PNG and self-contained HTML. The artifact
opens like this:

```yaml
---
metric: cart_to_checkout_start_conversion
accuracy_tier: T2
source_view: analytics.cart_checkout_funnel_weekly
engine: sqlite
fixture_backed: true
time_range_start: 2026-04-27
time_range_end: 2026-08-10
maturity_cutoff: 2026-08-10
dimensions: ['channel']
manifest:
  plugin_version: 0.2.0
  metric_def_sha: 630f5144a6d892eb
  query_sha: a2edd162be9583b0
  result_sha: 9960f2458b68254c
---
> ⚠️ FIXTURE DATA — these numbers come from committed synthetic fixtures,
> not from a warehouse.
```

Every line of that header is a control: the tier says what the number may be
used for, the maturity cutoff says which cohorts were withheld, the three
fingerprints let `kattfot:verify` tell you offline, months later, whether the
definition, the query or the result has drifted. The watermark is load-bearing.

Three gates keep the claim "it all runs offline" true, and all three run in CI
on every push: `pytest` (1,400+ tests), `scripts/scrub_check.py` (the
deny-token gate), and `scripts/demo_skills.py` (every functional skill, in an
environment scrubbed of credentials). [`examples/`](examples/) has committed
output you can read without cloning.

## How it is meant to be operated

The code enforces the controls; people still own the definitions. The division
of labour this template assumes:

- **A metric has a named owner.** Each definition's frontmatter carries
  `owner`, `accuracy_tier`, `version` and `last_reviewed`; the catalog flags a
  definition as stale when nobody has reviewed it inside the window. A
  definition change is a pull request against a markdown file, reviewed like
  code.
- **The agent runs unattended on the governed lane.** Reads of registered
  metrics, drift checks, the funnel pulse and the nowcast need no human in the
  loop, because every answer is allow-listed, tiered and receipted, and
  anything outside the allow-list is a refusal that names the rule.
- **A human signs off on anything that widens the surface.** Switching to the
  exploratory lane needs the user's explicit confirmation; a promoted draft
  metric stays inert (T3, `status: draft`) until a reviewer edits the tier;
  T1 (financial or regulatory) is refused outright rather than served
  without the machinery to back it.
- **Identity is never shared.** Every warehouse query runs as the individual
  human via browser OAuth. A service account is refused in code, so an audit
  line always names a person.

## The governance thesis

Anyone can write `SELECT COUNT(*) ... GROUP BY ...`. The hard part of
"self-serve analytics" was never the SQL — it is making sure that when an
agent runs that query on someone's behalf, the number it hands back carries
enough of its own history that a stranger, six months later, can tell whether
to trust it. `kattfot` answers that with mechanisms enforced in code, not in a
system prompt:

- **Per-user identity.** Every warehouse query runs as the individual human via
  browser OAuth (Application Default Credentials). A service-account or
  impersonated credential is **refused**, not warned about
  (`_shared/identity.py`).
- **One way in.** A `PreToolUse` hook (Claude Code) and a `beforeShellExecution`
  hook (Cursor) share one matcher that denies every warehouse access path
  except the plugin's vetted scripts — no `bq` CLI, no `gcloud`, no ad hoc
  `python -c` using `google-cloud-bigquery` directly, no `curl`/`wget` to
  `googleapis.com` — including inside `bash -c` and behind `sudo`/`env`
  prefixes. Without this, every other guard below is merely advisory.
- **Allow-listed metrics, dimensions, and filters.** A metric declares exactly
  what it may be grouped and filtered by; anything else is a clean refusal
  that names the rule and shows what *is* available.
- **PII refused in code.** A shared denylist (`_shared/pii.py`) refuses
  personal columns as a dimension or filter — control **R6** — *even when a
  metric's own allow-list would have permitted it*. This runs before the
  allow-list check, not after, precisely so a mistake in one metric's
  frontmatter cannot authorize a personal column.
- **Accuracy tiers.** T2 (operational, decision-grade) is served by the
  governed metric runtime and stamped on every artifact. T1
  (financial/regulatory) is refused outright — serving it honestly needs
  machinery this template does not implement. T3 (exploratory) is served,
  but only through its own lane (below), never as a governed metric.
- **An exploratory lane that relaxes the allow-lists, never the account.**
  `kattfot:explore` runs free-form SELECT SQL for the questions no governed
  metric can serve — over a **fail-closed register** of declared surfaces,
  read-only and single-statement in code, PII-scanned across every
  identifier, audit-chained, receipt-carrying, and stamped **T3 — not
  decision-grade** as loudly as the fixture watermark — a stamp that survives
  into every chart, dashboard, and PDF built from it. Governed refusals
  carry a machine-readable `EXPLORABLE` referral, and the lane switch always
  requires the user's explicit confirmation. `kattfot:promote` closes the
  loop: it scaffolds a tamper-checked draft metric from an exploration,
  inert until a human review edits the tier — the R4 gate *is* the review
  gate.
- **Per-stage maturity.** A funnel stage is withheld only until *that stage*
  settles, rather than gating the whole funnel on the slowest one — the part
  most conversion reporting gets wrong.
- **Content-addressed provenance.** Every artifact carries a manifest —
  fingerprints of the metric definition, the exact query, and the result —
  so `kattfot:verify` can later tell you, offline, whether a number still means
  what it claimed to.
- **Two things a `SUM/SUM` metric structurally cannot express**, served from
  a session-grain spine instead: **terminal-branch attribution** (of all
  sessions, the share that *ended* in each mutually exclusive branch) and
  **percentiles** (a median is not a ratio of two sums).
- **A scrub gate.** `scripts/scrub_check.py` blocks the private-original
  names this template was derived from, and the test suite asserts the repo
  stays clean.

The full reasoning behind each control — including what each one does
**not** guarantee — is in [`docs/governance-model.md`](docs/governance-model.md).

> **Status: Phase 4 complete, plus a research pass (2026-08) and the
> exploratory lane.** All **twenty-one skills** are accounted for.
> **Eighteen** run today, offline, against committed fixtures — and that is
> checked rather than claimed: `scripts/demo_skills.py` runs every one of
> them in an environment with `HOME` scrubbed of credentials and
> `KATTFOT_ENGINE` pinned to `sqlite`, and fails if any cannot. The remaining
> **three** (`release-scorecard`, `customer-journey`, `publish`) ship as
> documented outlines, not code; the module registry records each one's
> exclusion with a specific reason rather than a TODO — that gap is real and
> stated. The research pass promoted `funnel-nowcast` from outline to
> functional (a run-time-estimated Mack chain ladder dissolved the
> fit-the-generator objection), hash-chained the audit log into
> tamper-evidence, added a measured maturity-calibration script, an ambiguity
> refusal, and an indirect-injection adversarial suite — each grounded in the
> literature reviewed in
> [`docs/reference/research-review.md`](docs/reference/research-review.md).
> The exploratory-lane pass added `kattfot:explore` (governed T3 ad-hoc SQL
> over a fail-closed surface register) and `kattfot:promote` (exploration →
> reviewed draft metric), 16 → 18. Alongside the code sit the documentation
> suite under [`docs/`](docs/) and real committed output under
> [`examples/`](examples/).

## Quick start

The sixty-second block above is the shortest path. The rest of this section is
the full surface: the gates, every skill, the compose chain, and a real
warehouse.

**Want proof before you clone?** [`examples/`](examples/) has real, committed
output — a governed metric artifact, an interactive chart, a dashboard, PDF
exports (including one from a spine-analysis funnel breakdown), and the
chart-spec provenance JSON behind it — all fixture-backed and watermarked,
browsable on GitHub with nothing installed.

```bash
.venv/bin/python -m pytest        # the test suite
python3 scripts/scrub_check.py    # the deny-token gate
```

Both must be green before a commit. The scrub gate is also a test
(`tests/test_scrub_check.py::test_this_repo_is_clean`), so `pytest` alone
catches a leak.

### Run every skill, offline

```bash
.venv/bin/pip install -r plugins/kattfot/skills/visualize-metric/scripts/requirements.txt
.venv/bin/pip install -r plugins/kattfot/skills/export-report/scripts/requirements.txt

.venv/bin/python scripts/demo_skills.py
```

That runs all eighteen functional skills and prints one line each. It is the
third gate, alongside `pytest` and the scrub check, and it exists because
"everything works offline" is the claim a reader cannot verify by inspection
and the one most likely to quietly stop being true — a skill gains a
dependency, or starts needing a project id, and nothing fails until somebody
clones the repo.

"Offline" is enforced rather than assumed: each step runs as a subprocess
whose `HOME` points at an empty temporary directory (so ADC cannot be found),
with `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, and
`KATTFOT_BIGQUERY_PROJECT` removed from the environment.

The two optional dependencies above (`matplotlib`, `fpdf2`) are offline ones,
not network ones. Without them the gate fails and names the install command;
`--allow-missing-optional` downgrades that to a reported gap.

### Walk the compose chain

```bash
.venv/bin/python scripts/demo.py --all
```

That produces, in `outputs/kattfot/`:

| Output | What it is |
|---|---|
| `<date>-<metric>.md` | The governed artifact: frontmatter with tier and manifest, a Results table with numerator and denominator, the injected `## Visualization`, verbatim caveats, and the parameterized SQL |
| `charts/<slug>.spec.json` | The chart spec — the exact points plotted, as provenance |
| `charts/<slug>.svg` / `.png` | The static chart (matplotlib) |
| `charts/<slug>.html` | The same spec rendered interactive (stdlib only, self-contained) |
| `<date>-<metric>.pdf` | A portable stakeholder PDF with the chart inline |
| `charts/<date>-checkout-funnel.*` | The eight-stage funnel, composed from eight governed metrics |
| `audit-log.jsonl` | One append-only trace line per query |

Every one of those is watermarked **FIXTURE DATA** / **MOCK DATA**, because
the numbers come from a seeded generator rather than a business. That
watermark is load-bearing, not decoration.

Just the chart renderers, with no dependencies at all:

```bash
python3 plugins/kattfot/skills/render-chart/scripts/render_html.py \
  --spec plugins/kattfot/skills/render-chart/examples/trend.spec.json --out /tmp/chart.html

python3 plugins/kattfot/skills/render-dashboard/scripts/render_dashboard.py \
  --spec plugins/kattfot/skills/render-dashboard/examples/example.dashboard.json --out /tmp/dash.html
```

### Point it at a real warehouse

```bash
export KATTFOT_ENGINE=bigquery
export KATTFOT_BIGQUERY_PROJECT=<your-gcp-project>
gcloud auth application-default login
python3 plugins/kattfot/skills/setup/scripts/doctor.py --probe
```

You will also need the curated views:
`plugins/kattfot/skills/query-metric/views/` ships the DDL for both (the
session-grain spine and the weekly aggregate built on it), with
`${WAREHOUSE_PROJECT}` / `${SOURCE_DATASET}` placeholders to substitute.

## The skill surface

Twenty-one skills, invoked as `kattfot:<skill>`. Eighteen are complete and
runnable today; three ship as documented outlines because giving them honest
numbers needs parameters calibrated against a real book, and fitting them to
a synthetic fixture would produce a curve describing the generator rather
than any business.

| Skill | What it does |
|---|---|
| `kattfot:setup` | Environment doctor: gcloud, service-account env, ADC, deps, live probe |
| `kattfot:catalog` | The front door — every registered metric, latency KPI, and spine, read off disk |
| `kattfot:coverage` | "Can I measure X yet?" from the instrumentation register, with the blocker and its owner |
| `kattfot:load-context` | The consumer API: governed definitions by scope, with contract sentinels |
| `kattfot:query-metric` | A governed number: allow-listed, tiered, caveated, with a provenance receipt |
| `kattfot:visualize-metric` | Artifact → chart spec → SVG/PNG, with faithfulness rules |
| `kattfot:render-chart` | Chart spec → one self-contained interactive HTML (stdlib only) |
| `kattfot:render-dashboard` | Dashboard spec → one self-contained interactive HTML (stdlib only) |
| `kattfot:export-report` | Governed artifact → a portable PDF |
| `kattfot:verify` | Offline definition/query drift check against an artifact's manifest |
| `kattfot:reconcile` | Why their number differs from ours, attributed to a named cause |
| `kattfot:funnel-dropoff` | The stage funnel **plus** terminal-branch leak attribution |
| `kattfot:stage-timing` | Percentile stage latency (p50/p90/p99, mean, n) per transition |
| `kattfot:segment-compare` | A vs B, or before vs after, across funnel, leak mix, and timing |
| `kattfot:funnel-pulse` | Stage-aware recent-vs-baseline regression monitor, ranked by impact |
| `kattfot:funnel-nowcast` | Cohort-maturation now-cast (Mack chain ladder) for a not-yet-settled cohort: P10/P50/P90 capped at cohort size, with a self-scoring backtest |
| `kattfot:explore` | The exploratory lane: free-form SELECT SQL over a fail-closed surface register, T3-stamped, PII-scanned, audit-chained — for the questions no governed metric can serve |
| `kattfot:promote` | Exploration → governed draft: a tamper-checked metric scaffold, inert (T3 + draft) until human review edits the tier |

The five spine skills read the **session-grain spine** rather than the weekly
aggregate, because that is the only way to get what a `SUM/SUM` metric
structurally cannot express: where each session actually ended up, a median,
and an immature cohort's projected ultimate.

**Outlined, not implemented** (documented in the skill cheat sheet, excluded
in the module registry with a specific reason):

| Skill | What it would do |
|---|---|
| `kattfot:release-scorecard` | A pre-registered release's causal verdict via BSTS counterfactual (and DiD when a control segment is specified) |
| `kattfot:customer-journey` | Customer-grain journey analysis — the multi-order analog to the session-grain funnel |
| `kattfot:publish` | Push a governed artifact to a wiki as a read-only page, gated on explicit human approval before any write |

Full detail, every flag, and a worked example for each: see
[`docs/skill-cheatsheet.md`](docs/skill-cheatsheet.md).

## Choosing an engine

| `KATTFOT_ENGINE` | Where the query runs | Identity asserted | Percentiles |
|---|---|---|---|
| `sqlite` (default) | Committed fixture CSVs, loaded into an in-memory database | No — see below | Computed client-side |
| `bigquery` | The curated view, as the authenticated human | Yes, in code | Pushed down (`APPROX_QUANTILES`) |

Both run the **same SQL**. Skills author one canonical dialect (BigQuery) and
`_shared/dialect.py` translates it once for sqlite, so dialect knowledge stays
out of the governance layer and an artifact's `query_sha` receipt does not
depend on where it ran. The translation set is enumerated exhaustively in
`dialect.TRANSLATIONS` and pinned by a golden test; anything outside it
raises rather than being guessed at.

**One guarantee is genuinely weaker on the sqlite path, and it is worth
stating plainly rather than discovering:** `assert_user_identity()` is not
called. That is an explicit branch, with the reasoning written down in
`_shared/adapters/sqlite_adapter.py` — a fixture read touches no warehouse and
no credentials, so there is no query to attribute to a person, and requiring
a cloud login to read a committed CSV would be theatre that also makes
`git clone && pytest` impossible. In exchange every fixture-backed result is
marked `fixture_backed`, which becomes the watermark seen above, so such a
number cannot pass itself off as measured. Every *other* control —
allow-lists, PII refusal, the tier gate, maturity cutoffs, manifest receipts
— runs upstream of the adapter on both engines and cannot be reached around.
`tests/test_adapters.py` pins both halves of that trade.

## Environment variables

All configuration is `KATTFOT_`-prefixed. There are **no defaults pointing at
any real warehouse** — a template that shipped one would ship somebody
else's billing account.

| Variable | Used by | Meaning |
|---|---|---|
| `KATTFOT_ENGINE` | `_shared/adapters` | `sqlite` (default) or `bigquery`. An unknown value refuses rather than falling back. |
| `KATTFOT_BIGQUERY_PROJECT` | `query-metric`, `setup` | The GCP project. Expands the `${KATTFOT_BIGQUERY_PROJECT}` placeholder in metric definitions. No fallback — left unset, that placeholder appears literally (and intentionally) in artifact frontmatter and prose, since this public template has no real project id to substitute. |
| `KATTFOT_SAMPLE_DIR` | the sqlite adapter, the CSV-stub oracle | Fixture CSVs (default `data/kattfot/sample/`). |
| `KATTFOT_METRICS_DIR` | `query-metric` | Consumer-authored metric definitions, searched before the bundled catalog (default `context/kattfot/metrics/`). |
| `KATTFOT_OUTPUTS_DIR` | `query-metric`, `export-report` | Where artifacts land (default `outputs/kattfot/`). |
| `KATTFOT_AUDIT_LOG` | `query-metric` | Audit-log path. Disable with `KATTFOT_AUDIT_DISABLE=1`. |
| `KATTFOT_FUNNEL_SPEC` | `_shared/spine.py` | Override the resolved funnel-spec path outright (test/override hook). |
| `KATTFOT_FUNNELS_CONFIG` | `visualize-metric` | Override the funnel-composition config path. |
| `KATTFOT_COVERAGE_FILE` | `_shared/coverage.py`, `coverage`, `load-context` | Instrumentation coverage register path. |
| `KATTFOT_GUARDRAILS_FILE` | `_shared/guardrails.py` | Warn-level guardrails config path. |
| `KATTFOT_EXPLORATION_FILE` | `_shared/exploration.py`, `explore` | Exploration-register path. **Fail-closed**: unlike coverage/guardrails, a missing or unparseable register is a hard error, because it is a permission boundary, not advisory metadata. |

Every spine skill also takes `--engine` to override `KATTFOT_ENGINE` for one
run, and `--today YYYY-MM-DD` to pin the maturity window. Pass
`--today 2026-08-17` when reading the committed fixtures: that is the instant
they were generated for (recorded in `data/kattfot/sample/FIXTURES.md`), and
reading a fixed fixture at the real today withholds more of the tail every
week until the whole book looks immature.

`GOOGLE_APPLICATION_CREDENTIALS` is not configuration here — if it is set,
the identity check **refuses to run**, because it would mean a service
account.

## Architecture in brief

```
plugins/kattfot/
  hooks/                     PreToolUse matcher (Claude Code); .cursor/hooks/ mirrors it for Cursor
  config/                    the module registry, two funnel specs, funnel compositions, coverage + guardrails,
                              and the exploration register (the fail-closed T3 surface allow-list)
  skills/
    _shared/                 the governance engine: identity, PII, manifest, verify, reconcile,
                              numfmt, spine, coverage, guardrails, dialect, and the adapter seam
    _shared/adapters/        one BigQuery adapter, one sqlite adapter, capability advertisement
    query-metric/            the governed metric runtime: 15 T2 metrics, 8 entities, doctrine,
                              templates, view DDL, AUTHORING.md
    <18 skill dirs>/         one SKILL.md + scripts/ each — see docs/skill-cheatsheet.md
  contracts/consumer-api.md  the cross-plugin response contract (sentinels, formats)
scripts/                     repo tooling, NOT part of the shipped plugin
  generate_fixtures.py       the deterministic fixture generator
  demo.py                    the compose chain, end to end, offline
  demo_skills.py             all eighteen functional skills, offline, as a gate
  scrub_check.py             the deny-token gate
data/kattfot/sample/           the two committed fixture CSVs + FIXTURES.md (seed, instant, trim)
tests/                       governance tests + catalog linter, run by pytest
outputs/                     generated artifacts (git-ignored, reproducible)
examples/                    committed copies of real output — see examples/README.md
docs/                        this documentation suite
```

Every skill resolves its own root (`${CLAUDE_PLUGIN_ROOT}/skills/<skill>` on
Claude Code, `<workspace>/.cursor/skills/<skill>` on Cursor, else the
directory of the loaded `SKILL.md`), so the same plugin runs identically in
either tool. See [`docs/module-registry-design.md`](docs/module-registry-design.md)
for how a module, a spine, and a funnel spec fit together, and
[`docs/governance-model.md`](docs/governance-model.md) for the full
control-by-control breakdown.

### The two registers

`config/coverage.yaml` answers "can I measure X yet?" — including who owns
the blocker — and `config/guardrails.yaml` attaches warn-level caveats to a
query automatically: feed cutovers the window crosses, plus column and view
notes. Both **degrade to empty on a parse error**, because advisory metadata
that can break a query is worse than none.

### The browse module and its handoff edge

The `browse` module registers a second spine at **browse-session grain** plus
a declarative `handoff` edge to `checkout/session` on `checkout_session_id`.
It is marked `declared`, not `live`: the stage ladder, terminal branches,
allow-lists, maturity lags, and join key are authored and linted, and no view
is provisioned behind it — carrying that fixture too would roughly triple the
committed data for a template whose whole promise is that a fresh clone runs
offline. The edge exists to make one thing concrete: the hard part of a
cross-grain handoff is the denominator, not the schema. See
[`docs/module-registry-design.md`](docs/module-registry-design.md) for the
full walkthrough.

## The example domain

A fictional **direct-to-consumer e-commerce checkout**, chosen because its
funnel has a long, honest maturation tail — the thing that makes naive
conversion reporting wrong.

The spine grain is a **checkout session (cart)**, and the stages are: Cart
Created → Checkout Started → Payment Submitted → Payment Decisioned → Order
Confirmed → Shipped → Delivered → Return Window Complete. That last stage
settles about 30 days after delivery, so the most recent cohorts are always
partly unresolved. The engine withholds each stage only until *that stage*
settles rather than gating the whole funnel on the slowest one — which is
what lets an early-funnel rate stay fresh while a return-window rate honestly
waits six weeks.

Governed dimensions are `channel`, `payment_method`, `market`, `category`,
and `checkout_version`. `payment_method` is the one that drives *maturity* as
well as conversion: instant card authorization settles in seconds, a bank
transfer routed through a clearing cycle and a review queue takes days, so
the funnel spec carries a per-method maturity override.

Two modules exist: `checkout` (the full spine, 15 metrics, `live`) and
`browse` (upstream of it, `declared`, with the handoff edge described above).

### About the fixture numbers, and about the lags

Two honest caveats that the repo also states in machine-readable form:

- **The data is synthetic.** `scripts/generate_fixtures.py` is a seeded
  random process. Treat the *shape* as illustrative and the *values* as
  meaningless. Every artifact says so.
- **The maturity lags are hand-authored, not measured.** In a real deployment
  that block is generated by measuring open-anchored settle time on your own
  spine. The whole value of the mechanism is that the numbers were
  *observed*, so the spec marks itself `illustrative: true` /
  `calibrated_at: null`, every artifact repeats it, and a test holds those
  flags in place.

Swap the domain by replacing the funnel spec and the metric definitions — no
engine change required. [`docs/adding-a-module.md`](docs/adding-a-module.md)
is the walkthrough, and it points at the full checklist in
`plugins/kattfot/skills/query-metric/templates/MODULE-ONBOARDING.md`.

## Regenerating the fixtures

```bash
python3 scripts/generate_fixtures.py                    # both committed fixtures
python3 scripts/generate_fixtures.py --emit weekly      # just the aggregate
python3 scripts/generate_fixtures.py --spine-weeks 0 \
    --out-dir /tmp/kattfot-full                           # the full ~22 MB spine
python3 scripts/generate_fixtures.py --sessions 200000 --weeks 104
```

Deterministic: the same arguments produce byte-identical files, so a diff in
a committed CSV always means a parameter changed. The committed spine is
trimmed to the most recent 19 cohort weeks — the widest window any spine
skill reads at the funnel spec's own defaults — while the pre-aggregated
weekly view keeps the full book, because it is small enough to. See
`data/kattfot/sample/FIXTURES.md` for the seed, the observation instant, and
the exact row counts.

## Honest limitations

Worth stating plainly rather than discovering:

- **The numbers are fictional.** Everything the committed fixtures produce
  describes a seeded random process, not a real business. Every artifact,
  chart, and PDF says so via a MOCK DATA / FIXTURE DATA watermark.
- **The maturity lags are illustrative, not calibrated.** `calibrated_at` is
  `null` and `illustrative: true` on every stage-maturity block, and a test
  holds those flags in place so this cannot silently start implying it was
  measured. `stage-timing/scripts/calibrate_maturity.py` measures the settle
  curve and proposes calibrated lags, but on the committed fixtures its output
  keeps the illustrative flag on purpose — a measured *generator* is not a
  measured business.
- **`funnel-nowcast` runs, but on fixtures it now-casts the generator.** Its
  Mack chain ladder estimates every development factor from the committed
  spine, so the mechanism is real and offline-checkable; the *numbers* describe
  the seeded generator, are watermarked as such, and its own `--backtest`
  reports that the intervals undercover on this synthetic book (the documented
  failure mode of model-based nowcasts — see
  [`docs/reference/research-review.md`](docs/reference/research-review.md)).
- **Three of twenty-one skills are outlines, not code.** `release-scorecard`
  needs causal parameters — BSTS priors, control-series selection, pre/post
  windows — that are judgment calls against a real book and, unlike
  `funnel-nowcast`'s development factors, cannot be estimated from the spine at
  run time. `customer-journey` needs a person-grain spine this template does
  not register. `publish` writes to an external wiki, which a template has
  nowhere to write to.
- **The `browse` module is `declared`, not `live`.** Its funnel spec is
  authored and linted; no view or fixture backs it, so every spine-backed
  skill is excluded on it in the module registry.
- **The access-path hook is a blocklist, not a sandbox.** It cannot see into
  a script file's contents — a Python file that never mentions "bigquery" in
  the Bash command text that invokes it is not something the matcher can
  catch. It is a technical backstop for the sanctioned-path rule documented
  in each `SKILL.md`, not a substitute for it.
- **The PII denylist is defense-in-depth inside the vetted helpers only.**
  Reach the warehouse any other way and it has no effect; the durable,
  tool-independent backstop is column-level security in the warehouse
  itself, which this template does not configure for you.
- **On the sqlite engine, no identity is asserted.** See "Choosing an
  engine" above — every other control still runs, but there is genuinely no
  query to attribute to a person on that path.

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/what-is-kattfot.md`](docs/what-is-kattfot.md) | The narrative case: the problem governed analytics solves, and why |
| [`docs/governance-model.md`](docs/governance-model.md) | Every control, what it guarantees, and what it honestly does not |
| [`docs/glossary.md`](docs/glossary.md) | Spine, grain, accuracy tier, maturity cutoff, terminal branch, allow-list, and the rest of the vocabulary |
| [`docs/runbook.md`](docs/runbook.md) | Install, first run, the compose chain, the decision guide for which skill to use |
| [`docs/skill-cheatsheet.md`](docs/skill-cheatsheet.md) | Every skill, every flag, a worked example each |
| [`docs/module-registry-design.md`](docs/module-registry-design.md) | The registry + funnel-spec contract, and the handoff edge |
| [`docs/adding-a-module.md`](docs/adding-a-module.md) | The "make this yours" path — swap the domain without touching the engine |
| [`docs/reference/agentic-analytics-essay.md`](docs/reference/agentic-analytics-essay.md) | Background: ontologies, knowledge graphs, and semantic layers for agentic analytics |
| [`docs/reference/research-review.md`](docs/reference/research-review.md) | The literature grounding every control — what it validates, critiques, and the improvements built in response (with citations) |
| [`examples/`](examples/) | Real, committed output — a governed artifact, its chart, its PDF export, and a spine-analysis artifact, generated by actually running the skills |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the repository layout, how to run
the checks, and how to add or change a metric.

## License

MIT — see [LICENSE](LICENSE).
