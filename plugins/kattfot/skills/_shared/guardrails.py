"""Warn-level guardrails — the data-quality caveat layer.

Loads `config/guardrails.yaml` and matches it against a query so the helper can
auto-stamp caveats: feed-cutover discontinuities when the time window straddles
a known pipeline/feature change, and column/view notes when a referenced column
or the source view matches. This is the *warn*-severity complement to the
*refuse*-severity PII rule in `pii.py` (R6) — guardrails caveat a number, they
never block it.

Pure except for `load_guardrails` (one YAML read). The matchers are
side-effect-free and unit-tested. Shared by the query-metric helper and exposed
for the spine skills via `spine.guardrail_warnings`.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path


def _plugin_root() -> Path:
    # this file: <plugin_root>/skills/_shared/guardrails.py
    return Path(__file__).resolve().parents[2]


def guardrails_path() -> Path:
    env = os.environ.get("KATTFOT_GUARDRAILS_FILE")
    if env:
        return Path(env)
    return _plugin_root() / "config" / "guardrails.yaml"


def load_guardrails(path: Path | None = None) -> dict:
    """Read the guardrails config. Returns {} if absent or unreadable — guardrails
    are advisory, so a missing file must never block a query."""
    try:
        import yaml
    except ImportError:
        return {}
    p = path or guardrails_path()
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _as_date(value) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def cutovers_in_window(guardrails: dict, start: dt.date, end: dt.date) -> list:
    """Feed-cutover entries whose date falls within [start, end] (inclusive).

    A window that contains a cutover date spans a discontinuity — data before
    and after the date are not directly comparable.
    """
    hits = []
    for c in guardrails.get("feed_cutovers") or []:
        d = _as_date(c.get("date"))
        if d is not None and start <= d <= end:
            hits.append(c)
    return hits


def applicable_notes(guardrails: dict, source_view: str | None, columns: list) -> list:
    """Notes whose `applies_to_views` matches the source view OR whose
    `applies_to_columns` intersects the referenced columns. Notes with neither
    key are reference-only and never auto-injected."""
    cols = {c for c in (columns or [])}
    view = source_view or ""
    # match the bare table name too (source_view is dataset.table)
    view_tail = view.split(".")[-1] if view else ""
    out = []
    for n in guardrails.get("notes") or []:
        views = set(n.get("applies_to_views") or [])
        ncols = set(n.get("applies_to_columns") or [])
        if not views and not ncols:
            continue  # reference-only
        if (views & {view, view_tail}) or (ncols & cols):
            out.append(n)
    return out


def cutover_warnings(guardrails: dict, start: dt.date, end: dt.date) -> list:
    """Formatted DISCONTINUITY warning strings for cutovers in the window."""
    return [
        f"DISCONTINUITY: the {start.isoformat()}..{end.isoformat()} window "
        f"crosses {c['date']} ({c.get('label', c.get('id'))}). {c.get('effect', '')}".strip()
        for c in cutovers_in_window(guardrails, start, end)
    ]


def note_warnings(guardrails: dict, source_view: str | None, columns: list) -> list:
    """Formatted CAVEAT strings for column/view notes that apply."""
    return [
        f"CAVEAT ({n.get('id')}): {n.get('message', '')}".strip()
        for n in applicable_notes(guardrails, source_view, columns)
    ]


def warnings_for(
    source_view: str | None,
    columns: list,
    start: dt.date,
    end: dt.date,
    path: Path | None = None,
) -> list:
    """One-call entry point: load + match + format all applicable warn-level
    guardrails for a query. Used by the query-metric helper and the spine
    skills."""
    gr = load_guardrails(path)
    return cutover_warnings(gr, start, end) + note_warnings(gr, source_view, columns)
