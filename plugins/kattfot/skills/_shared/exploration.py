"""Loader for the exploration register (config/exploration.yaml).

The register is the allow-list of warehouse surfaces the exploratory lane
(`kattfot:explore`) may read with free-form SELECT SQL. It is a PERMISSION
BOUNDARY, so this loader **fails closed**, mirroring `spine.py`'s registry
loader rather than the degrade-to-empty pattern of `coverage.py` /
`guardrails.py`: a missing or unparseable register is a hard exit, never an
empty dict. Advisory metadata that can break a query is worse than none;
a permission boundary that silently vanishes is worse than that.

Exit codes match the rest of the plugin: 3 = missing dependency (pyyaml),
1 = register missing/unreadable. A YAML parse error propagates as the
exception it is — a broken permission file is a repo bug a human must see.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import sha16  # noqa: E402


#: The machine-readable referral appended to a governed refusal whose question
#: the exploratory lane might serve (R1 miss, R2/R3 off-allow-list, R4-T3).
#: One canonical string, shared by kattfot_query and load_context, because the
#: consumer contract documents the `EXPLORABLE -- ` prefix as an optional line
#: a consumer may match. Deliberately NOT appended to R6 (PII is refused in
#: both lanes), R4-T1 (the system of record, not exploration), or R1-AMBIGUOUS
#: (the fix there is choosing between governed readings, not leaving the lane).
EXPLORABLE_HINT = (
    "\nEXPLORABLE -- this question may be servable in the exploratory lane "
    "(T3, not decision-grade). With the user's explicit confirmation, run "
    "kattfot:explore."
)


def _plugin_root() -> Path:
    # this file: <plugin_root>/skills/_shared/exploration.py
    return Path(__file__).resolve().parents[2]


def register_path() -> Path:
    """`KATTFOT_EXPLORATION_FILE` wins (tests / per-deployment override), else
    the bundled config/exploration.yaml."""
    env = os.environ.get("KATTFOT_EXPLORATION_FILE")
    if env:
        return Path(env)
    return _plugin_root() / "config" / "exploration.yaml"


def load_register(path: Path | None = None) -> dict:
    """Parse the exploration register. FAIL-CLOSED by design.

    Missing pyyaml -> exit 3 with the install line. Missing file -> exit 1
    naming the path. A YAML error raises: a permission boundary must never
    degrade to "nothing registered" (which reads as "refuse everything" only
    until someone inverts the check) or to "everything allowed".
    """
    try:
        import yaml
    except ImportError:
        sys.stderr.write(
            "Missing dependency pyyaml. Install with:\n  pip install pyyaml\n"
        )
        sys.exit(3)
    p = path or register_path()
    if not p.exists():
        sys.stderr.write(
            f"Exploration register not found at {p}. The exploratory lane is "
            f"closed without it — restore the file or set "
            f"KATTFOT_EXPLORATION_FILE.\n"
        )
        sys.exit(1)
    return yaml.safe_load(p.read_text()) or {}


def surfaces(register: dict) -> dict:
    """The `surfaces:` mapping (surface -> entry dict), or {} when absent."""
    return register.get("surfaces") or {}


def surface(register: dict, table: str) -> dict | None:
    """The register entry for one `dataset.table` surface, or None."""
    entry = surfaces(register).get(table)
    return entry if isinstance(entry, dict) else None


def max_bytes_billed(register: dict) -> int | None:
    """The BigQuery scan cap for one ad-hoc query, or None when unconfigured."""
    val = (register.get("defaults") or {}).get("max_bytes_billed")
    return int(val) if val is not None else None


def register_sha(path: Path | None = None) -> str:
    """Fingerprint of the register file AS USED — recorded in every explore
    artifact's manifest so a reviewer can tell which allow-list version
    authorized the query."""
    p = path or register_path()
    return sha16(p.read_text())
