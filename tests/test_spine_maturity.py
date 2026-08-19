"""Stage-aware maturity for the spine-backed skills.

Pins the shared per-stage machinery in `_shared/spine.py`: the cutoff arithmetic, the
per-stage and per-group cutoff resolution, the maturity-mode resolution, the WHERE
builder, and the SQL fragments the skill scripts embed.

Scope note. The private original also asserted the assembled SQL of five spine-backed
skill scripts (`funnel_dropoff.build_stage_sql`, `stage_timing.build_sql`, and so on).
None of those scripts exists in this template yet, so those assertions are not ported —
importing a module that does not exist would be a failing test, and marking them
skipped would be worse: a governance check reporting itself as skipped is
indistinguishable from one nobody wrote.

What IS covered is everything those scripts consume — `gate()`, `deep_gate()`,
`windowed_count_expr()`, `resolve_stage_cutoffs()`, `build_where()` — exercised
directly against the live spec and against a synthetic one. The fragments those
builders return are the actual contract; a Phase-3 skill script that embeds them
correctly inherits this coverage, and `tests/test_dialect.py`'s
`test_spine_stage_sql_golden` pins the shape of the SQL they compose into.

Offline throughout: `spine.run_query` reaches an adapter, and nothing here calls it.
"""

import datetime as dt

import pytest

from _shared import spine

TODAY = dt.date(2026, 8, 17)


@pytest.fixture(scope="module")
def spec():
    return spine.load_spec()


# --- cutoff arithmetic (parity with the query helper) ------------------------


@pytest.mark.parametrize("lag", [0, 1, 2, 3, 7, 14, 44, 175])
def test_cutoff_is_a_fully_matured_monday(lag):
    cut = spine.maturity_cutoff_monday(TODAY, lag)
    assert cut.weekday() == 0
    assert cut + dt.timedelta(days=6) <= TODAY - dt.timedelta(days=lag)


def test_created_is_lag_zero(spec):
    assert spine.stage_lag_days(spec, "created") == 0


def test_stage_lags_match_the_table(spec):
    lags = spec["stage_maturity"]["lag_days"]
    assert spine.stage_lag_days(spec, "checkout_started") == lags["checkout_started"]
    assert spine.stage_lag_days(spec, "return_window_complete") == lags["return_window_complete"]
    # An early stage settles strictly sooner than a deep one.
    assert spine.stage_lag_days(spec, "checkout_started") < spine.stage_lag_days(
        spec, "return_window_complete"
    )


def test_unknown_stage_lag_is_none(spec):
    assert spine.stage_lag_days(spec, "not_a_stage") is None


def test_none_stage_is_none_not_zero(spec):
    """`None` means "no stage declared", which is different from a declared stage with
    a zero lag. Collapsing the two would silently give an ungated stage the freshest
    possible cutoff."""
    assert spine.stage_lag_days(spec, None) is None


def test_cutoff_for_stage_returns_none_for_an_unknown_stage(spec):
    assert spine.cutoff_for_stage(spec, "not_a_stage", TODAY) is None
    assert spine.cutoff_for_stage(spec, "shipped", TODAY) is not None


# --- resolve_stage_cutoffs ---------------------------------------------------


def test_resolve_stage_mode_caps_each_stage(spec):
    cutoffs, params, meta = spine.resolve_stage_cutoffs(spec, spec["stages"], TODAY, mode="stage")
    assert meta["mode"] == "stage"
    # Every stage resolves; deeper stages are gated to older cohorts.
    assert cutoffs["checkout_started"] > cutoffs["return_window_complete"]
    assert params["cut_checkout_started"] == cutoffs["checkout_started"]
    # The deep cohort is the oldest (most conservative) cutoff across the ladder.
    assert meta["deep_cohort_through"] == cutoffs["return_window_complete"].isoformat()
    assert params["cut_deep"] == cutoffs["return_window_complete"]


def test_resolve_stage_mode_reports_the_illustrative_basis(spec):
    """The metadata a skill puts in its artifact. `basis` has to carry through, or an
    artifact cannot say whether the lags were measured."""
    _, _, meta = spine.resolve_stage_cutoffs(spec, spec["stages"], TODAY, mode="stage")
    assert "illustrative" in str(meta["basis"]).lower()
    assert meta["calibrated_at"] is None
    assert set(meta["per_stage"]) == {s["key"] for s in spec["stages"]}
    for key, info in meta["per_stage"].items():
        assert info["lag_days"] >= 0
        assert info["cutoff_week"]


@pytest.mark.parametrize("mode", ["flag", "off"])
def test_flag_and_off_modes_add_no_cuts(spec, mode):
    cutoffs, params, meta = spine.resolve_stage_cutoffs(spec, spec["stages"], TODAY, mode=mode)
    assert all(v is None for v in cutoffs.values())
    assert params == {}
    assert meta["deep_cohort_through"] is None


def test_resolve_unknown_stage_is_a_hard_refusal(spec):
    """A stage with no lag refuses with the governance exit code rather than silently
    running ungated. An ungated deep stage reports unsettled cohorts as settled."""
    bad = [{"key": "x", "maturity_stage": "not_a_stage"}]
    with pytest.raises(SystemExit) as e:
        spine.resolve_stage_cutoffs(spec, bad, TODAY, mode="stage")
    assert e.value.code == spine.REFUSAL_EXIT


def test_transitions_resolve_via_their_arrival_stage(spec):
    """A transition is gated on where it ARRIVES, not where it starts. Otherwise recent
    cohorts whose slow members have not yet arrived bias the percentiles downward —
    survivorship, and it looks like an improvement."""
    cutoffs, params, meta = spine.resolve_stage_cutoffs(
        spec, spec["transitions"], TODAY, mode="stage"
    )
    assert cutoffs["cart_to_checkout_start"] > cutoffs["deliver_to_window_end"]
    assert meta["per_stage"]["cart_to_checkout_start"]["maturity_stage"] == "checkout_started"
    assert meta["per_stage"]["deliver_to_window_end"]["maturity_stage"] == "return_window_complete"


def test_stage_defs_default_their_maturity_stage_to_their_key():
    """`maturity_stage` is optional and defaults to the stage's own key, so a spec that
    names its stages after its maturity stages needs no duplication."""
    synthetic = {"stage_maturity": {"lag_days": {"alpha": 5}}}
    cutoffs, params, meta = spine.resolve_stage_cutoffs(
        synthetic, [{"key": "alpha"}], TODAY, mode="stage"
    )
    assert cutoffs["alpha"] == spine.maturity_cutoff_monday(TODAY, 5)
    assert meta["per_stage"]["alpha"]["maturity_stage"] == "alpha"


# --- maturity-mode resolution ------------------------------------------------


class _Args:
    def __init__(self, **kw):
        self.no_mature = kw.get("no_mature", False)
        self.maturity = kw.get("maturity", None)


def test_mode_defaults_to_stage(spec):
    assert spine.resolve_maturity_mode(_Args(), spec) == "stage"


def test_no_mature_wins_over_everything(spec):
    assert spine.resolve_maturity_mode(_Args(no_mature=True), spec) == "off"
    assert spine.resolve_maturity_mode(_Args(no_mature=True, maturity="stage"), spec) == "off"


def test_explicit_flag_mode(spec):
    assert spine.resolve_maturity_mode(_Args(maturity="flag"), spec) == "flag"


def test_mature_only_false_defaults_to_off():
    assert spine.resolve_maturity_mode(_Args(), {"defaults": {"mature_only": False}}) == "off"


# --- the SQL fragments the skill scripts embed -------------------------------


def test_gate_emits_a_cap_only_when_there_is_one():
    cutoffs = {"shipped": dt.date(2026, 7, 6), "created": None}
    assert spine.gate("cohort_week", "shipped", cutoffs) == " AND cohort_week <= @cut_shipped"
    assert spine.gate("cohort_week", "created", cutoffs) == ""
    assert spine.gate("cohort_week", "absent_key", cutoffs) == ""


def test_deep_gate_emits_only_when_a_deep_cut_exists():
    assert spine.deep_gate("cohort_week", {"cut_deep": dt.date(2026, 7, 6)}) == (
        " AND cohort_week <= @cut_deep"
    )
    assert spine.deep_gate("cohort_week", {}) == ""


def test_windowed_count_expr_default_windows():
    recent = spine.windowed_count_expr(
        "shipped_flag", "cohort_week", "shipped", {}, None, None, "recent"
    )
    assert recent == "shipped_flag AND cohort_week >= @rs_shipped AND cohort_week <= @end_shipped"
    baseline = spine.windowed_count_expr(
        "shipped_flag", "cohort_week", "shipped", {}, None, None, "baseline"
    )
    assert baseline == "shipped_flag AND cohort_week >= @bs_shipped AND cohort_week < @rs_shipped"


def test_windowed_count_expr_uses_a_case_for_group_overrides():
    """The per-group window is a CASE keyed on the group column, with the shared window
    as the ELSE — so a group without an override is unaffected. Additive, never a
    regression on the pre-override behaviour."""
    group_windows = {
        "bank_transfer_review": {
            "end": dt.date(2026, 6, 1),
            "recent_start": dt.date(2026, 5, 4),
            "baseline_start": dt.date(2026, 3, 9),
            "lag_days": 12,
        }
    }
    sql = spine.windowed_count_expr(
        "payment_decisioned_flag", "cohort_week", "payment_decisioned", {},
        "payment_method", group_windows, "recent",
    )
    assert "CASE WHEN payment_method = @grp_payment_decisioned_ov0" in sql
    assert "ELSE (cohort_week >= @rs_payment_decisioned" in sql
    params = spine.windowed_count_params("payment_decisioned", group_windows)
    assert params["grp_payment_decisioned_ov0"] == "bank_transfer_review"
    assert params["rs_payment_decisioned_ov0"] == dt.date(2026, 5, 4)
    assert params["end_payment_decisioned_ov0"] == dt.date(2026, 6, 1)
    assert params["bs_payment_decisioned_ov0"] == dt.date(2026, 3, 9)


def test_windowed_count_params_is_empty_without_overrides():
    assert spine.windowed_count_params("shipped", None) == {}


# --- per-group cutoffs / windows --------------------------------------------


@pytest.fixture
def override_spec():
    """A synthetic spec, so the override machinery is pinned deterministically rather
    than against whatever the live spec currently declares."""
    return {
        "cohort_column": "cohort_week",
        "source_project": "proj",
        "source_view": "analytics.spine",
        "stages": [
            {"key": "created", "flag": "cart_created_flag"},
            {"key": "payment_decisioned", "flag": "payment_decisioned_flag",
             "maturity_stage": "payment_decisioned"},
            {"key": "shipped", "flag": "shipped_flag", "maturity_stage": "shipped"},
        ],
        "stage_maturity": {
            "lag_days": {"created": 0, "payment_decisioned": 2, "shipped": 7},
            "lag_days_overrides": {
                "payment_decisioned": {
                    "by": "payment_method",
                    "values": {"bank_transfer_review": 30, "wallet": 5},
                },
            },
        },
    }


def test_stage_lag_overrides_returns_none_for_an_unoverridden_stage(override_spec):
    assert spine.stage_lag_overrides(override_spec, "shipped") is None
    assert spine.stage_lag_overrides(override_spec, "not_a_stage") is None
    assert spine.stage_lag_overrides(override_spec, None) is None


def test_stage_lag_overrides_returns_the_table_for_a_matching_stage(override_spec):
    cfg = spine.stage_lag_overrides(override_spec, "payment_decisioned")
    assert cfg["by"] == "payment_method"
    assert cfg["values"]["bank_transfer_review"] == 30


def test_group_cutoffs_none_when_the_dimension_does_not_match(override_spec):
    """Grouping by a dimension the override is not keyed on falls back to the shared
    cutoff — never a KeyError, and never a silently mismatched cap."""
    assert spine.group_cutoffs_for_stage(override_spec, "payment_decisioned", "channel", TODAY) is None


def test_group_cutoffs_present_when_the_dimension_matches(override_spec):
    cuts = spine.group_cutoffs_for_stage(
        override_spec, "payment_decisioned", "payment_method", TODAY
    )
    assert set(cuts) == {"bank_transfer_review", "wallet"}
    # A 30-day lag yields a much older cutoff than a 5-day one.
    assert cuts["bank_transfer_review"] < cuts["wallet"]


def test_group_windows_shape_and_fallback(override_spec):
    wins = spine.group_windows_for_stage(
        override_spec, "payment_decisioned", "payment_method", TODAY, 4, 8
    )
    w = wins["bank_transfer_review"]
    assert w["lag_days"] == 30
    assert w["end"] + dt.timedelta(days=30 + 6) <= TODAY
    assert w["baseline_start"] < w["recent_start"] < w["end"]
    # A stage with no override at all -> None, so the caller keeps the shared window.
    assert spine.group_windows_for_stage(
        override_spec, "shipped", "payment_method", TODAY, 4, 8
    ) is None
    # The wrong dimension -> None, even though this stage HAS overrides.
    assert spine.group_windows_for_stage(
        override_spec, "payment_decisioned", "channel", TODAY, 4, 8
    ) is None


# --- the WHERE builder ------------------------------------------------------


def test_build_where_binds_the_window_and_the_filters(spec):
    where, params = spine.build_where(
        spec, {"channel": "web"}, dt.date(2026, 1, 5), dt.date(2026, 8, 10),
        maturity_mode="stage", include_legacy=True,
    )
    cohort = spec["cohort_column"]
    assert f"{cohort} BETWEEN @start_date AND @end_date" in where
    assert "channel = @filter_0" in where
    assert params["filter_0"] == "web"
    assert params["start_date"] == dt.date(2026, 1, 5)
    # Stage mode adds no whole-funnel gate here; the per-stage caps go in the SELECT.
    assert spec["maturity_flag"] not in where


def test_build_where_flag_mode_adds_the_whole_funnel_gate(spec):
    where, _ = spine.build_where(
        spec, {}, dt.date(2026, 1, 5), dt.date(2026, 8, 10),
        maturity_mode="flag", include_legacy=True,
    )
    assert spec["maturity_flag"] in where


def test_build_where_casts_a_non_string_dimension(spec):
    """A BOOL column listed in `string_cast` must be cast, because filter values bind
    as strings. Emitted as canonical BigQuery `AS STRING`; the translator maps it."""
    assert "is_first_order" in spec["string_cast"]
    where, params = spine.build_where(
        spec, {"is_first_order": "true"}, dt.date(2026, 1, 5), dt.date(2026, 8, 10),
        maturity_mode="off", include_legacy=True,
    )
    assert "CAST(is_first_order AS STRING) = @filter_0" in where


def test_build_where_never_interpolates_a_filter_value(spec):
    injection = "web'; DROP TABLE spine;--"
    where, params = spine.build_where(
        spec, {"channel": injection}, dt.date(2026, 1, 5), dt.date(2026, 8, 10),
        maturity_mode="off", include_legacy=True,
    )
    assert injection not in where
    assert params["filter_0"] == injection
