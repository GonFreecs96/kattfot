---
name: verify
description: >
  Triggers on: "verify this artifact", "is this number still valid", "check
  this analytics artifact", "did the metric definition change", "verify the
  manifest", "is this report stale", "drift check". Offline drift check for a
  governed query-metric artifact — recomputes the metric-definition and query
  fingerprints from the current repo and reports whether they still match the
  artifact's manifest. Never touches the warehouse.
version: 0.1.0
---

# Verify

You are running the `verify` skill. Given a governed `query-metric` artifact, your job is to
answer **"is this number still the one this artifact claims it is?"** — *offline*, without
re-running anything against the warehouse.

Every `query-metric` artifact carries a **manifest** (added by `query-metric`): content
fingerprints of the metric definition (`metric_def_sha`), the query (`query_sha`), and the result
(`result_sha`). This skill recomputes the **definition** and **query** fingerprints from the
*current* repo state and compares them to what the artifact recorded. If a metric definition has
been edited since the artifact was produced, you will catch it here.

**Scope (read this):** this is a *definition + query* drift check. It deliberately does **not**
re-check `result_sha` — confirming the number itself would require re-running the query against
the warehouse, which is out of scope for the offline path. Say so plainly; do not imply the
number was re-validated against live data.

## Process

1. **Resolve the artifact path.** The user names a file (e.g. `outputs/assay/2026-06-12-…md`).
   If they don't, ask which artifact to verify.
2. **Run the helper:**
   ```bash
   python plugins/assay/skills/verify/scripts/verify_artifact.py <artifact-path>
   ```
   It parses the artifact's frontmatter manifest, reloads the metric definition by name,
   rebuilds the SQL from the recorded dimensions/filters/window, and prints a single status line
   (plus per-finding detail lines on drift).
3. **Report the status** in plain language (see below). Do not paste the whole artifact back.

## Statuses

| Status | Meaning | What to tell the user |
|--------|---------|-----------------------|
| `VERIFY_OK` | Definition + query fingerprints match the current repo | The artifact is consistent with the current metric definition. Note that the *number* itself wasn't re-run against the warehouse. |
| `DEFINITION_DRIFT` | The metric file changed since the artifact was made | The definition has been edited — show old → new hash. The artifact's number may no longer reflect how the metric is defined today; recommend a fresh `query-metric` run. |
| `QUERY_DRIFT` | The rebuilt SQL no longer matches | The query construction has changed (a column/view change, or a helper change). Recommend re-running. |
| `DEFINITION_MISSING` | The metric is no longer registered | The definition this artifact was built from can't be found. The metric may have been renamed or removed. |
| `MANIFEST_ABSENT` | The artifact predates manifests | There's nothing to verify against; recommend regenerating the artifact to get a manifest. |

## Refusals / boundaries

- **No warehouse access.** If the user asks you to confirm the *value* is still correct, explain
  that requires a re-run (`/query-metric`) — this skill only checks definition/query drift offline.
- **Read-only.** Never edit the artifact or the metric definition. Verify reports; it does not fix.

## Out of scope

- Re-running queries or checking `result_sha` against live data.
- Verifying anything other than `query-metric` artifacts (funnel/spine outputs have no manifest yet).
- Modifying metrics, views, or artifacts.

## Status in this template

The drift engine (`_shared/verify.py`) and this skill's CLI are complete and unit-tested.
The CLI resolves the metric definition through the `query-metric` helper, which is part of
the skill set this template builds toward — so `verify_artifact.py` needs that sibling skill
present to run end-to-end. The engine itself does not: `verify_frontmatter(fm, aq=…)` takes
the metric loader as an injected dependency, which is how the test suite exercises every
status above without a warehouse or a metric catalog.
