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
- **A scrub gate.** `scripts/scrub_check.py` blocks the private-original names
  this template was derived from, and the test suite asserts the repo is clean.

> **Status: Phase 2.** The governance engine, the hooks, the warehouse adapter seam,
> the `checkout` module (15 governed metrics over a cart-grain funnel), and the
> `query-metric` → `visualize-metric` → `render-chart` / `export-report` chain all
> run today, offline, against committed fixtures. The spine-backed skills
> (`funnel-dropoff`, `stage-timing`, `segment-compare`, `funnel-pulse`) are next.

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

### Run the whole chain

```bash
.venv/bin/pip install -r plugins/assay/skills/visualize-metric/scripts/requirements.txt
.venv/bin/pip install -r plugins/assay/skills/export-report/scripts/requirements.txt

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
| `ASSAY_COVERAGE_FILE` | `_shared/coverage.py` | Instrumentation coverage register path. |
| `ASSAY_GUARDRAILS_FILE` | `_shared/guardrails.py` | Warn-level guardrails config path. |

`GOOGLE_APPLICATION_CREDENTIALS` is not configuration here — if it is set, the
identity check **refuses to run**, because it would mean a service account.

## What is here

| Path | What it is |
|---|---|
| `plugins/assay/skills/_shared/` | The governance engine: identity, PII, manifest, verify, reconcile, numfmt, spine, coverage, guardrails, dialect, adapters |
| `plugins/assay/skills/_shared/adapters/` | The warehouse adapter seam: one BigQuery adapter, one sqlite adapter, capability advertisement |
| `plugins/assay/hooks/` + `.cursor/hooks/` | Access-path enforcement for both runtimes, over one shared matcher |
| `plugins/assay/config/` | The module registry, the `checkout` funnel spec, funnel compositions |
| `plugins/assay/skills/query-metric/` | The governed metric runtime: 15 T2 metrics, 7 entities, doctrine, templates, view DDL |
| `plugins/assay/skills/visualize-metric/` | Artifact → chart spec → SVG/PNG, with faithfulness rules |
| `plugins/assay/skills/render-chart/` | Chart spec → one self-contained interactive HTML (stdlib only) |
| `plugins/assay/skills/render-dashboard/` | Dashboard spec → one self-contained interactive HTML (stdlib only) |
| `plugins/assay/skills/export-report/` | Governed artifact → a portable PDF |
| `plugins/assay/skills/verify/` | Offline definition/query drift check against an artifact's manifest |
| `plugins/assay/skills/setup/` | Environment doctor: gcloud, service-account env, ADC, deps, live probe |
| `plugins/assay/contracts/consumer-api.md` | The cross-plugin response contract |
| `scripts/generate_fixtures.py` | The deterministic fixture generator |
| `scripts/demo.py` | The whole chain, end to end, offline |
| `scripts/scrub_check.py` | The deny-token gate |

Not here yet: the spine-backed skills (`funnel-dropoff`, `stage-timing`,
`segment-compare`, `funnel-pulse`), the `browse` module's spine, and
`reconcile` / `catalog` / `coverage` / `load-context` / `guardrails`. The module
registry records each spine-backed skill as excluded with a specific reason, and the
funnel spec already carries the `stages`, `transitions`, and `terminal_branches` they
will read — so wiring them is additive.

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
python3 scripts/generate_fixtures.py                  # the committed weekly aggregate
python3 scripts/generate_fixtures.py --emit both      # + the session-grain spine
python3 scripts/generate_fixtures.py --sessions 200000 --weeks 104
```

Deterministic: the same arguments produce byte-identical files, so a diff in the
committed CSV always means a parameter changed. The session spine is git-ignored
(~22 MB, and nothing in this phase queries it); the weekly aggregate is committed so
the demo and the tests work on a fresh clone. `data/assay/sample/FIXTURES.md` records
the seed and the observation instant.

## License

MIT — see [LICENSE](LICENSE).
