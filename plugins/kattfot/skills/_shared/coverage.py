"""Instrumentation coverage register — the "can I measure X yet?" layer.

Loads `config/coverage.yaml` and matches a stakeholder's phrasing against it so
the `coverage` skill can answer whether a thing is measurable, and so consumers
(via load-context) can resolve a coverage entry deterministically. This is the
consumable form of the upstream instrumentation-gap backlog.

Pure except for `load_coverage` (one YAML read). The matchers are
side-effect-free and unit-tested. A missing or unreadable file yields an empty
register — coverage is advisory metadata, never a gate on anything.
"""

from __future__ import annotations

import os
from pathlib import Path


# The status enum. `live` / `partial` mean queryable today (partial = with a
# coverage caveat); the rest are not-yet-measurable states that carry a blocker.
STATUSES = {"live", "partial", "empty", "blocked", "declined", "flag_gated"}
QUERYABLE = {"live", "partial"}


def _plugin_root() -> Path:
    # this file: <plugin_root>/skills/_shared/coverage.py
    return Path(__file__).resolve().parents[2]


def coverage_path() -> Path:
    env = os.environ.get("KATTFOT_COVERAGE_FILE")
    if env:
        return Path(env)
    return _plugin_root() / "config" / "coverage.yaml"


def load_coverage(path: Path | None = None) -> dict:
    """Read the coverage register. Returns {} if absent or unreadable — coverage
    is advisory, so a missing file must never error a caller."""
    try:
        import yaml
    except ImportError:
        return {}
    p = path or coverage_path()
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def entries(coverage: dict) -> list:
    return coverage.get("coverage") or []


def by_id(coverage: dict, entry_id: str) -> dict | None:
    for e in entries(coverage):
        if e.get("id") == entry_id:
            return e
    return None


def _haystack(entry: dict) -> str:
    """All matchable text for an entry, lower-cased: id, asks, fields, streams."""
    parts = [str(entry.get("id") or "")]
    for key in ("asks", "fields", "streams"):
        parts.extend(str(v) for v in (entry.get(key) or []))
    return " \n ".join(parts).lower()


def find(coverage: dict, term: str) -> list:
    """Entries whose id, asks, fields, or streams contain `term` (case-
    insensitive substring). File order preserved. An empty term matches nothing."""
    t = (term or "").strip().lower()
    if not t:
        return []
    return [e for e in entries(coverage) if t in _haystack(e)]


def summary(coverage: dict) -> dict:
    """Entries grouped by status, in the enum's natural order, for the bare
    `coverage` listing and the load-context `coverage` scope. Unknown statuses
    (should be caught by the linter) are grouped under their literal value."""
    out: dict[str, list] = {}
    for e in entries(coverage):
        out.setdefault(e.get("status", "unknown"), []).append(e)
    ordered_keys = [s for s in ("live", "partial", "empty", "blocked", "declined", "flag_gated")
                    if s in out]
    ordered_keys += [k for k in out if k not in ordered_keys]
    return {k: out[k] for k in ordered_keys}
