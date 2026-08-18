#!/usr/bin/env python3
"""verify_artifact.py — CLI for the assay:verify skill.

Reads a governed query-metric artifact, parses its manifest from the YAML
frontmatter, and recomputes the definition + query fingerprints from the
current repo state to detect drift. Offline only — never touches BigQuery.

Exit codes:
  0  VERIFY_OK
  1  DEFINITION_DRIFT / QUERY_DRIFT (the number's provenance has shifted)
  2  MANIFEST_ABSENT / DEFINITION_MISSING (cannot verify)
  3  usage / IO error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Make `_shared` importable when invoked directly (…/skills/verify/scripts → …/skills).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
}


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit("ERROR: artifact has no YAML frontmatter block.")
    return yaml.safe_load(m.group(1)) or {}


def main() -> int:
    p = argparse.ArgumentParser(description="Offline drift check for a query-metric artifact.")
    p.add_argument("artifact", help="path to the artifact .md file")
    args = p.parse_args()

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

    return _EXIT.get(status, 3)


if __name__ == "__main__":
    raise SystemExit(main())
