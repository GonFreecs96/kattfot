# assay

A working template for **governed agentic analytics** — a Claude Code / Cursor
plugin marketplace whose subject is not charts but *governance*: how an agent can
be given real access to a data warehouse and still be unable to produce a number
nobody can defend.

The mechanisms, all enforced in code rather than in a prompt:

- **Per-user identity.** Warehouse queries run as the individual human via browser
  OAuth (ADC). A service-account or impersonated credential is *refused*, not warned
  about (`_shared/identity.py`).
- **One way in.** A `PreToolUse` hook (Claude Code) and a `beforeShellExecution`
  hook (Cursor) share one matcher that denies every warehouse access path except
  the plugin's vetted scripts — the `bq` CLI, `gcloud`, `dbt`/`mf`, raw REST, and
  ad hoc `python -c` are all blocked, including inside `bash -c` and behind
  `sudo`/`env` prefixes. Without this, the guards below are optional.
- **PII refused by rule.** A shared denylist (`_shared/pii.py`) refuses personal
  columns as a dimension or filter *even when a metric explicitly allow-lists
  them*, with an allow-list for opaque keys and aggregate geography so real
  analysis still works.
- **Accuracy tiers and allow-lists.** Metrics declare a tier and the exact
  dimensions and filters they permit; anything else is a clean refusal that names
  the rule and shows what *is* available.
- **Per-stage maturity.** A funnel stage is withheld only until *that stage*
  settles, rather than gating the whole funnel on the slowest one. This is the part
  most conversion reporting gets wrong.
- **Provenance receipts.** Every artifact carries content fingerprints of the
  metric definition, the query, and the result (`_shared/manifest.py`), so
  `assay:verify` can later tell you offline whether a number still means what it
  claimed to. Wall-clock time is excluded from every hash, so a faithful replay
  reproduces them exactly.
- **Honest limits, in machine-readable form.** A coverage register answers "can I
  measure X yet?" with the blocker and its owner, so "not instrumented" is a governed
  answer rather than an analyst's recollection. A guardrails register attaches
  warn-level caveats to a query automatically — the feed cutovers its window crosses,
  the notes its columns carry.
- **Two capabilities a `SUM/SUM` metric structurally cannot express**, served from the
  session-grain spine instead: **terminal-branch attribution** (of all carts, the share
  that *ended* in each mutually exclusive branch — checked to sum to the carts created,
  because branches that do not sum decompose nothing) and **percentiles** (a median is
  not a ratio of two sums; the adapter pushes `APPROX_QUANTILES` down on BigQuery and
  computes it exactly offline, and every output says which happened).
- **A scrub gate.** `scripts/scrub_check.py` blocks the private-original names
  this template was derived from, and the test suite asserts the repo is clean.

> **Status: Phase 3.** All **fifteen functional skills** run today, offline, against
> committed fixtures — and that is checked rather than claimed:
> `scripts/demo_skills.py` runs every one of them in an environment with `HOME`
> scrubbed of credentials and `ASSAY_ENGINE` pinned to `sqlite`, and fails if any
> cannot. Four skills ship as documented outlines only (`funnel-nowcast`,
> `release-scorecard`, `applicant-journey`, `publish`); the module registry records
> each one's exclusion with a specific reason.

## Quick start

Nothing to configure. `ASSAY_ENGINE` defaults to `sqlite`, which runs each metric's
real SQL against the committed fixture CSVs — no cloud account, no credentials, no
network.

```bash
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt

.venv/bin/python -m pytest        # the test suite
python3 scripts/scrub_check.py    # the deny-token gate
```

Both must be green before a commit. The scrub gate is also a test
(`tests/test_scrub_check.py::test_this_repo_is_clean`), so `pytest` alone catches a
leak.

### Run every skill, offline

```bash
.venv/bin/pip install -r plugins/assay/skills/visualize-metric/scripts/requirements.txt
.venv/bin/pip install -r plugins/assay/skills/export-report/scripts/requirements.txt

.venv/bin/python scripts/demo_skills.py
```

That runs all fifteen skills and prints one line each. It is the third gate, alongside
`pytest` and the scrub check, and it exists because "everything works offline" is the
claim a reader cannot verify by inspection and the one most likely to quietly stop
being true — a skill gains a dependency, or starts needing a project id, and nothing
fails until somebody clones the repo.

"Offline" is enforced rather than assumed. Each step runs as a subprocess whose `HOME`
points at an empty temporary directory, so
`~/.config/gcloud/application_default_credentials.json` does not exist and a warehouse
query would be refused outright; `GOOGLE_APPLICATION_CREDENTIALS`,
`GOOGLE_CLOUD_PROJECT`, and `ASSAY_BIGQUERY_PROJECT` are removed, along with any
`ASSAY_*` config override that would make the gate certify your local files instead of
the committed ones.

The two optional dependencies above are offline ones, not network ones — matplotlib for
the static chart, fpdf2 for the PDF. Without them the gate fails and names the install
command; `--allow-missing-optional` downgrades that to a reported gap.

### Walk the compose chain

```bash
.venv/bin/python scripts/demo.py --all
```

That produces, in `outputs/assay/`:

| Output | What it is |
|---|---|
| `<date>-<metric>.md` | The governed artifact: frontmatter with tier and manifest, a Results table with numerator and denominator, the injected `## Visualization`, verbatim caveats, and the parameterized SQL |
| `charts/<slug>.spec.json` | The chart spec — the exact points plotted, as provenance |
| `charts/<slug>.svg` / `.png` | The static chart (matplotlib) |
| `charts/<slug>.html` | The same spec rendered interactive (stdlib only, self-contained) |
| `<date>-<metric>.pdf` | A portable stakeholder PDF with the chart inline |
| `charts/<date>-checkout-funnel.*` | The eight-stage funnel, composed from eight governed metrics |
| `audit-log.jsonl` | One append-only trace line per query |

Every one of those is watermarked **FIXTURE DATA** / **MOCK DATA**, because the
numbers come from a seeded generator rather than a business. That watermark is
load-bearing, not decoration.

Just the chart renderers, with no dependencies at all:

```bash
python3 plugins/assay/skills/render-chart/scripts/render_html.py \
  --spec plugins/assay/skills/render-chart/examples/trend.spec.json --out /tmp/chart.html

python3 plugins/assay/skills/render-dashboard/scripts/render_dashboard.py \
  --spec plugins/assay/skills/render-dashboard/examples/example.dashboard.json --out /tmp/dash.html
```

### Point it at a real warehouse

```bash
export ASSAY_ENGINE=bigquery
export ASSAY_BIGQUERY_PROJECT=<your-gcp-project>
gcloud auth application-default login
python3 plugins/assay/skills/setup/scripts/doctor.py --probe
```

You will also need the curated views: `plugins/assay/skills/query-metric/views/`
ships the DDL for both (the session-grain spine and the weekly aggregate built on
it), with `${WAREHOUSE_PROJECT}` / `${SOURCE_DATASET}` placeholders to substitute.

## Choosing an engine

| `ASSAY_ENGINE` | Where the query runs | Identity asserted | Percentiles |
|---|---|---|---|
| `sqlite` (default) | Committed fixture CSVs, loaded into an in-memory database | No — see below | Computed client-side |
| `bigquery` | The curated view, as the authenticated human | Yes, in code | Pushed down (`APPROX_QUANTILES`) |

Both run the **same SQL**. Skills author one canonical dialect (BigQuery) and
`_shared/dialect.py` translates it once for sqlite, so dialect knowledge stays out
of the governance layer and an artifact's `query_sha` receipt does not depend on
where it ran. The translation set is enumerated exhaustively in
`dialect.TRANSLATIONS` and pinned by a golden test; anything outside it raises
rather than being guessed at.

**One guarantee is genuinely weaker on the sqlite path, and it is worth stating
plainly rather than discovering:** `assert_user_identity()` is not called. That is an
explicit branch, with the reasoning written down in
`_shared/adapters/sqlite_adapter.py` — a fixture read touches no warehouse and no
credentials, so there is no query to attribute to a person, and requiring a cloud
login to read a committed CSV would be theatre that also makes `git clone && pytest`
impossible. In exchange every fixture-backed result is marked `fixture_backed`, which
becomes the watermark above, so such a number cannot pass itself off as measured.
Every *other* control — allow-lists, PII refusal, the tier gate, maturity cutoffs,
manifest receipts — runs upstream of the adapter on both engines and cannot be
reached around. `tests/test_adapters.py` pins both halves of that trade.

## Environment variables

All configuration is `ASSAY_`-prefixed. There are **no defaults pointing at any real
warehouse** — a template that shipped one would ship somebody else's billing account.

| Variable | Used by | Meaning |
|---|---|---|
| `ASSAY_ENGINE` | `_shared/adapters` | `sqlite` (default) or `bigquery`. An unknown value refuses rather than falling back. |
| `ASSAY_BIGQUERY_PROJECT` | `query-metric`, `setup` | The GCP project. Expands the `${ASSAY_BIGQUERY_PROJECT}` placeholder in metric definitions. No fallback. |
| `ASSAY_SAMPLE_DIR` | the sqlite adapter, the CSV-stub oracle | Fixture CSVs (default `data/assay/sample/`). |
| `ASSAY_METRICS_DIR` | `query-metric` | Consumer-authored metric definitions, searched before the bundled catalog (default `context/assay/metrics/`). |
| `ASSAY_OUTPUTS_DIR` | `query-metric`, `export-report` | Where artifacts land (default `outputs/assay/`). |
| `ASSAY_AUDIT_LOG` | `query-metric` | Audit-log path. Disable with `ASSAY_AUDIT_DISABLE=1`. |
| `ASSAY_FUNNEL_SPEC` | `_shared/spine.py` | Override the resolved funnel-spec path outright (test/override hook). |
| `ASSAY_FUNNELS_CONFIG` | `visualize-metric` | Override the funnel-composition config path. |
| `ASSAY_COVERAGE_FILE` | `_shared/coverage.py`, `coverage`, `load-context` | Instrumentation coverage register path. |
| `ASSAY_GUARDRAILS_FILE` | `_shared/guardrails.py` | Warn-level guardrails config path. |

Every spine skill also takes `--engine` to override `ASSAY_ENGINE` for one run, and
`--today YYYY-MM-DD` to pin the maturity window. Pass `--today 2026-08-17` when reading
the committed fixtures: that is the instant they were generated for (recorded in
`data/assay/sample/FIXTURES.md`), and reading a fixed fixture at the real today
withholds more of the tail every week until the whole book looks immature.

`GOOGLE_APPLICATION_CREDENTIALS` is not configuration here — if it is set, the
identity check **refuses to run**, because it would mean a service account.

## What is here

| Path | What it is |
|---|---|
| `plugins/assay/skills/_shared/` | The governance engine: identity, PII, manifest, verify, reconcile, numfmt, spine, coverage, guardrails, dialect, adapters |
| `plugins/assay/skills/_shared/adapters/` | The warehouse adapter seam: one BigQuery adapter, one sqlite adapter, capability advertisement |
| `plugins/assay/hooks/` + `.cursor/hooks/` | Access-path enforcement for both runtimes, over one shared matcher |
| `plugins/assay/config/` | The module registry, two funnel specs, funnel compositions, the coverage and guardrails registers |
| `plugins/assay/skills/query-metric/` | The governed metric runtime: 15 T2 metrics, 8 entities, doctrine, templates, view DDL |
| `plugins/assay/contracts/consumer-api.md` | The cross-plugin response contract |
| `scripts/generate_fixtures.py` | The deterministic fixture generator |
| `scripts/demo.py` | The compose chain, end to end, offline |
| `scripts/demo_skills.py` | All fifteen skills, offline, as a gate |
| `scripts/scrub_check.py` | The deny-token gate |

### The fifteen skills

| Skill | What it does |
|---|---|
| `assay:setup` | Environment doctor: gcloud, service-account env, ADC, deps, live probe |
| `assay:catalog` | The front door — every registered metric, latency KPI, and spine, read off disk |
| `assay:coverage` | "Can I measure X yet?" from the instrumentation register, with the blocker and its owner |
| `assay:load-context` | The consumer API: governed definitions by scope, with contract sentinels |
| `assay:query-metric` | A governed number: allow-listed, tiered, caveated, with a provenance receipt |
| `assay:visualize-metric` | Artifact → chart spec → SVG/PNG, with faithfulness rules |
| `assay:render-chart` | Chart spec → one self-contained interactive HTML (stdlib only) |
| `assay:render-dashboard` | Dashboard spec → one self-contained interactive HTML (stdlib only) |
| `assay:export-report` | Governed artifact → a portable PDF |
| `assay:verify` | Offline definition/query drift check against an artifact's manifest |
| `assay:reconcile` | Why their number differs from ours, attributed to a named cause |
| `assay:funnel-dropoff` | The stage funnel **plus** terminal-branch leak attribution |
| `assay:stage-timing` | Percentile stage latency (p50/p90/p99, mean, n) per transition |
| `assay:segment-compare` | A vs B, or before vs after, across funnel, leak mix, and timing |
| `assay:funnel-pulse` | Stage-aware recent-vs-baseline regression monitor, ranked by impact |

The last four read the **session-grain spine** rather than the weekly aggregate,
because that is the only way to get what a `SUM/SUM` metric structurally cannot
express: where each cart actually ended up, and a median.

Four skills ship as **documented outlines only**, each excluded in the module registry
with a specific reason rather than a TODO. `funnel-nowcast` and `release-scorecard`
need statistical parameters — chain-ladder development fractions, bootstrap seeds, BSTS
priors — that are judgment calls against a real book; fitting them to synthetic
fixtures would produce a curve describing the generator rather than any business.
`applicant-journey` is grain-scoped to a person spine this template does not register.
`publish` writes to an external wiki, which a template has nowhere to write to.

### The two registers

`config/coverage.yaml` answers "can I measure X yet?" — including who owns the blocker
— and `config/guardrails.yaml` attaches warn-level caveats to a query automatically:
feed cutovers the window crosses, plus column and view notes. Both **degrade to empty
on a parse error**, because advisory metadata that can break a query is worse than
none. That is exactly why `tests/test_coverage.py` and `tests/test_guardrails.py`
assert the shipped files parse and are non-empty rather than only testing the matchers:
a typo'd register would otherwise produce silence, and every matcher test would still
pass against the empty dict it degraded to.

### The browse module and its handoff edge

The `browse` module registers a second spine at **browse-session grain** plus a
declarative `handoff` edge to `checkout/session` on `checkout_session_id`. It is marked
`declared`, not `live`: the stage ladder, terminal branches, allow-lists, maturity lags,
and join key are authored and linted, and no view is provisioned behind it (a browse
spine over the same window is ~2.4× as many rows, because most visits never produce a
cart).

The edge exists to make one thing concrete: **the hard part of a cross-grain handoff is
the denominator, not the schema.** A browse session that never produces a cart carries
no `checkout_session_id` at all, so the edge's `coverage` is the browse-to-cart
*conversion rate* over all browse sessions — not a join-quality or data-quality figure,
and a low value means shoppers left rather than that rows failed to match. It also
cannot be derived from the checkout spine, because the sessions it is about are absent
from it: a different population, not a filter.

That number is therefore labelled `coverage_basis: declared_estimate` with a null
measurement date, and the registry linter enforces the pairing — a spine with no data
behind it can only ever carry an estimate, so an estimate cannot wear a measurement's
clothes.

## The example domain

A fictional **direct-to-consumer e-commerce checkout**, chosen because its funnel has
a long, honest maturation tail — the thing that makes naive conversion reporting wrong.

The spine grain is a **checkout session (cart)**, and the stages are: Cart Created →
Checkout Started → Payment Submitted → Payment Decisioned → Order Confirmed →
Shipped → Delivered → Return Window Complete. That last stage settles about 30 days
after delivery, so the most recent cohorts are always partly unresolved. The engine
withholds each stage only until *that stage* settles rather than gating the whole
funnel on the slowest one — which is what lets an early-funnel rate stay fresh while a
return-window rate honestly waits six weeks.

Governed dimensions are `channel`, `payment_method`, `market`, `category`, and
`checkout_version`. `payment_method` is the one that drives *maturity* as well as
conversion: instant card authorization settles in seconds, a bank transfer routed
through a clearing cycle and a review queue takes days, so the funnel spec carries a
per-method maturity override.

### About the fixture numbers, and about the lags

Two honest caveats that the repo also states in machine-readable form:

- **The data is synthetic.** `scripts/generate_fixtures.py` is a seeded random
  process. Treat the *shape* as illustrative and the *values* as meaningless. Every
  artifact says so.
- **The maturity lags are hand-authored, not measured.** In a real deployment that
  block is generated by measuring open-anchored settle time on your own spine. The
  whole value of the mechanism is that the numbers were *observed*, so the spec marks
  itself `illustrative: true` / `calibrated_at: null`, every artifact repeats it, and
  a test holds those flags in place.

Swap the domain by replacing the funnel spec and the metric definitions — no engine
change required. `skills/query-metric/templates/MODULE-ONBOARDING.md` is the
walkthrough.

## Regenerating the fixtures

```bash
python3 scripts/generate_fixtures.py                    # both committed fixtures
python3 scripts/generate_fixtures.py --emit weekly      # just the aggregate
python3 scripts/generate_fixtures.py --spine-weeks 0 \
    --out-dir /tmp/assay-full                           # the full ~22 MB spine
python3 scripts/generate_fixtures.py --sessions 200000 --weeks 104
```

Deterministic: the same arguments produce byte-identical files, so a diff in a
committed CSV always means a parameter changed.

Two fixtures are committed, and the session spine is **trimmed**. It carries one row
per cart at ~280 bytes, so the whole 78-week book is ~22 MB — more than a template
whose selling point is a working clone should ask anyone to carry. The committed spine
is the most recent 19 cohort weeks, which is not a round number: it is the widest
window any spine skill reads at the funnel spec's own defaults, and `funnel-pulse` is
what sets it (a 4-week recent window plus its 8 baseline weeks, lagged by the 44-day
return-window settle time). `tests/test_fixtures.py` recomputes that requirement from
the spec rather than trusting the constant, because a trim one week short does not fail
loudly — it gives the deepest stage a truncated baseline and a plausible wrong delta.

The trim is a **cohort-week window, not a row sample**, which buys two things worth
having: over the weeks it covers the spine still sums exactly to the weekly aggregate
(asserted, and the only check that the two views are projections of one book), and the
newest cohorts are still genuinely unsettled, so the maturity machinery has something
real to withhold. `--spine-weeks 0` regrows a strict superset.
`data/assay/sample/FIXTURES.md` records the seed, the observation instant, and the trim.

## License

MIT — see [LICENSE](LICENSE).
