"""Per-metric maturity: the stage-aware cohort cutoff contract.

The bug this guards against: a single global maturity cutoff over-censors
early-funnel metrics. A cohort that has fully settled its cart-to-checkout-start
outcome (minutes) must not be withheld for the six weeks a return-window outcome
needs. Withholding six weeks of readable data "to be safe" is not conservatism, it
is a different wrong answer.

Pins (1) the cutoff arithmetic, (2) resolution from the spec's stage table, (3) that
the SQL predicate appears only when a lag resolves, (4) that every metric in a
stage-maturity-governed module maps to a covered stage, and (5) that the shipped
lag table keeps declaring itself illustrative rather than measured.

Point (5) matters more than it looks. The entire value of this machinery is that the
lags were *observed*; a hand-picked lag is an assumption wearing a measurement's
clothes. Letting the flags quietly disappear would turn honest illustration into a
false claim, so they are held in place by a test.
"""

import datetime as dt
from pathlib import Path

import pytest
import yaml

import kattfot_query as aq
from _shared import spine

ROOT = Path(__file__).resolve().parents[1]
METRICS_DIR = ROOT / "plugins" / "kattfot" / "skills" / "query-metric" / "metrics"
CONFIG_DIR = ROOT / "plugins" / "kattfot" / "config"
SPEC = CONFIG_DIR / "checkout-funnel-spec.yaml"


def _stage_table():
    return (yaml.safe_load(SPEC.read_text()) or {})["stage_maturity"]


def _resolved_spec_for_metric(fm: dict) -> dict:
    """The funnel spec that governs this metric's maturity, resolved exactly as the
    runtime does.

    Resolution is per-METRIC rather than per-module, because a module can register
    several spines at different grains and a metric must be gated by the lag table of
    the one that owns its ladder. `spine_for_entity()` matches the metric's `entity:`
    against each spine's `primary_entity`; when nothing matches it falls back to the
    module's default spine.

    That fallback carries real weight in this catalog, and it is worth spelling out
    because it differs from what a per-`primary_entity` model would do. Metrics here
    declare the entity their numerator genuinely measures — `payment` for the approval
    rate, `shipment` for the delivery rate — none of which is any spine's
    `primary_entity` (that is `checkout_session`). They are *related* entities inside
    the same ladder, served by the same pre-aggregated view derived from the same
    spine, so falling back to the default spine's lag table is correct rather than a
    near-miss.

    A stricter reading — "a metric whose entity is no spine's primary entity is not
    governed, skip it" — would silently stop checking ten of the fifteen metrics in
    this catalog. `test_metric_entity_belongs_to_the_resolved_spine` below is the
    check that keeps the fallback honest: the metric's entity has to be one the spec
    actually declares, so a genuinely misfiled metric still fails.
    """
    resolved = aq.funnel_spec_path(fm).resolve()
    assert resolved.exists(), f"{fm.get('name')!r}: resolved spec {resolved} is missing"
    return yaml.safe_load(resolved.read_text()) or {}


def _stage_table_for_metric(fm: dict) -> dict:
    return (_resolved_spec_for_metric(fm).get("stage_maturity") or {}).get("lag_days") or {}


def _all_metrics():
    out = []
    for p in sorted(METRICS_DIR.rglob("*.md")):
        m = aq.FRONTMATTER_RE.match(p.read_text())
        if not m:
            continue
        fm = yaml.safe_load(m.group(1)) or {}
        if fm.get("name"):
            out.append((p, fm))
    return out


# --- cutoff arithmetic ------------------------------------------------------


@pytest.mark.parametrize(
    "today", [dt.date(2026, 8, 17), dt.date(2026, 1, 1), dt.date(2025, 9, 30)]
)
@pytest.mark.parametrize("lag", [0, 2, 7, 14, 44, 175])
def test_cutoff_is_a_fully_matured_monday(today, lag):
    cutoff = aq.maturity_cutoff_monday(today, lag)
    assert cutoff.weekday() == 0                       # always an ISO Monday
    # The LATEST cart in the cutoff week (cutoff+6d) is at least `lag` days old, so
    # no partially-counted boundary week can drag a governed weekly rate down.
    assert cutoff + dt.timedelta(days=6) <= today - dt.timedelta(days=lag)


def test_larger_lag_never_yields_a_fresher_cutoff():
    today = dt.date(2026, 8, 17)
    cutoffs = [aq.maturity_cutoff_monday(today, lag) for lag in (0, 7, 14, 44, 175)]
    assert cutoffs == sorted(cutoffs, reverse=True)


def test_query_metric_and_spine_cutoffs_agree():
    """Two implementations of the same rule exist (the query helper's and the spine's)
    because they are reached by different call paths. If they diverged, an early-stage
    ratio metric and a spine drop-off would silently disagree about which weeks are
    readable."""
    for lag in (0, 1, 2, 3, 7, 14, 44):
        today = dt.date(2026, 8, 17)
        assert aq.maturity_cutoff_monday(today, lag) == spine.maturity_cutoff_monday(today, lag)


# --- resolution from the spec ------------------------------------------------


def test_resolve_from_stage_table():
    metric = aq.load_metric("cart_to_checkout_start_conversion")
    mat = aq.resolve_maturity(metric, dt.date(2026, 8, 17))
    assert mat is not None
    assert mat["stage"] == "checkout_started"
    assert isinstance(mat["lag_days"], int)
    assert mat["cutoff_week"]


def test_explicit_lag_overrides_the_stage_table():
    metric = {"name": "m", "maturity_stage": "checkout_started", "maturity_lag_days": 123}
    mat = aq.resolve_maturity(metric, dt.date(2026, 8, 17))
    assert mat["lag_days"] == 123
    assert mat["source"] == "metric.maturity_lag_days"


def test_no_stage_means_no_gate():
    assert aq.resolve_maturity({"name": "m"}, dt.date(2026, 8, 17)) is None


def test_unknown_stage_is_a_hard_error():
    """A metric declaring a stage with no lag is a governance gap a reviewer must see,
    not something to no-op past. The alternative — falling back to no gate — would
    report an unsettled cohort as settled."""
    metric = {"name": "m", "maturity_stage": "not_a_real_stage"}
    with pytest.raises(SystemExit) as exc:
        aq.resolve_maturity(metric, dt.date(2026, 8, 17))
    assert "not_a_real_stage" in str(exc.value)


def test_early_stage_matures_sooner_than_late_stage():
    """The whole point of per-stage maturity: an early stage settles faster than the
    full funnel, so gating everything on the slowest one throws away readable data."""
    table = _stage_table()["lag_days"]
    assert table["checkout_started"] < table["return_window_complete"]
    assert table["payment_decisioned"] < table["shipped"] < table["delivered"]
    assert table["delivered"] < table["return_window_complete"]


def test_return_window_lag_accounts_for_delivery_plus_the_window():
    """The 30-day return window starts at DELIVERY, not at the order date, so the
    deepest stage's lag has to be the delivery tail PLUS the window — not 30."""
    table = _stage_table()["lag_days"]
    assert table["return_window_complete"] >= table["delivered"] + 30


def test_the_open_anchor_is_lag_zero():
    assert _stage_table()["lag_days"]["created"] == 0


# --- honesty about where the lags came from ---------------------------------


def test_shipped_lag_table_declares_itself_illustrative():
    """The template's lags are hand-authored, and the spec says so in machine-readable
    form. This test exists so the flags cannot quietly disappear: the machinery's
    entire value is that the numbers were measured, and implying measurement where
    there was none would be the single most dishonest thing this repo could do.

    A real deployment REMOVES these flags after generating the block from observed
    settle time — at which point this test should be updated deliberately, not
    deleted quietly.
    """
    table = _stage_table()
    assert table.get("illustrative") is True
    assert table.get("calibrated_at") is None
    assert table.get("percentile") is None
    assert "illustrative" in str(table.get("basis", "")).lower()


def test_the_illustrative_flag_reaches_the_artifact():
    """It is not enough for the spec to be honest — the flag has to travel into the
    output, or a reader of an artifact never sees it."""
    metric = aq.load_metric("delivered_to_return_window_complete_rate")
    mat = aq.resolve_maturity(metric, dt.date(2026, 8, 17))
    assert mat["illustrative"] is True


# --- per-group overrides ----------------------------------------------------


def test_the_slow_payment_method_has_a_longer_lag_than_the_default():
    """The override machinery's live case: book-wide `payment_decisioned` is set by the
    instant-card majority (~82% of volume), which is far too short for a method routed
    through a clearing cycle and a review queue. Without the override, a recent
    cohort's bank-transfer payments read as undecided when they are merely
    unanswered-so-far."""
    spec = yaml.safe_load(SPEC.read_text())
    cfg = spine.stage_lag_overrides(spec, "payment_decisioned")
    assert cfg is not None
    assert cfg["by"] == "payment_method"
    assert "bank_transfer_review" in cfg["values"]
    default_lag = spine.stage_lag_days(spec, "payment_decisioned")
    assert cfg["values"]["bank_transfer_review"] > default_lag * 2


def test_stages_without_an_override_get_none():
    spec = yaml.safe_load(SPEC.read_text())
    assert spine.stage_lag_overrides(spec, "shipped") is None
    assert spine.stage_lag_overrides(spec, "not_a_stage") is None


# --- SQL wiring -------------------------------------------------------------


def test_build_sql_adds_the_predicate_only_with_a_cutoff():
    metric = {
        "name": "m", "source_view": "ds.v", "time_column": "cohort_week",
        "numerator_column": "num", "denominator_column": "den",
    }
    sql_no, _ = aq.build_sql(metric, "proj", [], {})
    assert "@maturity_cutoff" not in sql_no

    sql_yes, params = aq.build_sql(metric, "proj", [], {}, dt.date(2026, 6, 1))
    assert "cohort_week <= @maturity_cutoff" in sql_yes
    assert params == {}  # bound as a DATE by the executor, not a STRING filter


def test_the_cutoff_actually_withholds_rows_on_the_fixture_path():
    """End to end: a deep-stage metric read over a recent window must return FEWER
    cohort weeks than the same window ungated. A cutoff that computed correctly and
    then failed to reach the query would pass every test above."""
    from _shared import adapters

    metric = aq.load_metric("delivered_to_return_window_complete_rate")
    start, end = dt.date(2026, 5, 4), dt.date(2026, 8, 10)
    cutoff = dt.date.fromisoformat(
        aq.resolve_maturity(metric, dt.date(2026, 8, 17))["cutoff_week"]
    )
    adapter = adapters.get_adapter("sqlite")

    sql_gated, _ = aq.build_sql(metric, "proj", [], {}, cutoff)
    gated = adapter.run_query(
        "proj", sql_gated,
        {"start_date": start, "end_date": end, "maturity_cutoff": cutoff},
    )
    sql_open, _ = aq.build_sql(metric, "proj", [], {}, None)
    ungated = adapter.run_query("proj", sql_open, {"start_date": start, "end_date": end})

    assert 0 < len(gated) < len(ungated)
    assert all(dt.date.fromisoformat(r["cohort_week"]) <= cutoff for r in gated)


def test_the_withheld_cohorts_are_the_ones_that_would_have_lied():
    """The reason the cutoff matters, demonstrated on the committed fixtures: the
    cohorts it withholds genuinely have a near-zero rate because nothing has settled
    yet, not because customers stopped keeping their orders."""
    from _shared import adapters

    metric = aq.load_metric("delivered_to_return_window_complete_rate")
    cutoff = dt.date.fromisoformat(
        aq.resolve_maturity(metric, dt.date(2026, 8, 17))["cutoff_week"]
    )
    sql, _ = aq.build_sql(metric, "proj", [], {}, None)
    rows = adapters.get_adapter("sqlite").run_query(
        "proj", sql, {"start_date": dt.date(2026, 5, 4), "end_date": dt.date(2026, 8, 10)}
    )
    withheld = [r for r in rows if dt.date.fromisoformat(r["cohort_week"]) > cutoff]
    kept = [r for r in rows if dt.date.fromisoformat(r["cohort_week"]) <= cutoff]
    assert withheld and kept, "the fixture window must straddle the cutoff"

    mean_kept = sum(r["numerator"] for r in kept) / sum(r["denominator"] for r in kept)
    mean_withheld = (
        sum(r["numerator"] for r in withheld) / sum(r["denominator"] for r in withheld)
    )
    assert mean_kept > 0.5, "settled cohorts should show a real kept rate"
    assert mean_withheld < mean_kept / 2, (
        "the withheld cohorts should be visibly unsettled — if they are not, the "
        "fixtures have lost their maturation tail and this whole mechanism is being "
        "tested against data that does not exercise it"
    )


# --- catalog coverage -------------------------------------------------------

_ALL_METRICS = _all_metrics()
_ALL_METRIC_IDS = [p.relative_to(METRICS_DIR).as_posix() for p, _ in _ALL_METRICS]


@pytest.mark.parametrize("path,fm", _ALL_METRICS, ids=_ALL_METRIC_IDS)
def test_metric_maturity_stage_resolves_when_the_module_is_governed(path, fm):
    table = _stage_table_for_metric(fm)
    assert table, (
        f"{path.name}: module {fm.get('module')!r} / entity {fm.get('entity')!r} "
        f"resolved no stage_maturity table. Every metric in this catalog is on a "
        f"governed spine, so an empty table means the registry lookup broke."
    )
    stage = fm.get("maturity_stage")
    assert stage, f"{path.name}: no maturity_stage on a stage-maturity-governed module"
    assert stage in table, (
        f"{path.name}: stage {stage!r} has no lag in {fm.get('module')}'s spec "
        f"(available: {sorted(table)})"
    )


@pytest.mark.parametrize("path,fm", _ALL_METRICS, ids=_ALL_METRIC_IDS)
def test_metric_entity_belongs_to_the_resolved_spine(path, fm):
    """What keeps the default-spine fallback above honest.

    Because a metric's entity need not be any spine's `primary_entity`, resolution
    falls back to the module's default spine — which would happily gate a genuinely
    misfiled metric against a lag table that has nothing to do with it. The invariant
    that closes that hole is narrower and truer: the metric's entity must be one the
    resolved spec actually declares in its own `entities:` block, i.e. it must sit
    somewhere on that spine's ladder.
    """
    spec = _resolved_spec_for_metric(fm)
    declared = set(spec.get("entities") or [])
    assert declared, f"{path.name}: resolved spec declares no `entities:` block"
    assert fm.get("entity") in declared, (
        f"{path.name}: entity {fm.get('entity')!r} is not on the ladder of the spine "
        f"that governs it ({aq.funnel_spec_path(fm).name} declares {sorted(declared)}). "
        f"Either the metric is filed under the wrong module, or the spec is missing an "
        f"entity it actually traverses."
    )


@pytest.mark.parametrize("path,fm", _ALL_METRICS, ids=_ALL_METRIC_IDS)
def test_metric_and_spine_agree_on_the_cohort_column(path, fm):
    """A metric gated by a spine's lag table must be bucketed on that spine's cohort
    column, or the cutoff is being applied to the wrong axis — arithmetically fine and
    completely meaningless."""
    spec = _resolved_spec_for_metric(fm)
    assert fm["time_column"] == spec["cohort_column"], (
        f"{path.name}: metric buckets on {fm['time_column']!r} but its spine's cohort "
        f"column is {spec['cohort_column']!r}"
    )
