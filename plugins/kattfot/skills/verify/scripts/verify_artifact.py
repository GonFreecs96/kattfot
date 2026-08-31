#!/usr/bin/env python3
"""verify_artifact.py — CLI for the kattfot:verify skill.

Reads a query artifact — governed (`query-metric`) or exploratory
(`kattfot:explore`, `skill: explore` in its frontmatter) — and recomputes its
fingerprints from the current repo state to detect drift. Offline only —
never touches BigQuery. Routing is automatic from the frontmatter; there is no
flag to pick a lane.

  - Governed artifact: parses the manifest, reloads the metric definition by
    name, rebuilds the SQL from the recorded dimensions/filters/window, and
    compares `metric_def_sha` / `query_sha` (`_shared/verify.verify_frontmatter`).
  - Exploratory artifact: re-parses the ```sql/```json Provenance fences (the
    same parser `kattfot:promote` uses for its tamper check —
    `_shared/explore_artifact.py`) and compares the recomputed `query_sha`
    against the manifest, plus the CURRENT exploration register's
    `register_sha` against the one recorded (`_shared/verify.verify_explore`).

`--audit-log [PATH]` additionally (or, with no artifact argument, solely)
replays the audit log's hash chain and compares it against the committed head
— the auditing step that makes a tamper-evident log mean something (see
`_shared/audit_chain.py`). PATH defaults to the same resolution the query
skill uses (`KATTFOT_AUDIT_LOG`, else `<outputs>/audit-log.jsonl`).

Exit codes (worst finding wins when both checks run):
  0  VERIFY_OK / CHAIN_OK
  1  DEFINITION_DRIFT / QUERY_DRIFT / PROVENANCE_TAMPERED / REGISTER_DRIFT /
     CHAIN_HEAD_MISMATCH (provenance shifted — DETECTED)
  2  MANIFEST_ABSENT / DEFINITION_MISSING / PROVENANCE_ABSENT /
     REGISTER_MISSING / CHAIN_HEAD_ABSENT / CHAIN_ABSENT (cannot verify)
  3  usage / IO error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Make `_shared` importable when invoked directly (…/skills/verify/scripts → …/skills).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _shared import audit_chain as audit_chain_lib  # noqa: E402
from _shared import explore_artifact as explore_artifact_lib  # noqa: E402
from _shared import verify as verify_lib  # noqa: E402

try:
    import yaml
except ImportError:
    sys.stderr.write("Missing dependency. Install with:\n  pip install pyyaml\n")
    sys.exit(3)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Exit code per status. 0 = clear, 1 = a confirmed shift/tamper was DETECTED,
# 2 = could not fully verify (inconclusive), 3 = usage/IO error.
_EXIT = {
    verify_lib.VERIFY_OK: 0,
    verify_lib.DEFINITION_DRIFT: 1,
    verify_lib.QUERY_DRIFT: 1,
    verify_lib.MANIFEST_ABSENT: 2,
    verify_lib.DEFINITION_MISSING: 2,
    verify_lib.PROVENANCE_TAMPERED: 1,
    verify_lib.REGISTER_DRIFT: 1,
    verify_lib.PROVENANCE_ABSENT: 2,
    verify_lib.REGISTER_MISSING: 2,
    audit_chain_lib.CHAIN_OK: 0,
    audit_chain_lib.CHAIN_HEAD_MISMATCH: 1,
    audit_chain_lib.CHAIN_HEAD_BEHIND: 2,
    audit_chain_lib.CHAIN_HEAD_ABSENT: 2,
    audit_chain_lib.CHAIN_ABSENT: 2,
}

# Severity for combining checks — NOT the exit code. A confirmed detection
# outranks an inconclusive "cannot verify", even though its exit code (1) is
# the smaller number: a tampered audit log co-occurring with a manifest-absent
# artifact must exit 1 (detected), not 2 (inconclusive). Combining by numeric
# max over exit codes would invert that and bury the alarm.
_SEVERITY = {0: 0, 3: 1, 2: 2, 1: 3}  # ok < usage < inconclusive < detected


def _worse(a: str | None, b: str) -> str:
    """The higher-severity of two statuses (by _SEVERITY of their exit codes)."""
    if a is None:
        return b
    return b if _SEVERITY[_EXIT.get(b, 3)] > _SEVERITY[_EXIT.get(a, 3)] else a


def default_audit_log_path() -> Path:
    """Mirror the query skill's resolution so `--audit-log` with no value
    checks the same file `query-metric` writes."""
    import os

    env = os.environ.get("KATTFOT_AUDIT_LOG")
    if env:
        return Path(env)
    base = os.environ.get("KATTFOT_OUTPUTS_DIR") or (Path.cwd() / "outputs" / "kattfot")
    return Path(base) / "audit-log.jsonl"


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit("ERROR: artifact has no YAML frontmatter block.")
    return yaml.safe_load(m.group(1)) or {}


def main() -> int:
    p = argparse.ArgumentParser(
        description="Offline drift check for a query-metric or kattfot:explore artifact."
    )
    p.add_argument("artifact", nargs="?", help="path to the artifact .md file")
    p.add_argument(
        "--audit-log",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="also replay the audit log's hash chain (default path if omitted)",
    )
    args = p.parse_args()

    if args.artifact is None and args.audit_log is None:
        p.error("nothing to verify: pass an artifact path and/or --audit-log")

    # Combine by severity, not by numeric exit code (see _worse): a detected
    # tamper must not be masked by a co-occurring "cannot verify".
    worst: str | None = None

    if args.artifact is not None:
        path = Path(args.artifact)
        if not path.exists():
            sys.stderr.write(f"ERROR: artifact not found: {path}\n")
            return 3

        fm = parse_frontmatter(path.read_text())
        if fm.get("skill") == "explore":
            # Same fences, same parser `kattfot:promote` uses for its tamper
            # check — re-reading the file here (rather than threading a body
            # string through `parse_frontmatter`) keeps the governed path's
            # parsing untouched and the routing decision a one-line check.
            _, sql, params = explore_artifact_lib.parse_artifact(path)
            result = verify_lib.verify_explore(fm, sql, params)
        else:
            result = verify_lib.verify_frontmatter(fm)

        status = result["status"]
        print(f"{status} -- {result['detail']}")
        for finding in result.get("findings", []):
            print(f"  - {finding['status']}: {finding['detail']}")
        worst = _worse(worst, status)

    if args.audit_log is not None:
        log_path = Path(args.audit_log) if args.audit_log else default_audit_log_path()
        chain = audit_chain_lib.verify_chain(log_path)
        print(f"{chain['status']} -- {chain['detail']}")
        worst = _worse(worst, chain["status"])

    return _EXIT.get(worst, 3) if worst is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
