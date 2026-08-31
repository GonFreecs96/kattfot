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
_CTE_NAME = re.compile(r"\b([A-Za-z_]\w*)\s+as\s*\(", re.IGNORECASE)

#: Targets that look like tables to a lexical reader but are not.
_BUILTIN_TARGETS = {"unnest"}

#: Clause-level keywords that END a FROM/JOIN item list, once reached at
#: bracket depth 0. Reaching one means the statement has moved past its
#: table list into a different clause (or a set operator introducing a new
#: SELECT), so nothing after it is a sibling of the tables just scanned.
#: Deliberately NOT here: ON, USING, JOIN, AS, WITH, FOR, TABLESAMPLE,
#: PIVOT/UNPIVOT, OFFSET — every one of those is legal INSIDE a FROM item
#: list (a join condition, a table decorator, UNNEST's WITH OFFSET alias
#: clause) and must be scanned straight through, never stopped at.
_CLAUSE_BOUNDARY = re.compile(
    r"\b(?:where|group|order|having|qualify|window|limit"
    r"|union|intersect|except)\b",
    re.IGNORECASE,
)


def cte_names(sql: str) -> set:
    """Every `<name> AS (` definition — CTE names a FROM may legally reference
    without the register knowing them."""
    return {m.group(1) for m in _CTE_NAME.finditer(strip_noise(sql))}


def _resync_to_comma(text: str, pos: int):
    """From `pos`, find the next top-level (bracket-depth 0) comma that
    starts another FROM-list sibling — or None if the FROM/JOIN item list
    ends before one.

    This is the one mechanism `referenced_tables` leans on for everything
    that can sit between a from-item and its next comma-joined sibling
    besides a plain bareword alias: a JOIN ... ON/USING condition,
    TABLESAMPLE, FOR SYSTEM_TIME AS OF, PIVOT/UNPIVOT, a table-function's
    own argument list (`UNNEST(arr)`, `ML.PREDICT(MODEL m, (SELECT ...))`),
    UNNEST ... WITH OFFSET, or a shape not yet invented. None of those
    introduce a new top-level comma or a real clause boundary, so scanning
    straight through them — respecting bracket depth, so a comma nested
    inside a `(...)` argument list or a `[...]` array literal is never
    mistaken for one — is sufficient. Nothing about any of those
    constructs needs to be named or special-cased for this to work.

    Stops (returns None) on: a real clause boundary at depth 0
    (`_CLAUSE_BOUNDARY`, or a `;` — belt and suspenders, since
    `assert_single_select` already guarantees one statement by the time
    the lane calls this); a `)`/`]` closing when depth is already 0 (this
    FROM sits inside an enclosing subquery or array whose own close was
    just reached — nothing past it belongs to this item list, and the
    enclosing from-item's own resync call, made independently, is what
    picks up any sibling after IT); or running off the end of the text.
    """
    depth = 0
    n = len(text)
    i = pos
    while i < n:
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            if depth == 0:
                return None
            depth -= 1
        elif depth == 0:
            if ch == ",":
                return i + 1
            if ch == ";":
                return None
            if _CLAUSE_BOUNDARY.match(text, i):
                return None
        i += 1
    return None


def referenced_tables(sql: str) -> set:
    """The table references of the statement: each FROM/JOIN target plus
    every later sibling in the same comma list, backticks stripped, CTE
    names and UNNEST excluded. Subqueries need no special handling — their
    own FROMs match the same scan.

    A from-item is a target — a bareword, a backticked name, or nothing
    parseable at all (a parenthesized subquery) — followed by however much
    text belongs to it: an alias, a join condition, a table-function's own
    argument list, a decorator. `_resync_to_comma` is what finds where that
    ends: either the next depth-0 comma (another sibling — the loop keeps
    going) or a real clause boundary (the item list is over). Earlier
    versions of this function tried to enumerate the shapes that can
    follow a target one at a time (an alias, then a subquery's parens,
    then a table-function call's parens...) and kept missing another one;
    resyncing to the next structurally-meaningful token, rather than
    parsing what's in between, is what makes the enumeration unnecessary.
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
            resynced = _resync_to_comma(text, pos)
            if resynced is None:
                break
            pos = resynced
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
