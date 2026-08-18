"""assay:verify — offline drift detection.

Builds a real artifact manifest from a temp metric, then confirms verify reports
OK when nothing changed and the right drift status when the definition moves.
No warehouse access: verify only recomputes the definition + query fingerprints.

**Why a stub helper.** The natural way to write this is to import the real
`query-metric` helper, but that skill is not in this template yet — so the metric
loader / SQL builder is supplied through the seam `verify_frontmatter` already
exposes for exactly this purpose: `verify_frontmatter(fm, aq=...)`. The stub
below is deliberately faithful on the two properties the drift logic depends on:

  - `_def_sha` fingerprints the WHOLE metric file, so editing any line — even a
    caveat that no SQL reads — moves it. That is what makes DEFINITION_DRIFT
    catch a definition edit the query cannot see.
  - `build_sql` reads the columns and view out of the definition, so a column
    swap moves the query fingerprint too.

The code under test is the real `_shared/verify.py`. Only the collaborator is
stubbed, and it is stubbed to the same contract the real helper implements — so
when `query-metric` lands, these tests keep describing its behavior.
"""

import textwrap

import pytest
import yaml

from _shared import manifest as mf
from _shared import verify as vf


METRIC_NAME = "verify_ex_rate"

METRIC_TEXT = textwrap.dedent(
    """\
    ---
    name: verify_ex_rate
    display_name: Verify example rate
    accuracy_tier: T2
    source_view: analytics.verify_example_view
    source_project: demo-proj
    time_column: cohort_week
    numerator_column: n
    denominator_column: d
    value_format: percent
    allowed_dimensions: []
    allowed_filters: []
    default_lookback_weeks: 8
    status: live
    ---

    # Verify example
    ## Caveats
    - original caveat
    """
)

ROWS = [{"cohort_week": "2026-03-02", "n": 84, "d": 100, "verify_ex_rate": 0.84}]


class _StubQueryHelper:
    """Stands in for the `query-metric` helper: loads a metric definition from a
    directory and builds parameterized SQL from it."""

    def __init__(self, metrics_dir):
        self.metrics_dir = metrics_dir

    def load_metric(self, name: str) -> dict:
        for path in sorted(self.metrics_dir.glob("*.md")):
            text = path.read_text()
            fm = yaml.safe_load(text.split("---")[1]) or {}
            if fm.get("name") == name:
                # The whole file is fingerprinted, so any edit moves the hash.
                fm["_def_sha"] = mf.sha16(text)
                return fm
        # The real helper exits rather than returning; verify catches SystemExit.
        raise SystemExit(f"metric '{name}' is not registered")

    def build_sql(self, metric: dict, project: str, dimensions, filters):
        group = ", ".join([metric["time_column"], *dimensions])
        sql = (
            f"SELECT {group}, "
            f"SUM({metric['numerator_column']}) AS numerator, "
            f"SUM({metric['denominator_column']}) AS denominator "
            f"FROM `{project}.{metric['source_view']}` "
            f"WHERE {metric['time_column']} BETWEEN @start_date AND @end_date "
            f"GROUP BY {group} ORDER BY {metric['time_column']}"
        )
        params = {f"filter_{i}": v for i, v in enumerate(filters.values())}
        return sql, params


@pytest.fixture()
def metric_dir(tmp_path):
    """A metrics dir holding our temp metric."""
    d = tmp_path / "metrics"
    d.mkdir()
    (d / "ex.md").write_text(METRIC_TEXT)
    return d


@pytest.fixture()
def aq(metric_dir):
    return _StubQueryHelper(metric_dir)


def _artifact_fm(aq) -> dict:
    """Build the frontmatter (incl. a real manifest) the way query-metric would."""
    metric = aq.load_metric(METRIC_NAME)
    sql, params = aq.build_sql(metric, metric["source_project"], [], {})
    params_out = {**params, "start_date": "2026-03-01", "end_date": "2026-06-12"}
    manifest = mf.build_manifest(
        metric_def_sha=metric["_def_sha"],
        sql=sql,
        params=params_out,
        rows=ROWS,
        metric_name=METRIC_NAME,
        plugin_ver="0.1.0",
    )
    return {
        "metric": METRIC_NAME,
        "source_project": metric["source_project"],
        "dimensions": [],
        "filters": {},
        "time_range_start": "2026-03-01",
        "time_range_end": "2026-06-12",
        "manifest": manifest,
    }


def test_verify_ok_when_nothing_changed(aq):
    fm = _artifact_fm(aq)
    result = vf.verify_frontmatter(fm, aq=aq)
    assert result["status"] == vf.VERIFY_OK
    # The offline scope must be stated, not implied.
    assert "result_sha" in result["detail"]


def test_definition_drift_when_caveat_edited(aq, metric_dir):
    """Editing a non-column line (a caveat) changes the def hash but not the
    query — exactly DEFINITION_DRIFT, no QUERY_DRIFT."""
    fm = _artifact_fm(aq)
    (metric_dir / "ex.md").write_text(METRIC_TEXT.replace("original caveat", "edited caveat"))

    result = vf.verify_frontmatter(fm, aq=aq)
    assert result["status"] == vf.DEFINITION_DRIFT
    statuses = {f["status"] for f in result["findings"]}
    assert statuses == {vf.DEFINITION_DRIFT}  # query did not move


def test_query_drift_when_column_changes(aq, metric_dir):
    """Swapping the numerator column moves both the def hash and the rebuilt query."""
    fm = _artifact_fm(aq)
    (metric_dir / "ex.md").write_text(METRIC_TEXT.replace("numerator_column: n", "numerator_column: x"))

    result = vf.verify_frontmatter(fm, aq=aq)
    statuses = {f["status"] for f in result["findings"]}
    assert vf.QUERY_DRIFT in statuses
    assert vf.DEFINITION_DRIFT in statuses


def test_definition_missing_when_metric_gone(aq):
    fm = _artifact_fm(aq)
    fm["metric"] = "no_such_metric_anywhere"
    result = vf.verify_frontmatter(fm, aq=aq)
    assert result["status"] == vf.DEFINITION_MISSING


def test_manifest_absent(aq):
    result = vf.verify_frontmatter({"metric": METRIC_NAME}, aq=aq)  # no manifest key
    assert result["status"] == vf.MANIFEST_ABSENT


def test_window_is_part_of_the_query_fingerprint(aq):
    """The recorded window is bound into the rebuilt query, so an artifact that
    claims a different range than it was built for is drift, not a match."""
    fm = _artifact_fm(aq)
    fm["time_range_end"] = "2026-07-31"
    result = vf.verify_frontmatter(fm, aq=aq)
    assert result["status"] == vf.QUERY_DRIFT
