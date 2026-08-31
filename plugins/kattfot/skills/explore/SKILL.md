---
name: explore
description: >
  Triggers on: "explore the data", "dig into", "ad-hoc query", "the metric
  can't answer this", "investigate why", or any analytics question no
  registered governed metric can serve. The exploratory lane: free-form
  SELECT SQL over the register-declared surfaces, stamped T3 (not
  decision-grade), with the full provenance machinery — manifest receipt,
  audit trace, PII refusal, fixture watermark. Requires the user's explicit
  confirmation before leaving the governed lane.
version: 0.1.0
---

# Explore

You are running the `explore` skill — the **exploratory lane**. It exists for
the questions the governed metric runtime structurally cannot answer: a
grouping no metric allow-lists, a shape that is not a SUM/SUM ratio, a
row-level look at outliers, a join or window the catalog never anticipated.
The trade is stated on every result: what comes back is **T3** — ad hoc, no
reviewed definition, no named owner, not decision-grade — and the artifact
says so as loudly as a fixture watermark.

**Scope (read this):** exploration relaxes the *allow-lists*, never the
*account*. The register decides which surfaces may be read, R6 (PII) is
refused exactly as hard as in the governed lane, every query lands in the
same hash-chained audit log, and every result carries a manifest receipt.

## Routing doctrine — refer, then confirm

The lane switch is always **visible and consented**. Route here only when
BOTH hold:

1. **A governed path refused or cannot serve.** Either a helper printed an
   `EXPLORABLE --` line under its refusal (R1 no such metric, R2/R3
   off-allow-list, R4 a T3 definition), or the user asked for something the
   governed shapes cannot express (row-level output, an unregistered
   grouping, an arbitrary join), or the user asked to explore by name.
2. **The user said yes, after being told the trade.** Before running
   anything, state (a) why the governed path cannot serve the question, and
   (b) that the exploratory result is T3 — not decision-grade, not for
   recurring use. Then wait for explicit confirmation. Never auto-route; a
   user who asked a governed question is owed a governed answer or a refusal,
   not a silent downgrade.

Never use this lane to re-ask a question the governed lane **refused for
cause**: a PII dimension (R6) is exactly as refused here, and a T1 question
still belongs to the system of record.

## Engine selection

| `KATTFOT_ENGINE` | What happens | `source_mode` |
|---|---|---|
| `sqlite` (default) | The SQL runs against the committed fixture CSVs in in-memory sqlite, via the dialect translator. Untranslatable constructs refuse cleanly (`EXPLORE-DIALECT`) — checked on a real run and, explicitly, under `--dry-run` too (see below). | `fixture_sql` |
| `bigquery` | The SQL runs on the live warehouse as the authenticated human (ADC; service accounts refused), capped by the register's `max_bytes_billed`. | `bigquery` |

## Cost preview (`--dry-run`)

`--dry-run` estimates what a query would scan instead of running it.
Governance still comes first: every guard above (statement, surface, star,
R6, dialect) runs unchanged, so a query that would be refused is refused,
not priced — `--dry-run` never bypasses a refusal. On `sqlite`, the dialect
check normally only happens as a side effect of executing the query, so
`--dry-run` runs it explicitly instead — an untranslatable construct
refuses (`EXPLORE-DIALECT`) exactly as it would on a real run, not just
when you finally execute for real.

- **On `bigquery`**, the helper makes a real `dry_run=True` job — the query
  is validated and priced server-side, nothing is scanned, nothing is
  billed — and reports `estimate.total_bytes_processed` next to the
  register's `max_bytes_billed`, with `estimate.would_exceed` telling you up
  front whether the run would be refused for scanning too much.
  `would_exceed` (and `max_bytes_billed`) is `null` rather than `false` when
  the register configures no cap — nothing to compare against, not a pass.
  This is a real, identity-asserted API call (same ADC rules as a normal
  run), just one that never executes the query.
- **On `sqlite`**, there is no cost dimension to estimate — a committed
  fixture CSV has no bytes-scanned concept — so the helper reports
  `estimate_available: false` with the reason, honestly, rather than a
  fabricated number. Every guard above (statement/surface/star/R6/dialect)
  still ran, so this still functions as an offline governance lint even
  with no price to show.
- Bytes are reported, never dollars: on-demand pricing varies by region and
  billing edition, so converting bytes scanned to a cost figure is a
  deployment-specific step, not something this template hard-codes.
- No rows, no manifest, and no audit-log line are produced by a dry run —
  nothing was read, so there is nothing to fingerprint or log as a read.
  **No artifact either** — see step 5.

## Skill root resolution

Resolve `EXPLORE_ROOT`, first match wins:

1. `${CLAUDE_PLUGIN_ROOT}/skills/explore` (Claude Code)
2. `<workspace>/.cursor/skills/explore` (Cursor)
3. the directory of this loaded SKILL.md

## Flow

1. **Confirm the lane switch** (see the routing doctrine above). Skip only
   when the user explicitly invoked exploration themselves.
2. **Read the register** at `config/exploration.yaml` (or
   `KATTFOT_EXPLORATION_FILE`) to see which surfaces exist, their status,
   and their notes — write SQL only against registered, `live` surfaces.
3. **Write one SELECT statement** in canonical BigQuery dialect. Reference
   surfaces as `dataset.table` exactly as registered — the helper qualifies
   the project itself. Bind user-supplied values with `--param name=value`
   and `@name` placeholders rather than interpolating them into the SQL.
4. **Run the helper — the only sanctioned path:**

   ```bash
   python3 $EXPLORE_ROOT/scripts/kattfot_explore.py \
     --question "<the user's question, in words>" \
     --sql "SELECT ..." \
     [--param name=value ...] [--engine sqlite|bigquery] [--dry-run]
   ```

5. **Render the artifact** from the helper's JSON using the Output format
   below, into `${KATTFOT_OUTPUTS_DIR:-outputs/kattfot}/<date>-explore-<slug>.md`
   (slug: a few words from the question, kebab-case). **Skip this step
   entirely on `--dry-run`.** A dry-run result has no `rows`, no
   `row_count`, and no `manifest` — the Output format below requires all
   three, and forcing a dry-run result through it means either stalling or
   fabricating a manifest for a query that never ran. Instead, report the
   estimate in chat: `estimate.total_bytes_processed_human` against
   `estimate.max_bytes_billed_human` (or the `estimate_unavailable_reason`
   on sqlite), state `estimate.would_exceed` plainly (including the `null`
   case — "no cap configured, so no signal either way"), then ask whether to
   run it for real. Only a real (non-dry-run) result gets an artifact.
6. **Chart it if the user wants a picture.** The artifact goes through the
   same governed pipeline as a metric: `kattfot:visualize-metric` builds the
   spec (name the column mapping with `--x` / `--value-column` / `--series`
   when the inference rule would guess wrong), and `kattfot:render-chart` /
   `kattfot:render-dashboard` / `kattfot:export-report` all render an
   **EXPLORATORY (T3) — NOT DECISION-GRADE** banner from the spec's
   `exploratory` flag — alongside the MOCK watermark when fixture-backed.
   The stamp survives into every rendered form; that is the condition under
   which a T3 number may become a chart at all.
7. **Present the result** with the T3 framing intact: lead with the caveat
   that this is exploratory, then the numbers. If the question sounds like it
   will recur, offer `kattfot:promote` to scaffold a governed draft.

## Refusal rules

Each refusal is enforced in the helper (exit 5), names its rule, and shows
what *is* available. Do not work around any of them.

| Rule | Trigger |
|---|---|
| `EXPLORE-STATEMENT` | Not exactly one SELECT/WITH statement, or a write/DDL/scripting keyword anywhere in it |
| `EXPLORE-SURFACE` | A referenced table is not in the exploration register, or is registered but not `live` |
| `EXPLORE-STAR` | `SELECT *` over a surface whose register entry does not attest `select_star: true` |
| `R6` | Any identifier in the statement matches the PII denylist — same rule, same hardness as the governed lane. **No override.** |
| `EXPLORE-DIALECT` | The sqlite engine cannot translate a construct — rewrite it or use `--engine bigquery` |

## Output format

```markdown
---
skill: explore
accuracy_tier: T3
question: "{question}"
surfaces: {surfaces}
source_mode: {source_mode}
source_project: {source_project}
engine: {engine}
fixture_backed: {fixture_backed}
generated_at: {generated_at}
manifest:
  plugin_version: {manifest.plugin_version}
  register_sha: {manifest.register_sha}
  query_sha: {manifest.query_sha}
  result_sha: {manifest.result_sha}
---

# Exploration — {question}

> **EXPLORATORY (T3) — NOT DECISION-GRADE.** Ad-hoc result: no reviewed
> definition, no named owner, no promise it means the same thing next week.
> If this question recurs, promote it to a governed T2 metric
> (`kattfot:promote`).

> (!) **FIXTURE DATA** — synthetic fixtures from a seeded generator; the
> values are meaningless. _(banner present only when fixture_backed is true)_

**Surfaces:** {surfaces}   **Rows:** {row_count}

## Results

{the result rows as a markdown table — or, above ~50 rows, a summarized
table plus the row count, never a silent truncation}

## Narrative

{2-5 sentences answering the question from the rows — describing shape, not
inventing precision. Name the T3 caveat once in prose.}

## Caveats

{every warning line from the helper, verbatim — the EXPLORATORY line first}

## Provenance

Executed SQL (canonical BigQuery dialect):

```sql
{query}
```

Bound parameters:

```json
{query_params, as JSON — render {} when none}
```

Manifest: `register_sha {register_sha}` · `query_sha {query_sha}` · `result_sha {result_sha}`
```

> The Provenance fences are load-bearing, not stylistic: `kattfot:promote`
> re-parses the SQL and parameters from exactly these blocks and recomputes
> `query_sha` as its tamper check. Render them verbatim from the helper JSON.

## Hard rules

- **Vetted skill scripts are the only path to the warehouse. No exceptions.**
  The sanctioned helper for this lane is `kattfot_explore.py`; the full
  sanctioned-path table lives in `query-metric/SKILL.md` ("Hard rules").
  Never reach the warehouse via `bq`/`gcloud`, `curl`, or an ad hoc python
  client — the hook denies those paths, and they skip the identity, PII, and
  audit machinery that makes this lane defensible.
- **Read-only SELECT.** The helper enforces it; never attempt DML/DDL.
- **Never interpolate user-supplied values into SQL text.** Bind them with
  `--param` — a bound value is data, not SQL.
- **Never present a T3 number without its tier.** Stripping the EXPLORATORY
  framing from a result is the one way this lane can do real damage.
- No service-account keys; never set `GOOGLE_APPLICATION_CREDENTIALS`.

## Out of scope

- **Anything T1.** Regulator-grade questions go to the system of record.
- **Editing the register.** Adding a surface is reviewed as governance.
- **Stripping the stamp.** A chart, dashboard, or PDF built from an explore
  artifact carries the EXPLORATORY banner by construction; producing an
  unstamped rendering of a T3 number is not a supported path.

## Status in this template

Functional and offline: the register ships with the two fixture-backed
checkout surfaces, so the full flow — guard, refusal, query, manifest, audit
— runs on a fresh clone with no credentials. `scripts/demo_skills.py`
exercises it, and `tests/test_explore.py` pins every refusal rule.
