"""kattfot:verify — offline drift detection.

Builds a real artifact manifest from a temp metric, then confirms verify reports
OK when nothing changed and the right drift status when the definition moves.
No warehouse access: verify only recomputes the definition + query fingerprints.

**Why a stub helper, now that the real one exists.** The `query-metric` helper does
ship in this template, and `verify_frontmatter(fm, aq=...)` accepts it directly — see
`test_drift_detection_works_with_the_real_helper` at the end of this file, which runs
exactly that. The stub is kept for the rest of the cases because it *isolates* the
code under test: these assertions are about `_shared/verify.py`'s drift logic, and a
failure should point there rather than at a metric definition somebody edited or a
catalog convention that changed. The stub is deliberately faithful on the two
properties the drift logic depends on:

  - `_def_sha` fingerprints the WHOLE metric file, so editing any line — even a
    caveat that no SQL reads — moves it. That is what makes DEFINITION_DRIFT
    catch a definition edit the query cannot see.
  - `build_sql` reads the columns and view out of the definition, so a column
    swap moves the query fingerprint too.

The code under test is the real `_shared/verify.py`. Only the collaborator is
stubbed, and it is stubbed to the same contract the real helper implements — so
when `query-metric` lands, these tests keep describing its behavior.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

import demo_skills
import kattfot_explore
import verify_artifact
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

    def build_sql(self, metric: dict, project: str, dimensions, filters,
                  maturity_cutoff=None):
        """Mirrors the real helper's signature, including the optional maturity
        cutoff — a stub whose signature drifts from its collaborator stops isolating
        the code under test and starts hiding an integration break."""
        group = ", ".join([metric["time_column"], *dimensions])
        where = f"{metric['time_column']} BETWEEN @start_date AND @end_date"
        if maturity_cutoff is not None:
            where += f" AND {metric['time_column']} <= @maturity_cutoff"
        sql = (
            f"SELECT {group}, "
            f"SUM({metric['numerator_column']}) AS numerator, "
            f"SUM({metric['denominator_column']}) AS denominator "
            f"FROM `{project}.{metric['source_view']}` "
            f"WHERE {where} "
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


# --- against the real helper and the real catalog ---------------------------
#
# The stub above isolates the drift logic. These run the same logic through the
# SHIPPED `query-metric` helper and a SHIPPED metric definition, which is what
# catches an integration mismatch the stub cannot see — the two collaborators
# drifting apart while each remains internally consistent.


def _real_artifact_fm(metric_name: str, today: str = "2026-08-17") -> dict:
    """Frontmatter as the real helper would produce it for a real bundled metric."""
    import datetime as dt

    import kattfot_query as real_aq

    metric = real_aq.load_metric(metric_name)
    project = real_aq.resolve_project(metric, "fixture_sql")
    maturity = real_aq.resolve_maturity(metric, dt.date.fromisoformat(today))
    cutoff = dt.date.fromisoformat(maturity["cutoff_week"]) if maturity else None

    start, end = real_aq.parse_time_range(
        "last_8_weeks", metric["default_lookback_weeks"], dt.date.fromisoformat(today)
    )
    sql, params = real_aq.build_sql(metric, project, [], {}, cutoff)
    params_out = {**params, "start_date": start.isoformat(), "end_date": end.isoformat()}
    if cutoff is not None:
        params_out["maturity_cutoff"] = cutoff.isoformat()

    manifest = mf.build_manifest(
        metric_def_sha=metric["_def_sha"],
        sql=sql,
        params=params_out,
        rows=[],
        metric_name=metric_name,
        plugin_ver="0.1.0",
    )
    out = {
        "metric": metric_name,
        "source_project": project,
        "dimensions": [],
        "filters": {},
        "time_range_start": start.isoformat(),
        "time_range_end": end.isoformat(),
        "manifest": manifest,
    }
    if cutoff is not None:
        out["maturity_cutoff"] = cutoff.isoformat()
    return out


def test_verify_ok_against_the_real_helper_and_a_real_metric():
    """The integration case, and the one that matters most in practice.

    Every metric in the bundled catalog declares a `maturity_stage`, so every real
    artifact's SQL carries `cohort_week <= @maturity_cutoff`. Verify therefore has to
    rebuild WITH that predicate, using the cutoff the artifact recorded — recomputing it
    would drift daily as time passed, and omitting it would report QUERY_DRIFT on every
    governed artifact ever produced. A verifier that always cries drift gets ignored,
    which is worse than not shipping one.
    """
    import kattfot_query as real_aq

    fm = _real_artifact_fm("cart_to_checkout_start_conversion")
    assert "maturity_cutoff" in fm, "a maturity-gated metric must record its cutoff"
    result = vf.verify_frontmatter(fm, aq=real_aq)
    assert result["status"] == vf.VERIFY_OK, result["detail"]


def test_verify_ok_against_a_deep_stage_metric():
    """The deepest cutoff in the catalog (~44 days), where the gap between recording and
    recomputing the cutoff is widest."""
    import kattfot_query as real_aq

    fm = _real_artifact_fm("delivered_to_return_window_complete_rate")
    result = vf.verify_frontmatter(fm, aq=real_aq)
    assert result["status"] == vf.VERIFY_OK, result["detail"]


def test_a_dropped_cutoff_is_query_drift_with_the_real_helper():
    """The failure mode the fix above prevents, asserted directly: an artifact whose
    recorded cutoff is missing rebuilds to different SQL, and verify says so rather than
    quietly matching."""
    import kattfot_query as real_aq

    fm = _real_artifact_fm("cart_to_checkout_start_conversion")
    del fm["maturity_cutoff"]
    result = vf.verify_frontmatter(fm, aq=real_aq)
    assert result["status"] == vf.QUERY_DRIFT


def test_a_moved_cutoff_is_query_drift_with_the_real_helper():
    import kattfot_query as real_aq

    fm = _real_artifact_fm("cart_to_checkout_start_conversion")
    fm["maturity_cutoff"] = "2026-01-05"
    result = vf.verify_frontmatter(fm, aq=real_aq)
    assert result["status"] == vf.QUERY_DRIFT


def test_an_unregistered_metric_is_definition_missing_with_the_real_helper():
    import kattfot_query as real_aq

    fm = _real_artifact_fm("cart_to_checkout_start_conversion")
    fm["metric"] = "a_metric_that_was_deleted"
    result = vf.verify_frontmatter(fm, aq=real_aq)
    assert result["status"] == vf.DEFINITION_MISSING


def test_a_quoted_cutoff_and_a_date_object_both_work():
    """The artifact is hand-editable markdown, so `maturity_cutoff: 2026-08-10` (parsed
    by PyYAML into a `dt.date`) and `"2026-08-10"` (a string) are both legitimate and
    must verify identically."""
    import datetime as dt

    import kattfot_query as real_aq

    fm = _real_artifact_fm("cart_to_checkout_start_conversion")
    as_string = dict(fm)
    as_date = dict(fm, maturity_cutoff=dt.date.fromisoformat(fm["maturity_cutoff"]))
    assert vf.verify_frontmatter(as_string, aq=real_aq)["status"] == vf.VERIFY_OK
    assert vf.verify_frontmatter(as_date, aq=real_aq)["status"] == vf.VERIFY_OK


def test_verify_matches_an_artifact_the_demo_actually_wrote(tmp_path):
    """End to end against a file on disk. Runs the shipped query helper, composes the
    artifact the way `scripts/demo.py` does, parses the frontmatter back out of the
    markdown, and verifies it — so a formatting change in the artifact template that
    broke verification would fail here rather than in someone's audit."""
    import datetime as dt
    import subprocess
    import sys
    from pathlib import Path

    import kattfot_query as real_aq

    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable, str(root / "scripts" / "demo.py"),
            "--metric", "payment_approval_rate",
            "--dimensions", "",
            "--time-range", "last_8_weeks",
            "--skip-pdf", "--skip-html",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "KATTFOT_OUTPUTS_DIR": str(tmp_path),
             "MPLCONFIGDIR": str(tmp_path / "mpl")},
    )
    if proc.returncode != 0:
        import pytest as _pytest

        # The chart step needs matplotlib. Its absence is a dependency gap, not a
        # verification failure, and the suite is deliberately installable without it —
        # so fail loudly on anything else and stay quiet about that one case.
        if "matplotlib" in (proc.stderr + proc.stdout):
            _pytest.skip("matplotlib is not installed; the render step cannot run")
        raise AssertionError(f"demo failed:\n{proc.stdout}\n{proc.stderr}")

    artifacts = list(tmp_path.glob("*.md"))
    assert len(artifacts) == 1, f"expected one artifact, got {artifacts}"
    text = artifacts[0].read_text()
    parsed = yaml.safe_load(text.split("---")[1])
    assert parsed["maturity_cutoff"], "the demo artifact must record its cutoff"

    result = vf.verify_frontmatter(parsed, aq=real_aq)
    assert result["status"] == vf.VERIFY_OK, result["detail"]

    _ = dt, real_aq  # referenced above; keeps linters quiet about the imports


# --- kattfot:explore artifacts ------------------------------------------------
#
# `verify_artifact.py` auto-detects `skill: explore` in the frontmatter and
# switches to `verify_explore` — a different receipt shape (no reviewed metric
# definition; `register_sha` stands in for it) with its own sentinels. These
# tests drive the real CLI end to end (`verify_artifact.main()`, the way
# test_promote.py drives `promote_metric.main()`), because the EXIT CODE is
# the contract a caller depends on, and `_shared/verify.py` alone cannot tell
# you what a status maps to.
#
# Mirrors test_promote.py's fixture style: a tmp KATTFOT_EXPLORATION_FILE puts
# register_sha under the test's control, so "the allow-list moved" or
# "the allow-list is gone" can be simulated without touching the repo's real
# config/exploration.yaml.

EXPLORE_SPINE = "analytics.cart_checkout_session_spine"
EXPLORE_SQL = f"SELECT market, COUNT(*) AS n FROM {EXPLORE_SPINE} GROUP BY market"


@pytest.fixture()
def explore_register(tmp_path, monkeypatch) -> Path:
    reg = tmp_path / "exploration.yaml"
    reg.write_text(f"surfaces:\n  {EXPLORE_SPINE}:\n    status: live\n")
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(reg))
    return reg


def _explore_result(monkeypatch, capsys) -> dict:
    monkeypatch.setenv("KATTFOT_AUDIT_DISABLE", "1")
    monkeypatch.setattr(
        sys, "argv",
        ["kattfot_explore", "--question", "How many sessions per market?",
         "--sql", EXPLORE_SQL],
    )
    assert kattfot_explore.main() in (0, None)
    return json.loads(capsys.readouterr().out)


def _explore_artifact(tmp_path, monkeypatch, capsys) -> Path:
    out = _explore_result(monkeypatch, capsys)
    art = tmp_path / "explore-artifact.md"
    demo_skills.compose_explore_artifact(out, art)
    return art


def _run_verify(artifact, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["verify_artifact", str(artifact)])
    code = verify_artifact.main()
    return code, capsys.readouterr().out


def test_explore_verify_ok_when_nothing_changed(tmp_path, explore_register, monkeypatch, capsys):
    art = _explore_artifact(tmp_path, monkeypatch, capsys)
    code, out = _run_verify(art, monkeypatch, capsys)
    assert code == 0
    assert vf.VERIFY_OK in out


def test_explore_tamper_is_detected(tmp_path, explore_register, monkeypatch, capsys):
    """Editing the executed SQL after the fact — the exact edit PROMOTE-TAMPER
    catches at promotion time — is PROVENANCE_TAMPERED here, at exit 1."""
    art = _explore_artifact(tmp_path, monkeypatch, capsys)
    art.write_text(art.read_text().replace("GROUP BY market", "GROUP BY market, region"))
    code, out = _run_verify(art, monkeypatch, capsys)
    assert code == 1
    assert vf.PROVENANCE_TAMPERED in out


def test_explore_register_drift_is_detected(tmp_path, explore_register, monkeypatch, capsys):
    art = _explore_artifact(tmp_path, monkeypatch, capsys)
    # simulate the register having changed since the exploration ran
    explore_register.write_text(
        explore_register.read_text() + "# register content changed since the exploration ran\n"
    )
    code, out = _run_verify(art, monkeypatch, capsys)
    assert code == 1
    assert vf.REGISTER_DRIFT in out


def test_explore_register_missing_cannot_verify(tmp_path, explore_register, monkeypatch, capsys):
    art = _explore_artifact(tmp_path, monkeypatch, capsys)
    explore_register.unlink()
    code, out = _run_verify(art, monkeypatch, capsys)
    assert code == 2
    assert vf.REGISTER_MISSING in out


def test_explore_missing_fence_cannot_verify(tmp_path, monkeypatch, capsys):
    """A manifest with nothing to recompute `query_sha` from — an artifact
    whose Provenance section was stripped rather than edited — is
    cannot-verify (PROVENANCE_ABSENT), not a silent pass."""
    art = tmp_path / "no-fence.md"
    art.write_text(
        "---\nskill: explore\naccuracy_tier: T3\nmanifest:\n"
        "  register_sha: abc123\n  query_sha: def456\n  result_sha: ghi789\n"
        "---\n\n## Provenance\n\n(no SQL fence here)\n"
    )
    code, out = _run_verify(art, monkeypatch, capsys)
    assert code == 2
    assert vf.PROVENANCE_ABSENT in out


def test_explore_manifest_absent_cannot_verify(tmp_path, monkeypatch, capsys):
    art = tmp_path / "no-manifest.md"
    art.write_text(
        "---\nskill: explore\naccuracy_tier: T3\n---\n\n"
        "## Provenance\n\n```sql\nSELECT 1\n```\n"
    )
    code, out = _run_verify(art, monkeypatch, capsys)
    assert code == 2
    assert vf.MANIFEST_ABSENT in out


def test_explore_tamper_and_register_drift_both_report_detected_wins(
    tmp_path, explore_register, monkeypatch, capsys
):
    """Both findings are reported, and the top-level status/exit is the
    DETECTED one — the explore-lane counterpart to the governed suite's
    combined-findings case (test_query_drift_when_column_changes)."""
    art = _explore_artifact(tmp_path, monkeypatch, capsys)
    art.write_text(art.read_text().replace("GROUP BY market", "GROUP BY market, region"))
    explore_register.write_text(
        explore_register.read_text() + "# register content changed since the exploration ran\n"
    )
    code, out = _run_verify(art, monkeypatch, capsys)
    assert code == 1
    assert vf.PROVENANCE_TAMPERED in out
    assert vf.REGISTER_DRIFT in out


def test_explore_tamper_and_register_missing_detected_outranks_inconclusive(
    tmp_path, explore_register, monkeypatch, capsys
):
    """The case `verify_explore`'s severity ranking exists for: tamper (exit 1,
    DETECTED) and register-missing (exit 2, cannot-verify) are DIFFERENT exit
    codes, unlike the tamper+drift combo above (both exit 1, so that test
    would still pass with the ranking reversed). Both findings must be
    reported, and the DETECTED one must win the top-level status/exit —
    burying a confirmed tamper behind "couldn't check the register" would be
    worse than the inconclusive report alone."""
    art = _explore_artifact(tmp_path, monkeypatch, capsys)
    art.write_text(art.read_text().replace("GROUP BY market", "GROUP BY market, region"))
    explore_register.unlink()
    code, out = _run_verify(art, monkeypatch, capsys)
    assert code == 1
    assert vf.PROVENANCE_TAMPERED in out
    assert vf.REGISTER_MISSING in out


def test_governed_artifact_routing_is_unchanged(tmp_path, monkeypatch, capsys):
    """A governed artifact carries no `skill:` frontmatter key at all (only
    `kattfot:explore` writes one) — confirm the CLI's new routing check still
    sends it through `verify_frontmatter`, end to end through a real demo
    artifact, exactly as before this change."""
    import os
    import subprocess

    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable, str(root / "scripts" / "demo.py"),
            "--metric", "payment_approval_rate",
            "--dimensions", "",
            "--time-range", "last_8_weeks", "--skip-pdf", "--skip-html",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "KATTFOT_OUTPUTS_DIR": str(tmp_path),
             "MPLCONFIGDIR": str(tmp_path / "mpl")},
    )
    if proc.returncode != 0:
        if "matplotlib" in (proc.stderr + proc.stdout):
            pytest.skip("matplotlib is not installed; the render step cannot run")
        raise AssertionError(f"demo failed:\n{proc.stdout}\n{proc.stderr}")

    artifacts = list(tmp_path.glob("*.md"))
    assert len(artifacts) == 1, f"expected one artifact, got {artifacts}"
    parsed = yaml.safe_load(artifacts[0].read_text().split("---")[1])
    assert "skill" not in parsed, (
        "a governed artifact must not carry a `skill:` key — its absence is "
        "what routes verify_artifact.py away from the explore path"
    )

    code, out = _run_verify(artifacts[0], monkeypatch, capsys)
    assert code == 0
    assert vf.VERIFY_OK in out
