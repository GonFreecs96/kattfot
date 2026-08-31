"""verify.py — offline drift check for a query artifact, governed or exploratory.

Two artifact families, two manifests, two verifiers sharing one module because
they share one question: "is this artifact still the thing it claims to be?"
**without touching BigQuery**.

For a governed ``query-metric`` artifact (``verify_frontmatter``), recompute
the definition and query fingerprints from the CURRENT repo state:

  - ``metric_def_sha`` — recomputed from the metric file as it exists now.
  - ``query_sha``      — recomputed by rebuilding the SQL from the current
                         metric definition + the artifact's recorded
                         dimensions / filters / window.

For an exploratory (T3) ``kattfot:explore`` artifact (``verify_explore``),
there is no reviewed definition to fingerprint — instead:

  - ``query_sha``    — recomputed from the artifact's own ``## Provenance``
                       fences (the exact SQL + bound params that ran), via
                       ``_shared/explore_artifact.py``. A mismatch means the
                       transcript was edited after the query ran — the same
                       tamper check `kattfot:promote` makes before it will
                       scaffold a draft, run here so a reader can catch it
                       without waiting until promotion time.
  - ``register_sha`` — recomputed from the CURRENT exploration register.
                       Mismatch means the allow-list that authorized the read
                       has moved since — the explore-lane parallel of
                       DEFINITION_DRIFT.

``result_sha`` is intentionally NOT re-checked by either verifier: it would
require re-running the query against BigQuery, or — for the explore lane —
re-deriving rows by parsing the artifact's rendered markdown table, which
would be a parser pretending to be a receipt, not a stronger check. Offline
verify is about *definition/register and query drift* — the "which version
produced this number" question — which is precisely the gap the manifest
closes. A `result_sha` re-check is a deliberate, separate (BigQuery-backed)
capability and is out of scope for this offline path.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from _shared import exploration as exploration_lib
from _shared import manifest as mf


def _as_date(value):
    """A frontmatter date value as a `dt.date`, or None.

    PyYAML parses an unquoted `2026-08-10` into a `dt.date` already; a quoted one
    arrives as a string. Both have to work, since the artifact is hand-editable
    markdown and either form is legitimate YAML.
    """
    if value is None or isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return None

# Stable single-line status prefixes (same style as the consumer-api sentinels).
# Governed (query-metric) lane:
VERIFY_OK = "VERIFY_OK"
DEFINITION_DRIFT = "DEFINITION_DRIFT"
QUERY_DRIFT = "QUERY_DRIFT"
DEFINITION_MISSING = "DEFINITION_MISSING"
MANIFEST_ABSENT = "MANIFEST_ABSENT"  # shared with the explore lane — no manifest, nothing to verify
# Exploratory (explore, T3) lane — parallel roles, different names because the
# finding means something different (no reviewed definition, an allow-list
# instead): PROVENANCE_TAMPERED parallels QUERY_DRIFT (the query moved, but
# because the transcript was edited, not because the repo changed underneath
# it), REGISTER_DRIFT parallels DEFINITION_DRIFT (the authorizing artifact
# moved), REGISTER_MISSING parallels DEFINITION_MISSING, and PROVENANCE_ABSENT
# is the explore-only case of an artifact with a manifest but no fences to
# recompute it from (an artifact predating the Provenance section, or one
# whose fences were stripped rather than edited).
PROVENANCE_TAMPERED = "PROVENANCE_TAMPERED"
PROVENANCE_ABSENT = "PROVENANCE_ABSENT"
REGISTER_DRIFT = "REGISTER_DRIFT"
REGISTER_MISSING = "REGISTER_MISSING"

#: Canonical exit code for every status this module can return — the ONE
#: source of truth. `verify_artifact.py`'s CLI builds its own `_EXIT` map by
#: merging this in (`**verify_lib.EXIT_CODE`) rather than re-listing exit
#: codes for verify.py's sentinels by hand, and it merges in the audit-chain
#: sentinels separately, since this module knows nothing about those. Keeping
#: the table here, beside the sentinels themselves, means a new status added
#: to this module cannot go live without an exit code — a second table
#: somewhere else is exactly how a forgotten entry would quietly default to
#: "lowest severity" and bury a real finding.
#:
#: 0 clear, 1 a confirmed drift/tamper was DETECTED, 2 could not fully verify
#: (inconclusive). 3 (usage/IO) is never produced by this module — it belongs
#: to the CLI layer — but is a valid key of `EXIT_SEVERITY` below for the same
#: reason `verify_artifact.py`'s original `_EXIT.get(status, 3)` defaulted to
#: it: an unrecognized status is a bug, not a legitimate outcome, so it must
#: not silently rank as OK.
EXIT_CODE = {
    VERIFY_OK: 0,
    DEFINITION_DRIFT: 1,
    QUERY_DRIFT: 1,
    DEFINITION_MISSING: 2,
    MANIFEST_ABSENT: 2,
    PROVENANCE_TAMPERED: 1,
    REGISTER_DRIFT: 1,
    PROVENANCE_ABSENT: 2,
    REGISTER_MISSING: 2,
}

#: Severity RANK of an exit code, worst wins — NOT the exit code itself, and
#: NOT monotonic with it. A confirmed detection (exit 1) outranks an
#: inconclusive cannot-verify (exit 2), even though 1 < 2 numerically: an
#: explore artifact that is both tampered AND missing its register must
#: report PROVENANCE_TAMPERED, not REGISTER_MISSING — burying a real tamper
#: behind "couldn't check everything" is worse than the inconclusive report
#: alone. Combining by raw exit-code max would invert that.
#:
#: This is the SAME table `verify_artifact.py`'s CLI uses (imported, not
#: copied) to combine the artifact check against `--audit-log` — one ranking,
#: not two that could quietly drift apart.
EXIT_SEVERITY = {0: 0, 3: 1, 2: 2, 1: 3}  # ok < usage < inconclusive < detected


def _status_severity(status: str) -> int:
    """`EXIT_SEVERITY` of one status's `EXIT_CODE` — worst-wins rank for
    combining findings. See `EXIT_CODE` for why an unrecognized status
    defaults to the exit-3 (usage/IO) rank rather than to OK."""
    return EXIT_SEVERITY[EXIT_CODE.get(status, 3)]


def _load_query_helpers():
    """Import the query-metric helper, putting its scripts dir on sys.path.

    verify lives in a sibling skill, so it locates query-metric/scripts relative
    to this file rather than assuming the runtime already imported it. Imported
    lazily (and injectable via ``verify_frontmatter(fm, aq=...)``) so this module
    stays importable — and unit-testable — without the query skill present.
    """
    scripts = Path(__file__).resolve().parents[1] / "query-metric" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import kattfot_query as aq  # noqa: WPS433 (intentional local import)

    return aq


def verify_frontmatter(fm: dict, *, aq=None) -> dict:
    """Verify one artifact's frontmatter against the current repo state.

    Returns ``{"status": <SENTINEL>, "detail": str, "findings": [...]}``.
    ``findings`` is present only when there is drift (it may carry both a
    definition and a query finding).
    """
    aq = aq or _load_query_helpers()

    manifest = fm.get("manifest") or {}
    if not manifest:
        return {
            "status": MANIFEST_ABSENT,
            "detail": "artifact has no manifest block (it predates manifests); "
            "nothing to verify against.",
        }

    metric_name = fm.get("metric")
    try:
        metric = aq.load_metric(metric_name)
    except SystemExit:
        return {
            "status": DEFINITION_MISSING,
            "detail": f"metric '{metric_name}' is no longer registered — the "
            "definition this artifact was built from cannot be located.",
        }

    findings = []

    # 1) Definition drift — the whole metric file is fingerprinted, so any
    #    change (allow-lists, columns, caveats, tier) surfaces here.
    recomputed_def = metric["_def_sha"]
    claimed_def = manifest.get("metric_def_sha")
    if recomputed_def != claimed_def:
        findings.append(
            {
                "status": DEFINITION_DRIFT,
                "detail": f"metric definition changed since this artifact: "
                f"{claimed_def} -> {recomputed_def}",
            }
        )

    # 2) Query drift — rebuild the SQL from the current definition + the
    #    artifact's recorded grain/window. Catches a column/view change or a
    #    helper-logic change even when (rarely) the def hash is unaffected.
    dimensions = list(fm.get("dimensions") or [])
    filters = dict(fm.get("filters") or {})
    project = fm.get("source_project") or metric.get("source_project") or "<project>"

    # The maturity cutoff is an INPUT to the query, so a faithful rebuild has to use
    # the one the artifact recorded — not a freshly resolved one, which would move
    # every day and make verify report drift purely because time passed.
    #
    # It comes from the artifact rather than being recomputed for the same reason the
    # window does. An artifact from a metric with a `maturity_stage` (every metric in
    # the bundled catalog) carries `cohort_week <= @maturity_cutoff` in its SQL, so
    # omitting it here would report QUERY_DRIFT on every single governed artifact —
    # which is worse than not checking, because a verifier that always cries drift
    # gets ignored.
    #
    # Absent (an older artifact, or a metric with no maturity gate) means rebuild
    # without the predicate, which is exactly what produced that artifact.
    cutoff_raw = fm.get("maturity_cutoff") or None
    cutoff = _as_date(cutoff_raw)
    sql, params = aq.build_sql(metric, project, dimensions, filters, cutoff)
    params_out = {
        **params,
        "start_date": str(fm.get("time_range_start")),
        "end_date": str(fm.get("time_range_end")),
    }
    if cutoff is not None:
        params_out["maturity_cutoff"] = cutoff.isoformat()
    recomputed_query = mf.query_sha(sql, params_out)
    claimed_query = manifest.get("query_sha")
    if recomputed_query != claimed_query:
        findings.append(
            {
                "status": QUERY_DRIFT,
                "detail": f"rebuilt query no longer matches the artifact: "
                f"{claimed_query} -> {recomputed_query}",
            }
        )

    if not findings:
        return {
            "status": VERIFY_OK,
            "detail": "definition and query fingerprints match the current repo "
            "state. (Offline verify does not check result_sha — confirming the "
            "number itself requires re-running the query against BigQuery.)",
        }

    return {
        "status": findings[0]["status"],
        "findings": findings,
        "detail": "; ".join(f["detail"] for f in findings),
    }


def verify_explore(fm: dict, sql: str | None, params: dict, *, exploration=None) -> dict:
    """Verify one exploratory (T3) artifact against the current repo state.

    The explore-lane counterpart to ``verify_frontmatter``, for a receipt shape
    that has no reviewed definition to fingerprint: ``sql``/``params`` come from
    re-parsing the artifact's own ``## Provenance`` fences (see
    ``_shared/explore_artifact.py`` — the same parser `kattfot:promote` uses for
    its tamper check), and ``register_sha`` stands in for ``metric_def_sha`` —
    the allow-list version that authorized the read, rather than a reviewed
    metric definition.

    Returns ``{"status": <SENTINEL>, "detail": str, "findings": [...]}``, same
    shape as ``verify_frontmatter``. ``findings`` is present only when there is
    more than one applicable finding (see ``_EXPLORE_SEVERITY`` below for how
    the top-level status is chosen when they disagree in severity).
    """
    exploration = exploration or exploration_lib

    manifest = fm.get("manifest") or {}
    if not manifest:
        return {
            "status": MANIFEST_ABSENT,
            "detail": "artifact has no manifest block (it predates manifests); "
            "nothing to verify against.",
        }

    if sql is None:
        return {
            "status": PROVENANCE_ABSENT,
            "detail": "no ```sql fence found in the artifact's Provenance "
            "section — there is nothing to recompute query_sha from.",
        }

    findings = []

    # 1) Provenance tamper — recompute query_sha from exactly the fences this
    #    artifact renders. A mismatch means the transcript was edited after
    #    the query ran (a manually changed WHERE clause, a hand-tweaked
    #    parameter) — the same check `kattfot:promote` makes as PROMOTE-TAMPER.
    recomputed_query = mf.query_sha(sql, params)
    claimed_query = manifest.get("query_sha")
    if recomputed_query != claimed_query:
        findings.append(
            {
                "status": PROVENANCE_TAMPERED,
                "detail": "the Provenance section no longer matches the query "
                f"that ran: {claimed_query} -> {recomputed_query}",
            }
        )

    # 2) Register drift — the allow-list version that authorized this read may
    #    have moved, or the register file itself may no longer be there.
    #    `register_sha()` fails on a missing/unreadable file with an
    #    exception, not a governance `sys.exit` (that belongs to
    #    `load_register`, which this path does not call) — but both shapes of
    #    failure are caught here regardless, so a broken register reports
    #    REGISTER_MISSING instead of crashing verify.
    try:
        current_register_sha = exploration.register_sha()
    except SystemExit:
        current_register_sha = None
    except Exception:
        current_register_sha = None

    if current_register_sha is None:
        register_missing = {
            "status": REGISTER_MISSING,
            "detail": "exploration register not found at "
            f"{exploration.register_path()} — cannot confirm which allow-list "
            "authorized this artifact's surfaces.",
        }
        if not findings:
            return register_missing
        findings.append(register_missing)
    else:
        claimed_register = manifest.get("register_sha")
        if current_register_sha != claimed_register:
            findings.append(
                {
                    "status": REGISTER_DRIFT,
                    "detail": "the exploration register has changed since "
                    f"this artifact was produced: {claimed_register} -> "
                    f"{current_register_sha}",
                }
            )

    if not findings:
        return {
            "status": VERIFY_OK,
            "detail": "query and register fingerprints match the current "
            "repo state. (Offline verify does not check result_sha — "
            "confirming the number itself requires re-running the query.)",
        }

    # Worst finding wins the top-level status — a confirmed tamper or drift
    # must not be masked by a co-occurring cannot-verify finding just because
    # it was appended later. `_status_severity` (== EXIT_SEVERITY of
    # EXIT_CODE) is the same ranking verify_artifact.py's `_worse` uses to
    # combine the artifact and --audit-log checks — one table, not two.
    worst = max(findings, key=lambda f: _status_severity(f["status"]))
    return {
        "status": worst["status"],
        "findings": findings,
        "detail": "; ".join(f["detail"] for f in findings),
    }
