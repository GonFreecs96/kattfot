"""Lexical guard for exploratory (ad-hoc T3) SQL.

The exploratory lane accepts free-form SQL, so its guards cannot lean on a
metric's allow-list — they have to read the statement itself. This module is
that reader, and it follows the same philosophy as the access-path hook
(`hooks/scripts/bq_path_matcher.py`): **lexical, blunt, and honest about it**.
Over-blocking is accepted where it reduces false negatives; a wall is allowed
to be blunt.

What it checks:
  * exactly one statement, and that statement is a SELECT (or WITH ... SELECT);
    statement-level write/DDL/scripting keywords are refused outright.
  * which tables the statement references (FROM / JOIN targets, including
    comma joins), so the caller can hold them against the exploration register.
    CTE names and UNNEST are exempted.
  * every identifier in the statement, so the caller can run each through the
    R6 PII denylist — projection, WHERE, ON, everywhere, not just dims/filters.
  * whether the projection uses `SELECT *` / `alias.*` (which the identifier
    scan cannot see through).

What it is NOT: a SQL parser. Known gaps, stated rather than discovered:
  * table references are found lexically. Deliberate obfuscation, or a
    construct outside the FROM/JOIN/comma shapes handled here, can hide one.
    The durable backstops are that the query still runs as the individual
    human's own identity — it can read nothing the human cannot — and
    warehouse-side ACLs.
  * the identifier scan is blunt: an alias named `email` refuses even when no
    such column exists. Rename the alias. The same bluntness over-blocks in
    the other direction too: `EXTRACT(DAY FROM x)` / `TRIM(x FROM y)` read
    their `FROM` as a table keyword, refusing `x` as an unregistered
    "surface" it never was. Accepted, per the opening paragraph — a false
    refusal, never a false pass.
  * string literals and comments are stripped before every check, so a
    keyword or table name inside a quoted value neither triggers nor hides
    anything **when the lexing agrees with the engine that will run the
    query**. `strip_noise`'s string-literal pattern honors `\`-escaped
    quotes and `#` line comments — real in BigQuery, so on that engine this
    holds without qualification: whatever this module treats as "inside a
    string" really is, as far as BigQuery's own parser is concerned. sqlite
    (used only for the committed synthetic fixtures, never for real data)
    disagrees: it has no backslash-escaping, so a value like `'x\' AND ...`
    is one string to this guard but two sqlite tokens with live SQL between
    them, and a table name in that gap is invisible to `referenced_tables`
    /`identifiers` yet real to sqlite. tests/test_explore_adversarial.py
    pins this exactly, bounded to the fixture engine.

Pure and dependency-free; raises SqlGuardError, which callers convert into a
governance refusal (exit 5).
"""

from __future__ import annotations

import re


class SqlGuardError(ValueError):
    """A guard refusal. The message is user-facing and names the rule."""


# --- Noise stripping --------------------------------------------------------
#
# One left-to-right alternation pass: at any position the earliest match wins,
# so a `--` inside a string literal is consumed as part of the string, and a
# quote inside a comment is consumed as part of the comment. Triple-quoted
# strings come before their single-quoted forms so the longer match is tried
# first at the same position.
_NOISE = re.compile(
    r"/\*.*?\*/"            # block comment
    r"|--[^\n]*"            # line comment
    r"|#[^\n]*"             # line comment (BigQuery also accepts #)
    r"|'''(?:\\.|[^\\])*?'''"  # triple-quoted string
    r'|"""(?:\\.|[^\\])*?"""'
    r"|'(?:\\.|[^'\\])*'"   # string literal
    r'|"(?:\\.|[^"\\])*"',
    re.DOTALL,
)


def strip_noise(sql: str) -> str:
    """Remove comments and string literals (each replaced by one space).

    For scanning only — the stripped text is never executed. Values the query
    binds arrive as parameters, so nothing governance-relevant lives in a
    literal; what does live there (an email address in a WHERE string) must
    not trip the identifier scan, and a keyword in a comment must not trip the
    statement gate.
    """
    return _NOISE.sub(" ", sql)


# --- Statement gate ---------------------------------------------------------

#: Statement-level keywords that have no place in a read-only exploratory
#: query. Matched as whole words on the noise-stripped text, so `created_at`
#: and `last_update` do not fire, and `-- drop this later` cannot either.
_DENY_KEYWORDS = re.compile(
    r"\b(insert|update|delete|merge|create|drop|alter|truncate|grant|revoke"
    r"|call|execute|begin|commit|declare|export|load)\b",
    re.IGNORECASE,
)

_FIRST_WORD = re.compile(r"[A-Za-z_]+")


def assert_single_select(sql: str) -> None:
    """Refuse anything that is not exactly one SELECT statement.

    Checks, in order: non-empty; no interior `;` (one trailing `;` is
    tolerated); first keyword is SELECT or WITH; no write/DDL/scripting
    keyword anywhere in the statement.
    """
    stripped = strip_noise(sql).strip()
    if not stripped:
        raise SqlGuardError("Empty SQL. Provide one SELECT statement.")
    body = stripped[:-1].rstrip() if stripped.endswith(";") else stripped
    if ";" in body:
        raise SqlGuardError(
            "Multiple statements detected (interior ';'). The exploratory "
            "lane runs exactly one SELECT statement per query."
        )
    first = _FIRST_WORD.match(body.lstrip("( \t\r\n"))
    if not first or first.group(0).lower() not in ("select", "with"):
        raise SqlGuardError(
            f"Statement must start with SELECT or WITH, got "
            f"{(first.group(0) if first else body[:20])!r}. The exploratory "
            f"lane is read-only SELECT."
        )
    denied = _DENY_KEYWORDS.search(body)
    if denied:
        raise SqlGuardError(
            f"Statement-level keyword {denied.group(0).upper()!r} is refused "
            f"— the exploratory lane is read-only SELECT, with no DDL, DML, "
            f"or scripting."
        )


# --- Table-reference extraction ---------------------------------------------

_FROM_JOIN = re.compile(r"\b(?:from|join)\b", re.IGNORECASE)
_TARGET = re.compile(r"\s*(`[^`]+`|[A-Za-z_][\w.]*)")
_ALIAS = re.compile(r"\s+(?:as\s+)?([A-Za-z_]\w*)", re.IGNORECASE)
_COMMA = re.compile(r"\s*,")
_CTE_NAME = re.compile(r"\b([A-Za-z_]\w*)\s+as\s*\(", re.IGNORECASE)
_OPEN_PAREN = re.compile(r"\s*\(")

#: Targets that look like tables to a lexical reader but are not.
_BUILTIN_TARGETS = {"unnest"}


def cte_names(sql: str) -> set:
    """Every `<name> AS (` definition — CTE names a FROM may legally reference
    without the register knowing them."""
    return {m.group(1) for m in _CTE_NAME.finditer(strip_noise(sql))}


def _skip_parenthesized(text: str, pos: int):
    """If `text[pos:]` opens with `(` (after optional whitespace), return the
    index just past its balanced closing `)` — else None.

    Depth-only bracket counting is safe here because `text` has already had
    every string literal and comment replaced by noise-stripping, so a `(`
    or `)` that was inside a former string cannot appear in what's left.
    Unbalanced input (should not occur in real SQL) returns None so the
    caller falls back to stopping, rather than looping past the end.
    """
    m = _OPEN_PAREN.match(text, pos)
    if not m:
        return None
    depth = 0
    i = m.end() - 1  # the '(' itself
    n = len(text)
    while i < n:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def referenced_tables(sql: str) -> set:
    """The table references of the statement: each FROM/JOIN target plus any
    comma-joined continuation, backticks stripped, CTE names and UNNEST
    excluded. Subqueries need no special handling — their own FROMs match
    the same scan.

    A parenthesized subquery contributes no ref of its own, but a comma list
    keeps going past one: `FROM (SELECT ...) a, real_table` must still find
    `real_table`. Skipping the subquery's balanced parens (rather than
    abandoning the whole comma list the moment a target isn't a bare or
    backticked name) is what makes that continuation reachable. The same
    move applies when a bareword target IS matched but is itself a call —
    `UNNEST(...)`, `EXTERNAL_QUERY(...)`, `ML.PREDICT(...)` — so its argument
    list doesn't get mistaken for "nothing more to see" either: without
    skipping past `(...)` there, `FROM UNNEST(arr) AS x, real_table` loses
    `real_table` the identical way, just one token later.
    """
    text = strip_noise(sql)
    exempt = {c.lower() for c in cte_names(sql)} | _BUILTIN_TARGETS
    refs: set = set()
    for kw in _FROM_JOIN.finditer(text):
        pos = kw.end()
        while True:
            m = _TARGET.match(text, pos)
            if m:
                name = m.group(1).strip("`")
                if name.lower() not in exempt:
                    refs.add(name)
                pos = m.end()
                # A table-function call: the matched name is immediately
                # followed by its argument list (`UNNEST(arr)`,
                # `ML.PREDICT(MODEL ..., (SELECT ...))`). Skip it so a
                # comma-sibling after the call is reachable below, exactly
                # like the subquery case.
                call_end = _skip_parenthesized(text, pos)
                if call_end is not None:
                    pos = call_end
            else:
                skip_to = _skip_parenthesized(text, pos)
                if skip_to is None:
                    break  # neither a name nor a subquery — nothing parseable, move on
                pos = skip_to  # the subquery itself; its own FROM/JOIN match separately
            alias = _ALIAS.match(text, pos)
            if alias:
                pos = alias.end()
            comma = _COMMA.match(text, pos)
            if not comma:
                break
            pos = comma.end()
    return refs


# --- Identifier scan --------------------------------------------------------

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def identifiers(sql: str) -> set:
    """Every word token in the noise-stripped statement — keywords, functions,
    columns, aliases alike. The caller runs each through the PII denylist;
    scanning everything is the bluntness that makes the scan complete."""
    return set(_IDENTIFIER.findall(strip_noise(sql)))


# --- SELECT * ---------------------------------------------------------------

#: `SELECT *`, `SELECT DISTINCT *`, `SELECT a, *`, or `alias.*` — projection
#: shapes the identifier scan cannot see through. `COUNT(*)` does not match:
#: its `*` follows `(`, not SELECT or a comma.
_SELECT_STAR = re.compile(
    r"(?:\bselect\s+(?:distinct\s+)?|,\s*)(?:[A-Za-z_]\w*\s*\.\s*)?\*",
    re.IGNORECASE,
)


def uses_select_star(sql: str) -> bool:
    return _SELECT_STAR.search(strip_noise(sql)) is not None
