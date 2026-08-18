"""manifest.py — a content-addressed receipt for a governed query result.

Every governed artifact carries a manifest: a small set of fingerprints that
pin *which metric-definition version*, *which query*, and *which result*
produced the number. Re-running with the same definition reproduces the same
``metric_def_sha`` and ``query_sha``; a result that was hand-edited after the
fact no longer matches its ``result_sha``. This is what makes a governed number
defensible months later — "here is proof of how it was made", not "trust me" —
and it is what ``assay:verify`` checks against.

Pure functions, no I/O on the hot path: the helper supplies raw inputs and gets
back a dict. The one filesystem read (the plugin version) is isolated in
``plugin_version()`` and degrades gracefully to ``"unknown"``.

Design note (determinism): ``generated_at`` — the single wall-clock read in the
query helper — is deliberately NOT part of any hash. The receipt pins inputs
and outputs, never the moment of execution, so a faithful replay reproduces the
fingerprints exactly. This mirrors the run-manifest discipline of a determinis-
tic engine: the run is defined by its content, not by when it happened.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Short, human-comparable fingerprint length (hex chars). 16 hex = 64 bits,
# ample for collision-resistance across one team's artifacts while staying
# eyeball-diffable in a markdown frontmatter block.
_SHA_LEN = 16


def sha16(data: str | bytes) -> str:
    """sha256 over UTF-8 text (or raw bytes), truncated to the first 16 hex chars."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:_SHA_LEN]


def canonical_json(obj) -> str:
    """Stable JSON: sorted keys, compact separators — the canonical form fed to sha16.

    ``default=str`` makes dates / Decimals / other non-JSON scalars hashable
    without the caller pre-stringifying them.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _norm_val(v):
    """Normalize a cell so equal values hash equally across platforms.

    Integral floats (``24556.0`` from a BigQuery SUM) collapse to ``int`` so
    they match the mock path's native ``int``; genuine floats are pinned to a
    fixed precision to defeat repr drift. Everything else (strings, dates) is
    left untouched.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else format(v, ".10g")
    return v


def result_sha(rows: list, drop_cols=()) -> str:
    """Fingerprint a result set over its EXACT building blocks.

    The derived ratio column (the metric value) is dropped via ``drop_cols``:
    it is a function of the integer numerator/denominator, so hashing the
    volumes is both lossless and free of float instability. Row order is
    normalized, so an ``ORDER BY`` change is not a content change — two runs
    over the same data agree regardless of sort.
    """
    drop = set(drop_cols)
    norm = [
        {k: _norm_val(v) for k, v in row.items() if k not in drop}
        for row in rows
    ]
    norm.sort(key=canonical_json)
    return sha16(canonical_json(norm))


def query_sha(sql: str, params: dict) -> str:
    """Fingerprint the exact SQL plus its bound parameters."""
    return sha16(canonical_json({"sql": sql, "params": params}))


def plugin_version(default: str = "unknown") -> str:
    """Read the plugin's semantic version from its plugin.json (best-effort)."""
    try:
        # _shared/manifest.py -> _shared -> skills -> <plugin root>
        plugin_root = Path(__file__).resolve().parents[2]
        meta = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text())
        return meta.get("version") or default
    except Exception:
        return default


def build_manifest(
    *,
    metric_def_sha: str,
    sql: str,
    params: dict,
    rows: list,
    metric_name: str,
    plugin_ver: str | None = None,
) -> dict:
    """Assemble the receipt for one governed query result.

    - ``plugin_version``  — which version of the engine produced this.
    - ``metric_def_sha``  — fingerprint of the metric-definition file as used.
    - ``query_sha``       — fingerprint of the exact SQL + bound parameters.
    - ``result_sha``      — fingerprint of the result volumes (tamper-evident).
    """
    return {
        "plugin_version": plugin_ver if plugin_ver is not None else plugin_version(),
        "metric_def_sha": metric_def_sha,
        "query_sha": query_sha(sql, params),
        "result_sha": result_sha(rows, drop_cols={metric_name}),
    }
