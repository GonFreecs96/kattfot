"""Query-construction regression net: every bundled metric builds the SQL we expect.

This is the engine-free half of a golden question-to-answer test per metric. It
cannot assert the *numbers* on a real warehouse, but it pins the *query
construction* and the governance shape — so a metric-YAML edit or a builder change
that breaks the SUM/SUM contract, drops the time filter, or mis-wires a dimension is
caught by `pytest` rather than at runtime — there is no CI, so someone has to run it.

The numeric half is covered separately and genuinely: `test_mock_oracle.py` runs the
arithmetic two independent ways over a committed fixture, and
`test_fixture_sql_agrees_with_the_python_oracle` below runs this exact generated SQL
against sqlite and checks it against that oracle. Between them, the query shape and
the number it produces are both pinned.
"""

import datetime as dt
from pathlib import Path

import pytest
import yaml

import kattfot_query as aq

ROOT = Path(__file__).resolve().parents[1]
METRICS_DIR = ROOT / "plugins" / "kattfot" / "skills" / "query-metric" / "metrics"


def _metrics():
    out = []
    for p in sorted(METRICS_DIR.rglob("*.md")):
        m = aq.FRONTMATTER_RE.match(p.read_text())
        if not m:
            continue
        fm = yaml.safe_load(m.group(1)) or {}
        if fm.get("name"):
            out.append((p, fm))
    return out


METRICS = _metrics()
IDS = [p.name for p, _ in METRICS]


@pytest.mark.parametrize("path,fm", METRICS, ids=IDS)
def test_canonical_sql_invariants(path, fm):
    sql, params = aq.build_sql(fm, "proj", [], {})
    name = fm["name"]
    num, den = fm["numerator_column"], fm["denominator_column"]
    tcol, view = fm["time_column"], fm["source_view"]

    assert f"FROM `proj.{view}`" in sql
    assert f"SAFE_DIVIDE(SUM({num}), SUM({den})) AS {name}" in sql
    assert f"SUM({num}) AS numerator" in sql
    assert f"SUM({den}) AS denominator" in sql
    assert f"{tcol} BETWEEN @start_date AND @end_date" in sql
    assert f"GROUP BY {tcol}" in sql
    assert params == {}, "no filters requested -> no bound params"


@pytest.mark.parametrize("path,fm", METRICS, ids=IDS)
def test_every_metric_translates_to_runnable_sqlite(path, fm):
    """Every metric's canonical SQL must survive the translator. This is what makes
    the offline path total rather than incidental: a metric whose SQL the translator
    cannot handle would fail only when someone happened to run that metric."""
    from _shared import dialect

    sql, _ = aq.build_sql(fm, "proj", list(fm["allowed_dimensions"])[:1], {}, dt.date(2026, 8, 10))
    translated = dialect.translate_sql(sql)
    dialect.assert_translated(translated)
    assert "SAFE_DIVIDE" not in translated
    assert "@" not in translated


@pytest.mark.parametrize("path,fm", METRICS, ids=IDS)
def test_every_metrics_allow_listed_columns_exist_on_the_view(path, fm):
    """A metric may only allow-list columns the source view actually has. Listing one
    it lacks is not a graceful refusal — it is a runtime SQL error the moment somebody
    groups by it, and the pre-aggregated view is easy to get wrong because its columns
    are a strict subset of the spine's."""
    from _shared import adapters

    adapter = adapters.get_adapter("sqlite")
    table = adapter.table_name(fm["source_view"])
    if table not in adapter.loaded_tables():
        pytest.fail(
            f"{path.name}: source view {fm['source_view']} has no fixture table "
            f"(loaded: {sorted(adapter.loaded_tables())})"
        )
    rows = adapter.run_query("proj", f'SELECT * FROM `proj.{fm["source_view"]}` LIMIT 1', {})
    view_columns = set(rows[0])
    required = set(
        [fm["time_column"], fm["numerator_column"], fm["denominator_column"]]
        + list(fm.get("allowed_dimensions") or [])
        # `allowed_filters` legitimately includes the cohort column, which IS on the
        # view, so no exclusion is needed here.
        + list(fm.get("allowed_filters") or [])
    )
    missing = required - view_columns
    assert not missing, (
        f"{path.name}: references columns absent from {fm['source_view']}: "
        f"{sorted(missing)}. View has: {sorted(view_columns)}"
    )


def test_dimension_and_filter_wiring():
    fm = aq.load_metric("cart_to_checkout_start_conversion")
    sql, params = aq.build_sql(fm, "proj", ["channel"], {"market": "us"})
    assert "channel" in sql
    assert "@filter_0" in sql
    assert params == {"filter_0": "us"}
    # The dimension participates in both SELECT and GROUP BY.
    assert sql.count("channel") >= 2


def test_maturity_predicate_appears_only_with_a_cutoff():
    metric = {
        "name": "m", "source_view": "ds.v", "time_column": "cohort_week",
        "numerator_column": "num", "denominator_column": "den",
    }
    sql_no, _ = aq.build_sql(metric, "proj", [], {})
    assert "@maturity_cutoff" not in sql_no

    sql_yes, params = aq.build_sql(metric, "proj", [], {}, dt.date(2026, 6, 1))
    assert "cohort_week <= @maturity_cutoff" in sql_yes
    # The cutoff is bound by the executor as a DATE, kept out of the STRING filters.
    assert params == {}


def test_fixture_sql_agrees_with_the_python_oracle():
    """The numeric half. The same window and grain, computed two ways that share no
    code: the generated SQL through sqlite, and `run_query_mock`'s Python
    filter-group-sum over the same CSV.

    This is what makes keeping the CSV-stub path worthwhile after the sqlite adapter
    landed. It stopped being the offline execution path and became an independent
    oracle — a reference implementation that disagrees loudly if either side drifts.
    """
    from _shared import adapters

    metric = dict(aq.load_metric("cart_to_checkout_start_conversion"))
    start, end = dt.date(2026, 6, 1), dt.date(2026, 7, 6)

    sql, params = aq.build_sql(metric, "proj", ["channel"], {}, None)
    sql_rows = adapters.get_adapter("sqlite").run_query(
        "proj", sql, {"start_date": start, "end_date": end, **params}
    )

    oracle_rows, _ = aq.run_query_mock(metric, ["channel"], {}, start, end, None)

    assert len(sql_rows) == len(oracle_rows) > 0
    by_key = {(r["cohort_week"], r["channel"]): r for r in sql_rows}
    for o in oracle_rows:
        s = by_key[(o["cohort_week"], o["channel"])]
        assert s["numerator"] == o["numerator"], (o["cohort_week"], o["channel"])
        assert s["denominator"] == o["denominator"]
        assert abs(s[metric["name"]] - o[metric["name"]]) < 1e-12


def test_fixture_sql_and_oracle_agree_under_a_filter():
    """Same cross-check with a bound filter, since that is where the two paths diverge
    most: SQL binds a parameter, the oracle does a Python string comparison."""
    from _shared import adapters

    metric = dict(aq.load_metric("payment_approval_rate"))
    start, end = dt.date(2026, 5, 4), dt.date(2026, 6, 29)
    filters = {"payment_method": "bank_transfer_review"}

    sql, params = aq.build_sql(metric, "proj", [], filters, None)
    sql_rows = adapters.get_adapter("sqlite").run_query(
        "proj", sql, {"start_date": start, "end_date": end, **params}
    )
    oracle_rows, _ = aq.run_query_mock(metric, [], filters, start, end, None)

    assert len(sql_rows) == len(oracle_rows) > 0
    assert [r["numerator"] for r in sql_rows] == [r["numerator"] for r in oracle_rows]
    assert [r["denominator"] for r in sql_rows] == [r["denominator"] for r in oracle_rows]
