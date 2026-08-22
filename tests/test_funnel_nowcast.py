"""funnel-nowcast: the Mack chain ladder, its funnel-specific guards, its lane.

Three layers, matching what can actually go wrong:

1. **The estimator against hand-computed values.** Mack's factors, variances,
   and MSEP have exact closed forms on a small triangle; the implementation
   must reproduce them to the digit, because an off-by-one in the recursion
   produces a plausible band around a subtly wrong center — the exact failure
   mode a governed forecast exists to avoid.
2. **The funnel-specific ports.** The ceiling cap (a cohort cannot convert
   more carts than it opened — the one place a naive actuarial port is wrong
   for funnels), the observed floor, the level-prior fallback for zero-arrival
   cohorts, and the refusals (fast stages, missing nowcast block, off-list
   dimensions).
3. **End to end on the committed fixtures**, including the backtest — which on
   this fixture book measurably undercovers its nominal interval and must SAY
   so. That warning firing is not a flaky detail; it is the Wolffram et al.
   (2023) finding reproduced on our own data, and the skill's whole honesty
   story depends on it being reported rather than absorbed.

Offline throughout — sqlite adapter over the committed spine.
"""

import datetime as dt
import json
import sys

import pytest

import funnel_nowcast as fn

TODAY = "2026-08-17"
ARGS = ["--today", TODAY, "--time-range", "last_19_weeks"]


def run(argv, monkeypatch, capsys) -> dict:
    monkeypatch.setattr(sys, "argv", ["funnel_nowcast"] + argv)
    fn.main()
    return json.loads(capsys.readouterr().out)


def refuse(argv, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["funnel_nowcast"] + argv)
    with pytest.raises(SystemExit):
        fn.main()


# ---------------------------------------------------------------------------
# 1. The estimator, against hand-computed Mack values
# ---------------------------------------------------------------------------

#: Four cohorts, J=3. Ratios chosen so every quantity below has a clean hand
#: computation (worked in the assertions' comments).
TRIANGLE = {
    "A": {1: 10, 2: 15, 3: 18},
    "B": {1: 20, 2: 30, 3: 33},
    "C": {1: 10, 2: 16},
    "D": {1: 8},
}
AGES = {"A": 3, "B": 3, "C": 2, "D": 1}


def test_factors_are_volume_weighted_over_covering_cohorts():
    f = fn.estimate_factors(TRIANGLE, AGES, j_max=3, window=None)
    # f_1 = (15+30+16)/(10+20+10); f_2 = (18+33)/(15+30) — D covers neither.
    assert f[0]["f"] == pytest.approx(61 / 40)
    assert f[0]["n_cohorts"] == 3 and f[0]["S"] == 40
    assert f[1]["f"] == pytest.approx(51 / 45)
    assert f[1]["n_cohorts"] == 2 and f[1]["S"] == 45


def test_mack_sigma2_matches_the_closed_form():
    f = fn.estimate_factors(TRIANGLE, AGES, j_max=3, window=None)
    # sigma2_1 = 1/2 * [10(1.5-f)^2 + 20(1.5-f)^2 + 10(1.6-f)^2], f = 1.525.
    assert f[0]["sigma2"] == pytest.approx(0.0375)
    # sigma2_2 = 1/1 * [15(1.2-f)^2 + 30(1.1-f)^2], f = 51/45.
    assert f[1]["sigma2"] == pytest.approx(0.1, rel=1e-6)


def test_trailing_window_drops_the_oldest_cohorts():
    f = fn.estimate_factors(TRIANGLE, AGES, j_max=3, window=2)
    # Only B and C inform f_1: (30+16)/(20+10).
    assert f[0]["f"] == pytest.approx(46 / 30)
    assert f[0]["n_cohorts"] == 2


def test_mack_projection_and_msep_match_hand_computation():
    factors = fn.estimate_factors(TRIANGLE, AGES, j_max=3, window=None)
    proj = fn.mack_project(observed=8, age=1, factors=factors, j_max=3)
    # ultimate = 8 * (61/40) * (51/45)
    assert proj["ultimate"] == pytest.approx(8 * (61 / 40) * (51 / 45))
    # MSEP: ult^2 * [ (s1/f1^2)(1/8 + 1/40) + (s2/f2^2)(1/12.2 + 1/45) ]
    acc = (0.0375 / (61 / 40) ** 2) * (1 / 8 + 1 / 40)
    acc += (0.1 / (51 / 45) ** 2) * (1 / (8 * 61 / 40) + 1 / 45)
    expected_sd = (proj["ultimate"] ** 2 * acc) ** 0.5
    assert proj["sd"] == pytest.approx(expected_sd, rel=1e-9)


def test_lognormal_quantiles_bracket_and_degenerate():
    q10 = fn.lognormal_quantile(100, 20, 10)
    q50 = fn.lognormal_quantile(100, 20, 50)
    q90 = fn.lognormal_quantile(100, 20, 90)
    assert q10 < q50 < q90
    assert q50 < 100  # a lognormal's median sits below its mean
    assert fn.lognormal_quantile(100, 0, 90) == 100


def test_dev_age_anchors_to_the_cohort_weeks_end():
    monday = dt.date(2026, 8, 3)
    # Sunday of that week is 2026-08-09; one full week later is 2026-08-16.
    assert fn.dev_age_weeks(monday, dt.date(2026, 8, 15)) == 0
    assert fn.dev_age_weeks(monday, dt.date(2026, 8, 16)) == 1
    assert fn.dev_age_weeks(monday, dt.date(2026, 8, 23)) == 2
    assert fn.dev_age_weeks(monday, dt.date(2026, 8, 1)) == 0  # never negative


# ---------------------------------------------------------------------------
# 2. Funnel-specific guards
# ---------------------------------------------------------------------------


def test_ultimate_is_capped_at_cohort_size():
    """Raw chain ladder has no ceiling — claims can develop past any bound, a
    funnel cannot. Factors here project 80 x 1.5 x 1.5 = 180 from a cohort of
    100; the row must cap and say it capped."""
    factors = [
        {"j": 1, "f": 1.5, "sigma2": 0.5, "S": 1000, "n_cohorts": 5,
         "ratio_min": 1.4, "ratio_max": 1.6},
        {"j": 2, "f": 1.5, "sigma2": 0.5, "S": 1000, "n_cohorts": 5,
         "ratio_min": 1.4, "ratio_max": 1.6},
    ]
    row = fn.nowcast_cohort(
        "2026-08-03", {1: 80}, age=1, size=100, factors=factors, j_max=3,
        quantiles=[10, 50, 90], prior=None,
    )
    assert row["capped_at_cohort_size"] is True
    assert row["ultimate"] == 100
    assert all(v <= 100 for v in row["quantiles"].values())


def test_quantiles_are_floored_at_observed():
    """Arrivals cannot un-arrive: even a wide lognormal's P10 must not fall
    below what is already on the board."""
    factors = [{"j": 1, "f": 1.05, "sigma2": 50.0, "S": 100, "n_cohorts": 5,
                "ratio_min": 0.9, "ratio_max": 1.3}]
    row = fn.nowcast_cohort(
        "2026-08-03", {1: 60}, age=1, size=1000, factors=factors, j_max=2,
        quantiles=[10, 50, 90], prior=None,
    )
    assert row["quantiles"]["p10"] >= 60


def test_zero_arrival_cohort_uses_the_level_prior_not_a_confident_zero():
    prior = {"mean": 0.45, "sd": 0.02, "n_cohorts": 10}
    row = fn.nowcast_cohort(
        "2026-08-03", {}, age=1, size=1000, factors=[], j_max=7,
        quantiles=[10, 50, 90], prior=prior,
    )
    assert row["method"] == "level_prior_too_young"
    assert row["ultimate"] == pytest.approx(450, abs=1)
    assert row["quantiles"]["p10"] < row["quantiles"]["p90"]


def test_no_prior_and_no_arrivals_is_unprojectable():
    row = fn.nowcast_cohort(
        "2026-08-03", {}, age=1, size=1000, factors=[], j_max=7,
        quantiles=[10, 50, 90], prior=None,
    )
    assert row["method"] == "unprojectable"
    assert row["ultimate"] is None


def test_level_prior_needs_at_least_two_mature_cohorts():
    assert fn.level_prior([0.45]) is None
    assert fn.level_prior([0.45, 0.47])["n_cohorts"] == 2


# ---------------------------------------------------------------------------
# 3. Refusals
# ---------------------------------------------------------------------------


def test_fast_settling_stage_is_refused(monkeypatch):
    """A now-cast of a stage that settles in a day is a worse way to read a
    governed actual; the spec's nowcast.stages allow-list is load-bearing."""
    refuse(ARGS + ["--stage", "checkout_started"], monkeypatch)


def test_missing_nowcast_block_refuses():
    with pytest.raises(SystemExit):
        fn.resolve_target({"source_view": "v", "transitions": []}, None)


def test_off_list_dimension_refuses(monkeypatch):
    refuse(ARGS + ["--group-by", "customer_email"], monkeypatch)


def test_development_horizon_derives_from_the_calibrated_lag():
    spec = {
        "stage_maturity": {"lag_days": {"delivered": 21}},
        "nowcast": {"stages": ["delivered"]},
    }
    # ceil((21+1)/7) = 4 whole weeks.
    assert fn.development_weeks(spec, "delivered", spec["nowcast"]) == 4


# ---------------------------------------------------------------------------
# 4. End to end on the fixtures
# ---------------------------------------------------------------------------


def test_nowcast_runs_forecast_lane_end_to_end(monkeypatch, capsys):
    out = run(ARGS, monkeypatch, capsys)
    assert out["lane"] == "FORECAST"
    assert out["accuracy_tier"] == "FORECAST"  # the lane IS the tier
    assert out["target_stage"] == "return_window_complete"
    g = out["groups"][0]
    mature = [c for c in g["cohorts"] if c["mature"]]
    projected = [c for c in g["cohorts"] if not c["mature"] and c.get("quantiles")]
    assert mature and projected
    for c in projected:
        q = c["quantiles"]
        assert q["p10"] <= q["p50"] <= q["p90"]
        assert c["observed"] <= c["ultimate"] <= c["cohort_size"]
    # The two projection paths both occur on this book: recent cohorts inside
    # the return window's pure delay have zero arrivals (level prior), while
    # cohorts a few weeks older project by chain ladder.
    methods = {c["method"] for c in projected}
    assert {"chain_ladder", "level_prior_too_young"} <= methods
    assert any("FORECAST LANE" in w for w in out["warnings"])
    assert any("FIXTURE NOW-CAST" in w for w in out["warnings"])


def test_projections_are_consistent_with_the_mature_level(monkeypatch, capsys):
    """The fixture generator is stationary, so every projected ultimate rate
    should land near the mature cohorts' band — a projection far outside it
    would mean the estimator, not the data, invented a trend."""
    out = run(ARGS, monkeypatch, capsys)
    g = out["groups"][0]
    mature_rates = [c["observed_rate"] for c in g["cohorts"] if c["mature"]]
    lo, hi = min(mature_rates) - 0.05, max(mature_rates) + 0.05
    for c in g["cohorts"]:
        if not c["mature"] and c.get("ultimate_rate") is not None:
            assert lo <= c["ultimate_rate"] <= hi, c["cohort_week"]


def test_backtest_reports_and_flags_undercoverage(monkeypatch, capsys):
    out = run(ARGS + ["--backtest"], monkeypatch, capsys)
    bt = out["groups"][0]["backtest"]
    assert bt["nominal_coverage"] == pytest.approx(0.80)
    assert bt["replays"] > 0
    assert bt["overall_empirical_coverage"] is not None
    assert all(h["n"] > 0 for h in bt["per_horizon"])
    # On this fixture book the Mack band measurably undercovers — the
    # documented failure mode of model-based nowcast intervals. The skill must
    # surface that as a warning, not absorb it.
    assert bt["overall_empirical_coverage"] < bt["nominal_coverage"]
    assert any("INTERVALS UNDERCOVER" in w for w in out["warnings"])


def test_group_by_builds_one_triangle_per_group(monkeypatch, capsys):
    out = run(ARGS + ["--group-by", "channel"], monkeypatch, capsys)
    groups = {json.dumps(g["group"]) for g in out["groups"]}
    assert len(groups) == len(out["groups"]) >= 2
    for g in out["groups"]:
        assert g["factors"], g["group"]


def test_maturity_gate_is_off_by_design_and_recorded(monkeypatch, capsys):
    out = run(ARGS, monkeypatch, capsys)
    assert out["maturity_mode"] == "off"
    assert out["maturity_cutoff"] is None
