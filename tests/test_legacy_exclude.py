"""The legacy-cohort exclusion contract: both keys, or neither.

A funnel spec may declare one retired cohort that every default read excludes — an
old checkout version, a sunset channel. In the private original this was a single
key. It is now a **pair**, `legacy_exclude_column` + `legacy_exclude_value`, and BOTH
must be present or the clause is omitted entirely.

The reason is the failure mode of the alternative. With only a column, the natural
implementation binds a missing value and emits `col != @legacy_excluded` with
`@legacy_excluded = NULL`. In SQL, `col != NULL` evaluates to NULL — never TRUE — so
the predicate excludes **every row**, not none of them. Every count comes back zero
or empty, the query succeeds, and nothing anywhere says why. A funnel that reports
zero carts looks like a pipeline outage rather than a spec typo.

This matters beyond the mechanics: a spec ported from the original's schema would
carry the single old key, silently stop excluding anything, and the failure would be
invisible in the output. That is the specific trap this file exists to hold shut, and
Phase 3's specs must respect the same pair.
"""

import datetime as dt

import pytest

from _shared import adapters, spine

START, END = dt.date(2025, 2, 17), dt.date(2026, 8, 10)


def _spec(**overrides) -> dict:
    base = {
        "cohort_column": "cohort_week",
        "source_project": "proj",
        "source_view": "analytics.cart_checkout_funnel_weekly",
        "allowed_dimensions": ["channel", "checkout_version"],
        "allowed_filters": ["channel", "checkout_version"],
        "string_cast": [],
        "maturity_flag": "is_mature_45d",
    }
    base.update(overrides)
    return base


def _where(spec, *, include_legacy=False):
    return spine.build_where(
        spec, {}, START, END, maturity_mode="off", include_legacy=include_legacy
    )


# --- both present: the clause is emitted and bound ---------------------------


def test_both_keys_present_emits_a_bound_exclusion():
    where, params = _where(
        _spec(legacy_exclude_column="checkout_version", legacy_exclude_value="v1-legacy")
    )
    assert "checkout_version != @legacy_excluded" in where
    assert params["legacy_excluded"] == "v1-legacy"
    # The value is bound, never interpolated.
    assert "v1-legacy" not in where


def test_the_excluded_value_is_cast_when_the_column_needs_it():
    where, _ = _where(
        _spec(
            legacy_exclude_column="is_first_order",
            legacy_exclude_value="true",
            string_cast=["is_first_order"],
        )
    )
    assert "CAST(is_first_order AS STRING) != @legacy_excluded" in where


# --- either key missing: the clause is omitted entirely ----------------------


def test_column_without_value_omits_the_clause():
    """The dangerous half. Binding a missing value would emit `col != NULL`, which is
    NULL rather than TRUE and would zero every row silently."""
    where, params = _where(_spec(legacy_exclude_column="checkout_version"))
    assert "legacy_excluded" not in where
    assert "legacy_excluded" not in params
    assert "!=" not in where


def test_value_without_column_omits_the_clause():
    where, params = _where(_spec(legacy_exclude_value="v1-legacy"))
    assert "legacy_excluded" not in where
    assert "legacy_excluded" not in params


def test_neither_key_omits_the_clause():
    """A spine with no retired cohort simply leaves both keys out. That is the normal
    case, not a degraded one."""
    where, params = _where(_spec())
    assert "legacy_excluded" not in where
    assert "legacy_excluded" not in params


def test_an_explicitly_null_value_omits_the_clause():
    """A spec written as `legacy_exclude_value:` with nothing after it parses to None.
    Treated the same as absent, because the SQL hazard is identical."""
    where, params = _where(
        _spec(legacy_exclude_column="checkout_version", legacy_exclude_value=None)
    )
    assert "legacy_excluded" not in where
    assert "legacy_excluded" not in params


def test_the_old_single_key_name_has_no_effect():
    """A spec ported from the original's schema carries `legacy_flow_profile` and no
    pair. It must not accidentally work, and it must not accidentally zero the result
    either — it simply does nothing, and the reader sees a full-book number.

    This is the exact silent regression the contract change created, so it is asserted
    directly rather than left implied by the tests above.
    """
    where, params = _where(_spec(legacy_flow_profile="Some Legacy Cohort"))
    assert "legacy_excluded" not in where
    assert "legacy_excluded" not in params
    assert "Some Legacy Cohort" not in where


# --- include_legacy overrides the exclusion ---------------------------------


def test_include_legacy_skips_a_fully_configured_exclusion():
    where, params = _where(
        _spec(legacy_exclude_column="checkout_version", legacy_exclude_value="v1-legacy"),
        include_legacy=True,
    )
    assert "legacy_excluded" not in where
    assert "legacy_excluded" not in params


# --- the live spec ----------------------------------------------------------


def test_the_shipped_spec_uses_the_pair_not_the_old_key():
    spec = spine.load_spec()
    assert spec.get("legacy_exclude_column"), "the shipped spec should declare the column"
    assert spec.get("legacy_exclude_value") is not None, "and the value, or neither"
    assert "legacy_flow_profile" not in spec, (
        "the single-key form is the pre-change schema and must not reappear — it would "
        "silently stop excluding anything"
    )


def test_the_shipped_exclusion_names_a_real_column_and_a_real_value():
    """A correctly-paired exclusion that names a value no row carries excludes nothing
    while looking completely normal. Checked against the committed fixtures, so a typo
    in either key fails here rather than producing a quietly wider number."""
    spec = spine.load_spec()
    col = spec["legacy_exclude_column"]
    val = spec["legacy_exclude_value"]
    adapter = adapters.get_adapter("sqlite")

    rows = adapter.run_query(
        "proj",
        f"SELECT {col} AS v, SUM(carts_created) AS n "
        f"FROM `proj.analytics.cart_checkout_funnel_weekly` GROUP BY {col}",
        {},
    )
    values = {r["v"]: r["n"] for r in rows}
    assert val in values, (
        f"legacy_exclude_value {val!r} matches no value in {col} "
        f"(present: {sorted(values)}) — the exclusion would silently do nothing"
    )
    assert values[val] > 0
    assert len(values) > 1, "excluding the only value present would zero the book"


def test_the_exclusion_actually_narrows_the_result_end_to_end():
    """The full loop: build the WHERE with the exclusion on, run it against the
    fixtures, and confirm it removed rows without removing all of them. This is the
    assertion that would have caught the `col != NULL` failure — that bug returns zero,
    which is neither 'unchanged' nor 'narrowed'."""
    spec = spine.load_spec()
    adapter = adapters.get_adapter("sqlite")
    view = f"`proj.{spec['source_view'].split('.')[0]}.cart_checkout_funnel_weekly`"
    # The weekly aggregate carries the same legacy column as the spine, so the same
    # exclusion is exercisable here.
    weekly_spec = _spec(
        legacy_exclude_column=spec["legacy_exclude_column"],
        legacy_exclude_value=spec["legacy_exclude_value"],
    )

    def total(include_legacy):
        where, params = spine.build_where(
            weekly_spec, {}, START, END, maturity_mode="off", include_legacy=include_legacy
        )
        sql = f"SELECT SUM(carts_created) AS n FROM {view}\n{where}"
        return adapter.run_query("proj", sql, params)[0]["n"]

    with_legacy = total(include_legacy=True)
    without_legacy = total(include_legacy=False)

    assert with_legacy > 0
    assert 0 < without_legacy < with_legacy, (
        "the exclusion must remove some rows and not all of them — zero here is the "
        "signature of the `col != NULL` bug this contract change prevents"
    )
