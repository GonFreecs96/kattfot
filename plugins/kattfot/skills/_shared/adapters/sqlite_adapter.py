"""sqlite_adapter.py — the offline adapter, backed by committed fixtures.

Why sqlite and not a Python re-implementation of each query: because the point of
an offline path is to exercise the *real* SQL. A hand-rolled aggregation in
Python can only serve the SUM/SUM shape — it cannot serve `COUNTIF` stage gating,
terminal-branch decomposition, or percentile timing without a second, divergent
implementation per skill. Loading the fixture CSV into `sqlite3` (standard
library — no new dependency) means the canonical BigQuery SQL a skill built is
what actually runs, translated once and executed.

Governance on this path
-----------------------
`assert_user_identity()` is **deliberately not called here**, and that is an
explicit branch rather than an omission. A fixture-backed read touches no
warehouse, no credentials, and no data that belongs to anyone — requiring a GCP
login to read a committed CSV would be theatre, and would make `git clone &&
pytest` impossible. The guard exists to keep warehouse queries attributable to a
person; there is nothing here to attribute.

What that costs, stated plainly rather than glossed: on this engine the identity
guard is not merely satisfied, it is *not exercised*. Every other governance
control is (allow-lists, PII refusal, tier gate, maturity cutoffs, manifest
receipts) — they run upstream of the adapter and cannot be reached around. But a
reader who believes "kattfot always runs as a human" should know that the sentence
applies to the warehouse path. In exchange, every result this adapter returns is
marked `fixture_backed`, which callers turn into the renderers' `mock: true`
watermark, so a fixture number cannot be mistaken for a measured one downstream.
`tests/test_adapters.py` pins both halves of that trade.
"""

from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path

from .. import dialect
from .base import (
    CAP_PUSHDOWN_AGGREGATION,
    Adapter,
    group_sort_key,
    normalize_groups,
    summarize_percentiles,
)

#: Default fixture location, relative to the repo root. Overridable with
#: `KATTFOT_SAMPLE_DIR` (the same variable the CSV-stub oracle path uses, so one
#: setting points both offline routes at the same data).
DEFAULT_SAMPLE_SUBDIR = ("data", "kattfot", "sample")

_BOOL_TRUE = {"true", "t", "yes", "y"}
_BOOL_FALSE = {"false", "f", "no", "n"}


def repo_root() -> Path:
    # this file: <repo>/plugins/kattfot/skills/_shared/adapters/sqlite_adapter.py
    return Path(__file__).resolve().parents[5]


def sample_dir() -> Path:
    env = os.environ.get("KATTFOT_SAMPLE_DIR")
    if env:
        return Path(env)
    return repo_root().joinpath(*DEFAULT_SAMPLE_SUBDIR)


# ---------------------------------------------------------------------------
# CSV -> sqlite loading
# ---------------------------------------------------------------------------


def _coerce(raw: str):
    """CSV text -> the narrowest sensible sqlite value.

    Integers must land as INTEGER or `SUM()` produces text concatenation
    surprises; dates must stay TEXT in ISO-8601 form because that is the only
    representation whose lexicographic order matches chronological order (which
    is what `BETWEEN` on a cohort column relies on). Empty string becomes NULL so
    `COUNT(col)` and `AVG(col)` skip it the way BigQuery would.
    """
    s = raw.strip()
    if s == "":
        return None
    low = s.lower()
    if low in _BOOL_TRUE:
        return 1
    if low in _BOOL_FALSE:
        return 0
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def load_csv_into(conn: sqlite3.Connection, path: Path, table: str) -> int:
    """Create `table` from a CSV and return the row count.

    Columns are all declared without an affinity: sqlite is dynamically typed,
    and letting each value keep the type `_coerce` gave it is exactly what makes
    the ISO-date/integer distinction survive.
    """
    with path.open(newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise SystemExit(f"ERROR: fixture CSV {path} is empty (no header row).")
        cols = [h.strip() for h in header]
        quoted = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(f'CREATE TABLE "{table}" ({quoted})')
        rows = ([_coerce(v) for v in raw] for raw in reader if raw)
        cur = conn.executemany(
            f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', rows
        )
        return cur.rowcount if cur.rowcount is not None else 0


class SqliteAdapter(Adapter):
    name = "sqlite"
    # No native percentiles and no user identity: both are honest omissions, and
    # `percentiles()` below covers the first by computing client-side.
    CAPABILITIES = frozenset({CAP_PUSHDOWN_AGGREGATION})
    fixture_backed = True

    def __init__(self, fixtures: Path | None = None):
        self._fixtures = fixtures
        self._conn: sqlite3.Connection | None = None
        self._loaded: dict[str, int] = {}

    # -- fixture db --------------------------------------------------------

    @property
    def fixtures(self) -> Path:
        return self._fixtures if self._fixtures is not None else sample_dir()

    def connection(self) -> sqlite3.Connection:
        """One in-memory db per adapter instance, holding one table per fixture CSV.

        In-memory rather than a file on disk: there is nothing to persist, the
        fixtures are the source of truth, and a stale cache file would be a
        genuinely confusing failure mode in a template.
        """
        if self._conn is not None:
            return self._conn
        d = self.fixtures
        if not d.exists():
            raise SystemExit(
                f"ERROR: no fixture directory at {d}. Generate one with\n"
                f"  python3 scripts/generate_fixtures.py\n"
                f"or point KATTFOT_SAMPLE_DIR at your own CSVs."
            )
        csvs = sorted(d.glob("*.csv"))
        if not csvs:
            raise SystemExit(
                f"ERROR: fixture directory {d} contains no .csv files. "
                f"Run scripts/generate_fixtures.py."
            )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        for path in csvs:
            n = load_csv_into(conn, path, path.stem)
            self._loaded[path.stem] = n
        conn.commit()
        self._conn = conn
        return conn

    def loaded_tables(self) -> dict[str, int]:
        self.connection()
        return dict(self._loaded)

    def table_name(self, view: str) -> str:
        return dialect.table_name(view)

    # -- execution ---------------------------------------------------------

    def run_query(self, project: str, sql: str, params: dict) -> list[dict]:
        """Translate the canonical SQL, run it, return plain dicts.

        `project` is accepted and ignored: it is part of the canonical SQL's
        table reference (which the translator flattens) and is kept in the
        signature so the two adapters stay drop-in interchangeable.
        """
        # No assert_user_identity() here — see the module docstring for why this
        # is an explicit branch and what it costs.
        translated, bind = dialect.translate(sql, params)
        conn = self.connection()
        try:
            cur = conn.execute(translated, bind)
        except sqlite3.Error as e:
            raise SystemExit(
                f"ERROR: fixture query failed on the sqlite adapter: {e}\n"
                f"Translated SQL:\n{translated}\n"
                f"Params: {bind}\n"
                f"Loaded fixture tables: {sorted(self.loaded_tables())}"
            )
        return [dict(r) for r in cur.fetchall()]

    # -- percentiles: computed client-side --------------------------------

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
        """Pull the latency column and compute p50/p90/p99 in Python.

        This is the capability difference the adapter seam exists to express, not
        a shim pretending otherwise: sqlite has no percentile aggregate, so the
        rows come back and `summarize_percentiles` (shared with nothing
        engine-specific) does the arithmetic. The `_percentile_basis` field on
        each row records that, so an artifact says where the number was computed.

        Grouping is by the whole tuple of group columns, and each column is
        emitted under its own alias — so a two-dimension call returns one row per
        (dim_a, dim_b) pair with both columns present, not an opaque composite.
        """
        groups = normalize_groups(group_expr, group_by)
        select_parts = [f"{expr} AS {alias}" for alias, expr in groups]
        select_parts.append(f"{value_expr} AS value")
        sql = (
            "SELECT\n  "
            + ",\n  ".join(select_parts)
            + f"\nFROM {from_sql}\n{where_sql}"
        )
        rows = self.run_query(project, sql, params)

        buckets: dict = {}
        for r in rows:
            if r.get("value") is None:
                continue
            key = tuple(r.get(alias) for alias, _ in groups)
            buckets.setdefault(key, []).append(float(r["value"]))

        out = []
        for key in sorted(buckets, key=group_sort_key):
            rec = {alias: value for (alias, _), value in zip(groups, key)}
            rec.update(summarize_percentiles(buckets[key], percentiles))
            rec["_percentile_basis"] = "exact nearest-rank (computed client-side)"
            out.append(rec)
        return out
