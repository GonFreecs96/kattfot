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
  (calibrated open→stage p95 lag in the module's funnel spec under `stage_maturity`: payment
  submitted ~0d → return window complete ~30d). When the requested range includes immature
  cohort weeks for that stage, `query-metric` appends:
  `MATURITY: cohorts after {date} are immature and may shift`; spine-skill outputs carry a
  per-row `mature_through` week + a `maturity` block. Consumers should not present immature
  figures as final.

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
| `coverage` | Instrumentation coverage register grouped by status (what's measurable / blocked / empty / declined + why) | Single (compact) |
| `coverage/{id}` | One coverage entry (e.g. `coverage/cart_abandon_reason`) | Single |
| `glossary` / `glossary/{term}` | Reserved-term definitions | Single/Multi (may be empty in v0) |

## Versioning

This contract version tracks the plugin version in `.claude-plugin/plugin.json`. Changes to
sentinels, separators, or response structure are breaking and require a minor version bump.
New scopes or optional fields are non-breaking.

## Status in this template

`load-context` and `query-metric` are the two skills this contract describes; both are part of
the full skill set the template builds toward. The contract is committed first, deliberately —
the response shape is the part consumers depend on, and pinning it before the implementation is
what stops each skill from inventing its own error vocabulary.
