"""The exploratory lane: every refusal rule, the fail-closed register, and the
provenance the lane promises.

The lane's design claim is "relax the allow-lists, never the account", so the
tests split the same way: everything that must refuse (statement gate, register
boundary, SELECT *, R6 over identifiers, dialect residue) and everything that
must survive into the output (the unconditional T3 warning, the manifest
fingerprints, the audit trace, uncapped rows).
"""

import json
import sys

import pytest
import yaml

import kattfot_explore
from _shared import exploration as exploration_lib
from _shared import sqlguard

SPINE = "analytics.cart_checkout_session_spine"
WEEKLY = "analytics.cart_checkout_funnel_weekly"

RATIO_SQL = (
    f"SELECT cohort_week, channel, "
    f"SAFE_DIVIDE(SUM(orders_returned), SUM(orders_confirmed)) AS r, "
    f"SUM(orders_returned) AS numerator, SUM(orders_confirmed) AS denominator "
    f"FROM {WEEKLY} GROUP BY cohort_week, channel ORDER BY cohort_week"
)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
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


# --- the statement gate ------------------------------------------------------


@pytest.mark.parametrize("sql, fragment", [
    (f"DELETE FROM {SPINE}", "read-only SELECT"),
    (f"DROP TABLE {SPINE}", "read-only SELECT"),
    ("SELECT 1; SELECT 2", "Multiple statements"),
    (f"SELECT channel FROM {SPINE}; DROP TABLE x", "Multiple statements"),
    (f"SELECT channel, (SELECT 1 WHERE EXISTS (SELECT 1)) FROM {SPINE} "
     f"UNION ALL SELECT 'x', (INSERT INTO t VALUES (1))", "INSERT"),
    ("", "Empty SQL"),
])
def test_non_select_statements_are_refused(sql, fragment, capsys, monkeypatch):
    err = run_refused(["--question", "q", "--sql", sql], capsys, monkeypatch)
    assert "ERROR (EXPLORE-STATEMENT)" in err
    assert fragment in err


def test_a_write_keyword_in_a_string_literal_does_not_trip_the_gate(capsys, monkeypatch):
    """The gate reads noise-stripped SQL, so prose in a literal is data."""
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT channel FROM {SPINE} WHERE terminal_branch = 'drop update delete' "
         f"GROUP BY channel"],
        capsys, monkeypatch,
    )
    assert out["row_count"] == 0  # no such branch; the point is it RAN


def test_a_trailing_semicolon_is_tolerated(capsys, monkeypatch):
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE};"],
        capsys, monkeypatch,
    )
    assert out["rows"][0]["n"] > 0


# --- the register boundary ---------------------------------------------------


def test_an_unregistered_table_is_refused_naming_the_register(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql", "SELECT a FROM analytics.secret_table"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "analytics.secret_table" in err
    assert WEEKLY in err  # the refusal shows what IS registered


def test_a_project_qualified_reference_is_refused_with_the_hint(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT a FROM some_project.{SPINE}"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "dataset.table" in err


def test_a_declared_surface_is_refused_with_its_reason(tmp_path, capsys, monkeypatch):
    reg = tmp_path / "exploration.yaml"
    reg.write_text(
        "surfaces:\n"
        "  analytics.browse_session_spine:\n"
        "    status: declared\n"
        "    notes: \"Nothing is provisioned behind it yet (PLAT-112).\"\n"
    )
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(reg))
    err = run_refused(
        ["--question", "q", "--sql", "SELECT a FROM analytics.browse_session_spine"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "declared" in err
    assert "PLAT-112" in err


def test_a_missing_register_fails_closed(tmp_path, capsys, monkeypatch):
    """The register is a permission boundary: absent register, no exploration —
    exit 1, not an empty allow-list and not an open door."""
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(tmp_path / "nope.yaml"))
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT 1 FROM {SPINE}"],
        capsys, monkeypatch, code=1,
    )
    assert "Exploration register not found" in err


def test_an_unparseable_register_fails_closed(tmp_path, monkeypatch):
    """Unlike coverage/guardrails (advisory -> degrade to empty), a broken
    permission file must surface, not vanish."""
    reg = tmp_path / "exploration.yaml"
    reg.write_text("surfaces: [unclosed\n  broken: {")
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(reg))
    with pytest.raises(yaml.YAMLError):
        exploration_lib.load_register()


# --- SELECT * ----------------------------------------------------------------


def test_select_star_is_allowed_on_an_attested_surface(capsys, monkeypatch):
    """The shipped views attest select_star because tests/test_view_ddl_pii.py
    scans their DDL — the attestation is checked, not asserted."""
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT * FROM {WEEKLY} WHERE cohort_week >= @w",
         "--param", "w=2026-07-01"],
        capsys, monkeypatch,
    )
    assert out["row_count"] > 0


def test_select_star_is_refused_on_an_unattested_surface(tmp_path, capsys, monkeypatch):
    reg = tmp_path / "exploration.yaml"
    reg.write_text(
        "surfaces:\n"
        "  analytics.cart_checkout_funnel_weekly:\n"
        "    status: live\n"          # no select_star attestation
    )
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(reg))
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT * FROM {WEEKLY}"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-STAR)" in err
    assert "select_star" in err


def test_count_star_is_not_select_star():
    assert sqlguard.uses_select_star("SELECT COUNT(*) FROM x") is False
    assert sqlguard.uses_select_star("SELECT a, t.* FROM x t") is True
    assert sqlguard.uses_select_star("SELECT a * b FROM x") is False


# --- R6 over every identifier ------------------------------------------------


@pytest.mark.parametrize("sql", [
    f"SELECT customer_email FROM {SPINE}",                       # projection
    f"SELECT channel FROM {SPINE} WHERE ssn = @x",               # filter
    f"SELECT channel, COUNT(*) AS by_dob FROM {SPINE} "
    f"JOIN analytics.cart_checkout_funnel_weekly USING (cohort_week) "
    f"WHERE date_of_birth IS NOT NULL GROUP BY channel",         # join + where
    f"SELECT market AS email FROM {SPINE}",                      # even an alias
])
def test_pii_identifiers_are_refused_anywhere_in_the_statement(sql, capsys, monkeypatch):
    err = run_refused(["--question", "q", "--sql", sql], capsys, monkeypatch)
    assert "ERROR (R6)" in err
    assert "No override" in err


def test_pii_inside_a_string_literal_is_data_not_an_identifier(capsys, monkeypatch):
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT channel FROM {SPINE} WHERE terminal_branch = 'email me' "
         f"GROUP BY channel"],
        capsys, monkeypatch,
    )
    assert out["row_count"] == 0


def test_safe_allowlisted_columns_stay_usable(capsys, monkeypatch):
    """market/postal-code-style geography and opaque keys must keep working —
    R6 that blocks governed-style analysis is its own failure mode."""
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT market, COUNT(*) AS n FROM {SPINE} GROUP BY market"],
        capsys, monkeypatch,
    )
    assert out["row_count"] > 0


# --- the lexical guard's own pieces ------------------------------------------


def test_referenced_tables_sees_joins_comma_joins_and_exempts_ctes():
    sql = (
        "WITH recent AS (SELECT * FROM analytics.a) "
        "SELECT * FROM recent r, analytics.b b2 "
        "JOIN analytics.c ON b2.k = c.k "
        "LEFT JOIN analytics.d USING (k)"
    )
    assert sqlguard.referenced_tables(sql) == {
        "analytics.a", "analytics.b", "analytics.c", "analytics.d",
    }


def test_referenced_tables_ignores_from_inside_strings_and_comments():
    sql = (
        "SELECT a -- FROM analytics.commented\n"
        "FROM analytics.real WHERE x = 'FROM analytics.quoted'"
    )
    assert sqlguard.referenced_tables(sql) == {"analytics.real"}


def test_unnest_is_not_a_table():
    sql = "SELECT x FROM analytics.a, UNNEST(arr) AS x"
    assert sqlguard.referenced_tables(sql) == {"analytics.a"}


# --- what the output must always carry ---------------------------------------


def test_the_t3_stamp_and_warning_are_unconditional(capsys, monkeypatch):
    out = run_ok(
        ["--question", "how many sessions?", "--sql",
         f"SELECT COUNT(*) AS n FROM {SPINE}"],
        capsys, monkeypatch,
    )
    assert out["accuracy_tier"] == "T3"
    assert out["skill"] == "explore"
    assert out["warnings"][0].startswith("EXPLORATORY (T3)")
    assert out["fixture_backed"] is True  # sqlite path in the suite
    assert any("FIXTURE DATA" in w for w in out["warnings"])


def test_rows_are_returned_uncapped(capsys, monkeypatch):
    """The user's explicit design decision: no LIMIT is imposed. The spine
    fixture holds >20k rows; all of them come back."""
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT checkout_session_id FROM {SPINE}"],
        capsys, monkeypatch,
    )
    assert out["row_count"] > 20_000
    assert len(out["rows"]) == out["row_count"]


def test_the_manifest_is_reproducible_and_content_addressed(capsys, monkeypatch):
    a = run_ok(["--question", "q", "--sql", RATIO_SQL], capsys, monkeypatch)
    b = run_ok(["--question", "q", "--sql", RATIO_SQL], capsys, monkeypatch)
    assert a["manifest"]["query_sha"] == b["manifest"]["query_sha"]
    assert a["manifest"]["result_sha"] == b["manifest"]["result_sha"]
    assert a["manifest"]["register_sha"] == b["manifest"]["register_sha"]
    c = run_ok(
        ["--question", "q", "--sql", RATIO_SQL + " LIMIT 3"], capsys, monkeypatch
    )
    assert c["manifest"]["query_sha"] != a["manifest"]["query_sha"]


def test_bound_params_are_parameters_not_interpolation(capsys, monkeypatch):
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT channel, COUNT(*) AS n FROM {SPINE} WHERE market = @m GROUP BY channel",
         "--param", "m=US"],
        capsys, monkeypatch,
    )
    assert out["query_params"] == {"m": "US"}
    assert "@m" in out["query"]  # the SQL keeps the placeholder; the value is bound


def test_the_audit_trace_records_the_exploration(tmp_path, capsys, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.delenv("KATTFOT_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("KATTFOT_AUDIT_LOG", str(log))
    run_ok(
        ["--question", "how many sessions?", "--sql",
         f"SELECT COUNT(*) AS n FROM {SPINE}"],
        capsys, monkeypatch,
    )
    record = json.loads(log.read_text().splitlines()[-1])
    assert record["metric"] is None
    assert record["accuracy_tier"] == "T3"
    assert record["question"] == "how many sessions?"
    assert record["surfaces"] == [SPINE]
    assert record["agent"]["skill"] == "explore"
    assert "rows" not in record  # a trace, not a copy
    assert log.with_name(log.name + ".head").exists()  # the hash chain advanced


def test_an_untranslatable_construct_refuses_cleanly_on_sqlite(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql",
         f"SELECT DATE_TRUNC(cohort_week, MONTH) AS m FROM {SPINE}"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-DIALECT)" in err
    assert "--engine bigquery" in err


def test_the_qualified_sql_is_canonical_bigquery_dialect(capsys, monkeypatch):
    """The artifact's query_sha must not depend on the engine, so the recorded
    SQL keeps the backticked project-qualified reference on every engine."""
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE}"],
        capsys, monkeypatch,
    )
    assert f"`${{KATTFOT_BIGQUERY_PROJECT}}.{SPINE}`" in out["query"]
