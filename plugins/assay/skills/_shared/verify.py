"""verify.py — offline drift check for a governed query artifact.

Given an artifact's parsed frontmatter (which carries the manifest written by
``query-metric``), recompute the definition and query fingerprints from the
CURRENT repo state and report whether they still match. This answers "is this
number still the one this artifact claims it is?" **without touching BigQuery**:

  - ``metric_def_sha`` — recomputed from the metric file as it exists now.
  - ``query_sha``      — recomputed by rebuilding the SQL from the current
                         metric definition + the artifact's recorded
                         dimensions / filters / window.

``result_sha`` is intentionally NOT re-checked here: it would require re-running
the query against BigQuery. Offline verify is about *definition and query
drift* — the "which version produced this number" question — which is precisely
the gap the manifest closes. A `result_sha` re-check is a deliberate, separate
(BigQuery-backed) capability and is out of scope for this offline path.
"""

from __future__ import annotations

import sys
from pathlib import Path

from _shared import manifest as mf

# Stable single-line status prefixes (same style as the consumer-api sentinels).
VERIFY_OK = "VERIFY_OK"
DEFINITION_DRIFT = "DEFINITION_DRIFT"
QUERY_DRIFT = "QUERY_DRIFT"
DEFINITION_MISSING = "DEFINITION_MISSING"
MANIFEST_ABSENT = "MANIFEST_ABSENT"


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
    import assay_query as aq  # noqa: WPS433 (intentional local import)

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
    sql, params = aq.build_sql(metric, project, dimensions, filters)
    params_out = {
        **params,
        "start_date": str(fm.get("time_range_start")),
        "end_date": str(fm.get("time_range_end")),
    }
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
