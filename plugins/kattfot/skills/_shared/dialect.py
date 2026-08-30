"""dialect.py — the one place BigQuery SQL becomes sqlite SQL.

Every skill in this plugin authors its SQL in **one canonical dialect:
BigQuery**. That is deliberate. The alternative — teaching each builder to emit
whichever dialect the active engine wants — scatters `if engine ==` branches
through code whose actual job is governance, and makes the generated SQL (and
therefore the artifact's `query_sha` receipt) differ depending on where it ran.
Here, a fixture-backed run and a BigQuery run of the same metric produce the
*same* canonical SQL and the *same* fingerprint; only the string handed to the
driver differs. See `_shared/adapters/` for the execution side.

Because everything funnels through one function, the translation set can be
enumerated — and it is, exhaustively, in `TRANSLATIONS` below. That list is what
makes the golden test (`tests/test_dialect.py`) meaningful rather than
decorative: it is the complete contract, not a sample of it.

Totality is enforced, not asserted. `translate_sql` runs `assert_translated()`
afterwards, which scans for any BigQuery-only construct still present. A new
builder that emits something unlisted fails loudly at the point of translation
instead of producing SQL that sqlite silently mis-parses (or, worse, parses into
a different number).

Public API:
    translate_sql(sql)            -> sqlite SQL
    translate_params(params)      -> sqlite-bindable params
    translate(sql, params)        -> both
    table_name(view)              -> flattened `dataset.view` -> `view`
"""

from __future__ import annotations

import datetime as dt
import re

# ---------------------------------------------------------------------------
# The translation set
# ---------------------------------------------------------------------------
# Each entry: (id, what BigQuery emits, what sqlite gets, why). This is the
# complete set — `assert_translated()` fails on anything outside it, so the
# table cannot silently fall behind the code.
TRANSLATIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "named_params",
        "@name",
        ":name",
        "BigQuery names parameters with @; sqlite3's named paramstyle uses :. "
        "The bind dict keys are bare in both, so only the SQL text moves.",
    ),
    (
        "qualified_identifier",
        "`project.dataset.view`",
        '"view"',
        "sqlite has no project/dataset namespace. The trailing element is the "
        "table name the fixture loader creates; quoting keeps it safe against "
        "keywords. (An ATTACH-per-dataset scheme would preserve two levels, but "
        "the fixture db is one file with one table per view, so flattening is "
        "both sufficient and simpler to reason about.)",
    ),
    (
        "safe_divide",
        "SAFE_DIVIDE(a, b)",
        "(CAST(a AS REAL) / NULLIF(b, 0))",
        "SAFE_DIVIDE returns NULL rather than raising on a zero denominator. "
        "NULLIF reproduces that exactly. The CAST is load-bearing: sqlite does "
        "integer division on two integers, so 84/100 would be 0 without it.",
    ),
    (
        "countif",
        "COUNTIF(cond)",
        "SUM(CASE WHEN (cond) THEN 1 ELSE 0 END)",
        "No COUNTIF in sqlite. SUM-over-CASE matches on the empty group too: "
        "both return NULL, not 0, which keeps rate denominators honest.",
    ),
    (
        "cast_string",
        "CAST(x AS STRING)",
        "CAST(x AS TEXT)",
        "STRING is BigQuery's name for the type sqlite calls TEXT.",
    ),
    (
        "safe_cast",
        "SAFE_CAST(x AS STRING)",
        "CAST(x AS TEXT)",
        "sqlite's CAST never raises, so SAFE_CAST's safety is already the "
        "default; the STRING -> TEXT rename still applies.",
    ),
    (
        "if_function",
        "IF(cond, a, b)",
        "CASE WHEN (cond) THEN a ELSE b END",
        "sqlite gained IIF in 3.32; CASE works on every version and is what "
        "the spine's stage gating already reads as.",
    ),
    (
        "date_diff",
        "DATE_DIFF(a, b, DAY)",
        "CAST(julianday(a) - julianday(b) AS INTEGER)",
        "julianday parses the ISO-8601 text these fixtures store. Argument "
        "order matches: DATE_DIFF is a-minus-b, like the subtraction.",
    ),
    (
        "timestamp_diff",
        "TIMESTAMP_DIFF(a, b, MINUTE)",
        "CAST((julianday(a) - julianday(b)) * 1440 AS INTEGER)",
        "Same julianday basis, scaled by unit (DAY 1, HOUR 24, MINUTE 1440, "
        "SECOND 86400).",
    ),
    (
        "current_date",
        "CURRENT_DATE()",
        "DATE('now')",
        "Both yield ISO-8601 YYYY-MM-DD, so downstream comparisons are "
        "unaffected.",
    ),
    (
        "approx_quantiles",
        "APPROX_QUANTILES(x, 100)[OFFSET(p)]",
        "(refused — see adapters.percentiles)",
        "sqlite has no percentile function, and faking one in SQL (a "
        "correlated-subquery rank walk) would be slow and subtly wrong on "
        "ties. Percentiles are an advertised adapter *capability* instead: the "
        "BigQuery adapter pushes APPROX_QUANTILES down, the sqlite adapter "
        "pulls the column and computes p50/p90/p99 in Python. A skill asks "
        "`adapter.percentiles(...)` and never sees the difference, so this "
        "construct must never reach the translator.",
    ),
    (
        "date_params",
        "ScalarQueryParameter(name, 'DATE', date(2026, 3, 2))",
        "'2026-03-02'",
        "sqlite has no date type. Dates bind as ISO-8601 text, whose "
        "lexicographic order matches chronological order — which is the only "
        "reason `cohort BETWEEN :start AND :end` is correct there. Emitting "
        "epoch ints or MM/DD/YYYY would compare wrong *silently*; "
        "tests/test_adapters.py pins this.",
    ),
)

TRANSLATION_IDS = tuple(t[0] for t in TRANSLATIONS)


class DialectError(Exception):
    """A construct that has no faithful sqlite equivalent reached the translator."""


# ---------------------------------------------------------------------------
# Balanced-paren function rewriting
# ---------------------------------------------------------------------------
# SAFE_DIVIDE(SUM(a), SUM(b)) cannot be rewritten with a regex: the arguments
# contain their own parens and commas. These helpers walk the string instead,
# which keeps nesting correct at any depth.


def _split_args(argtext: str) -> list[str]:
    """Split a call's argument text on top-level commas only."""
    args: list[str] = []
    depth = 0
    current: list[str] = []
    in_str: str | None = None
    for ch in argtext:
        if in_str:
            current.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in "'\"":
            in_str = ch
            current.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current or args:
        args.append("".join(current).strip())
    return args


def _rewrite_calls(sql: str, func: str, rewrite) -> str:
    """Replace every `func(...)` call in `sql` with `rewrite(args)`.

    Matches the name case-insensitively and only on a word boundary, so
    `COUNTIF` does not fire inside `MY_COUNTIF`. Innermost-last: the scan
    restarts after each replacement so nested calls of the same function
    resolve too.
    """
    pattern = re.compile(rf"\b{func}\s*\(", re.IGNORECASE)
    out = sql
    guard = 0
    while True:
        m = pattern.search(out)
        if m is None:
            return out
        guard += 1
        if guard > 500:  # pragma: no cover - runaway guard
            raise DialectError(f"runaway rewrite of {func}()")
        open_idx = m.end() - 1
        depth = 0
        close_idx = None
        in_str: str | None = None
        for i in range(open_idx, len(out)):
            ch = out[i]
            if in_str:
                if ch == in_str:
                    in_str = None
                continue
            if ch in "'\"":
                in_str = ch
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_idx = i
                    break
        if close_idx is None:
            raise DialectError(f"unbalanced parentheses in {func}() call")
        args = _split_args(out[open_idx + 1:close_idx])
        out = out[: m.start()] + rewrite(args) + out[close_idx + 1:]


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(r"@(\w+)")
# Anything between backticks, so an unexpanded `${VAR}` project placeholder (the
# template's way of not shipping somebody else's billing account) flattens like
# any other qualified name instead of surviving as a residual backtick.
_QUALIFIED_RE = re.compile(r"`([^`]+)`")
_APPROX_RE = re.compile(r"\bAPPROX_QUANTILES\s*\(", re.IGNORECASE)
_CAST_STRING_RE = re.compile(r"\b(SAFE_CAST|CAST)\s*\((.+?)\s+AS\s+STRING\s*\)", re.IGNORECASE)
_CURRENT_DATE_RE = re.compile(r"\bCURRENT_DATE\s*\(\s*\)", re.IGNORECASE)

_DIFF_SCALE = {"DAY": 1, "HOUR": 24, "MINUTE": 1440, "SECOND": 86400}


def table_name(view: str) -> str:
    """`dataset.view` (or `project.dataset.view`) -> the bare table name.

    The sqlite fixture db holds one table per curated view, named for the view's
    last path element. Kept public because the fixture loader must agree with
    the translator on the name.
    """
    return view.strip().strip("`").split(".")[-1]


def _rewrite_safe_divide(args: list[str]) -> str:
    if len(args) != 2:
        raise DialectError(f"SAFE_DIVIDE expects 2 arguments, got {len(args)}")
    num, den = args
    return f"(CAST({num} AS REAL) / NULLIF({den}, 0))"


def _rewrite_countif(args: list[str]) -> str:
    if len(args) != 1:
        raise DialectError(f"COUNTIF expects 1 argument, got {len(args)}")
    return f"SUM(CASE WHEN ({args[0]}) THEN 1 ELSE 0 END)"


def _rewrite_if(args: list[str]) -> str:
    if len(args) != 3:
        raise DialectError(f"IF() expects 3 arguments, got {len(args)}")
    cond, then, other = args
    return f"CASE WHEN ({cond}) THEN {then} ELSE {other} END"


def _rewrite_datediff(unit_default: str):
    def rewrite(args: list[str]) -> str:
        if len(args) != 3:
            raise DialectError(f"expects 3 arguments, got {len(args)}")
        a, b, unit = args[0], args[1], args[2].strip().upper()
        scale = _DIFF_SCALE.get(unit)
        if scale is None:
            raise DialectError(
                f"unsupported date/timestamp diff unit {unit!r}; "
                f"supported: {sorted(_DIFF_SCALE)}"
            )
        if scale == 1:
            return f"CAST(julianday({a}) - julianday({b}) AS INTEGER)"
        return f"CAST((julianday({a}) - julianday({b})) * {scale} AS INTEGER)"

    return rewrite


# ---------------------------------------------------------------------------
# Totality guard
# ---------------------------------------------------------------------------
# Anything here surviving translation means a builder emitted a construct the
# table above does not cover. Failing loudly beats handing sqlite something it
# will misread.
_RESIDUAL_CHECKS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"@\w+"), "BigQuery-style @parameter"),
    (re.compile(r"`"), "backticked identifier"),
    (re.compile(r"\bSAFE_DIVIDE\s*\(", re.IGNORECASE), "SAFE_DIVIDE()"),
    (re.compile(r"\bCOUNTIF\s*\(", re.IGNORECASE), "COUNTIF()"),
    (re.compile(r"\bAS\s+STRING\b", re.IGNORECASE), "CAST(... AS STRING)"),
    (re.compile(r"\bSAFE_CAST\s*\(", re.IGNORECASE), "SAFE_CAST()"),
    (re.compile(r"\bDATE_DIFF\s*\(", re.IGNORECASE), "DATE_DIFF()"),
    (re.compile(r"\bTIMESTAMP_DIFF\s*\(", re.IGNORECASE), "TIMESTAMP_DIFF()"),
    (re.compile(r"\bCURRENT_DATE\s*\(", re.IGNORECASE), "CURRENT_DATE()"),
    (re.compile(r"\bAPPROX_QUANTILES\s*\(", re.IGNORECASE), "APPROX_QUANTILES()"),
    (re.compile(r"\bOFFSET\s*\(", re.IGNORECASE), "array OFFSET() subscript"),
    (re.compile(r"\bDATE_TRUNC\s*\(", re.IGNORECASE), "DATE_TRUNC()"),
    (re.compile(r"\bQUALIFY\b", re.IGNORECASE), "QUALIFY clause"),
    (re.compile(r"\bSAFE\.", re.IGNORECASE), "SAFE. function prefix"),
    (re.compile(r"\bUNNEST\s*\(", re.IGNORECASE), "UNNEST()"),
    (re.compile(r"\bSTRUCT\s*<", re.IGNORECASE), "STRUCT<> type"),
)

# `IF(` is checked separately: a bare `IF` also appears inside `CASE WHEN ...`
# free text and in column names like `is_fraud_flag`, so the residual scan only
# looks for it as a call.
_RESIDUAL_IF = re.compile(r"(?<![\w.])IF\s*\(", re.IGNORECASE)


def assert_translated(sql: str) -> None:
    """Raise if any BigQuery-only construct survived translation."""
    if _RESIDUAL_IF.search(sql):
        raise DialectError(
            "untranslated BigQuery construct in sqlite SQL: IF() call. "
            "Add a rule to dialect.TRANSLATIONS."
        )
    for rx, label in _RESIDUAL_CHECKS:
        if rx.search(sql):
            raise DialectError(
                f"untranslated BigQuery construct in sqlite SQL: {label}. "
                f"Add a rule to dialect.TRANSLATIONS (and its golden case) "
                f"rather than letting sqlite guess."
            )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def translate_sql(sql: str) -> str:
    """Canonical BigQuery SQL -> sqlite SQL. Total over `TRANSLATIONS`."""
    if _APPROX_RE.search(sql):
        raise DialectError(
            "APPROX_QUANTILES() has no faithful sqlite equivalent. Percentiles "
            "are an adapter capability, not a translated construct: call "
            "adapters.get_adapter().percentiles(...), which pushes down on "
            "BigQuery and computes in Python on sqlite."
        )

    out = sql
    # Function rewrites first: they inject CAST/CASE text that the later
    # scalar rules (AS STRING, parameters) must also see.
    out = _rewrite_calls(out, "SAFE_DIVIDE", _rewrite_safe_divide)
    out = _rewrite_calls(out, "COUNTIF", _rewrite_countif)
    out = _rewrite_calls(out, "DATE_DIFF", _rewrite_datediff("DAY"))
    out = _rewrite_calls(out, "TIMESTAMP_DIFF", _rewrite_datediff("MINUTE"))
    out = _rewrite_calls(out, "IF", _rewrite_if)

    # CAST(x AS STRING) / SAFE_CAST(x AS STRING) -> CAST(x AS TEXT).
    out = _CAST_STRING_RE.sub(lambda m: f"CAST({m.group(2)} AS TEXT)", out)
    out = _CURRENT_DATE_RE.sub("DATE('now')", out)
    out = _QUALIFIED_RE.sub(lambda m: f'"{table_name(m.group(1))}"', out)
    out = _PARAM_RE.sub(lambda m: f":{m.group(1)}", out)

    assert_translated(out)
    return out


def translate_params(params: dict) -> dict:
    """Coerce bind values to types sqlite3 accepts.

    Dates and datetimes become ISO-8601 **text** (`.isoformat()`), which is the
    only representation whose lexicographic order matches chronological order —
    the property `BETWEEN` relies on. Booleans become 0/1 (sqlite has no bool).
    Everything else passes through untouched.
    """
    out: dict = {}
    for name, value in params.items():
        if isinstance(value, bool):
            out[name] = int(value)
        elif isinstance(value, dt.datetime):
            out[name] = value.isoformat(sep=" ")
        elif isinstance(value, dt.date):
            out[name] = value.isoformat()
        else:
            out[name] = value
    return out


def translate(sql: str, params: dict) -> tuple[str, dict]:
    """Both halves of the translation, as one call."""
    return translate_sql(sql), translate_params(params)
