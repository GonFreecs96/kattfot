# assay

A working template for **governed agentic analytics** — a Claude Code / Cursor
plugin marketplace whose subject is not charts but *governance*: how an agent can
be given real access to a data warehouse and still be unable to produce a number
nobody can defend.

The mechanisms, all enforced in code rather than in a prompt:

- **Per-user identity.** Queries run as the individual human via browser OAuth
  (ADC). A service-account or impersonated credential is *refused*, not warned
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
- **Provenance receipts.** Every artifact carries content fingerprints of the
  metric definition, the query, and the result (`_shared/manifest.py`), so
  `assay:verify` can later tell you offline whether a number still means what it
  claimed to. Wall-clock time is excluded from every hash, so a faithful replay
  reproduces them exactly.
- **Accuracy tiers and allow-lists.** Metrics declare a tier and the exact
  dimensions/filters they permit; anything else is a clean refusal.
- **A scrub gate.** `scripts/scrub_check.py` blocks the private-original names
  this template was derived from, and the test suite asserts the repo is clean.

> **Status: Phase 1.** The governance engine, the hooks, the rendering skills,
> and the test suite are complete and runnable. The metric catalog, funnel spec,
> execution adapter, and the remaining skills are the next phase — see
> "What is here" below for exactly what runs today.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt

.venv/bin/python -m pytest        # the test suite
python3 scripts/scrub_check.py    # the deny-token gate
```

Both must be green before a commit. The scrub gate is also a test
(`tests/test_scrub_check.py::test_this_repo_is_clean`), so `pytest` alone will
catch a leak.

Render the example chart and dashboard — no dependencies needed, both renderers
are pure standard library:

```bash
python3 plugins/assay/skills/render-chart/scripts/render_html.py \
  --spec plugins/assay/skills/render-chart/examples/trend.spec.json \
  --out /tmp/chart.html

python3 plugins/assay/skills/render-dashboard/scripts/render_dashboard.py \
  --spec plugins/assay/skills/render-dashboard/examples/example.dashboard.json \
  --out /tmp/dashboard.html
```

Check your environment for warehouse access:

```bash
export ASSAY_BIGQUERY_PROJECT=<your-gcp-project>
gcloud auth application-default login
python3 plugins/assay/skills/setup/scripts/doctor.py --probe
```

## Environment variables

All configuration is `ASSAY_`-prefixed. There are **no defaults pointing at any
real warehouse** — a template that shipped one would ship somebody else's
billing account.

| Variable | Used by | Meaning |
|---|---|---|
| `ASSAY_BIGQUERY_PROJECT` | `setup` (`doctor.py`) | GCP project for the `--probe` connectivity check. No fallback; unset is reported as a fixable check. |
| `ASSAY_FUNNEL_SPEC` | `_shared/spine.py` | Override the resolved funnel-spec YAML path outright (test/override hook). |
| `ASSAY_COVERAGE_FILE` | `_shared/coverage.py` | Override the instrumentation coverage register path. |
| `ASSAY_GUARDRAILS_FILE` | `_shared/guardrails.py` | Override the warn-level guardrails config path. |
| `ASSAY_OUTPUTS_DIR` | `export-report` | Where generated artifacts live (default `outputs/assay/`). |
| `ASSAY_SAMPLE_DIR` | query helper (next phase) | Directory of sample CSVs backing mock-mode metrics. |
| `ASSAY_METRICS_DIR` | query helper (next phase) | Directory of metric-definition markdown files. |

`GOOGLE_APPLICATION_CREDENTIALS` is not configuration here — if it is set, the
identity check **refuses to run**, because it would mean a service account.

## What is here

Runnable today:

| Path | What it is |
|---|---|
| `plugins/assay/skills/_shared/` | The governance engine: identity, PII, manifest, verify, reconcile, numfmt, spine, coverage, guardrails |
| `plugins/assay/hooks/` + `.cursor/hooks/` | The access-path enforcement hooks for both runtimes, over one shared matcher |
| `plugins/assay/skills/render-chart/` | Chart spec → one self-contained interactive HTML (stdlib only) |
| `plugins/assay/skills/render-dashboard/` | Dashboard spec → one self-contained interactive HTML (stdlib only) |
| `plugins/assay/skills/export-report/` | Governed artifact → a portable PDF |
| `plugins/assay/skills/verify/` | Offline definition/query drift check against an artifact's manifest |
| `plugins/assay/skills/setup/` | Environment doctor: gcloud, service-account env, ADC, deps, live probe |
| `plugins/assay/contracts/consumer-api.md` | The cross-plugin response contract (sentinels, formats, manifest shape) |
| `scripts/scrub_check.py` | The deny-token gate |
| `tests/` | The suite covering all of the above |

Deliberately not here yet: the metric catalog and module registry, the `checkout`
funnel spec and its fixtures, the execution adapter, and the remaining skills
(`query-metric`, `funnel-dropoff`, `stage-timing`, `segment-compare`,
`funnel-pulse`, `reconcile`, `catalog`, `coverage`, `load-context`,
`visualize-metric`). SKILL.md files reference those siblings as the intended
pipeline and each carries a "Status in this template" note saying so.

## The example domain

The template is built around a fictional **direct-to-consumer e-commerce
checkout**, chosen because its funnel has a long, honest maturation tail — the
thing that makes naive conversion reporting wrong.

The spine grain is a **checkout session (cart)**, and the stages are: Cart
Created → Checkout Started → Payment Submitted → Payment Decisioned → Order
Confirmed → Shipped → Delivered → Return Window Complete. That last stage settles
about 30 days out, so the most recent cohorts are always partly unresolved; the
engine withholds each stage only until *that stage* settles rather than gating the
whole funnel on the slowest one. Governed dimensions are `channel`,
`payment_method`, `market`, and `category`.

Swap the domain by replacing the funnel spec and metric definitions — no engine
change required.

## License

MIT — see [LICENSE](LICENSE).
