---
name: load-context
description: >
  Triggers on: "load kattfot context", "kattfot context for...", "governed definition
  of...", or invoked programmatically by another plugin or tool to load governed
  metric definitions, accuracy tiers, entity definitions, lineage notes, the
  instrumentation coverage register, and the spine/handoff registry. Pure file
  reads — never queries the warehouse (use kattfot:query-metric for a live number).
  The primary API for cross-plugin consumption of governed analytics definitions.
version: 0.1.0
---

# Load context

You are running the `load-context` skill. Return governed analytics **context** —
definitions, tiers, entities, lineage, doctrine, coverage — for the requested scope.
**You never execute a query.** For a live number the caller wants
`kattfot:query-metric`.

> **Contract:** `contracts/consumer-api.md` defines the response format, the error
> sentinels, and the freshness signals consumers depend on. Do not deviate from it.
> A consumer that has to parse prose to tell a governed answer from a refusal has
> no guarantee at all.

## Run the helper

```bash
python3 plugins/kattfot/skills/load-context/scripts/load_context.py <scope> \
  [--compact] [--limit N] [--offset N] [--freshness-days 90]
```

With no scope it lists the available scopes and stops. The helper enforces the
contract in code — the sentinels, the read-root boundary, and the tier gate are not
prompt-level rules, because the first caller to invoke this script directly would
bypass a prompt-level rule entirely.

## Scopes

| Scope | Returns |
|---|---|
| `catalog` | every registered metric (name, display name, tier, module, one-liner), plus latency KPIs and the entity registry |
| `metric/{slug}` | one metric's full definition file |
| `module/{name}` | every metric definition under that module |
| `entities` | the entity registry: slug, display name, module, status, `metric_backed`, grain |
| `entity/{name}` | one entity's full definition: grain, keys, governed dimensions, relationships, anchored business rules |
| `lineage/{module}` | the module's `LINEAGE.md` — shared business rules and view lineage |
| `tiers` | the accuracy-tier doctrine and the refusal rules |
| `governance` | the ownership and governance doctrine: owners, review cadence, freshness SLA |
| `spines` | the module registry as data: every spine, its `status`, its skills, and any `handoff` edge with its coverage caveat |
| `coverage` | the instrumentation coverage register, grouped by status |
| `coverage/{id}` | one coverage entry |
| `glossary` / `glossary/{term}` | reserved-term definitions (empty in this template — a documented state, not an error) |

Options: `--compact` returns frontmatter only for definition and multi-file scopes;
`--limit` / `--offset` paginate list scopes (default limit 20).

## Error sentinels

Single lines with stable prefixes, printed to **stdout** because they are the
response rather than a log line. The exit code is the machine-readable duplicate.

| Sentinel | Meaning | Exit |
|---|---|---|
| `SCOPE_NOT_FOUND -- {detail}` | nothing matched, including after a fuzzy attempt | 1 |
| `EMPTY_RESULT -- {detail}` | the scope resolved but holds nothing | 1 |
| `PATH_BOUNDARY -- {detail}` | the resolved path escaped the allowed read roots | 5 |
| `TIER_REFUSED -- {detail}` | the metric is T1 or T3 — not served by this API | 5 |

Report the sentinel as the answer. Do not translate `SCOPE_NOT_FOUND` into a guess,
and do not summarise `TIER_REFUSED` into "it wasn't available".

### Why `TIER_REFUSED` is the load-bearing one

The tier guarantee is worth nothing if it holds inside this repo and evaporates at
the interop boundary. A consumer handed a T1 (regulator-grade, from the system of
record) or T3 (exploratory, unreviewed) body with no marking will present it exactly
like a governed T2 number, in a briefing, to someone who will act on it.

So a *specifically requested* non-T2 metric is refused outright — the body is never
returned. A `module/` or `catalog` read is **not** refused wholesale, because the
caller asked for a set: each non-T2 member is listed with its tier and its body
withheld, so the consumer can see that it exists without receiving it.

### Why `PATH_BOUNDARY` exists

Scopes are addressed by name, not by path, precisely so a consumer is not coupled to
the on-disk layout. The boundary check is on the **resolved** path, after resolution
— a textual check on the slug would have to anticipate every encoding of "parent
directory", and resolving first is the only ordering that is actually safe.

## Freshness

When a returned file has not been touched inside the freshness threshold (default 90
days), the helper appends, after a blank line:

```
STALE: {filename} last updated {YYYY-MM-DD}, owner: {owner}
```

The `, owner: …` suffix is omitted when the file has no `owner:` frontmatter rather
than being invented. Read from the filesystem mtime rather than a hand-maintained
`updated:` field, because that field is the thing that goes stale. See
`doctrine/ownership-governance.md` for the review-cadence policy this backs.

## What this skill must NOT do

- **Never run a query.** Definitions only — `query-metric` owns execution. Never
  reach the warehouse through the `bq` CLI, `gcloud`, a REST call, or ad hoc python
  using `google-cloud-bigquery`; the access-path hook denies all of those, and this
  skill has no reason to try.
- **Never invent a definition** for an unregistered metric — return
  `SCOPE_NOT_FOUND` with the registered names.
- **Never return a T1/T3 body as though governed** — return `TIER_REFUSED`.
- **Never read outside the allowed roots** — return `PATH_BOUNDARY`.
- **Never paraphrase a definition into the answer.** Return the file. A summarised
  definition is a second, unversioned definition, and the whole point of a governed
  context layer is that there is one.
