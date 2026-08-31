"""Adversarial suite for the free-form SQL lane: probing `_shared/sqlguard.py`
and `kattfot_explore.py`'s validators the way an attacker would.

`tests/test_adversarial_injection.py` covers the GOVERNED lane's injection
story: there, the agent never writes SQL — it selects a vetted metric and a
vetted dimension from fixed allow-lists, so untrusted data arriving as a
*value* cannot become a new access path. The exploratory lane
(`kattfot:explore`) has no such allow-list to hide behind: the agent (or a
user pasting a query) writes the SQL text directly, and `sqlguard.py` is the
entire perimeter. Its own docstring says what it is plainly: "lexical, blunt,
and honest about it" — not a parser, with stated gaps rather than
undiscovered ones. This file is the attacker's-eye pass over that perimeter:
every probe below either

  (a) hits a WALL and is refused — pinned as a refusal, asserting the rule
      name (`EXPLORE-STATEMENT`, `EXPLORE-SURFACE`, `EXPLORE-STAR`, `R6`);
  (b) is a HARMLESS PASS — the probe gets past a gate but the lane's stated
      guarantee still holds (no unregistered table is actually read, no PII
      identifier reaches the projection, no write executes) — pinned with a
      comment naming why it is safe despite not being refused; or
  (c) is a genuine HOLE — the probe defeats a stated guarantee. None were
      found that were not also fixable; see the section 4 JSON-path item for
      the one case that looks like a hole and is documented as a deliberate
      gap instead (fixing it would mean scanning literal values, which the
      design explicitly rejects: a WHERE-clause literal containing an email
      address must not trip the scan).

Sections mirror the probe catalog this suite was built from:
  1. the statement gate (noise vs. real statements, every deny keyword)
  2. table-reference extraction (wildcards, escape hatches, shadowing)
  3. SELECT * detection
  4. the R6 identifier scan
  5. qualify_sql's rewrite
  6. parameter binding

Offline throughout — sqlite fixtures only, same as test_explore.py.
"""

from __future__ import annotations

import json
import sys

import pytest

import kattfot_explore
from _shared import sqlguard

SPINE = "analytics.cart_checkout_session_spine"
WEEKLY = "analytics.cart_checkout_funnel_weekly"


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


def run_execution_error(argv, capsys, monkeypatch) -> str:
    """For a probe that clears every governance gate and fails only because
    the resulting SQL cannot execute on the sqlite engine. Unlike a
    governance refusal (`refuse()`, which writes to stderr and calls
    `sys.exit(5)`), this path is `raise SystemExit(f"ERROR: ...")` straight
    out of the adapter, so the message lives on `.code` (a string), not on
    stderr, and the exit code is not 5. Asserting BOTH — that it is not a
    governance refusal, and what kind of failure it is instead — is the
    point: the probe must not have silently succeeded either.
    """
    monkeypatch.setattr(sys, "argv", ["kattfot_explore"] + argv)
    with pytest.raises(SystemExit) as exc:
        kattfot_explore.main()
    msg = str(exc.value.code)
    assert not msg.startswith("ERROR (EXPLORE"), f"unexpected governance refusal: {msg}"
    assert not msg.startswith("ERROR (R6)"), f"unexpected governance refusal: {msg}"
    assert capsys.readouterr().out == ""  # nothing printed — no partial result
    return msg


# =============================================================================
# 1. The statement gate
# =============================================================================


def test_semicolon_inside_a_string_literal_does_not_fire_the_gate():
    """strip_noise removes the literal first, so the ';' it contains is gone
    before the statement gate ever looks — this is data, not a delimiter."""
    sqlguard.assert_single_select(
        f"SELECT channel FROM {SPINE} WHERE terminal_branch = 'a; DROP TABLE x'"
    )  # no raise


def test_semicolon_inside_a_comment_does_not_fire_the_gate():
    sqlguard.assert_single_select(
        f"SELECT channel FROM {SPINE} -- a; DROP TABLE x\nWHERE 1=1"
    )  # no raise


def test_trailing_semicolon_then_comment_is_tolerated():
    sqlguard.assert_single_select(f"SELECT 1 FROM {SPINE}; -- trailing note")  # no raise


def test_a_real_second_statement_after_a_commented_one_still_fires():
    """The comment on the first line is noise; the ';' that follows it is real."""
    with pytest.raises(sqlguard.SqlGuardError, match="Multiple statements"):
        sqlguard.assert_single_select(
            f"SELECT 1 FROM {SPINE} -- looks like one statement\n"
            f"; SELECT 2 -- but a second one follows"
        )


def test_an_unterminated_string_literal_containing_a_semicolon_fails_closed():
    """strip_noise's string pattern requires a closing quote, so an
    unterminated literal is not consumed as a string at all — the raw text,
    ';' included, survives into the statement-count check. The failure mode
    is a refusal, not a silent bypass: an attacker cannot use a dangling
    quote to smuggle a hidden ';' past the gate, because the gate sees the
    literal ';' as real when the quote never closes."""
    with pytest.raises(sqlguard.SqlGuardError, match="Multiple statements"):
        sqlguard.assert_single_select("SELECT 'a; DROP TABLE x")


@pytest.mark.parametrize("prefix", ["/* leading block comment */  ", "   \n\t  ", "-- line comment\n"])
def test_leading_noise_before_select_is_tolerated(prefix):
    sqlguard.assert_single_select(f"{prefix}SELECT 1 FROM {SPINE}")  # no raise


def test_a_leading_parenthesized_select_is_tolerated():
    sqlguard.assert_single_select(f"  (SELECT 1 FROM {SPINE})")  # no raise


def test_select_keyword_casing_is_irrelevant():
    sqlguard.assert_single_select(f"SeLeCt 1 FROM {SPINE}")  # no raise
    sqlguard.assert_single_select(f"WiTh x AS (SELECT 1) sElEcT * FROM x")  # no raise


#: Every statement-level deny keyword, exercised as a real second clause
#: after a legitimate `WITH ... AS (...)` prefix — the shape that reaches
#: `_DENY_KEYWORDS` (rather than the first-word check) and so names the
#: keyword explicitly in the refusal.
_DENY_KEYWORD_STATEMENTS = [
    f"WITH x AS (SELECT 1 FROM {SPINE}) DELETE FROM t",
    f"WITH x AS (SELECT 1 FROM {SPINE}) UPDATE t SET a = 1",
    f"WITH x AS (SELECT 1 FROM {SPINE}) INSERT INTO t VALUES (1)",
    f"WITH x AS (SELECT 1 FROM {SPINE}) MERGE INTO t USING s ON 1=1 WHEN MATCHED THEN UPDATE SET a=1",
    f"WITH x AS (SELECT 1 FROM {SPINE}) CREATE TEMP FUNCTION f() AS (1)",
    f"WITH x AS (SELECT 1 FROM {SPINE}) DROP TABLE t",
    f"WITH x AS (SELECT 1 FROM {SPINE}) ALTER TABLE t ADD COLUMN c INT64",
    f"WITH x AS (SELECT 1 FROM {SPINE}) TRUNCATE TABLE t",
    f"WITH x AS (SELECT 1 FROM {SPINE}) GRANT SELECT ON t TO 'user:x@example.com'",
    f"WITH x AS (SELECT 1 FROM {SPINE}) REVOKE SELECT ON t FROM 'user:x@example.com'",
    f"WITH x AS (SELECT 1 FROM {SPINE}) CALL myproc()",
    f"WITH x AS (SELECT 1 FROM {SPINE}) EXECUTE IMMEDIATE 'SELECT 1'",
    f"WITH x AS (SELECT 1 FROM {SPINE}) BEGIN SELECT 1 FROM {SPINE} END",
    f"WITH x AS (SELECT 1 FROM {SPINE}) COMMIT",
    f"WITH x AS (SELECT 1 FROM {SPINE}) DECLARE y INT64 DEFAULT 1",
    f"WITH x AS (SELECT 1 FROM {SPINE}) EXPORT DATA OPTIONS(uri='gs://x') AS SELECT 1",
    f"WITH x AS (SELECT 1 FROM {SPINE}) LOAD DATA INTO t FROM FILES(format='CSV', uris=['gs://x'])",
]


@pytest.mark.parametrize("sql", _DENY_KEYWORD_STATEMENTS)
def test_every_deny_keyword_refuses_as_a_real_statement(sql):
    with pytest.raises(sqlguard.SqlGuardError, match="is refused"):
        sqlguard.assert_single_select(sql)


#: Each of these ALSO refuses when it is the first word — a different code
#: path (the "must start with SELECT or WITH" first-word check, since the
#: statement never gets far enough to hit `_DENY_KEYWORDS`), same outcome.
@pytest.mark.parametrize("sql, keyword", [
    ("EXECUTE IMMEDIATE 'DROP TABLE x'", "EXECUTE"),
    ("BEGIN SELECT 1 END", "BEGIN"),
    ("DECLARE y INT64", "DECLARE"),
    ("MERGE t USING s ON 1=1 WHEN MATCHED THEN DELETE", "MERGE"),
    ("TRUNCATE TABLE t", "TRUNCATE"),
    ("EXPORT DATA OPTIONS(uri='gs://x') AS SELECT 1", "EXPORT"),
    ("LOAD DATA INTO t FROM FILES(format='CSV', uris=['gs://x'])", "LOAD"),
    ("CREATE TEMP FUNCTION f() AS (1)", "CREATE"),
    ("GRANT SELECT ON t TO 'x'", "GRANT"),
    ("CALL proc()", "CALL"),
])
def test_deny_keyword_as_the_first_word_refuses_via_the_first_word_check(sql, keyword):
    with pytest.raises(sqlguard.SqlGuardError, match=f"got {keyword!r}"):
        sqlguard.assert_single_select(sql)


@pytest.mark.parametrize("identifier", ["created_at", "last_update", "deleted_flag", "merge_key"])
def test_deny_shaped_substrings_in_ordinary_column_names_do_not_refuse(identifier):
    """`_DENY_KEYWORDS` matches whole words: 'update' inside 'last_update'
    has no word boundary in front of it (the preceding char is '_', a word
    character), so it cannot fire. Same reasoning for 'merge' in
    'merge_key'. These must stay usable — a guard blunt enough to refuse
    ordinary columns is a failure mode of its own."""
    sql = f"SELECT {identifier} FROM {SPINE} WHERE {identifier} IS NOT NULL"
    sqlguard.assert_single_select(sql)  # no raise
    kattfot_explore.validate_pii(sql)  # no raise — none of these are PII either


# =============================================================================
# 2. Table-reference extraction
# =============================================================================


def test_a_backticked_project_qualified_reference_is_also_refused(capsys, monkeypatch):
    """The existing suite pins the BARE `some_project.dataset.table` form;
    the backticked form (how BigQuery SQL is actually usually written) must
    refuse the same way, with the same hint."""
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT a FROM `some_project.{SPINE}`"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "dataset.table" in err


def test_a_backticked_wildcard_table_is_refused(capsys, monkeypatch):
    """`` `dataset.table*` `` — a BigQuery wildcard-table scan across every
    table sharing the prefix. The backtick capture keeps the '*' as part of
    the ref text, so it cannot exact-match the register's `dataset.table`
    key and is refused as unregistered, exactly like any other unknown
    surface."""
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM `{WEEKLY}*`"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert f"{WEEKLY}*" in err


def test_an_unbackticked_wildcard_table_passes_the_register_but_cannot_execute(capsys, monkeypatch):
    """NOT a hole. Without backticks, '*' is not a legal character in a bare
    BigQuery identifier — this input is invalid SQL on the real target
    engine regardless of what this lexical layer does with it, and BigQuery
    itself is the backstop. Lexically, `_TARGET`'s bareword pattern
    (`[A-Za-z_][\\w.]*`) simply stops matching at the '*': the extracted ref
    is the exact registered table name with the '*' left dangling outside
    it. That lets it clear `validate_surfaces` (it IS the registered
    table)... and then the leftover '*' makes the qualified SQL a syntax
    error on sqlite, so the query never runs and no row is ever returned —
    a crash, not a wildcard scan, and not the row this lane promises either
    way."""
    msg = run_execution_error(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {WEEKLY}*"],
        capsys, monkeypatch,
    )
    assert "syntax error" in msg


def test_information_schema_reference_is_refused(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql", "SELECT table_name FROM analytics.INFORMATION_SCHEMA.TABLES"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err


def test_external_query_is_lexed_as_an_unregistered_pseudo_table(capsys, monkeypatch):
    """EXTERNAL_QUERY is a table-valued function, not a table — but the
    lexical target scanner cannot tell the difference, and that is exactly
    what saves it here: `EXTERNAL_QUERY` is captured as the FROM target and
    refused as an unregistered surface, never reaching a connection id or
    federated query string as anything but a refused identifier."""
    err = run_refused(
        ["--question", "q", "--sql",
         "SELECT * FROM EXTERNAL_QUERY('my_connection', 'SELECT 1')"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "EXTERNAL_QUERY" in err


def test_ml_predict_is_lexed_as_an_unregistered_pseudo_table(capsys, monkeypatch):
    """Same shape as EXTERNAL_QUERY. Note the nested subquery over a REAL
    registered surface inside the call is also picked up by the same global
    FROM/JOIN scan — but `ML.PREDICT` itself is refused first (refuse()
    exits on the first bad ref found), so nothing executes either way."""
    err = run_refused(
        ["--question", "q", "--sql",
         f"SELECT * FROM ML.PREDICT(MODEL `proj.ds.m`, (SELECT * FROM {SPINE}))"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "ML.PREDICT" in err


def test_a_comma_join_smuggling_a_second_unregistered_table_is_refused(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT a FROM {SPINE}, analytics.secret_table"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "analytics.secret_table" in err


def test_an_unregistered_table_inside_a_subquery_from_is_refused(capsys, monkeypatch):
    """`FROM (subquery)` is not itself a parseable target (the scanner moves
    on), but the subquery's own FROM is a second, independent match of the
    same global scan — subqueries need no special-casing, per the
    docstring."""
    err = run_refused(
        ["--question", "q", "--sql",
         "SELECT * FROM (SELECT * FROM analytics.secret_table) t"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "analytics.secret_table" in err


def test_a_cte_shadowing_a_bare_surface_name_reads_nothing_real(capsys, monkeypatch):
    """A CTE cannot be named `analytics.cart_checkout_session_spine` — a CTE
    name is a bare identifier, dots are not legal there — so the only
    'shadow' available is the bare, dataset-prefix-less form. Doing that
    does not smuggle in the real registered table: the CTE body here reads
    no table at all, so `surfaces` comes back empty and nothing beyond the
    CTE's own literal is ever touched. Not a hole — there is nothing to
    read."""
    out = run_ok(
        ["--question", "q", "--sql",
         "WITH cart_checkout_session_spine AS (SELECT 1 AS x) "
         "SELECT * FROM cart_checkout_session_spine"],
        capsys, monkeypatch,
    )
    assert out["surfaces"] == []
    assert out["rows"] == [{"x": 1}]


def test_a_cte_shadow_does_not_hide_a_real_unregistered_table_in_its_own_body(capsys, monkeypatch):
    """The more dangerous-looking version: the CTE's OWN body reads a real,
    unregistered table. Shadowing the outer reference with the CTE's bare
    name does not hide that inner FROM — the same global scan picks it up
    independently of nesting, and it still refuses."""
    err = run_refused(
        ["--question", "q", "--sql",
         "WITH secret_table AS (SELECT * FROM analytics.secret_table) "
         "SELECT * FROM secret_table"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "analytics.secret_table" in err


def test_unnest_with_alias_and_cross_join_form_stays_exempt():
    sql = f"SELECT x FROM {SPINE} CROSS JOIN UNNEST(SPLIT(terminal_branch, ',')) AS x"
    assert sqlguard.referenced_tables(sql) == {SPINE}


def test_a_longer_suffixed_name_does_not_collide_with_the_shorter_registered_one():
    """Substring/prefix confusion, checked in both directions. `_TARGET`'s
    greedy `[\\w.]*` swallows the whole `..._v2` suffix into ONE ref, so the
    ref extracted is the full longer string, not the registered prefix —
    it is looked up (and refused) as itself, never silently matched against
    the shorter registered surface."""
    ref = sqlguard.referenced_tables(f"SELECT a FROM {SPINE}_v2")
    assert ref == {f"{SPINE}_v2"}
    assert SPINE not in ref


# =============================================================================
# 3. SELECT * detection
# =============================================================================


@pytest.mark.parametrize("sql", [
    "SELECT * EXCEPT(x) FROM t",
    "SELECT * REPLACE(y AS z) FROM t",
    "SELECT a.* FROM t a",
    "SELECT a, * FROM t",
    "SELECT id, (SELECT * FROM x) FROM t",  # scalar subquery star
    "SELECT DISTINCT * FROM t",
])
def test_select_star_shapes_the_identifier_scan_cannot_see_through(sql):
    assert sqlguard.uses_select_star(sql) is True


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM t",
    "SELECT a, COUNT(*) AS n FROM t",
    "SELECT COUNT(*), a FROM t",
])
def test_count_star_variants_are_never_mistaken_for_projection_star(sql):
    assert sqlguard.uses_select_star(sql) is False


def test_alias_star_is_refused_on_an_unattested_surface(tmp_path, capsys, monkeypatch):
    """The existing suite pins bare `SELECT *`; `alias.*` is a different
    regex branch in `uses_select_star` and needs its own pin end-to-end."""
    reg = tmp_path / "exploration.yaml"
    reg.write_text(
        "surfaces:\n"
        f"  {WEEKLY}:\n"
        "    status: live\n"  # no select_star attestation
    )
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(reg))
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT w.* FROM {WEEKLY} w"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-STAR)" in err


def test_select_star_over_a_self_contained_cte_needs_no_attestation(capsys, monkeypatch):
    """Not a hole: `validate_select_star` only checks the surfaces that
    `validate_surfaces` actually resolved. When `SELECT *` sits over a CTE
    that reads no registered surface (section 2's shadow case), `entries`
    is empty, so there is nothing to be unattested — and nothing real for
    the star to expose. The gate correctly has nothing to check because
    there is nothing behind it."""
    out = run_ok(
        ["--question", "q", "--sql",
         "WITH cart_checkout_session_spine AS (SELECT 1 AS x, 2 AS y) "
         "SELECT * FROM cart_checkout_session_spine"],
        capsys, monkeypatch,
    )
    assert out["surfaces"] == []
    assert out["rows"] == [{"x": 1, "y": 2}]


# =============================================================================
# 4. The R6 identifier scan
# =============================================================================


def test_struct_dotted_pii_access_is_refused(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT s.customer_email FROM {SPINE} s"],
        capsys, monkeypatch,
    )
    assert "ERROR (R6)" in err
    assert "customer_email" in err


def test_pii_in_an_on_clause_is_refused(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql",
         f"SELECT a.channel FROM {SPINE} a JOIN {WEEKLY} b ON a.ssn = b.channel"],
        capsys, monkeypatch,
    )
    assert "ERROR (R6)" in err


def test_pii_in_order_by_is_refused(capsys, monkeypatch):
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT channel FROM {SPINE} ORDER BY date_of_birth"],
        capsys, monkeypatch,
    )
    assert "ERROR (R6)" in err


def test_r6_fires_before_a_dialect_error_the_construct_would_also_cause(capsys, monkeypatch):
    """QUALIFY is BigQuery-only and would fail translation on sqlite (see
    dialect.py's residual checks) — but PII refusal runs upstream of the
    engine entirely (`validate_pii` is called before `qualify_sql` / the
    adapter), so the R6 refusal is what actually surfaces, not an
    EXPLORE-DIALECT error. Governance refuses before the engine is ever
    asked to do anything with the SQL, translatable or not."""
    err = run_refused(
        ["--question", "q", "--sql",
         f"SELECT channel FROM {SPINE} "
         f"QUALIFY ROW_NUMBER() OVER (PARTITION BY ssn ORDER BY channel) = 1"],
        capsys, monkeypatch,
    )
    assert "ERROR (R6)" in err
    assert "EXPLORE-DIALECT" not in err


def test_backticked_pii_identifier_is_still_caught(capsys, monkeypatch):
    """Backticks are not comments or string quotes, so `strip_noise` leaves
    them untouched — but `identifiers()`'s word-token scan does not care
    about backticks either way; it just finds the word inside them."""
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT `email` FROM {SPINE}"],
        capsys, monkeypatch,
    )
    assert "ERROR (R6)" in err
    assert "'email'" in err


def test_pii_split_across_string_concatenation_cannot_form_an_identifier(capsys, monkeypatch):
    """Not a hole. 'em' and 'ail' are two separate string literals — both
    stripped as noise before the identifier scan runs — joined at RUNTIME
    by `||` into a VALUE, never a column reference. Standard SQL has no way
    to use a computed string as a dynamic identifier in a plain SELECT (the
    one construct that could, EXECUTE IMMEDIATE / dynamic SQL, is itself a
    denied statement-level keyword — see section 1). So this concatenation
    can produce the word 'email' in the output, but it can never make R6
    see a PII *column* that was not already named in the SQL text."""
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT 'em' || 'ail' AS x FROM {SPINE} LIMIT 1"],
        capsys, monkeypatch,
    )
    assert out["rows"] == [{"x": "email"}]


def test_json_path_pii_is_an_honest_gap_not_a_hole():
    """DOCUMENTED GAP, asserting current reality (the same pattern
    test_adversarial_injection.py uses for the exfiltration leg it cannot
    close). `JSON_VALUE(payload, '$.email')` names the extracted field only
    inside a string literal — `'$.email'` — which `strip_noise` removes
    before the identifier scan runs, by design: the design's whole
    contract is that literal VALUES are data, not something the scan reads,
    because the alternative (scanning literals) would refuse a query whose
    WHERE clause merely contains an email address as a comparison value.
    Fixing this probe would mean picking one of those two false positives
    back up.

    The durable backstops, named rather than assumed: the register's
    `select_star: true` attestation is CHECKED against the surface's own
    DDL by tests/test_view_ddl_pii.py, so a JSON blob column named `payload`
    can only reach this lane if a human scanned that view's definition and
    judged it PII-free; and warehouse-side column-level ACLs are the
    catch-all this module's own docstring already names for lexical gaps.
    If kattfot later adds literal-aware scanning (accepting the WHERE-value
    false-positive as the cost), this test flips to asserting the refusal —
    and that flip is the signal the gap closed, not a silent regression.
    """
    sql = f"SELECT JSON_VALUE(payload, '$.email') AS x FROM {SPINE}"
    assert "email" not in sqlguard.identifiers(sql)
    kattfot_explore.validate_pii(sql)  # does not raise — the gap, made explicit


# =============================================================================
# 5. qualify_sql's rewrite
# =============================================================================


def test_a_surface_name_inside_a_string_literal_is_rewritten_too(capsys, monkeypatch):
    """NOT a hole — a correctness quirk, pinned rather than fixed.
    `qualify_sql` substitutes the ref text everywhere in the raw SQL,
    literals included, because it works on whole-word boundaries over the
    unstripped string rather than re-parsing clause structure. A filter
    that happens to compare a column against the surface's own name as a
    STRING VALUE gets that value silently rewritten to the backticked
    qualified form. This changes what the literal compares against (a
    correctness surprise an analyst could hit), but it does not widen what
    is read, expose PII, or defeat the register: the rewrite only ever
    touches `ref` strings that `validate_surfaces` already authorized, and
    the query still reads only the one registered table it always did."""
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT channel FROM {SPINE} WHERE terminal_branch = '{SPINE}' GROUP BY channel"],
        capsys, monkeypatch,
    )
    assert f"`${{KATTFOT_BIGQUERY_PROJECT}}.{SPINE}`" in out["query"].split("WHERE")[0]
    # the literal's text visibly changed too — the quirk being pinned:
    assert f"'`${{KATTFOT_BIGQUERY_PROJECT}}.{SPINE}`'" in out["query"]
    assert out["row_count"] == 0  # the rewritten literal cannot match real data


def test_an_already_backticked_reference_is_not_double_backticked(capsys, monkeypatch):
    """The docstring says an analyst may write either the bare or the
    backticked form; both must resolve to the same canonical output so the
    query_sha receipt does not depend on which one was typed."""
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM `{SPINE}`"],
        capsys, monkeypatch,
    )
    assert f"FROM `${{KATTFOT_BIGQUERY_PROJECT}}.{SPINE}`" in out["query"]
    assert "``" not in out["query"]


# =============================================================================
# 6. Parameter binding
# =============================================================================


def test_a_hostile_param_value_travels_as_data_end_to_end(capsys, monkeypatch):
    payload = "x'; DROP TABLE orders; --"
    out = run_ok(
        ["--question", "q", "--sql",
         f"SELECT channel, COUNT(*) AS n FROM {SPINE} WHERE market = @m GROUP BY channel",
         "--param", f"m={payload}"],
        capsys, monkeypatch,
    )
    assert out["query_params"] == {"m": payload}
    assert "@m" in out["query"]
    assert payload not in out["query"]  # the SQL text never carries the payload
    assert out["row_count"] == 0  # no market equals it; the point is nothing broke


def test_a_hostile_param_name_that_matches_no_placeholder_fails_cleanly(capsys, monkeypatch):
    """The hostile text lives in the PARAM NAME this time, not the value.
    Since the bound dict key is the literal string `m';--` (not `m`), it
    never matches the `:m` placeholder `dialect.translate_sql` produced
    from `@m` — sqlite3 raises for a missing binding, which surfaces as a
    plain execution error, not a governance breach and not SQL text
    mutation. The hostile characters never reach the SQL string at all."""
    msg = run_execution_error(
        ["--question", "q", "--sql",
         f"SELECT channel, COUNT(*) AS n FROM {SPINE} WHERE market = @m GROUP BY channel",
         "--param", "m';--=US"],
        capsys, monkeypatch,
    )
    assert "binding" in msg.lower()


def test_an_unused_hostile_param_name_is_silently_harmless(capsys, monkeypatch):
    """A --param whose name is never referenced by the SQL (hostile or not)
    is just an extra, unused key in the bind dict — sqlite3 does not
    require every supplied name to be consumed, so the query runs exactly
    as if the flag had been omitted."""
    out = run_ok(
        ["--question", "q", "--sql", f"SELECT COUNT(*) AS n FROM {SPINE}",
         "--param", "x'; DROP TABLE--=payload"],
        capsys, monkeypatch,
    )
    assert out["query_params"] == {"x'; DROP TABLE--": "payload"}
    assert out["rows"][0]["n"] > 0
