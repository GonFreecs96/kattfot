---
name: verify
description: >
  Triggers on: "verify this artifact", "is this number still valid", "check
  this analytics artifact", "did the metric definition change", "verify the
  manifest", "is this report stale", "drift check", "was this exploration
  tampered with", "did the exploration register change". Offline drift check
  for a governed `query-metric` artifact OR an exploratory `kattfot:explore`
  artifact — auto-detected from the frontmatter. Recomputes the artifact's
  fingerprints from the current repo and reports whether they still match its
  manifest. Never touches the warehouse.
version: 0.1.0
---

# Verify

You are running the `verify` skill. Given a `query-metric` OR `kattfot:explore` artifact, your
job is to answer **"is this artifact still the thing it claims to be?"** — *offline*, without
re-running anything against the warehouse. Which lane an artifact belongs to is read from its
frontmatter, not asked for: `skill: explore` routes to the exploratory checks below, and
everything else routes to the governed ones. You never need to say which kind it is.

**Governed (`query-metric`) artifacts** carry a **manifest**: content fingerprints of the metric
definition (`metric_def_sha`), the query (`query_sha`), and the result (`result_sha`). This skill
recomputes the **definition** and **query** fingerprints from the *current* repo state and
compares them to what the artifact recorded. If a metric definition has been edited since the
artifact was produced, you will catch it here.

**Exploratory (`kattfot:explore`, T3) artifacts** carry a different manifest — there is no
reviewed definition to fingerprint, so `register_sha` (the exploration register version that
authorized the read) stands in for `metric_def_sha`. This skill recomputes `query_sha` from the
artifact's own `## Provenance` fences (the exact SQL + bound parameters that ran — the same
tamper check `kattfot:promote` makes before it will scaffold a draft) and `register_sha` from the
CURRENT exploration register, and compares both to what the artifact recorded.

**Scope (read this):** this is a *definition/register + query* drift check, for either lane. It
deliberately does **not** re-check `result_sha` — confirming the number itself would require
re-running the query against the warehouse, or (for an explore artifact) re-deriving rows from
its rendered markdown table, which would be a parser pretending to be a receipt, not a stronger
check. Say so plainly; do not imply the number was re-validated against live data.

## Process

1. **Resolve the artifact path.** The user names a file (e.g. `outputs/kattfot/2026-06-12-…md`).
   If they don't, ask which artifact to verify.
2. **Run the helper:**
   ```bash
   python plugins/kattfot/skills/verify/scripts/verify_artifact.py <artifact-path>
   ```
   It parses the artifact's frontmatter, routes on `skill: explore` (no flag needed), and prints
   a single status line plus per-finding detail lines when there is more than one. For a governed
   artifact it reloads the metric definition by name and rebuilds the SQL from the recorded
   dimensions/filters/window; for an explore artifact it re-parses the `## Provenance` fences and
   the current exploration register instead.
3. **Report the status** in plain language (see below). Do not paste the whole artifact back.

## Statuses

Governed (`query-metric`) artifacts:

| Status | Meaning | What to tell the user |
|--------|---------|-----------------------|
| `VERIFY_OK` | Definition + query fingerprints match the current repo | The artifact is consistent with the current metric definition. Note that the *number* itself wasn't re-run against the warehouse. |
| `DEFINITION_DRIFT` | The metric file changed since the artifact was made | The definition has been edited — show old → new hash. The artifact's number may no longer reflect how the metric is defined today; recommend a fresh `query-metric` run. |
| `QUERY_DRIFT` | The rebuilt SQL no longer matches | The query construction has changed (a column/view change, or a helper change). Recommend re-running. |
| `DEFINITION_MISSING` | The metric is no longer registered | The definition this artifact was built from can't be found. The metric may have been renamed or removed. |
| `MANIFEST_ABSENT` | The artifact predates manifests | There's nothing to verify against; recommend regenerating the artifact to get a manifest. |

Exploratory (`kattfot:explore`, T3) artifacts:

| Status | Meaning | What to tell the user |
|--------|---------|-----------------------|
| `VERIFY_OK` | Query + register fingerprints match the current repo | The artifact's Provenance still hashes to its manifest, and the register hasn't moved. Still T3 — not decision-grade, and the *number* itself wasn't re-run. |
| `PROVENANCE_TAMPERED` | The Provenance fences no longer hash to `query_sha` | The SQL or bound parameters shown in the artifact were edited after the query ran — show old → new hash. Treat the artifact as unreliable; do not promote or cite it as-is. |
| `REGISTER_DRIFT` | The exploration register changed since the artifact was produced | The allow-list version that authorized this read has moved — show old → new hash. The artifact may cite a surface that is no longer registered the same way; confirm before relying on it. |
| `REGISTER_MISSING` | The exploration register file can't be found or read | Cannot confirm which allow-list authorized this artifact's surfaces. Restore the register or `KATTFOT_EXPLORATION_FILE`. |
| `PROVENANCE_ABSENT` | The artifact has a manifest but no SQL fence to recompute `query_sha` from | The Provenance section is missing or was stripped — there's nothing to check the tamper claim against. Recommend re-running `kattfot:explore`. |
| `MANIFEST_ABSENT` | The artifact predates manifests | Same as the governed case — nothing to verify against. |

A tamper/drift finding (exit 1) always outranks a cannot-verify finding (exit 2) reported
alongside it — that combining rule is the same one `verify_artifact.py` uses to combine the
artifact check with `--audit-log` (see its module docstring for the exit-code table). Report
every finding line the helper prints, not just the headline status.

## Refusals / boundaries

- **No warehouse access.** If the user asks you to confirm the *value* is still correct, explain
  that requires a re-run (`/query-metric`, or a fresh `kattfot:explore`) — this skill only checks
  drift offline, for either lane.
- **Read-only.** Never edit the artifact, the metric definition, or the exploration register.
  Verify reports; it does not fix.

## Out of scope

- Re-running queries or checking `result_sha` against live data.
- Verifying anything other than `query-metric` and `kattfot:explore` artifacts (funnel/spine
  outputs have no manifest yet).
- Modifying metrics, views, artifacts, or the exploration register.

## Status in this template

The drift engine (`_shared/verify.py`) and this skill's CLI are complete and unit-tested, for
both lanes. The CLI resolves a governed artifact's metric definition through the `query-metric`
helper, and an explore artifact's Provenance fences through `_shared/explore_artifact.py` (the
same parser `kattfot:promote` uses for its tamper check) — both are part of the skill set this
template builds toward, so `verify_artifact.py` needs those sibling skills present to run
end-to-end. The engine itself does not: `verify_frontmatter(fm, aq=…)` takes the metric loader as
an injected dependency, and `verify_explore(fm, sql, params, exploration=…)` takes the register
loader the same way — which is how the test suite exercises every status above without a
warehouse or a metric catalog.
