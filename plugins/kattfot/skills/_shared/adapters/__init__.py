"""adapters — engine dispatch for the one place this plugin touches a database.

Selected by `KATTFOT_ENGINE`, defaulting to **sqlite** so that a fresh clone runs
the full compose chain with zero configuration and zero credentials. That default
is a template decision, not a production one: the shipped fixtures are synthetic,
every fixture-backed artifact is watermarked, and pointing this at a real
warehouse is one environment variable (`KATTFOT_ENGINE=bigquery`) plus
`KATTFOT_BIGQUERY_PROJECT`.

    from _shared import adapters
    adapters.resolve_engine()            # "sqlite" | "bigquery"
    adapters.get_adapter()               # the instance for the resolved engine
    adapters.get_adapter("bigquery")     # or an explicit one

Adapters are cached per engine per process: the sqlite adapter builds its
in-memory fixture db lazily on first query, so repeated reads in one run (a
funnel's eight stage metrics, say) load the CSVs once.
"""

from __future__ import annotations

import os

# Relative imports throughout this package, deliberately. `_shared` modules are
# reachable both as `_shared.x` and as bare `x` (the skill scripts and the spine
# skills historically use different styles), which is harmless for stateless
# modules but not for this one: two module identities would mean two `_CACHE`
# dicts, so a test that switched engines would clear one and read the other.
from .base import (  # noqa: F401  (re-exported)
    ALL_CAPABILITIES,
    CAP_DATE_TYPE,
    CAP_DRY_RUN,
    CAP_NATIVE_PERCENTILES,
    CAP_PUSHDOWN_AGGREGATION,
    CAP_USER_IDENTITY,
    Adapter,
    group_sort_key,
    normalize_groups,
    percentile_from_sorted,
    summarize_percentiles,
)

ENGINE_ENV = "KATTFOT_ENGINE"
DEFAULT_ENGINE = "sqlite"
KNOWN_ENGINES = ("sqlite", "bigquery")

_CACHE: dict[str, Adapter] = {}


def resolve_engine(engine: str | None = None) -> str:
    """Explicit argument wins, then `KATTFOT_ENGINE`, then the sqlite default."""
    name = (engine or os.environ.get(ENGINE_ENV) or DEFAULT_ENGINE).strip().lower()
    if name not in KNOWN_ENGINES:
        raise SystemExit(
            f"ERROR: unknown engine {name!r}. Set {ENGINE_ENV} to one of "
            f"{', '.join(KNOWN_ENGINES)} (default: {DEFAULT_ENGINE})."
        )
    return name


def get_adapter(engine: str | None = None) -> Adapter:
    name = resolve_engine(engine)
    if name not in _CACHE:
        if name == "bigquery":
            from .bigquery_adapter import BigQueryAdapter

            _CACHE[name] = BigQueryAdapter()
        else:
            from .sqlite_adapter import SqliteAdapter

            _CACHE[name] = SqliteAdapter()
    return _CACHE[name]


def reset_cache() -> None:
    """Drop cached adapters. Used by tests that switch engines mid-process."""
    _CACHE.clear()
