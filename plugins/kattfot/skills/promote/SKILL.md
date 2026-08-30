---
name: promote
description: >
  Triggers on: "promote this", "make this a real metric", "we'll need this
  number again", "turn this exploration into a metric". Scaffolds a DRAFT
  governed metric definition from a kattfot:explore artifact — tamper-checked
  against the artifact's manifest, pre-filled where derivable, inert
  (T3 + status: draft) until a human review edits the tier to T2. Never
  touches the warehouse.
version: 0.1.0
---

# Promote

You are running the `promote` skill — the closing of the exploratory loop.
The tier doctrine says a question asked twice deserves a reviewed T2 metric;
this skill does the mechanical half of that promotion and leaves the
judgment half loudly unfinished.

**Scope (read this):** promotion **scaffolds; it never serves.** The draft it
writes carries `accuracy_tier: T3` and `status: draft`, so
`kattfot:query-metric` refuses it (R4) until a human completes the draft's
Promotion checklist and edits the tier to T2. That refusal is the review
gate working, not a bug to route around. The draft lands in the **consumer
metrics directory** (`KATTFOT_METRICS_DIR`), never the plugin-bundled
catalog.

## Process

1. Locate the exploratory artifact to promote (a `kattfot:explore` output —
   frontmatter `skill: explore`, `accuracy_tier: T3`, with its Provenance
   fences intact).
2. Pick a snake_case metric name with the user.
3. Run the helper:

   ```bash
   python3 $PROMOTE_ROOT/scripts/promote_metric.py \
     --artifact outputs/kattfot/<date>-explore-<slug>.md \
     --name <metric_name> [--out-dir <dir>] [--force]
   ```

   (`PROMOTE_ROOT` resolves like every other skill root: plugin root, then
   `.cursor/skills/promote`, then this SKILL.md's directory.)

4. Report from the helper's JSON: where the draft landed, what was
   pre-filled, and — most importantly — the `needs_decision` list and any
   `shape_note`. Offer to walk the user through the checklist, pointing at
   `query-metric/AUTHORING.md`.

## What the helper checks and derives

| Check / derivation | Behavior |
|---|---|
| Input is an explore artifact | Refused otherwise (`PROMOTE-INPUT`, exit 5) — a governed artifact is already governed |
| Tamper check | The Provenance SQL + params must hash to the manifest's `query_sha`; mismatch refuses (`PROMOTE-TAMPER`, exit 5) — no draft descends from an edited transcript |
| Register drift | Warns (does not refuse) when the exploration register changed since the artifact was made |
| `SAFE_DIVIDE(SUM(x), SUM(y))`, one surface | Pre-fills source_view, numerator/denominator, time column, and allow-lists (PII-scanned again) |
| Any other shape | Scaffolds with `NEEDS-DECISION` placeholders and a shape note: the metric runtime serves SUM/SUM ratios — a percentile or a join promotes to a spine skill or a new curated view instead |

## Refusals / boundaries

- Never edit the draft's tier or status yourself; the human review does that.
- Never write into the plugin-bundled catalog (`query-metric/metrics/`).
- A tampered artifact is refused, not repaired — re-run `kattfot:explore`.

## Out of scope

- Serving the draft (that is `kattfot:query-metric`, after review).
- Choosing the owner, entity, or maturity stage — those are the judgment
  calls the checklist exists for.

## Status in this template

Functional and offline: promotion is pure file work (parse, hash, write),
exercised end-to-end by `scripts/demo_skills.py` and pinned by
`tests/test_promote.py`.
