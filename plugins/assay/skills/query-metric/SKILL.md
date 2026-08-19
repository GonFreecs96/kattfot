---
name: query-metric
description: >
  Run a registered analytics metric end-to-end against the curated view (or the
  committed fixtures) and produce a reviewable markdown artifact stamped with the
  metric's accuracy tier and a content-addressed provenance receipt. Enforces an
  allow-list on metrics, dimensions, and filters, and refuses cleanly on anything
  outside it. Warehouse queries run as the individual human user via browser
  OAuth — never a service account.
---

# Skill: query-metric

## Purpose

Take a natural-language question about a **registered** metric, validate it against
that metric's allow-list, run it, and write a markdown artifact a colleague could
audit six months later without asking you anything.

The governance is the product. Anyone can compute a conversion rate; the difficulty
is producing one that carries its own tier, its own caveats, its own bound
parameters, and a fingerprint proving which definition made it.

## Invocation

```
/query-metric <natural-language question>
```

Examples:
```
/query-metric Cart to checkout conversion by week for the last 12 weeks, split by channel.
/query-metric What's the payment approval rate for bank transfers in the UK?
/query-metric Show me the return rate by category over the last 26 weeks.
```

## Engine selection (read this first)

`ASSAY_ENGINE` decides where the query runs. It defaults to **`sqlite`**, so a fresh
clone works with no credentials and no configuration:

| `ASSAY_ENGINE` | What happens | `source_mode` |
|---|---|---|
| `sqlite` (default) | The metric's **real SQL** runs against the committed fixture CSVs, loaded into an in-memory database and translated once by `_shared/dialect.py`. | `fixture_sql` |
| `bigquery` | The metric's SQL runs against the curated view, as the authenticated human user. | `bigquery` |

A metric declaring `status: dummy` always routes to `mock_csv` instead — a Python
aggregation over a sample CSV — because such a metric's view is not provisioned
anywhere. That path exists as an independent numeric oracle (it shares no code with
the SQL path), not as the offline path for real metrics.

Both fixture modes set `fixture_backed: true` in the output, which every downstream
renderer turns into a MOCK DATA watermark. A fixture number cannot travel into a
chart or a PDF and lose that label.

## First-run setup

```
python3 -m venv .venv
.venv/bin/pip install -r "$AQM_ROOT/scripts/requirements.txt"
```

The sqlite path needs only `pyyaml`. The BigQuery path also needs
`google-cloud-bigquery`. **Missing dependencies are a hard error (exit 3), never a
silent fall back to synthetic numbers** — the whole point being that a number never
quietly comes from fixtures when you believed you were querying a warehouse.

## Auth model (BigQuery path)

Queries run as the individual human user via Application Default Credentials from
`gcloud auth application-default login`. Service-account credentials are **refused**
in code (exit 4). If you see a refusal:

- `GOOGLE_APPLICATION_CREDENTIALS` set → `unset` it and re-run. Most common cause.
- ADC missing or stale → `gcloud auth application-default login`.
- ADC is an impersonated service account → `gcloud config unset auth/impersonate_service_account`, then log in again.

There is no service-account fallback. The skill never runs as a shared key.

**On the sqlite path the identity guard is not exercised.** That is an explicit
branch in `_shared/adapters/sqlite_adapter.py`, with the reasoning written down
there: a fixture read touches no warehouse and no credentials, so there is nothing
to attribute, and requiring a GCP login to read a committed CSV would be theatre.
Worth knowing plainly rather than discovering: "assay always runs as a human"
applies to the warehouse path. Every other control — allow-lists, PII refusal, the
tier gate, maturity, the manifest — runs upstream of the adapter and holds on both.

## Skill root resolution

Resolve a logical `AQM_ROOT` once per session and substitute it everywhere below.
**First match wins:**

1. `${CLAUDE_PLUGIN_ROOT}/skills/query-metric/SKILL.md` exists → use it.
2. `<workspace>/.cursor/skills/query-metric/SKILL.md` exists → use it.
3. Otherwise, the directory containing the `SKILL.md` you actually loaded.

Do not guess other locations.

## Content discovery

| Path | Default | Override |
|---|---|---|
| Metric definitions (consumer) | `context/assay/metrics/**/*.md` | `ASSAY_METRICS_DIR` |
| Metric definitions (bundled) | `$AQM_ROOT/metrics/**/*.md` | — |
| Fixture / sample CSVs | `data/assay/sample/` | `ASSAY_SAMPLE_DIR` |
| Output artifacts | `outputs/assay/{date}-{slug}.md` | `ASSAY_OUTPUTS_DIR` |
| Audit log | `outputs/assay/audit-log.jsonl` | `ASSAY_AUDIT_LOG` (disable: `ASSAY_AUDIT_DISABLE=1`) |
| Refusal doctrine | `context/assay/accuracy-tiers.md`, else `$AQM_ROOT/doctrine/accuracy-tiers.md` | consumer file wins |

For any given `name:`, the helper walks the consumer dir first, then the bundled
catalog. Consumer definitions win — a team can override one metric without forking.

## Flow

### Step 0 — Load doctrine

Read `<cwd>/context/assay/accuracy-tiers.md` if present, else
`$AQM_ROOT/doctrine/accuracy-tiers.md`. Whichever you loaded is the canonical source
of the refusal rules R1–R6 and the tier definitions.

### Step 1 — Identify the metric

List metric files in both locations, merge with consumer-wins precedence on `name:`.
Match the question against each metric's `name` and `display_name`.

If both locations are empty, say so and point at
`$AQM_ROOT/templates/example-metric.md` and `$AQM_ROOT/AUTHORING.md`.

If nothing matches but the registry is populated, **refuse per R1** and list the
registered display names. The helper itself offers a nearest-match suggestion on a
typo, so pass the question through rather than guessing.

If the question is genuinely **exploratory** — a one-off cross-tab, a multi-step
dive with no governed metric intended — do not list T2 metrics at it. Say that it
belongs in an ad-hoc SQL lane, and that if it becomes a recurring need it should be
promoted to a registered T2 metric.

### Step 2 — Read the matching metric

Parse the frontmatter: `name`, `accuracy_tier`, `source_view`, `source_project`,
`time_column`, `time_column_type`, `numerator_column`, `denominator_column`,
`value_format`, `allowed_dimensions`, `allowed_filters`, `default_lookback_weeks`,
`status`, `maturity_stage`.

Also read the body — the `## Caveats` section goes into the output **verbatim**.

### Step 3 — Tier check

`T2` → proceed. `T1` → refuse per R4 (it belongs to the system of record).
`T3` → refuse per R4 and point at an ad-hoc lane. The helper enforces this too, so a
direct invocation cannot route around it.

### Step 4 — Parse the question

Extract dimensions ("split by channel" → `channel`), filters ("for bank transfers"
→ `payment_method=bank_transfer_review`), and the time range ("last 8 weeks",
"since March 1"). Absent a range, use `default_lookback_weeks`.

### Step 5 — Allow-list validation

- Dimension not in `allowed_dimensions` → **refuse per R2**, listing what is allowed.
- Filter column not in `allowed_filters` → **refuse per R3**, likewise.
- Individual-level request ("show me each order", "by session id") → **refuse per R5**.
- A PII column — personal name, email/phone, government ID, financial account or card
  number, health record, date of birth, street address → **refuse per R6, hard, no
  override.** Also enforced in `_shared/pii.py`, which checks *before* the
  allow-lists, so a metric that allow-lists a personal column by mistake has not
  thereby authorized it. Tokenized keys (`customer_token`, `order_id`) and aggregate
  geography (`market`, `city`, `postal_code`) are **not** PII and stay allowed.

Two honest limits on R6: the in-code check only protects the vetted helpers (the
access-path hook is what makes it more than advisory), and the durable backstop is
column-level security in the warehouse itself. Confirm with your data platform team
whether that is configured before treating the code guard as complete.

### Step 6 — Call the helper

```
<python> "$AQM_ROOT/scripts/assay_query.py" \
  --metric <metric_name> \
  --dimensions <comma-separated-or-empty> \
  --time-range <last_N_weeks | YYYY-MM-DD:YYYY-MM-DD> \
  --filter <col>=<value> \
  --output-format json
```

Optional: `--engine sqlite|bigquery` overrides `ASSAY_ENGINE` for one run;
`--today YYYY-MM-DD` pins the maturity cutoffs (**use this when reading the
committed fixtures** — they were generated for a fixed observation instant, recorded
in `data/assay/sample/FIXTURES.md`).

The helper re-validates the allow-lists, builds the parameterized SQL, applies the
maturity cutoff, runs it on the resolved adapter, and returns JSON on stdout.

### Step 7 — Parse helper output

- `metric_name`, `display_name`, `accuracy_tier`, `source_view`, `source_project`
- `column_label` — **use this as the value-column header**, not the raw `metric_name`.
- `value_format` — listed for context only; the helper has already applied it.
- `source_mode` (`bigquery` | `fixture_sql` | `mock_csv`), `source_mode_reason`
- `engine`, `fixture_backed`, `engine_capabilities`
- `query`, `query_params` — the parameterized SQL and its bound values
- `time_range`, `dimensions`, `filters`, `maturity`
- `rows`, `row_count`
- `total_numerator`, `total_denominator` and their `_display` forms — **the volume the
  headline rate sits on.** Surface it so a rate is never shown without its n.
- each row's `_display` map — **render the Results table from this**, not from raw
  values. Formatting is codified and tested in `_shared/numfmt.py`; do not re-derive it.
- `manifest` — the receipt: `{plugin_version, metric_def_sha, query_sha, result_sha}`.
  **Render verbatim.** Do not recompute or alter the hashes; `assay:verify` reads them
  back to detect drift.
- `warnings` — **surface prominently.** Kinds:
  - `FIXTURE DATA:` → the numbers are synthetic. Say so in the narrative, not only in
    the banner.
  - `MATURITY:` → cohort weeks after a cutoff were withheld. If it also says the lag is
    illustrative rather than measured, carry that through — implying measurement where
    there was none is the one thing this machinery must not do.
  - a **zero-row** warning → do **not** narrate a result. Report the empty or typo'd
    filter condition instead.
  - `DISCONTINUITY:` → the window crosses a known cutover; pre/post are not directly
    comparable.
  - `CAVEAT (...):` → a data-quality note; fold it into Caveats.

### Step 8 — Format the output

Use the template below. Save to `<ASSAY_OUTPUTS_DIR>/{YYYY-MM-DD}-{metric-slug}.md`.

Branch on the result first:

- **Zero rows** — do not invent a narrative. Surface the warning verbatim, state that
  the query returned nothing, and point at the likely typo'd filter value. Still write
  the artifact, with the SQL in Provenance, so the user can see exactly what ran.
- **Large result** (roughly > 25 rows) — lead with a compact summary (headline rate,
  volume, and the top/bottom slices on the dimension that matters), then put the full
  dump in a `<details>` block. Do not silently truncate; say what was summarized.
- **Small result** — render the full table.

### Step 9 — Present the path

Tell the user the output path. Do not paste the whole artifact into chat.

Then offer to chain onward — "want a chart of this? `/visualize-metric` will infer the
best chart type and add it to the artifact" — but only offer. Do not auto-run.

## Refusal rules

On any refusal: state which rule fired and the specific input that caused it, provide
the allow-list (R1–R3) so the user can see what is available, do not apologise, do not
propose a workaround that violates the same rule, and **do not write an output file**.

## Output format

```markdown
---
metric: {name}
display_name: {display_name}
accuracy_tier: {accuracy_tier}
source_view: {source_view}
source_project: {source_project}
source_mode: {bigquery | fixture_sql | mock_csv}
engine: {engine}
fixture_backed: {true | false}
generated_at: {iso_timestamp}
time_range_start: {start}
time_range_end: {end}
maturity_cutoff: {maturity.cutoff_week}   # omit the key when maturity is null
dimensions: {dimensions}
filters: {filters_dict}
manifest:
  plugin_version: {manifest.plugin_version}
  metric_def_sha: {manifest.metric_def_sha}
  query_sha: {manifest.query_sha}
  result_sha: {manifest.result_sha}
---

# {display_name} — {time_range_human}

**Accuracy tier:** {tier} ({tier_one_line_description})
**Source view:** `{source_project}.{source_view}` ({mode_description})
**Generated:** {iso_timestamp}

> ⚠️ **FIXTURE DATA** — these numbers come from committed synthetic fixtures, not a
> warehouse. They describe a seeded random process. ({source_mode_reason})
> _(banner present only when fixture_backed is true)_

**Volume:** {total_numerator_display} / {total_denominator_display} over the period ({row_count} rows).

## Results

| {time_column} | {each dimension} | {column_label} | numerator | denominator |
| --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... |

> Always keep the numerator/denominator columns — a rate alone hides whether it sits
> on n=175 or n=3,401. Fill every numeric cell from the row's `_display` map.

## Narrative

[2–3 paragraphs answering the question. Cite the metric by name. If fixture-backed,
say explicitly that this describes the synthetic data, not real behavior.]

## Caveats

[Verbatim from the metric definition's "Caveats" section, plus any warning-derived
notes.]

## Provenance

- **Metric definition:** `{metric_definition_path}`
- **Source mode:** {source_mode} ({source_mode_reason})
- **Engine:** {engine} — capabilities: {engine_capabilities}
- **Query (parameterized):**
  ```sql
  {query}
  ```
- **Parameters:** `{query_params}`
- **Maturity:** {maturity.stage} lag {maturity.lag_days}d, cohorts through {maturity.cutoff_week} ({maturity.source})
- **Manifest (receipt):** plugin `{manifest.plugin_version}` · metric-def `{manifest.metric_def_sha}` · query `{manifest.query_sha}` · result `{manifest.result_sha}` — a faithful re-run reproduces these; check with `assay:verify`.
```

`maturity_cutoff` is in the frontmatter rather than only in the Provenance prose
because it is an **input to the query**, exactly like the window is. `assay:verify`
rebuilds the SQL from the current definition plus the artifact's recorded inputs; if
the cutoff were missing it would rebuild without the predicate, disagree with the
recorded `query_sha`, and report drift on every governed artifact. A verifier that
always cries drift gets ignored, which is worse than not having one.

## Why the SQL is always BigQuery dialect

The helper emits canonical BigQuery SQL on every engine, and the sqlite adapter
translates it once (`_shared/dialect.py`). Two reasons, both worth knowing when
reading a `query` field in an artifact:

1. Dialect knowledge stays out of the governance layer. The allow-list checks, the
   maturity gate, and the SQL builder have no idea which engine they are serving.
2. The `query_sha` receipt is therefore engine-independent for a given project — the
   same metric read from fixtures and from the warehouse produces the same
   fingerprint, so `assay:verify` works across both.

The translation set is enumerated exhaustively in `dialect.TRANSLATIONS` and pinned by
a golden test. Anything outside it raises rather than being guessed at.

## Audit / trace log

Every run appends one JSON line to `<ASSAY_OUTPUTS_DIR>/audit-log.jsonl`: metric,
tier, source mode, engine, whether it was fixture-backed, view, project, dimensions,
filters, time range, row count, generated-at, the manifest, and — on the BigQuery path
— the authenticated user's email. It is a **trace, not a copy**: row data is never
logged. Writing is best-effort and append-only; an audit-log failure never blocks a
query.

## Hard rules

- **Vetted analytics skill scripts are the only path to the warehouse. No exceptions.**
  Never query, list, or describe warehouse data via a `bq`/`gcloud` CLI, `curl` against
  a REST API, or an ad hoc `python` one-liner using a client library — even for a
  "quick" check, even if asked to skip the wrapper "just this once". Those paths use
  the same credentials and skip `_shared/identity.py` and `_shared/pii.py` entirely. If
  a vetted script lacks a capability you need, that is a bug to fix in the plugin, not
  a reason to route around it. The `PreToolUse` / `beforeShellExecution` hook enforces
  this mechanically; do not try to satisfy a request by defeating it.

  **Sanctioned helpers** (each runs the identity and R6 checks before querying):

  | Skill | Script |
  |---|---|
  | `query-metric` | `$AQM_ROOT/scripts/assay_query.py` |
  | `setup` (probe only) | `$AQM_ROOT/../setup/scripts/doctor.py --probe` (a trivial `SELECT 1`) |

  Spine-backed skills will share `_shared/spine.py`, which applies the same checks
  through the same adapter. Repo-root `scripts/` one-offs are **not** sanctioned —
  they are maintainer tooling outside the plugin.

- Never write or read service-account JSON keys. Never set
  `GOOGLE_APPLICATION_CREDENTIALS`. Never invoke `gcloud auth
  activate-service-account` or `--impersonate-service-account`. Exit code 4 means
  stop and tell the user.
- Never paste credentials into the conversation or ask the user to.
- Never run destructive SQL without explicit confirmation in the same turn. Default is
  read-only `SELECT`.

## Out of scope

- Multiple metrics in one invocation (use the funnel mode of `visualize-metric`).
- Cross-metric joins.
- Writing back to any external system.
- Modifying the consumer's metric registry or curated views.
- Anything T1 or T3.

## Status in this template

Complete and runnable. `ASSAY_ENGINE=sqlite` (the default) runs the whole chain
against the committed fixtures with no credentials, so
`git clone && pytest && scripts/demo.py` works on a fresh machine.

The bundled catalog is the fictional `checkout` module: 15 T2 metrics over a
cart-grain funnel whose deepest stage is a ~30-day return window. That domain was
chosen because the return window makes the maturation tail real — the reason the
per-stage maturity machinery is not ornamental. Swap it by replacing the funnel spec
and the metric definitions; the engine does not change.

`reconcile` and `catalog` are referenced in the doctrine as the intended siblings and
are not in this repo yet.
