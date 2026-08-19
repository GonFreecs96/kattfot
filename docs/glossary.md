# assay — Glossary

*A reference for anyone reading this repo or a fork of it. Companion to
[`runbook.md`](runbook.md) (how to use these), the
[skill cheat sheet](skill-cheatsheet.md) (every flag + example), and
[`what-is-assay.md`](what-is-assay.md) (why this exists).*

How to read this: terms are grouped by theme — **Big picture**,
**Governance**, **Data & funnel domain**, **Skills**, **Registered metrics**,
**Config files**, **Scripts**, **Generated artifacts**. Skim the group you
need.

---

## Big picture

| Term | What it means |
|---|---|
| **assay** | The product this repo ships: a governed-analytics Claude Code / Cursor plugin, plus the repo tooling (fixture generator, demo scripts, scrub gate) around it. |
| **Module** | A governed business domain — a set of metrics, optionally a spine, and the registry entry that ties them together. This template registers two: `checkout` (live) and `browse` (declared). |
| **Spine** | One analysable grain within a module: a curated view with one row per instance of that grain, carrying every stage flag, stage timestamp, and terminal-branch classification the spine-backed skills need. A module may have several spines at different grains; exactly one is the default. |
| **Grain** | The unit one row of a view represents. The checkout module's grain is **one checkout session (cart)**; the browse module's is **one visit to the catalog**. Choosing the wrong grain for a denominator is the single most common way a correct-looking query answers the wrong question — see `checkout-session.md`'s "why the cart, and not the order." |
| **Weekly aggregate** vs. **session spine** | Two projections of the same stage ladder. The weekly aggregate (`cart_checkout_funnel_weekly`) is pre-summed at `cohort_week × dims` and backs the fifteen governed ratio metrics; the session spine (`cart_checkout_session_spine`) is one row per cart and backs the four spine-backed skills (drop-off, timing, compare, pulse), because a percentile or a terminal-branch decomposition cannot be recovered from sums. |
| **Handoff edge** | A declarative spine → spine composition (`config/modules.yaml`), naming a join key and a stated coverage, for when a question spans two grains — e.g. "what share of visits produce a cart" spans `browse/session` and `checkout/session`. Declarative rather than SQL buried in a skill, because the hard part is the denominator, not the join. |
| **Consumer / consuming plugin** | Another tool that calls `assay` for a governed number or definition via `assay:query-metric` / `assay:load-context`, rather than reading files directly. The integration is agent-mediated skill invocation, defined by `contracts/consumer-api.md`. |
| **Compose chain** | The intended skill pipeline: `query-metric → visualize-metric → export-report` (or `render-chart` / `render-dashboard`). One governed artifact flows through rendering and distribution without re-querying. |
| **Engine** | Which adapter a query runs on: `sqlite` (default — committed fixture CSVs, no credentials) or `bigquery` (the curated view, as the authenticated human). Both run the *same* canonical SQL; see `dialect.py`. |
| **Fixture-backed** / **MOCK DATA watermark** | `fixture_backed: true` on any result produced via the sqlite engine or a `status: dummy` metric. Every downstream chart, dashboard, and PDF turns this into a visible watermark — a fixture number cannot travel into a stakeholder-facing artifact and lose that label. |

---

## Governance vocabulary

The guarantees that make this safe to hand to an agent. Full detail (per
control, what it guarantees and what it does not) is in
[`governance-model.md`](governance-model.md); most of the underlying doctrine
lives in `plugins/assay/skills/query-metric/doctrine/accuracy-tiers.md`.

### Accuracy tiers

| Tier | Meaning | Served here |
|---|---|---|
| **T1** | Regulator-grade — reconciles to the system of record. | **No.** No T1 metric is registered; this runtime has not implemented the machinery T1 needs. |
| **T2** | Operational — decision-grade for product/process work, not regulator-facing. | **Yes.** The entire bundled catalog is T2, enforced by the catalog linter. |
| **T3** | Exploratory — directional only, no stable definition. | **No.** This template has no exploratory lane; a genuinely exploratory question should be named as one rather than forced through a T2 metric. |

### Refusal rules (R1–R6)

A refusal names the rule, the offending input, and the allow-list — no
apology, no workaround that violates the same rule, no artifact written.

| Rule | Trigger |
|---|---|
| **R1** | Unregistered metric — the question maps to no metric definition. |
| **R2** | Dimension not in the metric's / spec's `allowed_dimensions`. |
| **R3** | Filter on a column not in `allowed_filters`. |
| **R4** | Matched metric's tier is not served (T1 or T3). |
| **R5** | Individual-level reveal — a specific cart, order, or session id. Views aggregate to a grain; row-level reads are not available. |
| **R6** | **PII column reach — hard refuse, no override, enforced in code** (`_shared/pii.py`), checked *before* the allow-lists. Refused: personal names, contact details, government IDs, financial account/card numbers, health records, dates of birth, street addresses. **Not** PII: tokenized join keys (`customer_token`, `order_id`, `payment_token`, `checkout_session_id`) and aggregate geography (`market`, `region`, `city`, `postal_code`, `country`). |

> **R6 is the only *refuse*-severity rule.** Everything in `guardrails.yaml`
> is *warn*-severity (surfaces a caveat, lets the query proceed).

### Identity & provenance

| Term | What it means |
|---|---|
| **ADC** | *Application Default Credentials* — established by `gcloud auth application-default login`. Every query on the `bigquery` engine runs as **your own Google identity**. |
| **Service account refused** | `_shared/identity.py` refuses service-account / impersonated credentials at the helper level (exit code 4). There is no shared key, no fallback. `GOOGLE_APPLICATION_CREDENTIALS` being set is the most common cause — unset it. |
| **Allow-list** | Metrics, dimensions, and filters are explicitly enumerated per metric or per funnel spec. Off-list requests refuse (R1–R3). |
| **Manifest (the receipt)** | A block stamped on every `query-metric` artifact: `plugin_version`, `metric_def_sha`, `query_sha`, `result_sha`. `generated_at` is excluded from every hash, so a faithful re-run reproduces them. `assay:verify` reads it back. |
| **Audit / trace log** | `outputs/assay/audit-log.jsonl` — one append-only JSON line per run (metric, tier, engine, dims, filters, window, row count, the manifest, and — on the BigQuery path — the authenticated user's email). Row data is never logged. |
| **Parameterized / bound params** | Filter values are passed as bound query parameters, never inlined into SQL — asserted by `tests/test_query_metric_governance.py`. |
| **Access-path hook** | The `PreToolUse` (Claude Code) / `beforeShellExecution` (Cursor) hook that denies every warehouse access route except the plugin's vetted scripts. A blocklist over the Bash command text, not a sandbox. |
| **Scrub gate** | `scripts/scrub_check.py` — the deny-token check that keeps the private repository this template was derived from, and its identifiers, out of the codebase. Runs as part of `pytest`. |

### Error sentinels (the consumer-API contract)

Single-line responses `assay:load-context` and `assay:query-metric` return
when they cannot fulfill a request. Match on the prefix before `--`. Defined
in `plugins/assay/contracts/consumer-api.md`.

| Sentinel | Meaning |
|---|---|
| `SCOPE_NOT_FOUND` | No scope/metric matched (even after fuzzy matching). |
| `EMPTY_RESULT` | Scope matched but zero items (e.g. an empty glossary scope). |
| `PATH_BOUNDARY` | Resolved path is outside the allowed read roots. |
| `TIER_REFUSED` | Matched metric is T1 or T3 — not served by the governed path. The load-bearing one: a consumer can never silently embed a non-governed number as decision-grade. |
| `OFF_ALLOWLIST` | Requested dimension/filter isn't allow-listed (`query-metric`). |
| `AUTH_REQUIRED` | Caller's ADC is missing, or a service account was supplied (refused). |

### Freshness, maturity & data-quality signals

| Signal | Meaning |
|---|---|
| `STALE: {file} last updated {date}, owner: {owner}` | A definition/doctrine file is older than the freshness threshold (default 90 days). Appended by `load-context`. |
| `MATURITY: ...` | The metric's per-metric maturity cutoff withheld recent cohort weeks not yet settled for its stage. If it also says the lag is illustrative rather than measured, that flows through too. |
| `DISCONTINUITY:` | The time window crosses a known `feed_cutovers` entry in `guardrails.yaml`. Pre/post numbers aren't directly comparable. |
| `CAVEAT (...):` | A data-quality note (unit / join / column / view-shape) attached because a query touched a matching column or view (`guardrails.yaml` `notes`). |
| **Guardrails (warn vs. refuse)** | `guardrails.yaml` holds *warn*-severity traps. They surface a caveat and let the query proceed. Only **R6/PII** refuses. |
| **Zero-row warning** | The query returned nothing. The skill must not narrate a result — it reports the empty/likely-typo'd-filter condition and still writes the artifact so the SQL is visible. |

---

## Data & funnel domain (checkout module)

Definitions live in `plugins/assay/config/checkout-funnel-spec.yaml`, the
views doc (`skills/query-metric/views/checkout-views.md`), and the lineage
note (`metrics/checkout/LINEAGE.md`).

### The funnel

```
Cart Created → Checkout Started → Payment Submitted → Payment Decisioned →
Order Confirmed → Shipped → Delivered → Return Window Complete
```

| Stage / term | Meaning |
|---|---|
| **Stage flags** | Each stage is a boolean flag on the spine (`cart_created_flag`, `checkout_started_flag`, …, `return_window_complete_flag`). Stage conversion = `COUNTIF(flag)` over the segment, computed stage-to-stage and cumulative. |
| **`terminal_branch`** | The single mutually-exclusive "where it ended" bucket per cart (deepest-stage-first, ordinal-prefixed for sorting, e.g. `4d Payment Declined`, `7 Delivered (Return Window Open)`). The primary drop-off dimension — a curated heuristic, not a field a source system emits. |
| **Return window** | The ~30-day window after delivery during which a customer may send an order back. What makes `return_window_complete` genuinely unresolved for recent cohorts, and why the per-stage maturity machinery is not ornamental. |
| **Legacy exclusion** | `legacy_exclude_column` / `legacy_exclude_value` (`checkout_version` / `v1-legacy`) — a retired cohort every default read excludes. Both-or-nothing by design: setting only one key would silently zero every row (`col != NULL` is `NULL`, not `TRUE`). |

### Slicing dimensions

| Dimension | Meaning |
|---|---|
| **`channel`** | Acquisition surface: `web`, `mobile_app`, `marketplace`, `social`. |
| **`payment_method`** | How the authorization was attempted: `card_instant`, `wallet`, `bank_transfer_review`. Drives *maturity* as well as conversion — a bank transfer's decision settles in days, not seconds, so the funnel spec carries a per-method maturity override on `payment_decisioned`. |
| **`market`** | Selling region — aggregate geography, not PII. |
| **`category`** | The dominant category of the cart's contents, assigned at confirmation. |
| **`checkout_version`** | `v2` (current) or `v1-legacy` (retired, excluded by default). |
| **`is_first_order`** | Exists on the session spine only — not carried into the weekly aggregate, so it is an allowed dimension for spine-backed skills but not for `query-metric`. |

### Grain, time & lineage

| Term | Meaning |
|---|---|
| **Cohort week** (`cohort_week`) | The ISO-Monday week the cart was **created**, kept forever — a cart that confirms three weeks later still counts in its creation week's cohort. What makes weekly conversion comparable at all. |
| **Cohort time vs. activity time** | These metrics anchor to the cart's *creation* week and track it forward. A hypothetical activity-time analyzer would count by the week an *outcome* happened, which is a different lineage and does not reconcile 1:1 by design. |
| **Per-stage maturity** | A cohort is mature for a **(cohort × stage) pair**, not for itself. `stage_maturity.lag_days` in the funnel spec. `maturity_stage:` on a metric names which stage its numerator depends on. |
| **Illustrative lags** | The `stage_maturity` block in this template is hand-authored, not measured — `illustrative: true`, `calibrated_at: null`, held in place by `tests/test_maturity.py`. A real deployment generates this block by measuring open-anchored settle time on its own spine. |
| **Entity** | A noun with its own identity, one registered file each: `checkout_session`, `order`, `payment`, `shipment`, `customer`, `channel`, `market` (plus `browse_session`, `declared`). See `entities/README.md`, "Entities vs. dimensions." |
| **Dimension (vs. entity)** | A categorical anchored to a parent entity (`payment_method` belongs to `payment`), not a standalone entity — it has values, not a lifecycle or relationships of its own. |
| **Business rule** | A shared rule with an id, referenced from a metric's `business_rules:` frontmatter and defined once in a module's `LINEAGE.md` — e.g. `per_stage_maturity`, `confirmation_creates_the_order`, `decision_is_not_approval`. |

---

## Skills (19)

Invoke as `assay:<skill>`. Fifteen are functional; four are documented
outlines. See [`skill-cheatsheet.md`](skill-cheatsheet.md) for every flag and
a worked example.

### Read APIs & front doors

| Skill | What it does | Touches the warehouse? |
|---|---|---|
| **`query-metric`** | Runs a registered metric end-to-end → a governed **number** + provenance artifact. | Yes (as you) |
| **`load-context`** | Returns governed **definitions/context** by scope. Pure file reads. | No |
| **`catalog`** | "What metrics can I ask?" — every registered metric + latency KPI + spine, grouped by module. | No |
| **`coverage`** | "Can I measure X yet?" from the instrumentation register. | No |
| **`setup`** | One-time guided onboarding: environment doctor, per-user OAuth, a `SELECT 1` probe. | Probe only |

### Spine-backed analyses

| Skill | What it does |
|---|---|
| **`funnel-dropoff`** | Stage drop-off + **terminal-branch attribution** — *where* the funnel leaks. |
| **`stage-timing`** | Percentile stage latency (`p50`/`p90`/`p99`/mean, with `n`) per transition. |
| **`segment-compare`** | A/B **and** pre/post comparison across funnel + terminal-branch mix + timing. |
| **`funnel-pulse`** | Stage-aware recent-vs-baseline regression monitor, ranked by impact. |

### Difference attribution & integrity

| Skill | What it does |
|---|---|
| **`reconcile`** | Explains **why** a governed number differs from an external one, by re-running under one tweaked convention at a time. |
| **`verify`** | **Offline** drift check on a `query-metric` artifact's manifest. Does not re-check the value. |

### Visualization, output & publishing

| Skill | What it does |
|---|---|
| **`visualize-metric`** | Governed artifact → faithful chart (SVG + PNG), inferring chart type from the result shape; funnel mode composes stage metrics. |
| **`render-chart`** | Same chart-spec JSON → one self-contained interactive HTML. Pure stdlib. |
| **`render-dashboard`** | Composes multiple chart specs + KPI cards + tables into one governed dashboard HTML. Pure stdlib. |
| **`export-report`** | Governed artifact → a self-contained PDF. |

### Outlined (documented, not implemented)

| Skill | What it would do |
|---|---|
| **`funnel-nowcast`** | Cohort-maturation now-cast (chain-ladder / development-factor) for an immature cohort, with P10/P50/P90 intervals. |
| **`release-scorecard`** | A pre-registered release's causal verdict via BSTS counterfactual (+ DiD when a control segment is specified). |
| **`customer-journey`** | Customer-grain journey analysis — multi-order, the person-grain analog to the session-grain funnel. |
| **`publish`** | Push a governed artifact to a wiki as a read-only page, gated on explicit human approval. |

---

## Registered metrics (15, module `checkout`)

All are **T2**, back `analytics.cart_checkout_funnel_weekly`, and can be
sliced/filtered by `channel`, `payment_method`, `market`, `category`,
`checkout_version`. Full list via `assay:catalog`.

| Metric (`name`) | Measures |
|---|---|
| `cart_to_checkout_start_conversion` | Cart created → checkout started. |
| `checkout_start_to_payment_submit_rate` | Checkout started → payment submitted. |
| `payment_submit_to_decision_rate` | Payment submitted → decisioned. |
| `payment_approval_rate` | Decisioned → approved. |
| `payment_decline_rate` | Decisioned → declined (the complement of approval). |
| `fraud_check_failure_rate` | Submitted → stopped before any decision. |
| `approved_to_order_confirmed_rate` | Approved → order confirmed. |
| `order_confirmed_to_shipped_rate` | Confirmed → shipped. |
| `confirmed_not_shipped_rate` | Confirmed and still not shipped (a stuck state). |
| `shipped_to_delivered_rate` | Shipped → delivered. |
| `delivered_to_returned_rate` | Delivered → a return was initiated. |
| `delivered_to_return_window_complete_rate` | Delivered → the 30-day return window closed. |
| `cart_to_order_confirmed_end_to_end` | Cart created → order confirmed (end-to-end). |
| `cart_to_return_window_complete_end_to_end` | Cart created → return window complete (full-funnel). |
| `cart_to_stale_rate` | Share of a cohort going stale (no progress past the 30-day convention). |

---

## Config files (`plugins/assay/config/`)

| File | Purpose |
|---|---|
| `modules.yaml` | The module registry: which modules exist, which spines back them, which skills apply, and any `handoff` edge. |
| `checkout-funnel-spec.yaml` | The runtime contract for the checkout spine: stage ladder, `terminal_branches` order, timing `transitions`, allow-lists, defaults, the `stage_maturity` lag table, the `pulse:` block, named `kpis:`. |
| `browse-funnel-spec.yaml` | The same shape for the browse spine — `declared`, not `live`. |
| `funnels.yaml` | Named compositions of ordered stage metrics for `visualize-metric`'s funnel mode, plus an `end_to_end` reference metric each. |
| `coverage.yaml` | The "can I measure X yet?" register behind `coverage`. |
| `guardrails.yaml` | *Warn*-severity data-quality traps: `feed_cutovers` and `notes`. |

---

## Scripts (`scripts/` — repo tooling, NOT shipped in the plugin)

| Script | Purpose |
|---|---|
| `generate_fixtures.py` | The deterministic fixture generator — seeded, so the same arguments produce byte-identical CSVs. |
| `demo.py` | The compose chain, end to end, offline: query → artifact → chart → PDF. |
| `demo_skills.py` | All fifteen functional skills, offline, as a gate — fails if any cannot run with no credentials. |
| `scrub_check.py` | The deny-token gate. |

---

## Generated artifacts & demo

| Path | What's there |
|---|---|
| `outputs/` | All generated artifacts — git-ignored, reproducible. `outputs/assay/{date}-{slug}.md` are `query-metric` outputs; `charts/` holds SVG/PNG/HTML + spec JSON; `audit-log.jsonl` is the trace log. |
| `data/assay/sample/` | The two committed fixture CSVs (`cart_checkout_funnel_weekly.csv`, `cart_checkout_session_spine.csv`) plus `FIXTURES.md` (seed, observation instant, trim). |
| `tests/` | Governance tests + catalog linter, run by `pytest`. |

---

### Quick legend for the most-confused pairs

- **`query-metric` vs. `load-context`** — a *number* (queries the warehouse) vs. a *definition* (file reads only).
- **`catalog` vs. `coverage`** — what you **can** ask vs. what you **can't yet** (and who owns the blocker).
- **`visualize-metric` vs. `render-chart`** — same chart spec; static SVG/PNG (feeds the PDF) vs. interactive self-contained HTML.
- **`verify` vs. `reconcile`** — *did the definition drift?* (offline, no warehouse) vs. *why does our number differ from theirs?* (re-runs the query).
- **`funnel-pulse` vs. `funnel-dropoff`** — broad scheduled monitor that tells you *which* group moved vs. on-demand deep dive into *where* one segment leaks.
- **Spine vs. weekly aggregate** — per-cart detail (drop-off, timing, branches) vs. pre-aggregated `cohort_week × dims` (the governed ratios).
- **`sqlite` vs. `bigquery` engine** — same SQL, translated once; identity is asserted on one and not the other, and both say so.
