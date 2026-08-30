"""base.py — the warehouse adapter contract.

An adapter is the *only* thing in this plugin that touches a database. Everything
upstream of it — allow-list validation, PII refusal, maturity gating, SQL
assembly — is pure string building over canonical BigQuery dialect, which is why
swapping the engine is one dispatch rather than a rewrite.

Two responsibilities, deliberately kept separate:

1. **Execution.** `run_query(project, sql, params) -> list[dict]`. Same signature
   the pre-adapter `spine.run_query` had, so callers did not change.

2. **Capability advertisement.** Engines differ in what they can actually do, and
   pretending otherwise is how a "portable" layer starts lying. Percentiles are
   the honest example: BigQuery has `APPROX_QUANTILES` and can compute them
   inside the warehouse; sqlite has no percentile function at all. Rather than
   shim one badly in SQL, an adapter declares `CAP_NATIVE_PERCENTILES` or not,
   and implements `percentiles()` however it must. A skill asks for p50/p90/p99
   and gets them; the difference in *where* the arithmetic happened is reported,
   not hidden.

`capabilities()` exists so a skill can also branch on availability before
building a query — e.g. surface "computed client-side over N rows" in an
artifact's provenance instead of implying warehouse push-down.
"""

from __future__ import annotations

import datetime as dt

# Capability tokens. A skill checks these rather than the engine name, so adding
# a third engine never means editing a skill.
CAP_NATIVE_PERCENTILES = "native_percentiles"
CAP_PUSHDOWN_AGGREGATION = "pushdown_aggregation"
CAP_USER_IDENTITY = "user_identity"
CAP_DATE_TYPE = "date_type"

ALL_CAPABILITIES = frozenset(
    {
        CAP_NATIVE_PERCENTILES,
        CAP_PUSHDOWN_AGGREGATION,
        CAP_USER_IDENTITY,
        CAP_DATE_TYPE,
    }
)


class Adapter:
    """Base class. Subclasses set `name`/`CAPABILITIES` and implement `run_query`."""

    #: Engine slug, matching the value `KATTFOT_ENGINE` accepts.
    name: str = "abstract"

    #: What this engine can genuinely do. Not aspirational.
    CAPABILITIES: frozenset = frozenset()

    #: True when results come from committed fixtures rather than a warehouse.
    #: Callers stamp artifacts with the renderers' `mock: true` watermark on the
    #: strength of this, so a fixture-backed number can never be mistaken for a
    #: measured one.
    fixture_backed: bool = False

    def capabilities(self) -> frozenset:
        return self.CAPABILITIES

    def supports(self, capability: str) -> bool:
        if capability not in ALL_CAPABILITIES:
            raise ValueError(
                f"unknown capability {capability!r}; known: {sorted(ALL_CAPABILITIES)}"
            )
        return capability in self.CAPABILITIES

    # -- execution ---------------------------------------------------------

    def run_query(self, project: str, sql: str, params: dict) -> list[dict]:
        raise NotImplementedError

    # -- view naming -------------------------------------------------------

    def qualify_view(self, project: str, view: str) -> str:
        """How to reference a curated view in **canonical** (BigQuery) SQL.

        Both adapters return the same fully-qualified form on purpose. Canonical
        SQL is dialect-stable so an artifact's `query_sha` receipt does not
        depend on which engine served it; the flattening to a bare sqlite table
        name happens once, inside `dialect.translate_sql`. `table_name()` below
        is the adapter-side half of that mapping, for anything that needs the
        physical name (the fixture loader, chiefly).
        """
        return f"{project}.{view}"

    def table_name(self, view: str) -> str:
        """The physical relation this view is served from on this engine."""
        return view

    # -- percentiles (the capability-difference showcase) ------------------

    def percentiles(
        self,
        project: str,
        *,
        from_sql: str,
        where_sql: str,
        value_expr: str,
        percentiles: list[int],
        params: dict,
        group_expr: str | None = None,
        group_by: dict | list | None = None,
    ) -> list[dict]:
        """Percentiles of `value_expr`, optionally per group.

        Grouping takes either form:

          `group_expr="channel"`         one anonymous group column, keyed `grp`
          `group_by={"channel": "channel", "market": "market"}`
                                         one column per entry, keyed by its
                                         own name

        Returns one dict per group carrying the group columns as **separate
        keys** plus `n`, `mean`, and a `p{N}` per requested percentile — the
        same `list[dict]` shape `query-metric` returns, so a table or a chart
        consumes it with no extra unpacking. `group_expr`'s single `grp` key is
        the degenerate case of that and is kept because a one-dimension caller
        reads better without naming the column twice.

        Rows where `value_expr` is NULL are excluded, so each group's stats are
        over the population that actually reached both endpoints of the
        transition being measured.

        Implemented per-engine: pushed into the warehouse where the engine can,
        computed in Python where it cannot. Callers do not need to know which,
        but `_percentile_basis` on every row records which happened.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Grouping normalization (shared by both adapters)
# ---------------------------------------------------------------------------


def normalize_groups(
    group_expr: str | None = None, group_by: dict | list | None = None
) -> list[tuple[str, str]]:
    """`(group_expr, group_by)` -> an ordered `[(alias, sql_expr), ...]` list.

    One function so the two adapters cannot disagree about what a caller asked
    for. `group_by` wins when both are given; `group_expr` maps to the single
    alias `grp`. An alias that is not a bare identifier is refused rather than
    interpolated: aliases reach the SQL text (they cannot be bound), so this is
    the one place a caller-supplied string could become SQL.
    """
    if group_by:
        pairs = list(group_by.items()) if isinstance(group_by, dict) else list(group_by)
    elif group_expr:
        pairs = [("grp", group_expr)]
    else:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for alias, expr in pairs:
        alias = str(alias)
        if not alias.isidentifier():
            raise ValueError(
                f"group alias {alias!r} is not a bare identifier; aliases are "
                f"emitted into SQL text and cannot be bound as parameters"
            )
        if alias in seen:
            raise ValueError(f"duplicate group alias {alias!r}")
        seen.add(alias)
        out.append((alias, expr))
    return out


# ---------------------------------------------------------------------------
# Shared, engine-independent percentile arithmetic
# ---------------------------------------------------------------------------


def percentile_from_sorted(values: list[float], pct: int) -> float | None:
    """Nearest-rank percentile over a pre-sorted list.

    Matches `APPROX_QUANTILES(x, 100)[OFFSET(pct)]`'s definition rather than the
    interpolating one, so the two engines agree on the *definition* even though
    BigQuery's is approximate at scale and this is exact. Documented in the
    stage-timing artifact rather than papered over.
    """
    if not values:
        return None
    idx = int(round((pct / 100.0) * (len(values) - 1)))
    return values[max(0, min(idx, len(values) - 1))]


def group_sort_key(key: tuple) -> tuple:
    """Total ordering over group-value tuples, NULLs last.

    A group column can be text, an integer, or NULL in the same result set, and
    Python refuses to compare those. Sorting on `(is_none, text)` gives a stable
    order that matches what `ORDER BY` produces on the warehouse path closely
    enough for a table to read the same on either engine.
    """
    return tuple((v is None, "" if v is None else str(v)) for v in key)


def summarize_percentiles(values: list[float], percentiles: list[int]) -> dict:
    """`{n, mean, p50, p90, ...}` for one group's values."""
    clean = sorted(v for v in values if v is not None)
    out: dict = {"n": len(clean)}
    out["mean"] = (sum(clean) / len(clean)) if clean else None
    for pct in percentiles:
        out[f"p{pct}"] = percentile_from_sorted(clean, pct)
    return out


def iso(value):
    """ISO-8601 text for a date/datetime, pass-through otherwise."""
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    return value
