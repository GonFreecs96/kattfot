"""Manifest receipt: hashes are deterministic, content-sensitive, and exclude
the wall-clock. These are the guarantees `kattfot:verify` and cross-artifact
drift detection rely on.
"""

import json

from _shared import manifest as mf  # plugins/kattfot/skills on sys.path via conftest


# --- sha16 / canonical_json ------------------------------------------------


def test_sha16_is_stable_and_short():
    assert mf.sha16("hello") == mf.sha16("hello")
    assert len(mf.sha16("hello")) == 16
    assert mf.sha16("hello") != mf.sha16("hello ")  # one trailing space differs


def test_sha16_accepts_bytes_and_text_equivalently():
    assert mf.sha16("abc") == mf.sha16(b"abc")


def test_canonical_json_is_key_order_independent():
    a = mf.canonical_json({"b": 1, "a": 2})
    b = mf.canonical_json({"a": 2, "b": 1})
    assert a == b


# --- result_sha ------------------------------------------------------------


ROWS = [
    {"cohort_week": "2026-03-02", "rate": 0.841, "numerator": 20661, "denominator": 24556},
    {"cohort_week": "2026-03-09", "rate": 0.844, "numerator": 20334, "denominator": 24099},
]


def test_result_sha_ignores_row_order():
    assert mf.result_sha(ROWS, drop_cols={"rate"}) == mf.result_sha(
        list(reversed(ROWS)), drop_cols={"rate"}
    )


def test_result_sha_ignores_dropped_derived_column():
    """The derived ratio is a function of num/den — perturbing only it must not
    change the fingerprint (we drop it)."""
    tweaked = [{**ROWS[0], "rate": 0.999}, ROWS[1]]
    assert mf.result_sha(tweaked, drop_cols={"rate"}) == mf.result_sha(ROWS, drop_cols={"rate"})


def test_result_sha_changes_when_volume_changes():
    """A flipped/edited numerator IS a content change — must change the hash."""
    tampered = [{**ROWS[0], "numerator": 999}, ROWS[1]]
    assert mf.result_sha(tampered, drop_cols={"rate"}) != mf.result_sha(ROWS, drop_cols={"rate"})


def test_result_sha_integral_float_matches_int():
    """A warehouse SUM may return 24556.0; the mock path returns 24556. They must
    fingerprint identically."""
    as_float = [{"cohort_week": "2026-03-02", "numerator": 20661.0, "denominator": 24556.0}]
    as_int = [{"cohort_week": "2026-03-02", "numerator": 20661, "denominator": 24556}]
    assert mf.result_sha(as_float) == mf.result_sha(as_int)


# --- build_manifest --------------------------------------------------------


def test_build_manifest_shape_and_determinism():
    kw = dict(
        metric_def_sha="deadbeefdeadbeef",
        sql="SELECT 1",
        params={"start_date": "2026-03-01", "end_date": "2026-06-12"},
        rows=ROWS,
        metric_name="rate",
        plugin_ver="0.7.0",
    )
    m1 = mf.build_manifest(**kw)
    m2 = mf.build_manifest(**kw)
    assert m1 == m2
    assert set(m1) == {"plugin_version", "metric_def_sha", "query_sha", "result_sha"}
    assert m1["plugin_version"] == "0.7.0"
    assert m1["metric_def_sha"] == "deadbeefdeadbeef"


def test_query_sha_tracks_params():
    base = dict(metric_def_sha="x", sql="SELECT 1", rows=ROWS, metric_name="rate", plugin_ver="t")
    a = mf.build_manifest(params={"start_date": "2026-01-01"}, **base)
    b = mf.build_manifest(params={"start_date": "2026-02-01"}, **base)
    assert a["query_sha"] != b["query_sha"]  # different window -> different query fingerprint


def test_generated_at_is_excluded_from_hashes():
    """The receipt pins content, not the moment of execution. There is no path
    by which a timestamp can enter a hash — building twice yields identical
    fingerprints regardless of when."""
    kw = dict(
        metric_def_sha="x", sql="SELECT 1", params={}, rows=ROWS,
        metric_name="rate", plugin_ver="t",
    )
    assert mf.build_manifest(**kw) == mf.build_manifest(**kw)


def test_plugin_version_reads_plugin_json():
    """Best-effort read of the real plugin.json — should match the committed version."""
    from pathlib import Path

    plugin_json = (
        Path(mf.__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
    )
    expected = json.loads(plugin_json.read_text())["version"]
    assert mf.plugin_version() == expected
