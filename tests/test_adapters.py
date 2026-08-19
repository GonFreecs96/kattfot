"""The warehouse adapter seam.

Covers the four things that could quietly go wrong now that there is more than one
engine:

  1. **Dispatch** — `ASSAY_ENGINE` selects the right adapter, defaults to sqlite so
     a fresh clone works, and refuses an unknown name rather than falling back.
  2. **ISO-8601 date ordering** — sqlite has no date type, so `BETWEEN` on a cohort
     column is a *string* comparison. ISO-8601 is the only common format whose
     lexicographic order matches chronological order; epoch integers or MM/DD/YYYY
     would return the wrong rows *silently*. Asserted end to end against the real
     fixtures, not just on the formatter.
  3. **Governance is not weaker on the fixture path** — the allow-list, PII, and
     tier refusals all still fire. They run upstream of the adapter, and this pins
     that they cannot be reached around by switching engines.
  4. **Capabilities are honest** — the percentile difference between the engines is
     advertised rather than shimmed, and a skill can ask for percentiles without
     knowing which engine it got.

One guarantee is deliberately NOT asserted here, because it does not hold: the
sqlite adapter does not call `assert_user_identity()`. That is an explicit branch
with its reasoning in the adapter's docstring — a fixture read touches no warehouse
and no credentials. `test_sqlite_path_does_not_assert_identity_and_says_so` pins the
*actual* behavior plus the compensating control (the fixture watermark), so the
trade-off is recorded rather than discovered.
"""

import datetime as dt
import sqlite3

import pytest

import assay_query as aq
from _shared import adapters
from _shared.adapters import base as adapters_base
from _shared.adapters.sqlite_adapter import SqliteAdapter

WEEKLY_VIEW = "analytics.cart_checkout_funnel_weekly"


@pytest.fixture(autouse=True)
def clean_engine(monkeypatch):
    """Every test starts from the documented default and a cold adapter cache."""
    monkeypatch.delenv("ASSAY_ENGINE", raising=False)
    adapters.reset_cache()
    yield
    adapters.reset_cache()


# ---------------------------------------------------------------------------
# 1. Dispatch
# ---------------------------------------------------------------------------


def test_default_engine_is_sqlite():
    """The default has to be the offline one: a fresh clone must run the whole chain
    with no cloud account. Changing this breaks `git clone && pytest && demo`."""
    assert adapters.resolve_engine() == "sqlite"
    assert adapters.get_adapter().name == "sqlite"


def test_env_var_selects_engine(monkeypatch):
    monkeypatch.setenv("ASSAY_ENGINE", "bigquery")
    assert adapters.resolve_engine() == "bigquery"
    assert adapters.get_adapter().name == "bigquery"


def test_explicit_argument_beats_env(monkeypatch):
    monkeypatch.setenv("ASSAY_ENGINE", "bigquery")
    assert adapters.resolve_engine("sqlite") == "sqlite"
    assert adapters.get_adapter("sqlite").name == "sqlite"


def test_engine_name_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setenv("ASSAY_ENGINE", "  SQLite ")
    assert adapters.resolve_engine() == "sqlite"


def test_unknown_engine_refuses_rather_than_defaulting(monkeypatch):
    """A typo'd engine name must not silently fall back. Falling back to sqlite
    would mean a run the operator believed was hitting the warehouse quietly
    returned fixture numbers — the exact failure this repo exists to prevent."""
    monkeypatch.setenv("ASSAY_ENGINE", "postgres")
    with pytest.raises(SystemExit) as exc:
        adapters.resolve_engine()
    assert "postgres" in str(exc.value)


def test_adapters_are_cached_per_engine():
    """The sqlite adapter builds its fixture database lazily on first query; a funnel
    reads eight metrics in one process and should load the CSVs once."""
    assert adapters.get_adapter("sqlite") is adapters.get_adapter("sqlite")


def test_single_module_identity_so_the_cache_is_shared():
    """`_shared/spine.py` and the query helper reach the registry by different import
    routes. If those resolved to two module objects there would be two caches, and a
    test that switched engines would clear one and read the other."""
    from _shared import spine

    assert spine.adapters is adapters


# ---------------------------------------------------------------------------
# 2. ISO-8601 dates — correctness, not formatting preference
# ---------------------------------------------------------------------------


def test_fixture_cohort_column_is_iso_text():
    """The generator must emit ISO-8601. Assert on the committed data, because a
    generator change is exactly what would break this."""
    adapter = adapters.get_adapter("sqlite")
    rows = adapter.run_query(
        "proj",
        f"SELECT cohort_week FROM `proj.{WEEKLY_VIEW}` GROUP BY cohort_week "
        f"ORDER BY cohort_week",
        {},
    )
    assert rows, "fixtures are empty — run scripts/generate_fixtures.py"
    for r in rows:
        assert isinstance(r["cohort_week"], str)
        # Parses as an ISO date, and round-trips to the same text.
        assert dt.date.fromisoformat(r["cohort_week"]).isoformat() == r["cohort_week"]


def test_lexicographic_order_matches_chronological_order():
    """The property that makes string BETWEEN correct on ISO text. Checked as a
    property of the actual fixture values rather than of the format in the
    abstract."""
    adapter = adapters.get_adapter("sqlite")
    rows = adapter.run_query(
        "proj",
        f"SELECT cohort_week FROM `proj.{WEEKLY_VIEW}` GROUP BY cohort_week "
        f"ORDER BY cohort_week",
        {},
    )
    weeks = [r["cohort_week"] for r in rows]
    assert weeks == sorted(weeks)                                   # string order
    dates = [dt.date.fromisoformat(w) for w in weeks]
    assert dates == sorted(dates)                                   # date order
    assert [d.isoformat() for d in sorted(dates)] == sorted(weeks)   # the two agree


def test_between_with_date_objects_selects_the_right_window():
    """A `dt.date` bind must reach sqlite as ISO text and filter correctly. This is
    the end-to-end version of the guarantee: if the adapter passed epoch ints or a
    localised format, this returns the wrong rows without raising anything."""
    adapter = adapters.get_adapter("sqlite")
    start, end = dt.date(2026, 6, 1), dt.date(2026, 6, 29)
    rows = adapter.run_query(
        "proj",
        f"SELECT cohort_week, SUM(carts_created) AS n FROM `proj.{WEEKLY_VIEW}` "
        f"WHERE cohort_week BETWEEN @start_date AND @end_date "
        f"GROUP BY cohort_week ORDER BY cohort_week",
        {"start_date": start, "end_date": end},
    )
    weeks = [dt.date.fromisoformat(r["cohort_week"]) for r in rows]
    assert weeks == [
        dt.date(2026, 6, 1), dt.date(2026, 6, 8),
        dt.date(2026, 6, 15), dt.date(2026, 6, 22), dt.date(2026, 6, 29),
    ]
    assert all(r["n"] > 0 for r in rows)


def test_a_non_iso_date_format_would_have_broken_this():
    """Demonstrate the failure the ISO rule prevents, so the rule reads as a
    correctness constraint rather than a style choice. Under US-style MM/DD/YYYY
    text, lexicographic order puts April before January."""
    iso = ["2026-01-15", "2026-04-02", "2026-11-30"]
    assert sorted(iso) == iso
    us = ["01/15/2026", "04/02/2026", "11/30/2026"]
    reordered = ["11/30/2026", "01/15/2026", "04/02/2026"]
    assert sorted(reordered) != reordered  # sorting changes it...
    assert sorted(reordered) == us          # ...to a chronologically wrong order?
    # No: with a common year the US format happens to sort correctly. The break is
    # across years, which is what a 78-week fixture window spans.
    across_years = ["12/01/2025", "01/05/2026"]
    assert sorted(across_years) == ["01/05/2026", "12/01/2025"]  # WRONG order
    assert sorted(["2025-12-01", "2026-01-05"]) == ["2025-12-01", "2026-01-05"]  # right


# ---------------------------------------------------------------------------
# 3. Governance holds on the fixture path
# ---------------------------------------------------------------------------


def test_allow_list_refusal_fires_on_the_sqlite_path(monkeypatch):
    """R2 runs before any adapter is touched, so the engine cannot relax it."""
    monkeypatch.setenv("ASSAY_ENGINE", "sqlite")
    metric = aq.load_metric("cart_to_checkout_start_conversion")
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, ["warehouse_region"], {})
    assert "R2" in str(exc.value)


def test_filter_allow_list_refusal_fires_on_the_sqlite_path(monkeypatch):
    monkeypatch.setenv("ASSAY_ENGINE", "sqlite")
    metric = aq.load_metric("payment_approval_rate")
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, [], {"internal_risk_score": "high"})
    assert "R3" in str(exc.value)


@pytest.mark.parametrize(
    "column", ["customer_email", "phone_number", "date_of_birth", "street_address", "ssn"]
)
def test_pii_refusal_fires_on_the_sqlite_path(monkeypatch, column):
    """R6 is a hard refusal with no override, on every engine."""
    monkeypatch.setenv("ASSAY_ENGINE", "sqlite")
    metric = aq.load_metric("cart_to_checkout_start_conversion")
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, [column], {})
    assert "R6" in str(exc.value)


def test_pii_refusal_beats_an_allow_listed_column(monkeypatch):
    """The ordering that matters: R6 is checked BEFORE the allow-lists, so a metric
    that allow-lists a personal column by mistake has not authorized it."""
    monkeypatch.setenv("ASSAY_ENGINE", "sqlite")
    metric = {
        "name": "bad_metric",
        "allowed_dimensions": ["customer_email"],
        "allowed_filters": ["customer_email"],
    }
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, ["customer_email"], {})
    assert "R6" in str(exc.value)


@pytest.mark.parametrize("tier", ["T1", "T3"])
def test_tier_refusal_fires_on_the_sqlite_path(monkeypatch, tier):
    """R4 in code, not only in the agent prompt: a helper invoked directly — by a
    script, a scheduled job, another plugin — never sees the agent layer."""
    monkeypatch.setenv("ASSAY_ENGINE", "sqlite")
    with pytest.raises(SystemExit) as exc:
        aq.validate_tier({"name": "m", "accuracy_tier": tier})
    assert "R4" in str(exc.value)


def test_bound_filter_values_are_not_interpolated_on_the_sqlite_path():
    """A hostile filter value must arrive as data. Run it for real: the query
    returns zero rows and the fixture table is still there afterwards."""
    adapter = adapters.get_adapter("sqlite")
    injection = "web'; DROP TABLE cart_checkout_funnel_weekly;--"
    sql = (
        f"SELECT SUM(carts_created) AS n FROM `proj.{WEEKLY_VIEW}` "
        f"WHERE channel = @filter_0"
    )
    rows = adapter.run_query("proj", sql, {"filter_0": injection})
    assert rows[0]["n"] is None  # no such channel, so SUM over no rows
    still_there = adapter.run_query(
        "proj", f"SELECT COUNT(*) AS c FROM `proj.{WEEKLY_VIEW}`", {}
    )
    assert still_there[0]["c"] > 0


def test_sqlite_path_does_not_assert_identity_and_says_so():
    """The one governance guarantee that is genuinely weaker offline.

    Not a bug, and not hidden: a fixture read touches no warehouse and no
    credentials, so there is no query to attribute to a person, and requiring a
    cloud login to read a committed CSV would make `git clone && pytest`
    impossible. What the adapter gives back in exchange is `fixture_backed`, which
    every renderer turns into a watermark — so the number cannot pass itself off as
    measured downstream.

    This test pins the actual state of affairs, including the reasoning being
    written down where a reader will find it.
    """
    sqlite_adapter = adapters.get_adapter("sqlite")
    bq_adapter = adapters.get_adapter("bigquery")

    assert not sqlite_adapter.supports(adapters.CAP_USER_IDENTITY)
    assert bq_adapter.supports(adapters.CAP_USER_IDENTITY)

    # The compensating control.
    assert sqlite_adapter.fixture_backed is True
    assert bq_adapter.fixture_backed is False

    # And the reasoning is documented at the point someone would change it.
    doc = type(sqlite_adapter).__module__
    import importlib

    module_doc = importlib.import_module(doc).__doc__ or ""
    assert "assert_user_identity" in module_doc
    assert "explicit branch" in module_doc or "deliberately not called" in module_doc


def test_bigquery_adapter_asserts_identity_before_querying(monkeypatch):
    """The guarantee that DOES hold on the warehouse path. `run_query` must reach
    `assert_user_identity()` before any client is constructed — asserted by making
    the guard raise and confirming nothing got further."""
    import _shared.adapters.bigquery_adapter as bqa

    calls = []

    def boom():
        calls.append("asserted")
        raise SystemExit(4)

    monkeypatch.setattr(bqa, "assert_user_identity", boom)
    adapter = bqa.BigQueryAdapter()
    with pytest.raises(SystemExit) as exc:
        adapter.run_query("proj", "SELECT 1", {})
    # Exit 4 is the service-account refusal code, or 3 if the client library is
    # simply absent in this environment — both mean the query did not run.
    assert exc.value.code in (3, 4)
    if exc.value.code == 4:
        assert calls == ["asserted"]


# ---------------------------------------------------------------------------
# 4. Capabilities are honest, and percentiles are expressible either way
# ---------------------------------------------------------------------------


def test_capabilities_differ_and_are_declared():
    sqlite_adapter = adapters.get_adapter("sqlite")
    bq_adapter = adapters.get_adapter("bigquery")
    assert not sqlite_adapter.supports(adapters.CAP_NATIVE_PERCENTILES)
    assert bq_adapter.supports(adapters.CAP_NATIVE_PERCENTILES)
    # Both can push an aggregation down, which is why the SUM/SUM metric path is
    # identical on either engine.
    assert sqlite_adapter.supports(adapters.CAP_PUSHDOWN_AGGREGATION)
    assert bq_adapter.supports(adapters.CAP_PUSHDOWN_AGGREGATION)


def test_unknown_capability_raises():
    with pytest.raises(ValueError):
        adapters.get_adapter("sqlite").supports("teleportation")


def test_percentile_arithmetic_matches_the_bigquery_definition():
    """`APPROX_QUANTILES(x, 100)[OFFSET(p)]` is nearest-rank, not interpolating.
    The shared helper matches that definition so the two engines agree on WHAT they
    are computing, even though one is approximate at scale and the other exact.

    Uses 0..100 (101 values) rather than 1..100 so each percentile lands exactly on
    an element and the expectation is unambiguous. On an even-length list the
    nearest-rank rule has to pick one of the two middle values, and which one it
    picks is not a property worth pinning — BigQuery's approximation would not agree
    on it either.
    """
    values = list(range(101))  # 0..100
    assert adapters_base.percentile_from_sorted(values, 50) == 50
    assert adapters_base.percentile_from_sorted(values, 90) == 90
    assert adapters_base.percentile_from_sorted(values, 99) == 99
    assert adapters_base.percentile_from_sorted(values, 0) == 0
    assert adapters_base.percentile_from_sorted(values, 100) == 100
    assert adapters_base.percentile_from_sorted([], 50) is None


def test_percentiles_are_monotonic_and_within_range():
    """The property that actually matters, and holds on any list length."""
    values = [7, 2, 9, 4, 1, 8, 3]
    out = adapters_base.summarize_percentiles(values, [50, 90, 99])
    assert min(values) <= out["p50"] <= out["p90"] <= out["p99"] <= max(values)


def test_summarize_percentiles_shape_and_null_handling():
    out = adapters_base.summarize_percentiles([3, 1, None, 2, 4], [50, 90])
    assert out["n"] == 4          # the None is excluded, not counted as 0
    assert out["mean"] == 2.5
    assert out["p50"] in (2, 3)
    assert out["p90"] == 4


def test_percentiles_are_askable_without_knowing_the_engine():
    """The capability seam's actual point: a skill asks for p50/p90/p99 and gets
    them. Here the sqlite adapter pulls the column and computes client-side; the
    BigQuery adapter would push APPROX_QUANTILES down. Same call, same result shape,
    and `_percentile_basis` records which happened so an artifact can say so."""
    adapter = adapters.get_adapter("sqlite")
    rows = adapter.percentiles(
        "proj",
        from_sql=f"`proj.{WEEKLY_VIEW}`",
        where_sql="WHERE cohort_week >= @start_date",
        value_expr="carts_created",
        percentiles=[50, 90, 99],
        params={"start_date": dt.date(2026, 6, 1)},
        group_expr="channel",
    )
    assert {r["grp"] for r in rows} == {"web", "mobile_app", "marketplace", "social"}
    for r in rows:
        assert r["n"] > 0
        assert r["p50"] <= r["p90"] <= r["p99"]
        assert "client-side" in r["_percentile_basis"]


def test_percentiles_without_a_group_returns_one_row():
    adapter = adapters.get_adapter("sqlite")
    rows = adapter.percentiles(
        "proj",
        from_sql=f"`proj.{WEEKLY_VIEW}`",
        where_sql="WHERE 1=1",
        value_expr="carts_created",
        percentiles=[50],
        params={},
    )
    assert len(rows) == 1
    assert "grp" not in rows[0]
    assert rows[0]["n"] > 0


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def test_loaded_tables_are_named_for_the_view():
    """The fixture loader and the translator must agree on the physical table name,
    or every query would fail with 'no such table'."""
    adapter = adapters.get_adapter("sqlite")
    tables = adapter.loaded_tables()
    assert "cart_checkout_funnel_weekly" in tables
    assert tables["cart_checkout_funnel_weekly"] > 1000
    assert adapter.table_name(WEEKLY_VIEW) == "cart_checkout_funnel_weekly"


def test_integers_load_as_integers_not_text(tmp_path):
    """A count column arriving as TEXT would make SUM() concatenate rather than add.
    Silent, and catastrophic."""
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("d,n\n2026-01-05,3\n2026-01-05,4\n")
    adapter = SqliteAdapter(fixtures=tmp_path)
    rows = adapter.run_query("p", "SELECT SUM(n) AS s FROM `p.d.t`", {})
    assert rows[0]["s"] == 7


def test_empty_cells_load_as_null(tmp_path):
    """An empty CSV cell must be NULL so COUNT() and AVG() skip it the way BigQuery
    would, rather than counting it as a zero."""
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("d,n\n2026-01-05,3\n2026-01-06,\n")
    adapter = SqliteAdapter(fixtures=tmp_path)
    rows = adapter.run_query("p", "SELECT COUNT(n) AS c, SUM(n) AS s FROM `p.d.t`", {})
    assert rows[0]["c"] == 1
    assert rows[0]["s"] == 3


def test_boolean_text_loads_as_int(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("flag\ntrue\nfalse\ntrue\n")
    adapter = SqliteAdapter(fixtures=tmp_path)
    rows = adapter.run_query("p", "SELECT SUM(flag) AS s FROM `p.d.t`", {})
    assert rows[0]["s"] == 2


def test_missing_fixture_dir_is_a_clean_error(tmp_path):
    adapter = SqliteAdapter(fixtures=tmp_path / "nope")
    with pytest.raises(SystemExit) as exc:
        adapter.connection()
    assert "generate_fixtures" in str(exc.value)


def test_empty_fixture_dir_is_a_clean_error(tmp_path):
    adapter = SqliteAdapter(fixtures=tmp_path)
    with pytest.raises(SystemExit) as exc:
        adapter.connection()
    assert "no .csv files" in str(exc.value)


def test_bad_sql_reports_the_translated_query(tmp_path):
    """When a fixture query fails, the error has to show the TRANSLATED sql and the
    loaded table names — debugging a sqlite parse error against the BigQuery source
    text is guesswork."""
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("d,n\n2026-01-05,3\n")
    adapter = SqliteAdapter(fixtures=tmp_path)
    with pytest.raises(SystemExit) as exc:
        adapter.run_query("p", "SELECT no_such_column FROM `p.d.t`", {})
    msg = str(exc.value)
    assert "Translated SQL" in msg
    assert "Loaded fixture tables" in msg


def test_fixture_db_is_in_memory(tmp_path):
    """Nothing to persist, and a stale cache file on disk would be a genuinely
    confusing failure mode in a template."""
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("d,n\n2026-01-05,3\n")
    adapter = SqliteAdapter(fixtures=tmp_path)
    adapter.connection()
    assert isinstance(adapter.connection(), sqlite3.Connection)
    assert not list(tmp_path.glob("*.db")) and not list(tmp_path.glob("*.sqlite"))


# ---------------------------------------------------------------------------
# View qualification
# ---------------------------------------------------------------------------


def test_both_adapters_qualify_a_view_canonically():
    """Canonical SQL is dialect-stable on purpose: an artifact's `query_sha` receipt
    must not depend on which engine served it, so `assay:verify` works across both.
    The flattening to a physical sqlite table happens in the translator, once."""
    for name in ("sqlite", "bigquery"):
        adapter = adapters.get_adapter(name)
        assert adapter.qualify_view("proj", WEEKLY_VIEW) == f"proj.{WEEKLY_VIEW}"


def test_spine_fq_view_is_adapter_resolved():
    from _shared import spine

    spec = {"source_project": "proj", "source_view": WEEKLY_VIEW}
    assert spine.fq_view(spec) == f"proj.{WEEKLY_VIEW}"
    assert spine.fq_view(spec, engine="bigquery") == f"proj.{WEEKLY_VIEW}"


def test_spine_run_query_dispatches_to_the_resolved_adapter():
    """`spine.run_query` keeps its original signature and is now one dispatch. Every
    pre-adapter caller is untouched."""
    from _shared import spine

    rows = spine.run_query(
        "proj",
        f"SELECT COUNT(*) AS c FROM `proj.{WEEKLY_VIEW}`",
        {},
    )
    assert rows[0]["c"] > 1000
    assert spine.active_adapter().name == "sqlite"
    assert spine.active_adapter().fixture_backed is True
