# assay — Skill Cheat Sheet

*Deep per-skill reference: invocation, every helper flag, examples, what
comes back, and the gotchas. The [`SKILL.md`](../plugins/assay/skills/) for
each skill is always the authoritative source; this is the fast lookup. See
also the [glossary](glossary.md) and [runbook](runbook.md).*

---

## Conventions that apply to every skill (read once)

- **Invoke in chat** as `assay:<skill>`. Most skills take a **plain-English
  question** — the agent parses dimensions, filters, window, and toggles
  from it and then calls the underlying helper script. The helper commands
  below are what the agent runs; you rarely type them by hand, but they show
  the exact knobs.
- **Interpreter:** use the project venv (`.venv/bin/python`), not bare
  `python3`, once you have installed dependencies — the default `sqlite`
  engine needs only `pyyaml`; the `bigquery` engine also needs
  `google-cloud-bigquery`; `visualize-metric`'s `render.py` needs
  `matplotlib`; `export-report` needs `fpdf2`. Missing deps are a hard error,
  **exit 3** — never a silent fallback to synthetic numbers.
- **Skill root resolution.** Every skill resolves its own root the same way,
  first match wins: `${CLAUDE_PLUGIN_ROOT}/skills/<skill>` (Claude Code),
  else `<workspace>/.cursor/skills/<skill>` (Cursor), else the directory of
  the `SKILL.md` you actually loaded. Substitute that wherever a helper
  path below is shown relative to a skill root.
- **Auth (warehouse-touching skills):** run as **you** via ADC on
  `ASSAY_ENGINE=bigquery`. **Exit 2** = ADC missing/stale
  (`gcloud auth application-default login`); **exit 4** = service account
  refused (`unset GOOGLE_APPLICATION_CREDENTIALS`); **exit 5** = an
  allow-list breach (the spine-backed skills). There is no service-account
  fallback. On the default `ASSAY_ENGINE=sqlite`, no identity is asserted at
  all — see [`governance-model.md`](governance-model.md#1-per-user-identity).
- **`--output-format json`** is what the agent uses to get a parseable blob
  from every helper; **`--today YYYY-MM-DD`** pins the window for
  deterministic/scheduled runs, and is required to get sane numbers when
  reading the committed fixtures (their observation instant is
  `2026-08-17`, recorded in `data/assay/sample/FIXTURES.md`).
- **Output:** query/analysis skills write a markdown artifact to
  `outputs/assay/{date}-{slug}.md` (override `ASSAY_OUTPUTS_DIR`), print the
  path, and **do not paste the artifact into chat**. `query-metric` also
  appends a trace line to `outputs/assay/audit-log.jsonl`.
- **Refusals** name the rule (R1–R6) + the offending input, give the
  allow-list, propose no rule-violating workaround, and **write no
  artifact**. R6/PII is a hard, code-enforced refuse with no override.

Allow-listed dimensions/filters for the checkout spine (from
`config/checkout-funnel-spec.yaml`): `channel`, `payment_method`, `market`,
`category`, `checkout_version`, `is_first_order`, `terminal_branch` (dim
only). Common filter values: `channel=web|mobile_app|marketplace|social`,
`payment_method=card_instant|wallet|bank_transfer_review`.

---

# Read APIs & front doors

## `query-metric` — run a registered metric → a governed number

**Purpose:** take a plain-English question about a *registered* metric,
validate against its allow-list, run it (the real SQL against the resolved
engine, or a CSV-stub oracle when `status: dummy`), write a tier-stamped
artifact with a manifest.

```
assay:query-metric <natural-language question>
```

**Examples**
```
assay:query-metric Cart to checkout conversion by week for the last 12 weeks, split by channel.
assay:query-metric What's the payment approval rate for bank transfers in the UK?
assay:query-metric Show me the return rate by category over the last 26 weeks.
```

**Helper**
```bash
<python> "$AQM_ROOT/scripts/assay_query.py" \
  --metric <metric_name> \
  --dimensions <comma-separated|empty> \
  --time-range <last_N_weeks | YYYY-MM-DD:YYYY-MM-DD> \
  --filter <col>=<value>  [--filter <col>=<value> ...] \
  [--engine sqlite|bigquery] [--today YYYY-MM-DD] \
  --output-format json
```

| Flag | Meaning |
|---|---|
| `--metric` | The metric `name:` (snake_case). Resolves consumer dir → bundled catalog. |
| `--dimensions` | Comma-separated group-by dims; must be in the metric's `allowed_dimensions` (else R2). |
| `--time-range` | `last_N_weeks` or `YYYY-MM-DD:YYYY-MM-DD`. Omit → the metric's `default_lookback_weeks`. |
| `--filter col=value` | Repeatable; column must be in `allowed_filters` (else R3); value is **bound**, never inlined. |
| `--engine` | Override `ASSAY_ENGINE` for one run. |
| `--today` | Pin "now" for deterministic fixture/scheduled runs. |

**Returns / writes:** JSON with `rows[]` (each with a `_display` map — render
from this, don't reformat), `total_numerator`/`total_denominator` (+
`_display`), `column_label`, `value_format`, `source_mode`
(`bigquery`/`fixture_sql`/`mock_csv`), `engine`, `fixture_backed`,
`engine_capabilities`, `query` + `query_params`, `manifest`
(`plugin_version`/`metric_def_sha`/`query_sha`/`result_sha`), `maturity`,
`warnings[]`. Artifact = frontmatter (tier, manifest, `maturity_cutoff`) +
Results + Narrative + verbatim Caveats + Provenance (parameterized SQL).

**Gotchas:** the CSV-stub mock path triggers **only** on `status: dummy`
(banner stamped) — never a silent fallback. Zero rows → reports the
empty/typo'd-filter condition, doesn't narrate. `MATURITY:` /
`DISCONTINUITY:` / `CAVEAT(...)` warnings fold into Caveats. On the sqlite
engine every result is `fixture_backed: true`, which becomes a MOCK DATA
watermark downstream. Out of scope: multiple metrics in one call,
cross-metric joins, anything T1/T3. After writing it, offers
`assay:visualize-metric` (offer only, never auto-runs).

---

## `load-context` — governed definitions (no warehouse access)

**Purpose:** the cross-plugin read API. Returns definitions/context by
**scope**; pure file reads. Honors `contracts/consumer-api.md`.

```
assay:load-context <scope> [--compact] [--limit N] [--offset N] [--freshness-days 90]
```

| Scope | Returns |
|---|---|
| `catalog` | Every registered metric (name, display, tier, module, one-liner), plus latency KPIs and the entity registry. |
| `metric/{slug}` | One metric's full definition. |
| `module/{name}` | Every metric definition under that module (e.g. `module/checkout`). |
| `entities` | The entity registry: slug, display name, module, status, `metric_backed`, grain. |
| `entity/{name}` | One entity's full definition (e.g. `entity/checkout_session`). |
| `lineage/{module}` | The module's `LINEAGE.md`. |
| `tiers` | Accuracy-tier doctrine + refusal rules. |
| `governance` | Ownership and review-cadence doctrine. |
| `spines` | The module registry as data: every spine, its `status`, its skills, any `handoff` edge. |
| `coverage` / `coverage/{id}` | The instrumentation register / one entry. |
| `glossary` / `glossary/{term}` | Reserved-term definitions (empty in this template — a documented state, not an error). |

**Examples**
```
assay:load-context catalog
assay:load-context metric/cart_to_checkout_start_conversion
assay:load-context module/checkout --compact
assay:load-context entity/checkout_session
assay:load-context spines
```

**Returns:** single file = raw content incl. frontmatter; multiple =
`--- {filename} ---` separators; list scopes paginate with a footer.
Appends `STALE: {file} last updated {date}, owner: {owner}` past the
90-day freshness threshold (the `, owner:` suffix omitted when the file has
none).

**Sentinels:** `SCOPE_NOT_FOUND -- {detail}` (exit 1), `EMPTY_RESULT --
{detail}` (exit 1), `PATH_BOUNDARY -- {detail}` (exit 5), `TIER_REFUSED --
{detail}` (exit 5 — the load-bearing one: a `metric/{slug}` that's T1/T3).
Report the sentinel as the answer; never translate `SCOPE_NOT_FOUND` into a
guess.

**Gotchas:** never runs a query — `query-metric` owns execution. Never
paraphrase a definition into the answer; return the file. A `module/` or
`catalog` read is not refused wholesale for one non-T2 member — each is
listed with its tier and its body withheld instead.

---

## `catalog` — "what can I ask?"

**Purpose:** human-facing list of every registered metric and latency KPI,
grouped by module. The friendly front door. Never queries anything.

```
assay:catalog
```

**Helper**
```bash
python3 plugins/assay/skills/catalog/scripts/catalog.py --format markdown
# --format json for machine-readable
```

**Returns:** `metrics[]` (name, display name, first line of `## Definition`,
tier, module, entity, status, owner), `latency_kpis[]` (key, display name,
transition, percentiles, tier, `queryable` + `not_queryable_because`),
`spines[]` (module/spine, grain, `status`, spine-backed skills, any
`handoff` edge). Read off disk on every invocation — never hand-maintained.

**Gotchas:** a KPI or spine can be registered but marked `queryable: false`
(a `declared` spine with no view behind it — e.g. everything on
`browse/session`) — report that status, don't quietly present it as
available. Never invent a metric for an unregistered ask; point at
`AUTHORING.md` and `templates/example-metric.md` instead.

---

## `coverage` — "can I measure X yet?"

**Purpose:** answer whether a field/event/stream is instrumented; if not,
the blocker + owner + ref to watch. Reads `config/coverage.yaml`; never
runs a query.

```
assay:coverage <thing you want to measure>      # or no arg → list everything
```

**Helper**
```bash
# the whole register, grouped by status — the triage view
python3 plugins/assay/skills/coverage/scripts/coverage_lookup.py --format markdown

# match a stakeholder's phrasing
python3 plugins/assay/skills/coverage/scripts/coverage_lookup.py \
  --term "why was the cart abandoned" --format markdown

# resolve one entry exactly
python3 plugins/assay/skills/coverage/scripts/coverage_lookup.py \
  --id cart_abandon_reason --format markdown
```

**Examples**
```
assay:coverage why was the cart abandoned
assay:coverage carrier scan events
assay:coverage promo code attribution
```

**Status verdicts:** `live` (yes — points at the metric/spine), `partial`
(yes-but — names the hole), `empty` (not yet — schema wired, 0 rows in
production), `blocked` (no — blocker + owner), `declined` (no, not coming),
`flag_gated` (not yet — gating flag/launch). Always gives `blocker`
**verbatim** + `blocker_ref` + `owner` + `verified:` date. No match → routes
to `assay:catalog`, never invents a verdict. Exit 0 on a hit, 1 on a clean
no-match.

**Gotchas:** never present a `partial` entry's number as complete — the
worked example in this register is `return_reason_codes`: codes exist for
~31% of returns (the self-service portal cohort), and the missing 69% skew
toward the segments that don't self-serve. Never soften a `declined` — it's
a recorded decision, not an open question. Owners in this template are role
placeholders and refs are fictional.

---

## `setup` — one-time onboarding

**Purpose:** get a new user from "installed" to "ran my first governed
metric." Runs an env doctor, walks per-user OAuth, a `SELECT 1` probe. Never
runs a real metric or a service account.

```
assay:setup
```

**Helper**
```bash
<python> "$ROOT/scripts/doctor.py"            # offline: gcloud, sa-env, adc, deps
<python> "$ROOT/scripts/doctor.py" --probe    # adds a SELECT 1 as you against $ASSAY_BIGQUERY_PROJECT
<python> "$ROOT/scripts/doctor.py" --project <id>   # override ASSAY_BIGQUERY_PROJECT for the probe
```

**Remediation:** `deps` (agent runs the venv install) · `sa-env` (user
`unset GOOGLE_APPLICATION_CREDENTIALS`) · `gcloud` (user installs the SDK) ·
`adc` (user runs `gcloud auth application-default login`). The doctor is
non-fatal by design — fix reds, re-run until green.

**Gotchas:** there is deliberately **no default project** — export
`ASSAY_BIGQUERY_PROJECT` before the live probe means anything. The whole
plugin is already usable (`pytest`, `scripts/demo.py`, a chart, a PDF) before
any of the doctor's checks pass, because the default `ASSAY_ENGINE=sqlite`
needs none of them — `setup` is only for pointing at a real warehouse.

---

# Spine-backed analyses

*All four query the checkout module's session-grain spine
(`analytics.cart_checkout_session_spine`), default to **stage-aware
maturity** (`--maturity stage`: each stage/transition gated to cohorts
mature for it) plus legacy-`v1-legacy`-excluded, are aggregate-only, and
exit 5 on an allow-list breach. `--maturity flag` reverts to a legacy
single-window gate; `--include-legacy` includes the retired checkout
version.*

## `funnel-dropoff` — where does it leak?

**Purpose:** ordered stage funnel (stage-to-stage + cumulative) **plus**
terminal-branch decomposition (share of carts ending in each exit) for one
segment.

```
assay:funnel-dropoff <natural-language question>
```

**Examples**
```
assay:funnel-dropoff where does the mobile_app checkout funnel drop off?
assay:funnel-dropoff checkout funnel by channel for the last 12 weeks
assay:funnel-dropoff bank transfer drop-off split by market
```

**Helper**
```bash
python3 plugins/assay/skills/funnel-dropoff/scripts/funnel_dropoff.py \
  [--group-by <dim>] [--filter <col>=<value> ...] \
  [--time-range last_N_weeks | YYYY-MM-DD:YYYY-MM-DD] \
  [--maturity stage|flag|off] [--no-mature] [--include-legacy] \
  [--today YYYY-MM-DD] [--engine sqlite|bigquery]
```

| Flag | Meaning |
|---|---|
| `--filter col=value` | Segment filter(s), allow-listed. |
| `--group-by` | Single dim to split by (optional). |
| `--time-range` | Default = the spec's lookback (26 weeks). |
| `--maturity` | `stage` (default) · `flag` (legacy single window) · `off`. |
| `--no-mature` | Alias for `--maturity off` (include immature cohorts). |
| `--include-legacy` | Include the retired `v1-legacy` checkout cohort. |

**Returns:** `groups[]` → `started` (freshest lag-0 cohort), `deep_started`
(the single settled cohort behind the chart/branches), `stages[]` (`count`,
`stage_conversion`, `cumulative`, `mature_through`, `lag_days`),
`branches[]` (`count`, `pct_of_started`), `branch_coherence` +
`branch_coherent`; plus a top-level `maturity` block, a funnel `chart_spec`,
both parameterized SQL strings, and `warnings`.

**Gotchas:** each stage row is over its **own** mature cohort
(`mature_through`), so `cumulative` down the table mixes windows and need
not decrease monotonically — the `chart_spec` (single settled cohort) is the
coherent funnel shape. `branch_coherent` must be checked before reading the
branch shares — if `false`, the branch table is not a decomposition of
anything. `terminal_branch` is a curated heuristic evaluated deepest-first.
A branch share is a **share of carts**, not a share of exits.

---

## `stage-timing` — how slow is each step?

**Purpose:** percentile + mean latency per transition, over the population
that reached **both** endpoints.

```
assay:stage-timing <natural-language question>
```

**Examples**
```
assay:stage-timing how long does payment take from submit to decision, by payment_method?
assay:stage-timing confirmation to ship timing for the mobile_app channel over the last quarter
assay:stage-timing time to deliver by market
```

**Helper**
```bash
python3 plugins/assay/skills/stage-timing/scripts/stage_timing.py \
  [--group-by <dim>[,<dim>]] [--filter <col>=<value> ...] \
  [--transitions confirm_to_ship,payment_to_decision] \
  [--percentiles 50,90,99] \
  [--time-range last_N_weeks | YYYY-MM-DD:YYYY-MM-DD] \
  [--maturity stage|flag|off] [--no-mature] [--include-legacy] \
  [--today YYYY-MM-DD] [--engine sqlite|bigquery]
```

| Flag | Meaning |
|---|---|
| `--transitions` | Subset of the spec's transition keys (default all): `cart_to_checkout_start`, `checkout_to_payment`, `payment_to_decision`, `confirm_to_ship`, `ship_to_deliver`, `deliver_to_window_end`. |
| `--percentiles` | Default `50,90,99`. |
| `--group-by` | Comma-separated; more than one dimension is supported, each under its own key in the result. |
| `--maturity` | `stage` (default; each transition gated to cohorts mature for its **arrival** stage) · `flag` · `off`. |

**Named KPIs:** the spec's `kpis:` block pins `time_to_ship_days`
(`confirm_to_ship`, reported in days though the transition unit is minutes)
and `time_to_decision_minutes` (`payment_to_decision`) as citable-by-name
latency KPIs; naming one resolves to its transition and reports which KPI
was read.

**Returns:** `groups[]` → `transitions[]` with `n`, `mean`,
`percentiles{p50,p90,p99}`, `unit`, `maturity_stage`, `lag_days`,
`mature_through`; plus top-level `percentile_basis`, `maturity`, `kpis`,
`warnings`.

**Gotchas:** `n` differs per transition — it is the *surviving* population,
not the funnel start count. Percentiles are approximate on BigQuery
(`APPROX_QUANTILES`, pushed down) and exact on sqlite (computed client-side)
— `percentile_basis` records which, and the artifact must say so. Means are
tail-skewed — lead with p50. Where a KPI's `report_unit` differs from the
transition's own unit, convert and say you converted.

---

## `segment-compare` — A vs B, or before vs after

**Purpose:** diff two segments across funnel (cumulative-from-start),
terminal-branch leak mix, and timing — as `A` · `B` · `Δ`. Two modes,
descriptive only — no significance test, no causal claim.

```
assay:segment-compare <natural-language question>
```

**Examples**
```
assay:segment-compare within the web channel, compare card_instant vs bank_transfer_review
assay:segment-compare did the mobile_app funnel change before vs after 2026-06-01?
assay:segment-compare web vs marketplace, last 12 weeks
```

**Helper — filter-compare** (A vs B in one window)
```bash
python3 plugins/assay/skills/segment-compare/scripts/segment_compare.py \
  --filter channel=web \
  --a-filter payment_method=card_instant --b-filter payment_method=bank_transfer_review \
  --a-label "Card (instant)" --b-label "Bank transfer (review)" \
  [--transitions ...] [--percentiles 50] \
  [--time-range last_16_weeks] [--maturity stage|flag|off] [--include-legacy] \
  --today 2026-08-17
```

**Helper — pre/post** (one segment split at a date)
```bash
python3 plugins/assay/skills/segment-compare/scripts/segment_compare.py \
  --filter channel=mobile_app \
  --split-date 2026-06-01 \
  --time-range 2026-03-02:2026-08-10 --today 2026-08-17
```

| Flag | Meaning |
|---|---|
| `--filter` | Shared narrowing filter (both modes). |
| `--a-filter` / `--b-filter` | The two sides (filter-compare). |
| `--a-label` / `--b-label` | Friendly labels for the sides. |
| `--split-date` | Triggers pre/post (`cohort_week < date` vs. `>=`). |
| `--percentiles` | Default `50` (median); the first is the headline. |

**Returns:** `segment_a` / `segment_b` (each with `started`, `deep_started`,
`stages` with `mature_through`, `branches`, `timing`, `branch_coherent`, and
its own SQL), plus `comparison` (stage Δpp, branch Δpp sorted by magnitude,
timing Δ at the headline percentile), `maturity`, `percentile_basis`,
`warnings`. Both sides share **one** `today`, so both are gated identically.

**Gotchas:** specify exactly one mode — both or neither refuses. Deltas are
descriptive, **not** significance-tested — cite both sides' `n`. In pre/post,
a **recent** post window can leave a deep stage (Delivered, Return Window
Complete) with little or no settled cohort — that reads as a low `n`, not a
low rate. Lean on the fast upstream stages (checkout start, payment submit)
for a recent change.

---

## `funnel-pulse` — scheduled regression monitor

**Purpose:** recent-vs-baseline sweep over every value of one dimension
(default `channel`). Flags stage-conversion and terminal-branch shifts
ranked by impact (carts × Δpp). Stage-aware: each stage uses its own mature
window. Emits a digest; **never posts**.

```
assay:funnel-pulse [natural-language, or run with defaults]
```

**Examples**
```
assay:funnel-pulse
assay:funnel-pulse monitor by payment_method
assay:funnel-pulse pulse for market=us only, last 6 weeks recent vs 12 baseline
```

**Helper**
```bash
python3 plugins/assay/skills/funnel-pulse/scripts/funnel_pulse.py \
  [--by channel] [--filter market=us] \
  [--recent-weeks 4] [--baseline-weeks 8] \
  [--min-started 200] [--threshold-pp 3.0] \
  [--maturity stage|flag] [--today YYYY-MM-DD] [--engine sqlite|bigquery]
```

| Flag | Default (from `checkout-funnel-spec.yaml` `pulse:`) | Meaning |
|---|---|---|
| `--by` | `channel` | Monitoring unit (any allowed dim). |
| `--recent-weeks` / `--baseline-weeks` | 4 / 8 | Recent window vs. the baseline before it (applied per stage). |
| `--min-started` | 200 | Skip a group below this in either window (→ `skipped_low_n`). |
| `--threshold-pp` | 3.0 | Flag a shift ≥ this many pp. |
| `--maturity` | `stage` | `stage` (per-stage windows) · `flag` (legacy single deepest-cohort window). `--no-mature` is **not available** — the helper refuses rather than running two unsettled windows against each other silently. |
| `--today` | unset | Leave unset in scheduled runs → each stage uses its latest mature window. |

**Per-group override:** `payment_decisioned` carries a longer lag for
`payment_method=bank_transfer_review` (12d vs. the 2d default). When
`--by payment_method` is used, that group is compared over its own longer
window, on **both** sides of the rate — flagged `lag_override: true` in the
output.

**Returns:** a `maturity` block (`per_stage` recent/baseline windows +
`group_overrides`), `windows` (freshest + deepest headline windows),
`thresholds`, `scanned`/`qualified`/`skipped_low_n`, `flags[]` sorted by
impact, `flows[]` (per-group end-to-end + every stage delta, each with its
own `mature_through`).

**Gotchas:** still a cohort monitor, not a within-day alarm. A missing
calibrated lag for a declared `maturity_stage` is a **hard refusal** (exit
5), not a degraded run. Rows with `lag_override: true` are on a *different*
window from their neighbours by design — say so, or a window difference
reads as a behaviour difference. Direction on intermediate stages is a
heuristic — a Decisioned drop paired with an Approved rise is usually a
routing/mix shift, not a regression; the end-to-end delta is the
unambiguous signal. The committed fixture spine is trimmed to 19 cohort
weeks — exactly what the default windows need; a wider `--baseline-weeks`
offline silently reads a truncated baseline.

## `funnel-nowcast` — what will this immature cohort settle at?

**Does:** project a not-yet-mature cohort's eventual conversion for a deep
stage, from what it has realized so far, using Mack's distribution-free chain
ladder over the session spine. Every projected value is **FORECAST**-lane
(never a governed T2 actual) and reconciles to the T2 number as the cohort
matures.

```bash
# the keystone target — return-window completion, projected for open cohorts
.venv/bin/python plugins/assay/skills/funnel-nowcast/scripts/funnel_nowcast.py \
  --today 2026-08-17 --time-range last_19_weeks --stage return_window_complete

# by segment (per-group triangle; a blended curve drifts with mix), with the
# self-scoring backtest that reports empirical vs nominal interval coverage
.venv/bin/python .../funnel_nowcast.py --today 2026-08-17 \
  --group-by channel --backtest
```

**Comes back:** per-cohort rows (observed, method, P50 ultimate, P10–P90,
rate), the estimated development factors with a stability read, the level
prior when a cohort is too young for its triangle to say anything, and — with
`--backtest` — per-horizon empirical coverage next to nominal.

**Why it exists (it was an outline through Phase 4):** the objection was that
a now-cast's parameters are judgment calls that would fit the fixture
generator. Mack's chain ladder *estimates* every factor and variance from the
triangle at run time, so nothing is authored; the `nowcast:` block carries
estimation controls only. See
[`reference/research-review.md`](reference/research-review.md) §4.

**Gotchas:** two funnel-specific ports of the actuarial method — ultimates are
**capped at cohort size** (a funnel has a ceiling claims lack) and cohorts
still inside a pure delay use a **level prior** rather than a confident zero.
Model-based intervals run narrow (Wolffram et al. 2023); the built-in backtest
measures it — on the committed fixtures it reports ~56% coverage against an 80%
nominal band and warns `INTERVALS UNDERCOVER`. Maturity gating is **off by
design** here (reading the withheld cohorts is the point); the FORECAST lane
replaces it. Fixture runs now-cast the generator, watermarked as such.

---

# Difference attribution & integrity

## `reconcile` — why does our number ≠ theirs?

**Purpose:** attribute a gap between a governed number and an external one
to a named cause by re-running one tweaked convention at a time. Every
re-run goes through the **same** `query-metric` helper as a first-class
query — same allow-lists, same PII refusal, same tier gate.

```
assay:reconcile
```
Gather first (it asks if missing): their value, the metric, **our** window,
**their** window, their filters/segment, their grain, and (if it's an old
artifact) its path.

**Helper**
```bash
python3 plugins/assay/skills/reconcile/scripts/reconcile_number.py \
  --metric <name> --their-value <float> --time-range <ours> \
  [--their-time-range <theirs>] [--filter col=value ...] \
  [--their-filter col=value ...] [--their-metric <sibling>] \
  [--their-artifact <path>] [--today YYYY-MM-DD] [--engine sqlite|bigquery]
```

| Flag | Meaning |
|---|---|
| `--their-value` | The number to explain (e.g. `0.81`). |
| `--time-range` / `--their-time-range` | Our window vs. theirs — window mismatch is the #1 cause. |
| `--their-filter` | A slice they applied that we did not. |
| `--their-metric` | A sibling metric, if they measured a different grain. |
| `--their-artifact` | An old `assay` artifact → enables an exact, offline definition-drift check. |

**Causes:** `AGREE`, `DATE_WINDOW`, `MATURITY_CUTOFF`, `TIMEZONE`,
`FILTER_DIFF`, `GRAIN`, `UNKNOWN`. `definition_drift` is reported separately
when an artifact path was given. Report the cause + reproducing value in
plain language; for `UNKNOWN`, say so and list what was `tried` — never
invent a cause.

**Gotchas:** `MATURITY_CUTOFF` is worth recognising on sight — on a
deep-stage metric the gap it produces is enormous, not marginal (this
template's fixtures put `delivered_to_return_window_complete_rate` at ~88%
settled vs. ~37% read through today). Sub-tolerance difference is `AGREE`,
not a manufactured cause. On the sqlite engine the verdict demonstrates the
mechanism rather than a real disagreement — the helper marks that in
`warnings`. One metric per invocation.

---

## `verify` — is this artifact still valid? (offline)

**Purpose:** recompute the definition + query fingerprints from the current
repo and compare to the artifact's manifest. **No warehouse access.**

```
assay:verify <artifact-path>
```

**Helper**
```bash
python3 plugins/assay/skills/verify/scripts/verify_artifact.py <artifact-path>
```

**Statuses:** `VERIFY_OK` (consistent — the *number* itself wasn't
re-run), `DEFINITION_DRIFT` (the metric file changed — shows old→new hash),
`QUERY_DRIFT` (rebuilt SQL no longer matches), `DEFINITION_MISSING` (metric
renamed/removed), `MANIFEST_ABSENT` (the artifact predates manifests).

**Gotchas:** deliberately does **not** re-check `result_sha` — that needs a
live re-run via `assay:query-metric`. Only verifies `query-metric` artifacts
(the spine-backed skills' outputs have no manifest yet). Read-only.

---

# Visualization, output & publishing

## `visualize-metric` — artifact → faithful SVG/PNG (+ funnel mode)

**Purpose:** infer the chart type from the result shape and inject a
`## Visualization` section into the artifact. Draws **only** from the
Results table; for funnel mode, runs `query-metric` once per stage.

```
assay:visualize-metric [artifact path] [intent]
```

**Examples**
```
assay:visualize-metric                                    # newest artifact, inferred chart
assay:visualize-metric outputs/assay/2026-08-17-payment-approval-rate.md
assay:visualize-metric show the checkout funnel
assay:visualize-metric compare the return rate across categories   # → ranked bar
```

**Helpers** (needs `matplotlib` in the venv)
```bash
# 1. build the spec
<python> "$VM_ROOT/scripts/chart_spec.py" \
  --artifact <artifact_path> \
  [--intent trend|compare|breakdown|share] \
  [--chart-type single_line|multi_series_line|ranked_bar|funnel] \
  [--baseline <fraction>] \
  [--flag-last-n <N>] \
  > <charts_dir>/<date>-<slug>.spec.json
# funnel mode: chart_spec.py --funnel checkout --stage-json s1.json s2.json ...

# 2. render SVG + PNG
<python> "$VM_ROOT/scripts/render.py" \
  --spec <charts_dir>/<date>-<slug>.spec.json \
  --out-base <charts_dir>/<date>-<slug>
```

| Flag | Meaning |
|---|---|
| `--intent` | `trend`/`compare`/`breakdown`/`share` — nudges, doesn't override the deterministic shape rule. |
| `--chart-type` | Force a type; the helper notes if it down-ranks a better fit. |
| `--baseline` | Steady-state reference line (fraction). |
| `--flag-last-n` | Trailing N points render **hollow** ("still maturing") — set from the maturity caveat. |

**Chart-type rule:** 0 dims + many time points → single line; ≥1 dim +
>1 time point + ≤6 series → multi-series line; >6 series, or a single time
point → ranked bar; funnel intent → composed funnel (stamps the **lowest**
tier across stages, stops if any stage is T1/T3).

**Gotchas:** governed-artifact only — an artifact missing `metric`,
`accuracy_tier`, `source_view`, or `generated_at` is refused. Rate axes
anchor at 0, never configurable. `NULL` is not zero — a `—` cell is a missing
rate, not a 0% one. Fixture-backed data is watermarked (checked from
`fixture_backed`, falling back to `source_mode`). For funnel mode, the
product of the stage rates will **not** exactly equal the named
`end_to_end` reference metric — each stage carries its own maturity cutoff,
so they are computed over slightly different cohort windows; the spec
records both plus `maturity_cutoff_span`.

---

## `render-chart` — same spec → interactive self-contained HTML

**Purpose:** a second backend over the **same chart spec** — emits one
interactive ECharts `.html`. Pure stdlib (no venv/pip/node).

```
assay:render-chart [spec.json] [intent/caption]
```

**Examples**
```
assay:render-chart                                              # newest spec in the charts dir
assay:render-chart outputs/assay/charts/2026-08-17-payment-approval-rate.spec.json
assay:render-chart show the checkout funnel                    # build the spec, then render HTML
```

**Helper**
```bash
python3 "$RC_ROOT/scripts/render_html.py" \
  --spec <charts_dir>/<date>-<slug>.spec.json \
  --out  <charts_dir>/<date>-<slug>.html \
  --caption "<one plain-language sentence>" \
  [--theme auto|light|dark] [--cdn] \
  [--echarts <path>] [--theme-tokens <path>] [--footer-note <text>]
```

| Flag | Meaning |
|---|---|
| `--theme` | `auto` (follow OS, default) / `light` / `dark`. |
| `--cdn` | Reference ECharts from a CDN (~20 KB file) instead of inlining (~1 MB). |
| `--caption` | One plain sentence under the chart — write it yourself, never overstate beyond the spec. |

**Returns:** prints `{"html","bytes","chart_type","inlined"}`. Spec types:
`single_line`, `multi_series_line`, `ranked_bar`, `funnel`. Embeds the exact
spec in the page for audit.

**Gotchas:** rendering backend only — never queries the warehouse. Anything
from warehouse data must go through `chart_spec.py` first (preserves
tier/source/mock/axis policy). No dependencies to install — ECharts is
vendored at `vendor/echarts.min.js`; re-vendor it or pass `--cdn` if it's
ever missing.

---

## `render-dashboard` — compose charts → one governed dashboard HTML

**Purpose:** a whole surface — stamped header, KPI cards, a responsive chart
grid, tables, verbatim caveats — as one self-contained `.html`. Chart
blocks **are** `render-chart` specs, drawn by the same shared
option-builders.

```
assay:render-dashboard [spec.dashboard.json] [--theme auto|light|dark] [--cdn]
```

**Helper**
```bash
python3 "$RD_ROOT/scripts/render_dashboard.py" \
  --spec <study>.dashboard.json \
  --out  <study>-dashboard.html \
  [--theme auto|light|dark] [--cdn] \
  [--echarts <path>] [--chart-builders <path>] [--theme-tokens <path>]
```

**Dashboard spec (top level):** `title`, `subtitle`, `accuracy_tier`,
`source`, `window`, `published_by`, `generated_at`, `mock`; `kpis[]`
(`{label,value,detail,emphasis: accent|good|bad|none}`); `sections[]` of
`charts` (`layout: two-col|one-col`, `blocks[]` each `{caption,height?,spec}`
where `spec` is a render-chart spec) and `table`
(`columns[{label,align,format}]`, `rows[]`, optional `highlight`);
`caveats[]` (verbatim, never trimmed); `footer_note`. See
`examples/example.dashboard.json` for a complete, minimal spec.

**Gotchas:** refuses any chart block whose type is not allow-listed
(`single_line`/`multi_series_line`/`ranked_bar`/`funnel`). Set
`accuracy_tier` to the lowest input tier; `mock: true` if any input is mock.
Emits a file — attaching/hosting it anywhere is a downstream, human choice.

---

## `export-report` — artifact → self-contained PDF

**Purpose:** package a governed artifact as one downloadable PDF — chart
inline when present, every table-bearing section, Narrative, verbatim
Caveats, tier+source stamp, MOCK DATA banner. Local only. Accepts **two**
artifact shapes: a `query-metric` / `visualize-metric` artifact (keyed by
`metric:` in frontmatter, usually with a chart, its table under
`## Results`) or a spine-analysis artifact from `funnel-dropoff`,
`stage-timing`, `segment-compare`, or `funnel-pulse` (keyed by `skill:`
instead, chart-less, its table(s) under its own skill-specific heading(s) —
e.g. `## Stage funnel` + `## Where it leaks (terminal branch)`, or
`## Latency by transition`). An artifact with **neither** `metric` nor
`skill` is refused.

```
assay:export-report [artifact path]
```
Run `assay:visualize-metric` first so a metric artifact has a
`## Visualization` section (without one — or for a chart-less spine
artifact — the PDF is still produced, just chart-less).

**Helper** (needs `fpdf2` in the venv)
```bash
<python> "$ER_ROOT/scripts/report_pdf.py" \
  --artifact <artifact_path> \
  [--published-by "<name>"] \
  [--out <path.pdf>]
```

**Returns:** prints `{"pdf_path", "title"}`; PDF written next to the
artifact (`outputs/assay/{date}-{slug}.pdf` by default). Renders **every**
table-bearing section in the artifact's own document order — not a single
hardcoded `## Results` lookup — so a spine artifact's several
skill-specific tables all render with no per-skill heading map to maintain
(see `table_sections()` in `report_pdf.py`). Reproduces numbers and caveats
**verbatim**; renders a MOCK DATA banner when `fixture_backed` or
`source_mode` is `mock_csv`/`fixture_sql`; quantizes the chart so the file
stays small.

**Gotchas:** exports governed output only — an artifact missing
`accuracy_tier`, `source_view`, `generated_at`, or **both** `metric` and
`skill` is refused. No upload/email/publish — this skill only writes a
local file. Any T1/T3 metric is refused further upstream, by
`query-metric`, before this skill ever sees it.

---

# Outlined skills (documented, not implemented)

These three are named throughout this documentation suite and in the module
registry (`config/modules.yaml`), each excluded there with a specific,
stated reason — but there is **no script behind any of them in this
repository**. Do not invoke them expecting real output. They exist here so
a fork knows what the next skills would need, and so the boundary of
"what this template actually does" is stated rather than implied.
(`funnel-nowcast` was here through Phase 4 and is now implemented — see its
entry under *Spine-backed analyses* above.)

## `release-scorecard` — pre/post release verdict

**Would do:** accept a pre-registered hypothesis (segment, launch date,
expected direction, optional control segment), compose a governed pre/post
read plus now-cast context for immature cohorts, and issue a causal verdict
via a BSTS counterfactual (and difference-in-differences when a control
segment is specified).

**Why it is not implemented here:** unlike `funnel-nowcast`'s development
factors — which are estimable from the triangle at run time, and which is why
that skill now ships — a causal verdict's parameters (BSTS priors,
control-series selection, pre/post windows) cannot be derived from the spine,
and computed over generated data they would measure the generator's
parameters, not a release.

## `customer-journey` — person-grain journey analysis

**Would do:** the multi-order analog to the session-grain funnel — a true
customer-grain conversion/repeat rate (the rate the per-session funnel
understates when one person makes several attempts), and journey shares
(multi-order, same-day repeat, re-attempt after a decline).

**Why it is not implemented here:** it is grain-scoped to a customer spine
this template does not register. Session grain cannot express "did ANY of
this customer's sessions reach X" — that is a different denominator, not a
different filter on the session spine.

## `publish` — governed artifact → read-only wiki page

**Would do:** compose a governed artifact (or a space overview) into a
read-only wiki page, resolved via a deterministic page registry, with a
read-only banner, the tier stamp, and verbatim caveats — **requiring
explicit human approval before any write**, never publishing an artifact
that lacks governed frontmatter, and never hand-editing a page it owns.

**Why it is not implemented here:** it writes to an external system this
template has nowhere to write to. Today, distribution means
`assay:export-report` (a local PDF) or `assay:render-chart` /
`assay:render-dashboard` (a local HTML file) — a human then sends, hosts, or
attaches the file themselves.

---

## Compose chains (at a glance)

```
query-metric ──→ visualize-metric ──→ export-report      # number → chart → PDF
                          │
                          └──────────→ render-chart       # → interactive HTML
                          └──────────→ render-dashboard   # → multi-panel HTML

funnel-pulse ──→ funnel-dropoff ──→ stage-timing ──→ segment-compare
   # regression deep-dive: broad sweep → where it leaks → how slow → describe the shift
   # any of these four (chart-less) ──→ export-report   # spine analysis → PDF

reconcile   # any governed metric, against an external value, on demand
verify      # any query-metric artifact, offline, at any time
```
