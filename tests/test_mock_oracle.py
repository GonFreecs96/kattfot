"""Numeric oracle for the CSV-stub aggregation path.

`run_query_mock` is the one piece of ratio arithmetic this repo owns outright:
everywhere else the engine does the SUM/SUM. A regression in it — a flipped
numerator and denominator, a sum-versus-count slip, a broken time filter — would
pass every shape test in the suite silently, because the shape would be unchanged
and only the number would be wrong.

Pinned two ways, which is the point:

  1. a **committed golden snapshot** (human-reviewed expected rows), and
  2. an **independent re-aggregation** computed a different way at runtime
     (filter -> sort -> itertools.groupby -> sum), so a bug in `run_query_mock`
     diverges from a reference that shares none of its code path.

Both compare against something that is not `run_query_mock`, which is what makes a
second opinion a second opinion rather than a restatement.

Why this path still exists at all now that the sqlite adapter runs the real SQL
offline: it stopped being the production offline path and became the oracle FOR that
path. `test_metric_sql_regression.py` checks the generated SQL against this
implementation over the same fixture, so the redundancy is a cross-check rather than
duplication.
"""

import csv
import datetime as dt
import json
from itertools import groupby
from pathlib import Path

import pytest

import kattfot_query as aq

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "plugins" / "kattfot" / "skills" / "query-metric" / "templates"
GOLDEN = json.loads((ROOT / "tests" / "golden" / "example_mock.json").read_text())

METRIC = "example_checkout_completion_rate"
TIME, NUM, DEN = "week_start", "sessions_completed", "sessions_started"
START, END = dt.date(2026, 3, 1), dt.date(2026, 6, 12)


@pytest.fixture(autouse=True)
def point_at_templates(monkeypatch):
    """Resolve the example metric and its sample CSV from the templates dir, so this
    test exercises the shipped starter metric rather than a fixture written for it."""
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(TEMPLATES))
    monkeypatch.setenv("KATTFOT_SAMPLE_DIR", str(TEMPLATES))


def _independent_aggregate(dimensions, filters, start, end):
    """Reference implementation, deliberately a different idiom from the helper's
    defaultdict accumulation: read, filter, sort, groupby, sum."""
    rows = []
    with (TEMPLATES / "example_view.csv").open() as f:
        for r in csv.DictReader(f):
            d = dt.date.fromisoformat(r[TIME])
            if not (start <= d <= end):
                continue
            if any(r[k] != v for k, v in filters.items()):
                continue
            rows.append(r)
    keycols = [TIME] + list(dimensions)

    def kf(r):
        return tuple(r[c] for c in keycols)

    rows.sort(key=kf)
    out = []
    for key, grp in groupby(rows, key=kf):
        grp = list(grp)
        num = sum(int(g[NUM]) for g in grp)
        den = sum(int(g[DEN]) for g in grp)
        rec = dict(zip(keycols, key))
        rec["numerator"] = num
        rec["denominator"] = den
        rec[METRIC] = num / den if den else None
        out.append(rec)
    return out


def _run(dimensions, filters, start=START, end=END):
    metric = aq.load_metric(METRIC)
    rows, _ = aq.run_query_mock(metric, dimensions, filters, start, end)
    return rows


# --- golden snapshot --------------------------------------------------------


def test_full_window_matches_golden():
    assert _run([], {}) == GOLDEN["full_window_no_dim"]


def test_by_dimension_matches_golden():
    assert _run(["segment"], {}) == GOLDEN["by_segment"]


# --- independent second opinion ---------------------------------------------


def test_full_window_matches_independent():
    assert _run([], {}) == _independent_aggregate([], {}, START, END)


def test_by_dimension_matches_independent():
    assert _run(["segment"], {}) == _independent_aggregate(["segment"], {}, START, END)


def test_filter_matches_independent():
    got = _run([], {"segment": "segment_a"})
    assert got == _independent_aggregate([], {"segment": "segment_a"}, START, END)
    # And the filter genuinely narrowed the data.
    assert got and all(r["denominator"] > 0 for r in got)
    unfiltered = _run([], {})
    assert sum(r["denominator"] for r in got) < sum(r["denominator"] for r in unfiltered)


def test_time_window_subset_matches_independent():
    start, end = dt.date(2026, 4, 1), dt.date(2026, 4, 30)
    rows = _run([], {}, start, end)
    assert rows == _independent_aggregate([], {}, start, end)
    assert rows, "the April window should not be empty"
    assert all(start <= dt.date.fromisoformat(r[TIME]) <= end for r in rows)


def test_maturity_cutoff_withholds_later_cohorts():
    """The cutoff is applied in the same place on both paths. A cutoff that silently
    did nothing would leave the deep-stage metrics reporting unsettled cohorts."""
    metric = aq.load_metric(METRIC)
    cutoff = dt.date(2026, 4, 27)
    rows, _ = aq.run_query_mock(metric, [], {}, START, END, cutoff)
    assert rows
    assert all(dt.date.fromisoformat(r[TIME]) <= cutoff for r in rows)
    # And it actually withheld something, rather than passing vacuously.
    assert len(rows) < len(_run([], {}))


# --- orientation tripwire ---------------------------------------------------


def test_numerator_is_completed_not_started():
    """Pin the ratio orientation. A flipped numerator and denominator — the classic
    silent bug, since both are integers and both aggregate fine — would push the rate
    above 1 and fail here."""
    for r in _run([], {}):
        assert r["numerator"] <= r["denominator"]
        assert 0 < r[METRIC] <= 1


def test_zero_denominator_yields_none_not_a_division_error(tmp_path, monkeypatch):
    """`SAFE_DIVIDE` returns NULL on a zero denominator and so must this path — an
    absent rate is a different finding from a zero one, and a ZeroDivisionError would
    take down a whole governed run over one empty cell."""
    monkeypatch.setenv("KATTFOT_SAMPLE_DIR", str(tmp_path))
    (tmp_path / "example_view.csv").write_text(
        "week_start,segment,sessions_started,sessions_completed\n"
        "2026-04-06,segment_a,0,0\n"
    )
    metric = aq.load_metric(METRIC)
    rows, _ = aq.run_query_mock(metric, [], {}, START, END)
    assert rows[0][METRIC] is None
    assert rows[0]["denominator"] == 0


def test_missing_csv_is_a_clean_error(tmp_path, monkeypatch):
    monkeypatch.setenv("KATTFOT_SAMPLE_DIR", str(tmp_path))
    metric = aq.load_metric(METRIC)
    with pytest.raises(SystemExit) as exc:
        aq.run_query_mock(metric, [], {}, START, END)
    assert "generate_fixtures" in str(exc.value) or "KATTFOT_SAMPLE_DIR" in str(exc.value)
