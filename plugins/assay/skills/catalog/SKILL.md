---
name: catalog
description: >
  Triggers on: "what metrics can I ask", "assay catalog", "list metrics", "what's
  in the metric catalog", "what can assay answer". Lists every registered metric —
  name, accuracy tier, module, and what it measures — plus the named percentile
  latency KPIs and the registered spines, so a user knows which governed questions
  exist before asking one. The friendly front door to the catalog. Never queries
  anything.
version: 0.1.0
---

# Catalog

You are running the `catalog` skill. Show the user the full set of **registered**
things they can ask for. This is the human-facing companion to
`assay:load-context catalog`, which returns the same information in the
machine-parseable consumer-api format.

The catalog spans **two metric classes**, and the split is structural rather than
organisational:

1. **Ratio metrics** — `SUM(numerator) / SUM(denominator)` at a governed grain,
   served by `assay:query-metric`. One markdown file each under
   `skills/query-metric/metrics/`.
2. **Latency KPIs** — percentiles, served by `assay:stage-timing`. These live in a
   funnel spec's `kpis:` block, *not* in the metrics directory, because a median is
   not a ratio of two sums and cannot be expressed as a metric definition at all.

Surface both, or a KPI like `time_to_ship_days` is undiscoverable.

## Run the helper — do not enumerate by hand

```bash
python3 plugins/assay/skills/catalog/scripts/catalog.py --format markdown
```

The helper reads the metric files and the spec `kpis:` blocks **off disk on every
invocation**, walking exactly the directories the resolver walks (consumer dir from
`ASSAY_METRICS_DIR` first, then the bundled catalog, consumer winning on a `name:`
collision).

Do not write the list out from memory or from a previous run. A hand-maintained
catalog is wrong the first time somebody adds a metric, and it is wrong *silently* —
the metric exists, works, and is invisible. The one thing this skill must never do
is disagree with the runtime about what is registered.

`--format json` gives the same content as data if you need to post-process it.

## What the output contains

- `metrics[]` — name, display name, the first line of the metric's own
  `## Definition`, tier, module, entity, status, owner.
- `latency_kpis[]` — key, display name, transition, percentiles, tier, and
  `queryable` plus `not_queryable_because`.
- `spines[]` — each registered module/spine, its grain, its `status`, the
  spine-backed skills available on it, and any `handoff` edge.

## Honesty about what is queryable

A KPI can be registered on a spine the module registry marks `declared` — contract
authored and linted, no view provisioned. The helper lists it with
`queryable: false` and the reason, and you must carry that through. Presenting it as
available is the catalog's version of the false promise the registry exists to
prevent; hiding it loses the fact that the contract is already fixed and the skill
would run the moment data lands.

The same applies to spines: report `status` in the spine table, and do not describe
a `declared` spine's skills as available.

## Present it

Lead with a one-line count ("15 governed ratio metrics, 3 latency KPIs, 2
spines"), then the tables the helper renders, grouped by module. Close with how to
use them:

- **Run a ratio metric:** `assay:query-metric <plain-English question>` — e.g.
  "cart to checkout conversion by channel since May".
- **Read a latency KPI:** `assay:stage-timing <transition> timing …` — e.g. "time
  to ship by market and category".
- **See where a funnel leaks:** `assay:funnel-dropoff`.
- **Check whether something is measurable at all:** `assay:coverage` — the catalog
  says what you *can* ask, coverage says what you cannot yet and who owns the
  blocker.
- **Pull a definition into another tool:** `assay:load-context metric/{slug}`.
- **See the catalog as data:** `assay:load-context catalog`.

## Boundaries

- **Never execute a query.** This skill lists what is available; it produces no
  numbers, so there is nothing here to tier, caveat, or watermark.
- **Never invent a metric.** If a user asks for something that is not listed, that
  is a `coverage` question or an authoring request, not a gap to paper over. Point
  at `skills/query-metric/AUTHORING.md` and
  `templates/example-metric.md`.
- **Do not restate a metric's caveats as though they were the metric.** A catalog
  entry is a pointer; the tier, the caveats, and the maturity cutoff travel with the
  *result*, not with the listing.
- If the helper finds no metrics at all, say so plainly and point at the authoring
  guide rather than describing an empty catalog as a working one.
