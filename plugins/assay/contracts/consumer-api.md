# Consumer API Contract

> **Status:** Canonical. All plugins consuming `assay` through `assay:load-context`
> or `assay:query-metric` SHOULD rely on the formats specified here. Breaking changes
> require a plugin minor version bump.

This contract defines the response format, error handling, and scope vocabulary for
`assay`'s two cross-plugin read APIs. It exists so consuming plugins (a briefing
surface, a research agent, a chat bot, any future consumer) can depend on stable,
parseable output without reading SKILL.md internals.

The point of a written contract here is narrow but load-bearing: a consumer must be
able to tell a **governed** number from a refusal without parsing prose. Every failure
mode below is a single line with a stable prefix, so the consumer's failure handling is
a prefix match rather than a heuristic.

## The two APIs

| Skill | Use | Touches the warehouse? |
|-------|-----|-------------------|
| `assay:load-context` | Governed **definitions / context** — metric specs, tiers, caveats, lineage, glossary | No — pure file reads |
| `assay:query-metric` | A governed **number** — runs a registered metric and returns values + provenance | Yes — as the caller's own ADC identity |

Rule of thumb for consumers: call `load-context` to *ground reasoning in canonical
definitions*; call `query-metric` to *put an actual figure in front of a user*.

## Error Sentinels

A read API that cannot fulfill a request returns a single-line sentinel. Match on the prefix
before `--`.

| Sentinel | Meaning | Returned by |
|----------|---------|-------------|
| `SCOPE_NOT_FOUND -- {detail}` | No scope/metric matched, including fuzzy matching | `load-context` |
| `EMPTY_RESULT -- {detail}` | Scope matched but zero items (e.g. `glossary` before it is populated) | `load-context` |
| `PATH_BOUNDARY -- {detail}` | Resolved path is outside allowed directories | both |
| `TIER_REFUSED -- {detail}` | Matched metric is T1 or T3 — not served by the governed path | both |
| `OFF_ALLOWLIST -- {detail}` | Requested dimension/filter not in the metric's allow-list | `query-metric` |
| `AUTH_REQUIRED -- {detail}` | Caller's ADC missing or a service account was supplied (refused) | `query-metric` |

Format: `{SENTINEL} -- {human-readable detail}`. `TIER_REFUSED` is the load-bearing one — it
guarantees a consumer can never silently embed a non-governed (financial/regulatory or
exploratory) metric as if it were decision-grade.

A sentinel is printed to **stdout**, because it is the response rather than a log line: a
consumer prefix-matches it the same way it would parse content. The **exit code** is the
machine-readable duplicate, so a shell caller does not have to parse anything:

| Exit | Meaning |
|------|---------|
| `0` | content returned |
| `1` | `SCOPE_NOT_FOUND` / `EMPTY_RESULT` — nothing to return, nothing wrong |
| `5` | `TIER_REFUSED` / `PATH_BOUNDARY` — a governance refusal (the same code the spine skills use) |
| `2` / `3` / `4` | missing ADC, missing dependency, service account refused (`query-metric` only) |

Splitting 1 from 5 is deliberate: "no such metric" and "that metric is not governed" call
for completely different handling by a consumer, and a single non-zero code would force it
back to parsing prose.

**Withholding applies to descriptions too.** In `catalog`, a non-T2 metric is listed with
its name and tier, and its one-line summary — which is the first line of the metric's own
`## Definition`, i.e. body content — is replaced by `TIER_REFUSED — tier {T}, body
withheld`. In `module/{name}`, the set is not refused wholesale (the caller asked for a
set); each non-T2 member is named with its tier and its body withheld.

## Success Response Formats

### load-context
- **Single file:** full file content including YAML frontmatter. No wrapper.
- **Multiple files:** concatenated with `--- {filename} ---` separators (consumers may split on
  `^--- .+ ---$`).
- **Paginated:** footer `_Showing items {start}-{end} of {total}. Use --offset {next} for next page._`

### query-metric
Returns the standard artifact block: YAML frontmatter (`metric`, `accuracy_tier`, `source_view`,
`generated_at`, `time_range`, `dimensions`, `filters`, `manifest`) + `## Results` table +
`## Caveats` (verbatim) + `## Provenance` (parameterized SQL + bound params + manifest). The
accuracy-tier header line is always present so a consumer can surface the tier.

**Manifest (the receipt).** Every `query-metric` artifact carries a `manifest` block in its
frontmatter — content fingerprints that make the number replayable and tamper-evident:

| Field | Meaning |
|-------|---------|
| `plugin_version` | Plugin version that produced the artifact |
| `metric_def_sha` | Fingerprint of the metric-definition file *as used* (16 hex) |
| `query_sha` | Fingerprint of the exact SQL + bound parameters |
| `result_sha` | Fingerprint of the result volumes — changes if the number is altered |

`generated_at` is **excluded** from every hash, so a faithful re-run over unchanged data and an
unchanged definition reproduces all three fingerprints. Consumers can compare a `metric_def_sha`
across two artifacts to detect that a metric definition changed between runs; `assay:verify`
performs this drift check offline.

## Freshness & maturity signals

- **Definition staleness** (`load-context`): if a metric/doctrine file is older than the
  freshness threshold, append after a blank line:
  `STALE: {filename} last updated {YYYY-MM-DD}, owner: {owner}` (the `, owner: ...` suffix is
  omitted when the file has no `owner:` frontmatter — e.g. `tiers`).
- **Data maturity** (`query-metric` + spine skills): maturity is applied **per stage** — each
  metric / funnel stage / timing transition is withheld only until its own stage settles
  (open→stage lag in the module's funnel spec under `stage_maturity`: payment submitted ~1d →
  return window complete ~44d). When the requested range includes immature cohort weeks for
  that stage, `query-metric` appends a `MATURITY:` line; spine-skill outputs carry a per-row
  `mature_through` week plus a `maturity` block and a top-level `maturity_cutoff`. Consumers
  should not present immature figures as final.
- **Fixture backing** (every skill that produces a number): `fixture_backed: true` means the
  number came from committed synthetic fixtures rather than a warehouse. It is not advisory —
  the renderers turn it into a MOCK DATA watermark, and a consumer must carry it too.

The `STALE:` and `MATURITY:` prefixes are stable; consumers may detect them by line prefix.

## Scope Quick Reference — load-context

| Scope | Returns | Type |
|-------|---------|------|
| `catalog` | All registered metrics: name, display, tier, module, one-liner (entities + latency KPIs appended) | Single (compact) |
| `metric/{slug}` | One metric's full definition | Single |
| `module/{name}` | All metric defs for a module (e.g. `module/checkout`) | Multi |
| `entities` | The entity registry: entity, display_name, module, lifecycle_module, status (live/declared), grain, one-liner | Single (compact) |
| `entity/{name}` | One entity's full definition: grain, keys, governed dimensions, relationships, anchored business rules (e.g. `entity/checkout_session`) | Single |
| `lineage/{module}` | The cohort-vs-activity-time `LINEAGE.md` note | Single |
| `tiers` | Accuracy-tier doctrine + refusal rules (R1–R6) | Single |
| `governance` | Ownership & governance doctrine: owners per entity/module, review cadence, freshness SLA | Single |
| `spines` | The module registry as data: every spine, its `status` (`live` / `declared`), the spine-backed skills available on it, and any `handoff` edge with its coverage basis and denominator | Single (JSON) |
| `coverage` | Instrumentation coverage register grouped by status (what's measurable / blocked / empty / declined + why) | Single (compact) |
| `coverage/{id}` | One coverage entry (e.g. `coverage/cart_abandon_reason`) | Single |
| `glossary` / `glossary/{term}` | Reserved-term definitions | Single/Multi (may be empty in v0) |

`spines` was added after the first version of this contract. Adding a scope is
non-breaking, and this one closes a real gap: a `handoff` edge's `coverage` is a governed
number with a subtle denominator (for `browse/session -> checkout/session` it is a
*conversion rate*, not a join-quality figure), and without this scope the only way to reach
it was to read the registry YAML — exactly the path-coupling the scope vocabulary exists to
avoid. The edge also carries `coverage_basis` (`declared_estimate` or
`measured_source_share`) and `coverage_measured_at`, so a consumer can tell an estimate
from a measurement without asking.

## Versioning

This contract version tracks the plugin version in `.claude-plugin/plugin.json`. Changes to
sentinels, separators, or response structure are breaking and require a minor version bump.
New scopes or optional fields are non-breaking.

## Status in this template

Both skills this contract describes now ship, and the contract is enforced in code rather than
in a prompt: `skills/load-context/scripts/load_context.py` raises the sentinels, checks the
read-root boundary on the *resolved* path, and applies the tier gate at the point content would
otherwise be returned. That placement is the point — the first caller to invoke the helper
directly would bypass any prompt-level rule, and a consumer API's guarantees have to hold for
the callers who never read the prompt.

The contract was committed before either implementation, deliberately: the response shape is
what consumers depend on, and pinning it first is what stopped each skill inventing its own
error vocabulary. `tests/test_read_apis.py` asserts every sentinel, both exit codes, and that
a non-T2 body never leaves the process.
