"""The four spine-backed skills, exercised end to end against the fixture database.

`tests/test_spine_maturity.py` pins the shared per-stage machinery against a
synthetic spec. This file pins the four skills that *consume* it, running their real
`main()` against the committed session spine — which is the part that had never
happened before Phase 3, and where the interesting bugs were.

Three things get specific attention:

1. **The per-group maturity override, against a database.** `windowed_count_expr()`
   emits a `CASE`-per-group when the monitored dimension matches a stage's override,
   and until now that SQL had only ever been string-compared in a unit test. A
   `CASE` that parses but binds the wrong parameter, or inverts WHEN and ELSE, is
   invisible to a string assertion and produces a plausible wrong number. So the
   counts are checked against an independent computation over the CSV — a different
   idiom, which is what makes it an oracle rather than a restatement.

2. **Terminal-branch coherence.** The branches are mutually exclusive and exhaustive,
   so under one cohort cutoff they must sum to exactly the carts created in that
   window. Cheap to assert, and it is what catches the branch query and the stage
   query disagreeing about their gate.

3. **`funnel-pulse` refusing on a missing calibration.** The convenient resolver
   returns `None` for an uncalibrated stage, which would make the monitor run ungated
   and say nothing about it. The refusing path is used, and the refusal is tested.

Offline throughout: `KATTFOT_ENGINE` defaults to sqlite, so every query below runs the
skills' real canonical SQL against the committed fixtures.
"""

import csv
import datetime as dt
import json
import sys
from pathlib import Path

import pytest
import yaml

import funnel_dropoff
import funnel_pulse
import segment_compare
import stage_timing
from _shared import adapters, dialect, spine

ROOT = Path(__file__).resolve().parents[1]
SPINE_CSV = ROOT / "data" / "kattfot" / "sample" / "cart_checkout_session_spine.csv"
SPEC_PATH = ROOT / "plugins" / "kattfot" / "config" / "checkout-funnel-spec.yaml"

#: The fixtures' observation instant. Every window below is pinned to it, because
#: reading a fixed fixture at the real today withholds more of the tail every week.
TODAY = "2026-08-17"
TODAY_DATE = dt.date(2026, 8, 17)
WINDOW = ["--time-range", "last_16_weeks", "--today", TODAY]


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load(SPEC_PATH.read_text())


@pytest.fixture(scope="module")
def raw_spine():
    with SPINE_CSV.open() as f:
        return list(csv.DictReader(f))


def run(module, argv, monkeypatch, capsys) -> dict:
    """Call a skill's real `main()` with a patched argv and parse its JSON."""
    monkeypatch.setattr(sys, "argv", [module.__name__] + argv)
    module.main()
    return json.loads(capsys.readouterr().out)


def refuse(module, argv, monkeypatch) -> SystemExit:
    monkeypatch.setattr(sys, "argv", [module.__name__] + argv)
    with pytest.raises(SystemExit) as exc:
        module.main()
    return exc.value


# ---------------------------------------------------------------------------
# 1. The per-group maturity override, against a real database
# ---------------------------------------------------------------------------
# The override's whole purpose: `payment_decisioned` is dominated book-wide by
# instant card authorization, so its 2-day default lag is badly wrong for a bank
# transfer routed through a clearing cycle. The check is that the SQL actually
# applies a DIFFERENT window to that group — not that the metadata says it did.


def _independent_count(rows, method, flag, start, end) -> int:
    """Count matching rows straight from the CSV, excluding the legacy cohort.

    Deliberately a different idiom from the SQL path (no gating expression, no
    CASE, no parameter binding) so agreement between the two means something.
    """
    return sum(
        1
        for r in rows
        if r["payment_method"] == method
        and r["checkout_version"] != "v1-legacy"
        and r[flag] == "true"
        and start <= r["cohort_week"] <= end
    )


@pytest.fixture(scope="module")
def override_windows(spec):
    """The windows `funnel-pulse` should apply when monitoring by payment_method."""
    pulse = spec["pulse"]
    wins, _meta = funnel_pulse.stage_windows(
        spec, TODAY_DATE, int(pulse["recent_weeks"]), int(pulse["baseline_weeks"]),
        "stage", "payment_method",
    )
    return wins


def test_the_override_is_live_in_the_shipped_spec(spec):
    """Guard against this whole section going vacuous. If the shipped spec ever loses
    its override, the tests below would pass by testing the default path twice."""
    cfg = spine.stage_lag_overrides(spec, "payment_decisioned")
    assert cfg, "checkout-funnel-spec.yaml no longer declares a payment_decisioned override"
    assert cfg["by"] == "payment_method"
    assert "bank_transfer_review" in cfg["values"]
    default_lag = spine.stage_lag_days(spec, "payment_decisioned")
    assert cfg["values"]["bank_transfer_review"] > default_lag, (
        "an override that is not longer than the default cannot change any window, so "
        "it would exercise nothing"
    )


def test_override_produces_a_genuinely_different_window(override_windows):
    """The precondition for everything below: the override's window must not coincide
    with the stage default, or a broken CASE would return the right answer anyway."""
    w = override_windows["payment_decisioned"]
    gw = w["group_windows"]["bank_transfer_review"]
    assert (gw["recent_start"], gw["end"]) != (w["recent_start"], w["end"])
    assert gw["end"] < w["end"], "a longer lag must produce an older cutoff"


def test_override_group_counts_match_the_override_window_not_the_default(
    monkeypatch, capsys, override_windows, raw_spine
):
    """THE test for risk #1: the CASE-per-group SQL, executed, against an oracle.

    `bank_transfer_review` must be counted over its own longer window. A malformed
    or mis-bound `CASE` that silently fell through to the ELSE branch would return
    the DEFAULT window's count — a different number, plausible, and wrong. Both are
    computed here, so the assertion distinguishes them.
    """
    out = run(funnel_pulse, ["--by", "payment_method", "--min-started", "100",
                             "--today", TODAY], monkeypatch, capsys)
    flow = next(f for f in out["flows"] if f["grp"] == "bank_transfer_review")
    stage = next(s for s in flow["stages"] if s["key"] == "payment_decisioned")
    assert stage["lag_override"] is True

    w = override_windows["payment_decisioned"]
    gw = w["group_windows"]["bank_transfer_review"]
    expected = _independent_count(
        raw_spine, "bank_transfer_review", "cart_created_flag",
        gw["recent_start"].isoformat(), gw["end"].isoformat(),
    )
    would_be_wrong = _independent_count(
        raw_spine, "bank_transfer_review", "cart_created_flag",
        w["recent_start"].isoformat(), w["end"].isoformat(),
    )
    assert expected != would_be_wrong, "the two windows must give different counts"
    assert stage["recent_n"] == expected, (
        f"the override window should count {expected} carts; got {stage['recent_n']}. "
        f"The stage default would give {would_be_wrong} — if that is what came back, "
        f"the CASE fell through to its ELSE branch."
    )


def test_override_applies_to_the_numerator_as_well_as_the_denominator(
    monkeypatch, capsys, override_windows, raw_spine
):
    """A rate whose numerator and denominator came from different windows is not a
    rate. Both sides must be gated by the same CASE, which is why `stage_sql` wraps
    the first-stage count in `windowed_count_expr` too."""
    out = run(funnel_pulse, ["--by", "payment_method", "--min-started", "100",
                             "--today", TODAY], monkeypatch, capsys)
    flow = next(f for f in out["flows"] if f["grp"] == "bank_transfer_review")
    stage = next(s for s in flow["stages"] if s["key"] == "payment_decisioned")

    gw = override_windows["payment_decisioned"]["group_windows"]["bank_transfer_review"]
    start, end = gw["recent_start"].isoformat(), gw["end"].isoformat()
    num = _independent_count(raw_spine, "bank_transfer_review", "payment_decisioned_flag", start, end)
    den = _independent_count(raw_spine, "bank_transfer_review", "cart_created_flag", start, end)
    assert stage["recent_rate"] == pytest.approx(num / den)


def test_a_group_without_an_override_keeps_the_stage_default(
    monkeypatch, capsys, override_windows, raw_spine
):
    """The ELSE branch. The override is additive: a group with no entry must be
    unaffected, or turning an override on would silently move every other group."""
    out = run(funnel_pulse, ["--by", "payment_method", "--min-started", "100",
                             "--today", TODAY], monkeypatch, capsys)
    flow = next(f for f in out["flows"] if f["grp"] == "card_instant")
    stage = next(s for s in flow["stages"] if s["key"] == "payment_decisioned")
    assert stage["lag_override"] is False

    w = override_windows["payment_decisioned"]
    expected = _independent_count(
        raw_spine, "card_instant", "cart_created_flag",
        w["recent_start"].isoformat(), w["end"].isoformat(),
    )
    assert stage["recent_n"] == expected


def test_the_override_is_inert_when_monitoring_a_different_dimension(
    monkeypatch, capsys, spec
):
    """`--by channel` must not apply a payment-method override to a channel. The
    dimension mismatch has to fall back to the shared window rather than keying the
    CASE on the wrong column."""
    out = run(funnel_pulse, ["--by", "channel", "--today", TODAY], monkeypatch, capsys)
    assert out["maturity"]["per_stage"]["payment_decisioned"]["group_overrides"] is None
    for flow in out["flows"]:
        for stage in flow["stages"]:
            assert stage["lag_override"] is False


def test_every_parameter_in_the_override_sql_is_bound(monkeypatch, spec, override_windows):
    """A `CASE` referencing a parameter nobody bound is the failure mode a string
    assertion cannot see: `windowed_count_expr` and `windowed_count_params` build the
    WHEN clauses and the binds independently, and they have to agree on every name.

    Checked on the TRANSLATED SQL, because translation is what turns `@name` into
    `:name` and is therefore the form the driver actually receives.
    """
    import re

    where, params = spine.build_where(
        spec, {}, dt.date(2026, 4, 6), dt.date(2026, 8, 10),
        maturity_mode="off", include_legacy=False,
    )
    params.update(funnel_pulse.window_params(override_windows))
    sql = funnel_pulse.stage_sql(spec, "payment_method", where, override_windows)
    translated, bind = dialect.translate(sql, params)

    referenced = set(re.findall(r":(\w+)", translated))
    missing = sorted(referenced - set(bind))
    assert not missing, f"the override SQL references unbound parameters: {missing}"
    # And at least one override parameter is genuinely in play.
    assert any(name.endswith("_ov0") for name in referenced), (
        "no override parameter reached the SQL, so this test proved nothing"
    )


def test_a_malformed_case_would_be_caught(spec, override_windows):
    """What the tests above would have caught, demonstrated.

    Three realistic corruptions of the generated `CASE` — an unbalanced paren, a
    dropped ELSE, and a typo'd bind name — must each fail loudly at translation or
    execution rather than returning a number. This is the assertion that makes the
    rest of this section a regression guard instead of a snapshot.
    """
    adapter = adapters.get_adapter("sqlite")
    where, params = spine.build_where(
        spec, {}, dt.date(2026, 4, 6), dt.date(2026, 8, 10),
        maturity_mode="off", include_legacy=False,
    )
    params.update(funnel_pulse.window_params(override_windows))
    good = funnel_pulse.stage_sql(spec, "payment_method", where, override_windows)

    # The good SQL runs.
    assert adapter.run_query("proj", good, params)

    corruptions = {
        "unbalanced paren": good.replace("CASE WHEN", "CASE ((WHEN", 1),
        "dropped ELSE": good.replace("ELSE (cohort_week >= @rs_payment_decisioned", "(", 1),
        "typo'd bind": good.replace("@grp_payment_decisioned_ov0", "@grp_typo", 1),
    }
    for label, broken in corruptions.items():
        assert broken != good, f"the {label} corruption did not change the SQL"
        with pytest.raises((SystemExit, dialect.DialectError)):
            adapter.run_query("proj", broken, params)


# ---------------------------------------------------------------------------
# 2. Terminal-branch coherence
# ---------------------------------------------------------------------------


def test_branches_sum_to_carts_created_in_the_same_settled_window(monkeypatch, capsys):
    """The branches decompose the funnel or they decompose nothing.

    Exclusive and exhaustive by construction, gated to the same deepest cohort as the
    stage query — so equality is required, not approximate. A mismatch means the two
    queries disagree about the window, or the view left rows unclassified.
    """
    out = run(funnel_dropoff, WINDOW, monkeypatch, capsys)
    assert out["branch_coherent"] is True
    g = out["groups"][0]
    c = g["branch_coherence"]
    assert c["coherent"] is True
    assert c["difference"] == 0
    assert c["branch_total"] == c["carts_in_deep_window"] == g["deep_started"]
    assert g["deep_started"] > 0, "an empty settled window would make this vacuous"
    assert sum(b["count"] for b in g["branches"]) == g["deep_started"]


def test_branch_coherence_holds_per_group_when_split(monkeypatch, capsys):
    """Grouping must not break the decomposition: each group's branches sum to that
    group's own carts. This is where a mis-joined group key would show up — the
    branch query and the stage query index by group independently."""
    out = run(funnel_dropoff, WINDOW + ["--group-by", "channel"], monkeypatch, capsys)
    assert len(out["groups"]) == 4
    for g in out["groups"]:
        c = g["branch_coherence"]
        assert c["coherent"], f"{g['group']}: branches sum to {c['branch_total']} vs {c['carts_in_deep_window']}"
        assert g["deep_started"] > 0


def test_branch_shares_are_shares_of_carts_and_sum_to_one(monkeypatch, capsys):
    out = run(funnel_dropoff, WINDOW, monkeypatch, capsys)
    g = out["groups"][0]
    total_share = sum(b["pct_of_started"] for b in g["branches"])
    assert total_share == pytest.approx(1.0)


def test_incoherence_is_surfaced_as_a_warning_not_a_footnote():
    """The reporting side of the check. A caller who ignores `branch_coherent` still
    gets told, because the shares are unusable rather than merely suspect."""
    branches = [("a", 10), ("b", 5)]
    c = funnel_dropoff.check_branch_coherence(branches, 20)
    assert c["coherent"] is False
    assert c["difference"] == -5
    assert "must be equal" in c["basis"]


def test_an_unlisted_branch_is_appended_rather_than_dropped(spec):
    """Silently discarding a branch the spec does not list would break coherence in
    the one case where the spec and the view have actually diverged — which is the
    case worth surfacing."""
    counts = {spec["terminal_branches"][0]: 3, "99 Something New": 2}
    ordered = funnel_dropoff.order_branches(spec, counts)
    labels = [b for b, _n in ordered]
    assert "99 Something New (other)" in labels
    assert sum(n for _b, n in ordered) == 5


# ---------------------------------------------------------------------------
# 3. funnel-pulse and the refusing cutoff resolver
# ---------------------------------------------------------------------------


@pytest.fixture
def uncalibrated_spec(tmp_path, spec):
    """The shipped spec with one stage's calibrated lag removed."""
    broken = yaml.safe_load(SPEC_PATH.read_text())
    del broken["stage_maturity"]["lag_days"]["shipped"]
    p = tmp_path / "uncalibrated-spec.yaml"
    p.write_text(yaml.safe_dump(broken))
    return p


def test_pulse_refuses_when_a_stage_has_no_calibrated_lag(monkeypatch, uncalibrated_spec):
    """Risk #2, pinned. The convenient resolver (`cutoff_for_stage`) returns None for
    an uncalibrated stage; using it here would produce an ungated window and a monitor
    that reports the tail arriving as a change, silently. The refusing resolver is
    used instead, and this is the test that keeps it that way."""
    monkeypatch.setenv("KATTFOT_FUNNEL_SPEC", str(uncalibrated_spec))
    exc = refuse(funnel_pulse, ["--by", "channel", "--today", TODAY], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


def test_the_refusal_names_the_stage_and_the_fix(monkeypatch, uncalibrated_spec, capsys):
    monkeypatch.setenv("KATTFOT_FUNNEL_SPEC", str(uncalibrated_spec))
    refuse(funnel_pulse, ["--by", "channel", "--today", TODAY], monkeypatch)
    err = capsys.readouterr().err
    assert "shipped" in err
    assert "stage_maturity.lag_days" in err


def test_the_refusal_fires_in_flag_mode_too(monkeypatch, uncalibrated_spec):
    """`--maturity flag` narrows a known-good cutoff set; it does not skip resolving
    one. If the refusal only fired in stage mode, `--maturity flag` would be a way to
    run an uncalibrated monitor without noticing."""
    monkeypatch.setenv("KATTFOT_FUNNEL_SPEC", str(uncalibrated_spec))
    exc = refuse(
        funnel_pulse, ["--by", "channel", "--maturity", "flag", "--today", TODAY], monkeypatch
    )
    assert exc.code == spine.REFUSAL_EXIT


def test_pulse_declines_to_run_ungated_and_says_so(monkeypatch, capsys):
    """`--no-mature` is not honoured for a cohort monitor: two unsettled windows
    compared against each other measure the tail arriving. It narrows to `flag` and
    announces the substitution rather than doing what was asked."""
    out = run(funnel_pulse, ["--by", "channel", "--no-mature", "--today", TODAY],
              monkeypatch, capsys)
    assert out["maturity_mode"] == "flag"
    assert any("not available for a cohort monitor" in w for w in out["warnings"])


def test_pulse_uses_a_different_window_per_stage(monkeypatch, capsys, spec):
    """The point of stage-awareness: the freshest stage must be readable weeks after
    the deepest one is still settling."""
    out = run(funnel_pulse, ["--by", "channel", "--today", TODAY], monkeypatch, capsys)
    fresh = out["windows"]["freshest"]
    deep = out["windows"]["deepest"]
    assert fresh["stage"] == spec["stages"][0]["key"]
    assert deep["stage"] == spec["stages"][-1]["key"]
    assert fresh["recent"]["end"] > deep["recent"]["end"]
    assert fresh["lag_days"] < deep["lag_days"]


def test_pulse_flag_mode_collapses_every_stage_onto_one_window(monkeypatch, capsys):
    out = run(funnel_pulse, ["--by", "channel", "--maturity", "flag", "--today", TODAY],
              monkeypatch, capsys)
    ends = {w["recent"]["end"] for w in out["maturity"]["per_stage"].values()}
    assert len(ends) == 1


def test_pulse_skips_thin_groups_instead_of_flagging_noise(monkeypatch, capsys):
    """The volume floor. A group below it is listed as skipped, not flagged — and the
    two lists must partition what was scanned."""
    out = run(funnel_pulse, ["--by", "channel", "--min-started", "100000",
                             "--today", TODAY], monkeypatch, capsys)
    assert out["qualified"] == 0
    assert out["flags"] == []
    assert len(out["skipped_low_n"]) == out["scanned"]


def test_pulse_flags_are_ranked_by_impact(monkeypatch, capsys):
    out = run(funnel_pulse, ["--by", "payment_method", "--min-started", "100",
                             "--today", TODAY], monkeypatch, capsys)
    impacts = [f["impact"] for f in out["flags"]]
    assert impacts == sorted(impacts, reverse=True)
    for f in out["flags"]:
        assert f["direction"] in {"regression", "improvement"}
        assert f["kind"] in {"stage", "branch"}


def test_pulse_branch_direction_is_read_from_the_spec(monkeypatch, capsys, spec):
    """Rising into the spec's best-listed branch is an improvement; rising into any
    other exit is a regression. Spec-driven rather than keyed on a label convention,
    so it generalises to a spine whose branches are named differently."""
    out = run(funnel_pulse, ["--by", "payment_method", "--min-started", "100",
                             "--today", TODAY], monkeypatch, capsys)
    best = spec["terminal_branches"][0]
    for f in out["flags"]:
        if f["kind"] != "branch":
            continue
        rising = f["delta_pp"] > 0
        good = f["name"] == best
        assert f["direction"] == ("improvement" if rising == good else "regression")


def test_pulse_never_writes_anywhere(monkeypatch, capsys, tmp_path):
    """It is a monitor that emits a digest, not a publisher. Asserted by running it
    with the outputs directory pointed at an empty temp dir and checking nothing
    appeared."""
    monkeypatch.setenv("KATTFOT_OUTPUTS_DIR", str(tmp_path))
    run(funnel_pulse, ["--by", "channel", "--today", TODAY], monkeypatch, capsys)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# 4. stage-timing and the percentile seam
# ---------------------------------------------------------------------------


def test_stage_timing_reports_where_the_percentile_was_computed(monkeypatch, capsys):
    """The capability difference is advertised, not hidden. Offline it is exact and
    client-side; on BigQuery it is APPROX_QUANTILES pushed down. An artifact has to be
    able to say which."""
    out = run(stage_timing, WINDOW, monkeypatch, capsys)
    assert "client-side" in out["percentile_basis"]
    assert any(w.startswith("PERCENTILE BASIS:") for w in out["warnings"])


def test_stage_timing_percentiles_are_ordered_and_have_volume(monkeypatch, capsys):
    out = run(stage_timing, WINDOW, monkeypatch, capsys)
    cells = out["groups"][0]["transitions"]
    assert len(cells) == 6
    for c in cells:
        assert c["n"] > 0, f"{c['transition']} has no population"
        p = c["percentiles"]
        assert p["p50"] <= p["p90"] <= p["p99"]
        assert c["mature_through"], "every transition must state its mature window"


def test_stage_timing_population_shrinks_down_the_funnel(monkeypatch, capsys):
    """`n` is the population that reached BOTH endpoints, so it must fall as the
    transitions get deeper. If it did not, the gating would not be doing anything."""
    out = run(stage_timing, WINDOW, monkeypatch, capsys)
    ns = [c["n"] for c in out["groups"][0]["transitions"]]
    assert ns[0] > ns[-1]


def test_stage_timing_groups_by_two_dimensions_with_separate_keys(monkeypatch, capsys):
    """The multi-key percentile shape. One row per group tuple, each dimension under
    its own key — the same `list[dict]` shape query-metric returns, so a table or a
    chart consumes it with no new unpacking. A single opaque `grp` would be undefined
    for two dimensions."""
    out = run(stage_timing, WINDOW + ["--group-by", "market,category"], monkeypatch, capsys)
    assert out["group_by"] == ["market", "category"]
    assert len(out["groups"]) == 12  # 3 markets x 4 categories
    keys = set()
    for g in out["groups"]:
        assert set(g["group"]) == {"market", "category"}
        keys.add((g["group"]["market"], g["group"]["category"]))
    assert len(keys) == 12
    assert "grp" not in out["groups"][0]["group"]


def test_stage_timing_group_totals_reconcile_with_the_ungrouped_read(monkeypatch, capsys):
    """Splitting must partition, not resample: the per-group populations for a
    transition have to sum to the ungrouped one."""
    whole = run(stage_timing, WINDOW + ["--transitions", "confirm_to_ship"],
                monkeypatch, capsys)
    split = run(stage_timing, WINDOW + ["--transitions", "confirm_to_ship",
                                        "--group-by", "market,category"],
                monkeypatch, capsys)
    total = whole["groups"][0]["transitions"][0]["n"]
    parts = sum(g["transitions"][0]["n"] for g in split["groups"])
    assert parts == total


def test_stage_timing_gates_each_transition_on_its_arrival_stage(monkeypatch, capsys):
    """A transition is withheld until where it ARRIVES has settled. Gating on
    departure would let recent cohorts, whose slow members have not arrived,
    bias the percentiles fast — which reads as an improvement."""
    out = run(stage_timing, WINDOW, monkeypatch, capsys)
    per_stage = out["maturity"]["per_stage"]
    assert per_stage["cart_to_checkout_start"]["maturity_stage"] == "checkout_started"
    assert per_stage["deliver_to_window_end"]["maturity_stage"] == "return_window_complete"
    assert (
        per_stage["cart_to_checkout_start"]["cutoff_week"]
        > per_stage["deliver_to_window_end"]["cutoff_week"]
    )


def test_stage_timing_surfaces_the_named_kpis(monkeypatch, capsys, spec):
    """A KPI is a thin alias over a transition. Surfacing it on the transition it
    measures is what stops `kattfot:catalog` and this skill describing different
    things."""
    out = run(stage_timing, WINDOW, monkeypatch, capsys)
    for kpi in spec["kpis"]:
        assert kpi["key"] in {k["key"] for k in out["kpis"][kpi["transition"]]}


def test_stage_timing_refuses_an_unknown_transition(monkeypatch):
    exc = refuse(stage_timing, WINDOW + ["--transitions", "not_a_transition"], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


@pytest.mark.parametrize("bad", ["50,101", "abc", "-1", ""])
def test_stage_timing_refuses_a_bad_percentile(monkeypatch, bad):
    exc = refuse(stage_timing, WINDOW + ["--percentiles", bad], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


# ---------------------------------------------------------------------------
# 5. segment-compare
# ---------------------------------------------------------------------------


def test_filter_compare_produces_a_delta_across_all_three_views(monkeypatch, capsys, spec):
    out = run(segment_compare, WINDOW + [
        "--filter", "channel=web",
        "--a-filter", "payment_method=card_instant",
        "--b-filter", "payment_method=bank_transfer_review",
    ], monkeypatch, capsys)
    assert out["mode"] == "filter_compare"
    assert len(out["comparison"]["stages"]) == len(spec["stages"])
    assert out["comparison"]["branches"]
    assert len(out["comparison"]["timing"]) == len(spec["transitions"])
    assert out["segment_a"]["started"] > out["segment_b"]["started"]


def test_the_slow_payment_method_is_visibly_slower(monkeypatch, capsys):
    """The generator plants a real capability difference — a bank transfer decides
    days after a card. If this stopped showing up, either the timing gate or the
    fixture would have quietly broken."""
    out = run(segment_compare, WINDOW + [
        "--a-filter", "payment_method=card_instant",
        "--b-filter", "payment_method=bank_transfer_review",
    ], monkeypatch, capsys)
    row = next(t for t in out["comparison"]["timing"] if t["transition"] == "payment_to_decision")
    assert row["delta"] > 60, "bank transfer should decide at least an hour later at p50"


def test_both_segments_share_one_cutoff_set(monkeypatch, capsys):
    """The reason this is a skill and not two runs of funnel-dropoff. If the sides
    were gated differently, the delta would partly measure the window."""
    out = run(segment_compare, WINDOW + [
        "--a-filter", "market=us", "--b-filter", "market=uk",
    ], monkeypatch, capsys)
    a = {s["key"]: s["mature_through"] for s in out["segment_a"]["stages"]}
    b = {s["key"]: s["mature_through"] for s in out["segment_b"]["stages"]}
    assert a == b


def test_pre_post_splits_the_window_without_overlap(monkeypatch, capsys):
    out = run(segment_compare, [
        "--time-range", "2026-04-06:2026-08-10", "--today", TODAY,
        "--split-date", "2026-06-15",
    ], monkeypatch, capsys)
    assert out["mode"] == "pre_post"
    assert out["segment_a"]["window"]["end"] == "2026-06-14"
    assert out["segment_b"]["window"]["start"] == "2026-06-15"
    assert any("PRE/POST MATURITY" in w for w in out["warnings"])


def test_pre_post_totals_reconcile_with_the_whole_window(monkeypatch, capsys):
    """The split must partition the carts, not resample them."""
    whole = run(funnel_dropoff, ["--time-range", "2026-04-06:2026-08-10",
                                 "--today", TODAY], monkeypatch, capsys)
    split = run(segment_compare, [
        "--time-range", "2026-04-06:2026-08-10", "--today", TODAY,
        "--split-date", "2026-06-15",
    ], monkeypatch, capsys)
    assert (
        split["segment_a"]["started"] + split["segment_b"]["started"]
        == whole["groups"][0]["started"]
    )


def test_segment_compare_refuses_both_modes_at_once(monkeypatch):
    exc = refuse(segment_compare, WINDOW + [
        "--split-date", "2026-06-15", "--a-filter", "market=us",
    ], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


def test_segment_compare_refuses_neither_mode(monkeypatch):
    exc = refuse(segment_compare, WINDOW, monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


def test_segment_compare_refuses_a_split_date_outside_the_window(monkeypatch):
    exc = refuse(segment_compare, [
        "--time-range", "2026-04-06:2026-08-10", "--today", TODAY,
        "--split-date", "2026-01-05",
    ], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


def test_segment_compare_declares_itself_descriptive(monkeypatch, capsys):
    """No significance test, no causal claim — and it must say so, because a pre/post
    table invites exactly that reading."""
    out = run(segment_compare, WINDOW + [
        "--a-filter", "market=us", "--b-filter", "market=uk",
    ], monkeypatch, capsys)
    assert any("not significance-tested" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# 6. Governance holds on every spine skill, on the offline path
# ---------------------------------------------------------------------------
# These run upstream of the adapter, so switching engines cannot relax them. The
# parametrization is over all four skills, so a new skill that forgot to validate
# would fail here rather than in review.

SKILL_CASES = [
    (funnel_dropoff, []),
    (stage_timing, []),
    (funnel_pulse, ["--by", "channel"]),
    (segment_compare, ["--a-filter", "market=us", "--b-filter", "market=uk"]),
]
SKILL_IDS = [m.__name__ for m, _ in SKILL_CASES]


@pytest.mark.parametrize("module,extra", SKILL_CASES, ids=SKILL_IDS)
def test_filter_allow_list_refusal(monkeypatch, module, extra):
    exc = refuse(module, extra + ["--today", TODAY, "--filter", "warehouse_region=eu"], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


@pytest.mark.parametrize("module,extra", SKILL_CASES, ids=SKILL_IDS)
@pytest.mark.parametrize("column", ["customer_email", "street_address", "date_of_birth", "ssn"])
def test_pii_filter_refusal(monkeypatch, module, extra, column):
    """R6, on every skill, with no override — even though none of these columns is in
    any allow-list, so the allow-list would have refused anyway. The point is the
    ORDER: PII is checked first, so a spec that mistakenly allow-listed a personal
    column has still not authorized it."""
    exc = refuse(module, extra + ["--today", TODAY, "--filter", f"{column}=x"], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


@pytest.mark.parametrize(
    "module,extra",
    [(funnel_dropoff, []), (stage_timing, []), (funnel_pulse, [])],
    ids=["funnel_dropoff", "stage_timing", "funnel_pulse"],
)
def test_dimension_allow_list_refusal(monkeypatch, module, extra):
    arg = "--by" if module is funnel_pulse else "--group-by"
    exc = refuse(module, extra + ["--today", TODAY, arg, "internal_risk_score"], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


@pytest.mark.parametrize(
    "module,extra",
    [(funnel_dropoff, []), (stage_timing, []), (funnel_pulse, [])],
    ids=["funnel_dropoff", "stage_timing", "funnel_pulse"],
)
def test_pii_dimension_refusal(monkeypatch, module, extra):
    arg = "--by" if module is funnel_pulse else "--group-by"
    exc = refuse(module, extra + ["--today", TODAY, arg, "customer_email"], monkeypatch)
    assert exc.code == spine.REFUSAL_EXIT


@pytest.mark.parametrize("module,extra", SKILL_CASES, ids=SKILL_IDS)
def test_a_hostile_filter_value_is_bound_not_interpolated(monkeypatch, capsys, module, extra):
    """The value arrives as data. Run it for real: the segment is empty, the query
    completes, and the fixture table is still there afterwards."""
    injection = "web'; DROP TABLE cart_checkout_session_spine;--"
    out = run(module, extra + ["--today", TODAY, "--filter", f"channel={injection}"],
              monkeypatch, capsys)
    assert out["skill"]
    still_there = adapters.get_adapter("sqlite").run_query(
        "proj", "SELECT COUNT(*) AS c FROM `proj.analytics.cart_checkout_session_spine`", {}
    )
    assert still_there[0]["c"] > 1000


@pytest.mark.parametrize("module,extra", SKILL_CASES, ids=SKILL_IDS)
def test_every_skill_stamps_fixture_backing_and_its_maturity_cutoff(
    monkeypatch, capsys, module, extra
):
    """Two contract requirements at once.

    `fixture_backed` is what every renderer turns into the MOCK DATA watermark, so a
    fixture number cannot pass itself off as measured. `maturity_cutoff` is what an
    artifact's frontmatter records and what `kattfot:verify` reads — omitting it makes
    verify report drift on every artifact.
    """
    out = run(module, extra + ["--today", TODAY], monkeypatch, capsys)
    assert out["engine"] == "sqlite"
    assert out["fixture_backed"] is True
    assert out["maturity_cutoff"], "no maturity cutoff recorded for the artifact"
    assert dt.date.fromisoformat(out["maturity_cutoff"])
    assert any(w.startswith("FIXTURE DATA:") for w in out["warnings"])


@pytest.mark.parametrize("module,extra", SKILL_CASES, ids=SKILL_IDS)
def test_every_skill_excludes_the_legacy_cohort_by_default(monkeypatch, capsys, module, extra):
    """Both-or-nothing in practice: the spine declares a legacy cohort, so the default
    read excludes it and says which one."""
    out = run(module, extra + ["--today", TODAY], monkeypatch, capsys)
    legacy = out["legacy"]
    assert legacy["declared"] is True
    assert legacy["excluded"] is True
    assert legacy["column"] == "checkout_version"
    assert legacy["value"] == "v1-legacy"


@pytest.mark.parametrize("module,extra", SKILL_CASES, ids=SKILL_IDS)
def test_every_skill_reports_the_illustrative_maturity_basis(monkeypatch, capsys, module, extra):
    """The lags are hand-authored. Nothing may imply otherwise, on any skill."""
    out = run(module, extra + ["--today", TODAY], monkeypatch, capsys)
    assert "illustrative" in str(out["maturity"].get("basis")).lower()
    assert out["maturity"].get("calibrated_at") is None


def test_a_declared_spine_fails_on_its_missing_view_rather_than_faking_it(monkeypatch):
    """The registry marks `browse/session` as declared and lists no skills against it.
    `skills:` is documentation plus a lint target, not a runtime gate — so a caller
    who asks anyway gets a clean error about the missing table, which is the intended
    behaviour: a clear failure on a real gap beats a silent wrong answer."""
    monkeypatch.setattr(sys, "argv", ["funnel_dropoff", "--module", "browse", "--today", TODAY])
    with pytest.raises(SystemExit) as exc:
        funnel_dropoff.main()
    assert exc.value.code != 0
    assert "browse_session_spine" in str(exc.value)
