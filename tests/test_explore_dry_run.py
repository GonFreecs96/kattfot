"""`kattfot:explore --dry-run` — the cost preview.

Design in one line: governance precedes cost. Every guard the normal
exploratory lane runs (statement, surface, star, R6) runs first and
unchanged, so a query that would be refused is refused, not priced — a
dry run never sees a query the lane would not otherwise let through. Past
the guards, the estimate itself is capability-gated the same way
`percentiles()` is: BigQuery genuinely can price a query without running it
(`QueryJobConfig(dry_run=True)`), sqlite fixtures cannot (no cost dimension
to a committed CSV), and the lane reports the honest difference —
`estimate_available: false` with a reason on sqlite — rather than faking a
number.

`tests/test_adapters.py` covers the BigQuery adapter's `dry_run()` in
isolation (job config shape, typed params, identity assertion). This file
covers the lane: guard order (including dialect translatability, which
normally only runs inside `run_query` and has to be checked explicitly on
the sqlite `--dry-run` path since that never calls it), the sqlite
honest-reply path, the client-side `would_exceed` comparison against the
register's `max_bytes_billed` (both sides of the cap, and the `None`
tri-state when no cap is configured), and that a dry run leaves no trace in
the audit log (nothing was read, so nothing is logged as a read).
"""

import json
import sys

import pytest

import kattfot_explore
import _shared.adapters.bigquery_adapter as bqa
from _shared import exploration as exploration_lib

SPINE = "analytics.cart_checkout_session_spine"
WEEKLY = "analytics.cart_checkout_funnel_weekly"


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    """Same default as test_explore.py: audit off unless a test explicitly
    wants to inspect the log."""
    monkeypatch.setenv("KATTFOT_AUDIT_DISABLE", "1")


def run_ok(argv, capsys, monkeypatch) -> dict:
    monkeypatch.setattr(sys, "argv", ["kattfot_explore"] + argv)
    assert kattfot_explore.main() in (0, None)
    return json.loads(capsys.readouterr().out)


def run_refused(argv, capsys, monkeypatch, code=5) -> str:
    monkeypatch.setattr(sys, "argv", ["kattfot_explore"] + argv)
    with pytest.raises(SystemExit) as exc:
        kattfot_explore.main()
    assert exc.value.code == code, f"expected exit {code}, got {exc.value.code}"
    return capsys.readouterr().err


# ---------------------------------------------------------------------------
# Governance precedes cost: every refusal still fires with --dry-run
# ---------------------------------------------------------------------------


def test_a_pii_query_with_dry_run_still_refuses_no_estimate(capsys, monkeypatch):
    """R6 is checked before the engine is even resolved, so --dry-run cannot
    reach a query that would otherwise be refused."""
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT customer_email FROM {SPINE}",
         "--dry-run"],
        capsys, monkeypatch,
    )
    assert "ERROR (R6)" in err
    assert "No override" in err


def test_an_unregistered_surface_with_dry_run_still_refuses(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql", "SELECT a FROM analytics.secret_table",
         "--dry-run"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err


def test_select_star_on_an_unattested_surface_with_dry_run_still_refuses(
    tmp_path, capsys, monkeypatch
):
    reg = tmp_path / "exploration.yaml"
    reg.write_text(
        "surfaces:\n"
        "  analytics.cart_checkout_funnel_weekly:\n"
        "    status: live\n"          # no select_star attestation
    )
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(reg))
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT * FROM {WEEKLY}", "--dry-run"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-STAR)" in err


def test_a_non_select_statement_with_dry_run_still_refuses(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql", f"DELETE FROM {SPINE}", "--dry-run"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-STATEMENT)" in err


DIALECT_SQL = (
    f"SELECT APPROX_QUANTILES(gmv_usd, 100)[OFFSET(50)] AS p50 FROM {SPINE}"
)


def test_an_untranslatable_construct_with_dry_run_still_refuses_on_sqlite(
    capsys, monkeypatch
):
    """The bug this guards against: `run_query` (where dialect translation
    normally happens) never executes under `--dry-run`, so without an
    explicit pre-check an untranslatable construct would exit 0 with
    `--dry-run` and exit 5 without it — silently contradicting the "every
    governance guard still ran" claim on the sqlite honest-reply path."""
    err = run_refused(
        ["--question", "q", "--sql", DIALECT_SQL, "--dry-run"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-DIALECT)" in err
    assert "--engine bigquery" in err


def test_dry_run_and_a_real_run_refuse_the_same_construct_with_the_same_text(
    capsys, monkeypatch
):
    """Parity check: the refusal text must not depend on whether the query
    was being priced or executed — a caller should learn the same rule
    either way."""
    with_dry_run = run_refused(
        ["--question", "q", "--sql", DIALECT_SQL, "--dry-run"], capsys, monkeypatch
    )
    without_dry_run = run_refused(
        ["--question", "q", "--sql", DIALECT_SQL], capsys, monkeypatch
    )
    assert with_dry_run == without_dry_run


# ---------------------------------------------------------------------------
# The sqlite path: honest "no estimate", not a refusal
# ---------------------------------------------------------------------------


def test_sqlite_dry_run_reports_no_estimate_available_with_a_reason(
    capsys, monkeypatch
):
    out = run_ok(
        ["--question", "how many sessions?", "--sql",
         f"SELECT COUNT(*) AS n FROM {SPINE}", "--dry-run"],
        capsys, monkeypatch,
    )
    assert out["skill"] == "explore"
    assert out["dry_run"] is True
    assert out["accuracy_tier"] == "T3"
    assert out["engine"] == "sqlite"
    assert out["fixture_backed"] is True
    assert out["estimate_available"] is False
    assert "estimate" not in out
    assert "cost dimension" in out["estimate_unavailable_reason"]
    # The T3 stamp survives the dry-run path too.
    assert any(w.startswith("EXPLORATORY (T3)") for w in out["warnings"])


def test_sqlite_dry_run_still_produces_no_rows_manifest_or_row_count(
    capsys, monkeypatch
):
    """Nothing was read, so none of the read-shaped fields exist — this is a
    different JSON envelope than a normal (non-dry-run) result, not the same
    shape with rows omitted."""
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE}",
         "--dry-run"],
        capsys, monkeypatch,
    )
    assert "rows" not in out
    assert "row_count" not in out
    assert "manifest" not in out


def test_sqlite_dry_run_still_carries_the_qualified_sql_and_params(
    capsys, monkeypatch
):
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT channel FROM {SPINE} WHERE market = @m", "--dry-run",
         "--param", "m=US"],
        capsys, monkeypatch,
    )
    assert out["query_params"] == {"m": "US"}
    assert "@m" in out["query"]
    assert out["surfaces"] == [SPINE]


# ---------------------------------------------------------------------------
# The BigQuery path: would_exceed on both sides of the register cap
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bigquery_project(monkeypatch):
    """Every bigquery-mode test needs a project to resolve against; harmless
    for the sqlite-mode tests above, which never read it."""
    monkeypatch.setenv("KATTFOT_BIGQUERY_PROJECT", "test-proj")


def test_dry_run_would_exceed_is_false_under_the_cap(capsys, monkeypatch):
    monkeypatch.setattr(
        bqa.BigQueryAdapter,
        "dry_run",
        lambda self, project, sql, params: {"total_bytes_processed": 1000},
    )
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE}",
         "--engine", "bigquery", "--dry-run"],
        capsys, monkeypatch,
    )
    assert out["estimate_available"] is True
    assert out["estimate"]["total_bytes_processed"] == 1000
    assert out["estimate"]["would_exceed"] is False
    assert out["estimate"]["total_bytes_processed_human"]  # non-empty rendering


def test_dry_run_would_exceed_is_true_over_the_cap(capsys, monkeypatch):
    cap = exploration_lib.max_bytes_billed(exploration_lib.load_register())
    assert cap is not None
    monkeypatch.setattr(
        bqa.BigQueryAdapter,
        "dry_run",
        lambda self, project, sql, params: {"total_bytes_processed": cap + 1},
    )
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE}",
         "--engine", "bigquery", "--dry-run"],
        capsys, monkeypatch,
    )
    assert out["estimate"]["total_bytes_processed"] == cap + 1
    assert out["estimate"]["max_bytes_billed"] == cap
    assert out["estimate"]["would_exceed"] is True


def test_bigquery_dry_run_result_shape_and_no_rows(capsys, monkeypatch):
    monkeypatch.setattr(
        bqa.BigQueryAdapter,
        "dry_run",
        lambda self, project, sql, params: {"total_bytes_processed": 42},
    )
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE}",
         "--engine", "bigquery", "--dry-run"],
        capsys, monkeypatch,
    )
    assert out["engine"] == "bigquery"
    assert out["source_mode"] == "bigquery"
    assert out["dry_run"] is True
    assert out["accuracy_tier"] == "T3"
    assert out["fixture_backed"] is False
    assert "rows" not in out
    assert "row_count" not in out
    assert "manifest" not in out
    assert "estimate_unavailable_reason" not in out


def test_dry_run_would_exceed_is_none_when_the_register_has_no_cap(
    tmp_path, capsys, monkeypatch
):
    """A tri-state, not a bool: no `defaults.max_bytes_billed` in the
    register means there is nothing to compare the estimate against, so
    `would_exceed` must be `None` — an untested `False` here would silently
    read as "definitely under the cap" when no cap was ever configured."""
    reg = tmp_path / "exploration.yaml"
    reg.write_text(
        "surfaces:\n"
        f"  {SPINE}:\n"
        "    status: live\n"
        "    select_star: true\n"
        # deliberately no `defaults:` block at all
    )
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(reg))
    monkeypatch.setattr(
        bqa.BigQueryAdapter,
        "dry_run",
        lambda self, project, sql, params: {"total_bytes_processed": 999},
    )
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE}",
         "--engine", "bigquery", "--dry-run"],
        capsys, monkeypatch,
    )
    assert out["estimate"]["max_bytes_billed"] is None
    assert out["estimate"]["max_bytes_billed_human"] is None
    assert out["estimate"]["would_exceed"] is None


# ---------------------------------------------------------------------------
# No audit line: a dry run reads nothing, so nothing is logged as a read
# ---------------------------------------------------------------------------


def test_no_audit_line_is_appended_for_a_sqlite_dry_run(tmp_path, capsys, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.delenv("KATTFOT_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("KATTFOT_AUDIT_LOG", str(log))
    run_ok(
        ["--question", "how many sessions?", "--sql",
         f"SELECT COUNT(*) AS n FROM {SPINE}", "--dry-run"],
        capsys, monkeypatch,
    )
    assert not log.exists()


def test_no_audit_line_is_appended_for_a_bigquery_dry_run(tmp_path, capsys, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.delenv("KATTFOT_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("KATTFOT_AUDIT_LOG", str(log))
    monkeypatch.setattr(
        bqa.BigQueryAdapter,
        "dry_run",
        lambda self, project, sql, params: {"total_bytes_processed": 42},
    )
    run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE}",
         "--engine", "bigquery", "--dry-run"],
        capsys, monkeypatch,
    )
    assert not log.exists()


def test_a_normal_non_dry_run_query_still_appends_an_audit_line(
    tmp_path, capsys, monkeypatch
):
    """Contrast case: confirms the previous two tests are actually pinning
    dry-run-specific behavior, not a suite-wide audit misconfiguration."""
    log = tmp_path / "audit.jsonl"
    monkeypatch.delenv("KATTFOT_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("KATTFOT_AUDIT_LOG", str(log))
    run_ok(
        ["--question", "how many sessions?", "--sql",
         f"SELECT COUNT(*) AS n FROM {SPINE}"],
        capsys, monkeypatch,
    )
    assert log.exists()
    assert len(log.read_text().splitlines()) == 1
