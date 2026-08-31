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
  (c) is a genuine HOLE — the probe defeats a stated guarantee, and
      `sqlguard.py` gets a real (minimal) fix alongside the pinning test.
      Section 2's comma-joined-table-after-a-subquery case is exactly this:
      it was missed on the first pass through this file and caught on a
      second, more adversarial re-read — worth saying plainly rather than
      quietly folding the fix in as if it had been there from the start. A
      THIRD pass caught the same bug's sibling, which the second pass's fix
      had not generalized far enough to close: a matched bareword target
      immediately followed by `(` (a table-function call — `UNNEST(...)`
      chief among them, since it is also exempt from the register) lost a
      comma-sibling the identical way a subquery did, just one branch of
      the same `if` over. Fixed generally this time — any table-function
      call, not UNNEST-specifically — which is the shape of lesson this
      history is recording: patch the specific probe that found the bug,
      but check the *general* shape before calling it closed. Section 7
      documents two more findings from the second re-read that were judged
      NOT to need a code fix (a bounded, engine-specific string-literal
      dialect gap, and a known-shape over-block) and pins them instead,
      with the reasoning for that call.

Sections mirror the probe catalog this suite was built from:
  1. the statement gate (noise vs. real statements, every deny keyword)
  2. table-reference extraction (wildcards, escape hatches, shadowing,
     comma-after-subquery, comma-after-table-function-call)
  3. SELECT * detection
  4. the R6 identifier scan
  5. qualify_sql's rewrite
  6. parameter binding
  7. re-attack findings judged NOT to need a `sqlguard.py` change

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


# --- a real hole, found on re-attack: comma-joined table after a subquery --
#
# `referenced_tables`'s comma-continuation loop used to `break` the instant
# a FROM target wasn't a bare or backticked name — which is exactly what a
# leading `(subquery)` looks like. That silently abandoned the rest of the
# comma list: `FROM (SELECT 1) sub, real_table` produced `refs == set()`,
# so `validate_surfaces`/`validate_select_star` had nothing to check and let
# `real_table` through unregistered, unattested, and unscanned by anything
# that depends on `refs` — a plain legal cross join, no obfuscation. Fixed
# by skipping the subquery's balanced parens instead of abandoning the loop,
# so the comma continuation is still walked afterward. These tests would
# fail against the pre-fix guard (`refs == set()`, no EXPLORE-SURFACE).


@pytest.mark.parametrize("sql", [
    # bare (dataset-prefix-less) form — the exact original repro: this text
    # also happens to equal the sqlite fixture's PHYSICAL table name, so
    # pre-fix this did not just skip the register check, it returned real
    # rows from it.
    "SELECT w.channel FROM (SELECT 1 AS x) sub, cart_checkout_funnel_weekly w LIMIT 3",
    f"SELECT a FROM {SPINE} t1, (SELECT 1 AS x) sub2, analytics.secret_table t3",
    "SELECT a FROM (SELECT 1) sub, analytics.secret_table",
])
def test_a_table_comma_joined_after_a_subquery_is_refused(sql, capsys, monkeypatch):
    err = run_refused(["--question", "q", "--sql", sql], capsys, monkeypatch)
    assert "ERROR (EXPLORE-SURFACE)" in err


def test_select_star_after_a_subquery_comma_needs_attestation(tmp_path, capsys, monkeypatch):
    """The compounding shape: SELECT * over a surface reached only past a
    leading subquery in the FROM list. Before the fix, `refs` (and so
    `entries`) came back empty, so `validate_select_star` had nothing
    unattested to complain about — a legal cross join defeated the
    register AND the star gate AND (for a truly PII-bearing column) the R6
    scan's usefulness at once, since nothing named the surface for any of
    them to check. Post-fix, the surface is found and the attestation is
    enforced exactly as it would be without the leading subquery."""
    reg = tmp_path / "exploration.yaml"
    reg.write_text(f"surfaces:\n  {WEEKLY}:\n    status: live\n")  # no select_star
    monkeypatch.setenv("KATTFOT_EXPLORATION_FILE", str(reg))
    err = run_refused(
        ["--question", "q", "--sql", f"SELECT * FROM (SELECT 1 AS z) sub, {WEEKLY} w LIMIT 3"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-STAR)" in err


@pytest.mark.parametrize("sql, expected", [
    # subquery alone, no sibling: nothing to add, loop still terminates.
    ("SELECT a FROM (SELECT 1 AS a) sub", set()),
    # subquery then a real sibling.
    (f"SELECT a FROM (SELECT 1) sub, {SPINE} t", {SPINE}),
    # a real table BEFORE the subquery, then a real sibling AFTER it — the
    # pre-fix code kept the first (its own separate iteration never touched
    # the subquery) but lost the second; both must survive now.
    (f"SELECT a FROM {SPINE} t1, (SELECT 1) sub2, {WEEKLY} t3", {SPINE, WEEKLY}),
    # doubly-nested parens around the subquery.
    (f"SELECT a FROM ((SELECT 1)) sub, {SPINE} t", {SPINE}),
    # JOIN after a subquery was already fine pre-fix (JOIN is its own
    # independent top-level match, not reached via comma-continuation) —
    # kept here as a regression pin, not a new fix.
    (f"SELECT a FROM (SELECT 1) sub JOIN {SPINE} t ON 1=1", {SPINE}),
])
def test_referenced_tables_around_a_subquery_comma_sibling(sql, expected):
    assert sqlguard.referenced_tables(sql) == expected


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


# --- the SAME bug's sibling, found on a second re-attack: a matched target
# immediately followed by "(" -----------------------------------------------
#
# The subquery fix above only patched the branch where `_TARGET` FAILS (a
# bare `(`). It missed the branch where `_TARGET` SUCCEEDS on a bareword
# that is itself a table-function call — `UNNEST(...)`, `ML.PREDICT(...)`,
# `EXTERNAL_QUERY(...)`: the match consumes only the name, leaving `pos` at
# the `(`, where `_ALIAS` and `_COMMA` both fail (neither matches a bare
# `(`) and the loop broke — abandoning the comma continuation exactly like
# the original bug, just one token later. `UNNEST` is the dangerous case
# specifically because it is also exempt: the call was silently skipped
# AND everything after it was silently dropped, with nothing left in `refs`
# for `validate_surfaces`/`validate_select_star` to check. Fixed generally
# (not UNNEST-specifically): any successfully matched target immediately
# followed by `(` has its argument list skipped via `_skip_parenthesized`
# before the alias/comma step, the same treatment the subquery branch
# already got. `EXTERNAL_QUERY`/`ML.PREDICT` weren't full holes before this
# (the call name itself is unregistered and already refuses), but they hit
# the identical code path, which is why one of them gets a test too.


@pytest.mark.parametrize("sql", [
    "SELECT n FROM UNNEST([1,2]) AS n, analytics.secret_table t",
    "SELECT * FROM UNNEST([1]) AS n, analytics.secret_table t",  # compounding w/ SELECT *
])
def test_a_table_comma_joined_after_unnest_is_refused(sql, capsys, monkeypatch):
    err = run_refused(["--question", "q", "--sql", sql], capsys, monkeypatch)
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "analytics.secret_table" in err


@pytest.mark.parametrize("sql, expected", [
    # UNNEST alone: still exempt, still no false positive.
    ("SELECT x FROM UNNEST(arr) AS x", set()),
    # a real table, then UNNEST, then nothing — pre-existing shape, still fine.
    ("SELECT x FROM analytics.a, UNNEST(arr) AS x", {"analytics.a"}),
    # UNNEST, then a real sibling — the fix.
    ("SELECT n FROM UNNEST([1,2]) AS n, analytics.secret_table t", {"analytics.secret_table"}),
    # a real table, THEN unnest, THEN another real sibling — nothing lost
    # on either side of the call.
    (f"SELECT a FROM {SPINE} t1, UNNEST([1]) AS n, {WEEKLY} t3", {SPINE, WEEKLY}),
    # one more instance of the general shape, per the reviewer's ask: any
    # bareword-call target, not just UNNEST, must not eat its sibling.
    ("SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1') e, analytics.secret_table t",
     {"EXTERNAL_QUERY", "analytics.secret_table"}),
])
def test_referenced_tables_around_a_table_function_call_comma_sibling(sql, expected):
    assert sqlguard.referenced_tables(sql) == expected


def test_join_after_unnest_still_finds_the_next_real_table():
    """JOIN is its own independent top-level `_FROM_JOIN` match, so it was
    never at risk from this bug — pinned as a regression guard rather than
    left to be assumed."""
    sql = f"SELECT a FROM UNNEST([1]) u JOIN {SPINE} t ON 1=1"
    assert sqlguard.referenced_tables(sql) == {SPINE}


def test_a_real_unnest_query_is_not_falsely_refused_at_the_register_gate(capsys, monkeypatch):
    """A real, legitimate query — spine CROSS JOIN UNNEST over one of its
    own array-shaped columns — must clear every governance gate: the fix
    must not turn into a new false refusal for the ordinary case UNNEST
    exists to serve. It cannot be proven all the way through `main()`
    on sqlite: UNNEST has no sqlite translation at all (a dialect
    limitation, unrelated to governance — see dialect.py's residual
    checks), so this asserts the failure that DOES surface is
    EXPLORE-DIALECT, never EXPLORE-SURFACE — proof the register/star/PII
    gates all passed and only the fixture engine's translation gap stopped
    it."""
    err = run_refused(
        ["--question", "q", "--sql",
         f"SELECT COUNT(*) AS n FROM {SPINE} t "
         f"CROSS JOIN UNNEST(SPLIT(t.terminal_branch, ',')) AS x"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-DIALECT)" in err
    assert "EXPLORE-SURFACE" not in err


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


# =============================================================================
# 7. Re-attack findings judged NOT to need a sqlguard.py change
# =============================================================================


def test_backslash_escaped_quote_hides_a_table_from_the_guard_on_sqlite_only():
    """DOCUMENTED, BOUNDED GAP — not fixed, per the module docstring's known-
    gaps list (updated alongside this test).

    `strip_noise`'s string-literal pattern honors `\\`-escaped quotes, which
    is correct BigQuery lexing: on the real target engine, whatever this
    guard treats as "inside a string" really is one, as far as BigQuery's
    own parser is concerned, so nothing executable is hidden THERE. sqlite
    (the fixture engine — committed synthetic data only, never real) has no
    backslash-escaping: a lone `'` always closes a string, full stop. Fed
    the same text, the two engines disagree about where the string ends,
    and this guard follows BigQuery's rule even when the query is about to
    run on sqlite.

    `'x\\' AND cohort_week IN (SELECT c FROM tuff_secret_table) --'` is ONE
    string to this guard (the backslash "escapes" the first `'`, so
    scanning continues to the LAST `'`) — `tuff_secret_table` never reaches
    `identifiers()` or `referenced_tables()`. sqlite reads it differently:
    `'x\\'` closes at the second character (no escaping), and everything
    from there to the next real `'` is live SQL, `tuff_secret_table`
    included. Proven end to end in
    test_backslash_escaped_quote_end_to_end_fails_only_at_the_sqlite_layer
    below: the query is not refused, and fails only because the fixture db
    has no table by that name — i.e. this guard let it through, and only
    the ABSENCE of a matching CSV is what stops it. A fork whose
    `KATTFOT_SAMPLE_DIR` happened to hold a same-named table would not be
    stopped by this guard.

    Not fixed because the two directions trade off against each other and
    neither is free: rejecting backslash-escapes entirely would make this
    guard MIS-lex real BigQuery queries that legitimately escape a quote,
    over-blocking on the production engine to protect a fixture engine that
    is synthetic-only by design; a dual-dialect-aware lexer is more parser
    than this module claims to be. The bound is real, though: this can only
    matter on sqlite, sqlite only ever serves committed synthetic fixtures
    (or whatever a fork points `KATTFOT_SAMPLE_DIR` at — a documented,
    deliberate escape hatch, not this guard's job to close), and on the
    engine that actually holds real data (BigQuery, reached via the
    individual human's own ADC identity) the guard's lexing is correct. If
    this ever changes — say, sqlite becomes a real per-deployment path —
    this test flips from asserting the hidden name to asserting it is
    caught, and that flip is the signal to act on, not a silent regression.
    """
    sql = (
        f"SELECT cohort_week FROM {SPINE} "
        "WHERE 'x\\' AND cohort_week IN (SELECT c FROM tuff_secret_table) --' IS NULL"
    )
    assert "tuff_secret_table" not in sqlguard.identifiers(sql)
    assert "tuff_secret_table" not in sqlguard.referenced_tables(sql)


def test_backslash_escaped_quote_end_to_end_fails_only_at_the_sqlite_layer(capsys, monkeypatch):
    """Companion to the gap test above: proves the guard does not refuse
    this query (no EXPLORE-STATEMENT/-SURFACE/R6 in the failure), and that
    the ONLY thing standing between this input and a live table read on
    sqlite is the fixture directory not happening to contain a table named
    `tuff_secret_table` — not this guard."""
    sql = (
        f"SELECT cohort_week FROM {SPINE} "
        "WHERE 'x\\' AND cohort_week IN (SELECT c FROM tuff_secret_table) --' IS NULL"
    )
    msg = run_execution_error(["--question", "q", "--sql", sql], capsys, monkeypatch)
    assert "no such table: tuff_secret_table" in msg


def test_extract_from_and_trim_from_over_block_the_column_as_a_table(capsys, monkeypatch):
    """NIT, pinned as documented over-blocking, not fixed. `_FROM_JOIN`
    matches any `\\bfrom\\b`, including the FROM inside `EXTRACT(part FROM
    expr)` / `TRIM(expr FROM chars)` function-call syntax — standard SQL,
    not obfuscation. The scanner reads `cart_created_at` as a FROM target
    and refuses it as an unregistered "surface" it never was.

    This is the safe direction the module's opening paragraph names
    explicitly ("over-blocking is accepted... a wall is allowed to be
    blunt"): a real, legitimate query gets a false refusal, but nothing
    is ever under-blocked by it — there is no way for this same mechanism
    to let an unauthorized table through. A tighter fix (excluding a FROM
    immediately preceded by EXTRACT(/TRIM(/SUBSTRING() is possible but not
    done here, to keep the change surface to the genuine hole in section 2
    and the honest gap above; this test exists so the over-block is a
    tracked, intentional trade-off rather than a surprise a real analyst
    hits first."""
    err = run_refused(
        ["--question", "q", "--sql",
         f"SELECT EXTRACT(DAY FROM cart_created_at) AS d FROM {SPINE}"],
        capsys, monkeypatch,
    )
    assert "ERROR (EXPLORE-SURFACE)" in err
    assert "cart_created_at" in err
