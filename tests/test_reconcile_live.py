"""The `reconcile` skill's runner: cause attribution against real data.

`tests/test_reconcile.py` covers the pure engine — `classify()` driven by a stub
runner, so the cause logic is tested without a database. This file covers the other
half: the CLI's `run_value`, which turns a `Request` into an actual governed metric
read, and therefore whether each cause is **reachable in practice**.

That distinction is not academic. `MATURITY_CUTOFF` was unreachable in the live path
until `Request` gained a `mature` flag: the hypothesis widened the window, the runner
kept applying the metric's cutoff, the widened window was clamped straight back, and
the variant reproduced our own number instead of theirs — forever. A stub runner
cannot show that, because a stub has no cutoff to apply. So the tests below run the
real thing.

Offline: every re-run goes through the query-metric helper on the sqlite adapter, so
the causes are demonstrated against the committed fixtures.

The CLI is `reconcile_number.py`, not `reconcile.py`, because `_shared/reconcile.py` is
reachable as a top-level `reconcile` (see `_shared/spine.py`'s path insertion) and two
modules of one name resolve by import order — which made this file pass alone and fail
in the full suite.
"""

import datetime as dt
import json
import sys

import pytest

import reconcile_number as cli
from _shared import reconcile as rc

TODAY = "2026-08-17"
TODAY_DATE = dt.date(2026, 8, 17)

#: A shallow metric (settles in days) and a deep one (settles after the 30-day
#: return window). The gap the maturity cutoff produces is enormous on the second and
#: negligible on the first, which is the point.
SHALLOW = "cart_to_checkout_start_conversion"
DEEP = "delivered_to_return_window_complete_rate"


def run(argv, capsys) -> dict:
    old = sys.argv
    sys.argv = ["reconcile"] + argv
    try:
        code = cli.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == 0, out
    return json.loads(out)


def governed_value(metric: str, time_range: str, mature: bool = True, filters=()) -> float:
    """The number the governed path produces, via the CLI's own runner."""
    run_value, _trace = cli.make_runner(TODAY_DATE, "sqlite")
    import assay_query as aq

    m = aq.load_metric(metric)
    start, end = aq.parse_time_range(time_range, m.get("default_lookback_weeks", 8), TODAY_DATE)
    return run_value(rc.Request(metric=metric, start=start, end=end,
                                filters=tuple(filters), mature=mature))


# --- AGREE ------------------------------------------------------------------


def test_a_matching_number_is_agree_not_a_manufactured_cause(capsys):
    """Sub-tolerance noise is `AGREE`. Manufacturing a cause for it would train the
    reader to distrust the causes that are real."""
    ours = governed_value(SHALLOW, "last_8_weeks")
    v = run([
        "--metric", SHALLOW, "--their-value", f"{ours:.4f}",
        "--time-range", "last_8_weeks", "--today", TODAY,
    ], capsys)
    assert v["cause"] == rc.AGREE
    assert v["run_count"] == 1, "AGREE must cost exactly one query"


# --- MATURITY_CUTOFF: the cause that was structurally unreachable -----------


def test_maturity_cutoff_is_reachable_and_reproduces_their_number(capsys):
    """The regression test for the `Request.mature` change.

    Their figure is the same metric over a window running to today with the immature
    tail included. The hypothesis has to drop the cutoff as well as widen the window,
    or the governed cutoff clamps it back and the cause can never be found.
    """
    theirs = governed_value(DEEP, "last_8_weeks", mature=False)
    v = run([
        "--metric", DEEP, "--their-value", f"{theirs:.4f}",
        "--time-range", "last_8_weeks", "--today", TODAY,
    ], capsys)
    assert v["cause"] == rc.MATURITY_CUTOFF
    assert v["reproduced"] == pytest.approx(theirs, abs=5e-4)
    assert "immature tail" in v["detail"]


def test_the_maturity_gap_on_a_deep_metric_is_enormous_not_marginal(capsys):
    """Why this cause is worth recognising on sight. Two people can both be "looking
    at the return rate", over the same window, and be tens of points apart with
    neither having made a mistake."""
    mature = governed_value(DEEP, "last_8_weeks")
    immature = governed_value(DEEP, "last_8_weeks", mature=False)
    assert mature - immature > 0.3, (
        f"expected a large maturity gap on a deep metric; got {mature:.3f} vs "
        f"{immature:.3f}. If this shrank, the fixtures stopped having a real tail."
    )


def test_the_hypothesis_drops_the_cutoff_as_well_as_widening_the_window():
    """Pinned on the hypothesis itself, so the reason is visible where the change was
    made rather than only in the behaviour it enables."""
    base = rc.Request(metric=DEEP, start=dt.date(2026, 6, 1), end=dt.date(2026, 7, 6))
    cause, req, _why = next(
        c for c in rc.candidate_hypotheses(base, today=TODAY_DATE)
        if c[0] == rc.MATURITY_CUTOFF
    )
    assert cause == rc.MATURITY_CUTOFF
    assert req.end == TODAY_DATE
    assert req.mature is False
    assert base.mature is True, "the default must stay governed"


# --- DATE_WINDOW ------------------------------------------------------------


def test_date_window_is_recovered_when_their_window_is_known(capsys):
    theirs = governed_value(SHALLOW, "2026-04-06:2026-05-25")
    v = run([
        "--metric", SHALLOW, "--their-value", f"{theirs:.4f}",
        "--time-range", "last_8_weeks",
        "--their-time-range", "2026-04-06:2026-05-25",
        "--today", TODAY,
    ], capsys)
    assert v["cause"] == rc.DATE_WINDOW
    assert v["reproduced"] == pytest.approx(theirs, abs=5e-4)


# --- FILTER_DIFF ------------------------------------------------------------


def test_filter_diff_is_recovered_when_they_omitted_our_filter(capsys):
    """We filtered to one channel; they did not. The hypothesis drops our filter."""
    theirs = governed_value(SHALLOW, "last_8_weeks")
    v = run([
        "--metric", SHALLOW, "--their-value", f"{theirs:.4f}",
        "--time-range", "last_8_weeks", "--filter", "channel=marketplace",
        "--today", TODAY,
    ], capsys)
    assert v["cause"] == rc.FILTER_DIFF
    assert "did not apply" in v["detail"]


def test_filter_diff_is_recovered_when_they_applied_one_we_did_not(capsys):
    theirs = governed_value(SHALLOW, "last_8_weeks", filters=[("channel", "social")])
    v = run([
        "--metric", SHALLOW, "--their-value", f"{theirs:.4f}",
        "--time-range", "last_8_weeks",
        "--their-filter", "channel=social", "--today", TODAY,
    ], capsys)
    assert v["cause"] == rc.FILTER_DIFF
    assert "additionally filtered" in v["detail"]


# --- GRAIN ------------------------------------------------------------------


def test_grain_is_recovered_from_a_sibling_metric(capsys):
    """They measured something adjacent. The two metrics share a stage and differ in
    denominator, which is the commonest version of this in practice."""
    theirs = governed_value("cart_to_order_confirmed_end_to_end", "last_8_weeks")
    v = run([
        "--metric", SHALLOW, "--their-value", f"{theirs:.4f}",
        "--time-range", "last_8_weeks",
        "--their-metric", "cart_to_order_confirmed_end_to_end", "--today", TODAY,
    ], capsys)
    assert v["cause"] == rc.GRAIN


def test_an_unregistered_sibling_is_skipped_not_matched(capsys):
    """A variant the runner cannot compute returns None, and None must never count as
    a match — otherwise an unregistered metric name would silently "explain" any gap.
    """
    v = run([
        "--metric", SHALLOW, "--their-value", "0.11",
        "--time-range", "last_8_weeks",
        "--their-metric", "no_such_metric", "--today", TODAY,
    ], capsys)
    assert v["cause"] == rc.UNKNOWN
    grain_attempts = [t for t in v["tried"] if t["cause"] == rc.GRAIN]
    assert grain_attempts and all(t["value"] is None for t in grain_attempts)


# --- UNKNOWN ----------------------------------------------------------------


def test_an_unexplainable_gap_is_unknown_with_the_attempts_listed(capsys):
    """The honest outcome. A cause that was not reproduced is worse than an admitted
    gap, because it closes the investigation."""
    v = run([
        "--metric", SHALLOW, "--their-value", "0.11",
        "--time-range", "last_8_weeks", "--today", TODAY,
    ], capsys)
    assert v["cause"] == rc.UNKNOWN
    assert v["tried"], "UNKNOWN must show what was attempted"
    assert "manual investigation" in v["detail"]


# --- definition drift -------------------------------------------------------


def test_definition_drift_is_detected_from_an_artifact_manifest(tmp_path, capsys):
    """The cause the numeric search cannot find: two runs whose definition changed in
    between can agree numerically and still not mean the same thing."""
    artifact = tmp_path / "old.md"
    artifact.write_text(
        "---\nmetric: cart_to_checkout_start_conversion\n"
        "manifest:\n  metric_def_sha: deadbeefdeadbeef\n---\n\n# old artifact\n"
    )
    v = run([
        "--metric", SHALLOW, "--their-value", "0.70",
        "--time-range", "last_8_weeks", "--today", TODAY,
        "--their-artifact", str(artifact),
    ], capsys)
    drift = v["definition_drift"]
    assert drift["drifted"] is True
    assert drift["cause"] == rc.DEFINITION_DRIFT
    assert "deadbeefdeadbeef" in drift["detail"]


def test_a_matching_manifest_reports_no_drift(tmp_path, capsys):
    import assay_query as aq

    sha = aq.load_metric(SHALLOW)["_def_sha"]
    artifact = tmp_path / "current.md"
    artifact.write_text(
        f"---\nmetric: {SHALLOW}\nmanifest:\n  metric_def_sha: {sha}\n---\n\n# artifact\n"
    )
    v = run([
        "--metric", SHALLOW, "--their-value", "0.70",
        "--time-range", "last_8_weeks", "--today", TODAY,
        "--their-artifact", str(artifact),
    ], capsys)
    assert v["definition_drift"]["drifted"] is False


def test_a_missing_artifact_is_a_clean_error(tmp_path):
    old = sys.argv
    sys.argv = ["reconcile", "--metric", SHALLOW, "--their-value", "0.7",
                "--time-range", "last_8_weeks", "--today", TODAY,
                "--their-artifact", str(tmp_path / "nope.md")]
    try:
        with pytest.raises(SystemExit) as exc:
            cli.main()
    finally:
        sys.argv = old
    assert "does not exist" in str(exc.value)


# --- governance on the re-run path -----------------------------------------
# A reconciliation is the read nobody is watching, so it is the one that must not be
# allowed to reach around the guards "just to check something".


def test_every_rerun_is_stamped_and_traced(capsys):
    v = run([
        "--metric", SHALLOW, "--their-value", "0.11",
        "--time-range", "last_8_weeks", "--today", TODAY,
    ], capsys)
    assert v["engine"] == "sqlite"
    assert v["fixture_backed"] is True
    assert any("FIXTURE DATA" in w for w in v["warnings"])
    assert v["run_count"] == len(v["runs"]) > 1
    for r in v["runs"]:
        assert set(r) >= {"metric", "window", "filters", "mature", "value"}


def test_an_unregistered_metric_refuses_before_any_query():
    old = sys.argv
    sys.argv = ["reconcile", "--metric", "not_a_metric", "--their-value", "0.5",
                "--time-range", "last_8_weeks", "--today", TODAY]
    try:
        with pytest.raises(SystemExit) as exc:
            cli.main()
    finally:
        sys.argv = old
    assert "R1" in str(exc.value)


def test_a_pii_filter_is_refused_on_the_rerun_path(capsys):
    """R6 runs inside the runner, so it fires on the hypothesis re-runs too — not only
    on the caller's own arguments."""
    old = sys.argv
    sys.argv = ["reconcile", "--metric", SHALLOW, "--their-value", "0.5",
                "--time-range", "last_8_weeks", "--today", TODAY,
                "--filter", "customer_email=someone@example.com"]
    try:
        with pytest.raises(SystemExit) as exc:
            cli.main()
    finally:
        sys.argv = old
    assert "R6" in str(exc.value)


def test_a_non_allow_listed_filter_is_refused(capsys):
    old = sys.argv
    sys.argv = ["reconcile", "--metric", SHALLOW, "--their-value", "0.5",
                "--time-range", "last_8_weeks", "--today", TODAY,
                "--filter", "internal_risk_score=high"]
    try:
        with pytest.raises(SystemExit) as exc:
            cli.main()
    finally:
        sys.argv = old
    assert "R3" in str(exc.value)


def test_a_t3_metric_is_refused_before_reconciling(tmp_path, monkeypatch):
    """R4 on the way in. Reconciling an ungoverned number would produce a governed-
    looking verdict about it."""
    (tmp_path / "m.md").write_text(
        "---\nname: exploratory\ndisplay_name: E\naccuracy_tier: T3\nmodule: checkout\n"
        "status: live\nsource_view: analytics.cart_checkout_funnel_weekly\n"
        "time_column: cohort_week\nnumerator_column: carts_created\n"
        "denominator_column: carts_created\nvalue_format: percent\n"
        "allowed_dimensions: []\nallowed_filters: []\ndefault_lookback_weeks: 8\n"
        "entity: checkout_session\n---\n\n## Definition\nx\n"
    )
    monkeypatch.setenv("ASSAY_METRICS_DIR", str(tmp_path))
    old = sys.argv
    sys.argv = ["reconcile", "--metric", "exploratory", "--their-value", "0.5",
                "--time-range", "last_8_weeks", "--today", TODAY]
    try:
        with pytest.raises(SystemExit) as exc:
            cli.main()
    finally:
        sys.argv = old
    assert "R4" in str(exc.value)
