#!/usr/bin/env python3
"""verify_artifact.py — CLI for the assay:verify skill.

Reads a governed query-metric artifact, parses its manifest from the YAML
frontmatter, and recomputes the definition + query fingerprints from the
current repo state to detect drift. Offline only — never touches BigQuery.

`--audit-log [PATH]` additionally (or, with no artifact argument, solely)
replays the audit log's hash chain and compares it against the committed head
— the auditing step that makes a tamper-evident log mean something (see
`_shared/audit_chain.py`). PATH defaults to the same resolution the query
skill uses (`ASSAY_AUDIT_LOG`, else `<outputs>/audit-log.jsonl`).

Exit codes (worst finding wins when both checks run):
  0  VERIFY_OK / CHAIN_OK
  1  DEFINITION_DRIFT / QUERY_DRIFT / CHAIN_HEAD_MISMATCH (provenance shifted)
  2  MANIFEST_ABSENT / DEFINITION_MISSING / CHAIN_HEAD_ABSENT / CHAIN_ABSENT
     (cannot verify)
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
from _shared import verify as verify_lib  # noqa: E402

try:
    import yaml
except ImportError:
    sys.stderr.write("Missing dependency. Install with:\n  pip install pyyaml\n")
    sys.exit(3)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_EXIT = {
    verify_lib.VERIFY_OK: 0,
    verify_lib.DEFINITION_DRIFT: 1,
    verify_lib.QUERY_DRIFT: 1,
    verify_lib.MANIFEST_ABSENT: 2,
    verify_lib.DEFINITION_MISSING: 2,
    audit_chain_lib.CHAIN_OK: 0,
    audit_chain_lib.CHAIN_HEAD_MISMATCH: 1,
    audit_chain_lib.CHAIN_HEAD_ABSENT: 2,
    audit_chain_lib.CHAIN_ABSENT: 2,
}


def default_audit_log_path() -> Path:
    """Mirror the query skill's resolution so `--audit-log` with no value
    checks the same file `query-metric` writes."""
    import os

    env = os.environ.get("ASSAY_AUDIT_LOG")
    if env:
        return Path(env)
    base = os.environ.get("ASSAY_OUTPUTS_DIR") or (Path.cwd() / "outputs" / "assay")
    return Path(base) / "audit-log.jsonl"


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit("ERROR: artifact has no YAML frontmatter block.")
    return yaml.safe_load(m.group(1)) or {}


def main() -> int:
    p = argparse.ArgumentParser(description="Offline drift check for a query-metric artifact.")
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

    worst = 0

    if args.artifact is not None:
        path = Path(args.artifact)
        if not path.exists():
            sys.stderr.write(f"ERROR: artifact not found: {path}\n")
            return 3

        fm = parse_frontmatter(path.read_text())
        result = verify_lib.verify_frontmatter(fm)

        status = result["status"]
        print(f"{status} -- {result['detail']}")
        for finding in result.get("findings", []):
            print(f"  - {finding['status']}: {finding['detail']}")
        worst = max(worst, _EXIT.get(status, 3))

    if args.audit_log is not None:
        log_path = Path(args.audit_log) if args.audit_log else default_audit_log_path()
        chain = audit_chain_lib.verify_chain(log_path)
        print(f"{chain['status']} -- {chain['detail']}")
        worst = max(worst, _EXIT.get(chain["status"], 3))

    return worst


if __name__ == "__main__":
    raise SystemExit(main())
