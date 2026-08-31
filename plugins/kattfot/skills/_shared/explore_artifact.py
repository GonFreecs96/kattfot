"""explore_artifact.py — the shared parser for an exploratory (T3) artifact's
Provenance fences.

`kattfot:promote` and `kattfot:verify` both need exactly the same three things
out of an explore artifact: its frontmatter, the executed SQL, and the bound
parameters — and both use them as a TAMPER-CHECK INPUT, recomputing
``query_sha`` and comparing it to the manifest. Two independent regexes
parsing the same fences is exactly how the two consumers could quietly
disagree about what counts as tampering, so this module is the one parser
both call.

Parsing is kept separate from policy: ``parse_artifact`` returns what the
fences actually contain (``sql`` is ``None`` when there is no ```sql fence,
``params`` is ``{}`` when there is no ```json fence) and raises ``SystemExit``
only on a STRUCTURAL failure — the file is missing, there is no frontmatter
block at all, or a fence that IS present fails to parse (broken YAML/JSON: a
corrupt payload, not an absent one). Whether an absent fence is a refusal
(``kattfot:promote``, which cannot scaffold a draft without one) or a
cannot-verify report (``kattfot:verify``, which says PROVENANCE_ABSENT and
moves on) is each caller's own call — with its own message and exit code.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)
SQL_FENCE_RE = re.compile(r"```sql\s*\n(.*?)```", re.DOTALL)
JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def parse_artifact(path: Path) -> tuple[dict, str | None, dict]:
    """Frontmatter, SQL (or ``None``), bound params — from exactly the fences
    the explore SKILL.md's Output format specifies.

    Raises ``SystemExit`` for: a missing file, a missing frontmatter block, or
    a present fence whose contents don't parse. An absent ```sql or ```json
    fence is reported via the return value, not raised — see the module
    docstring for why that split matters.
    """
    try:
        import yaml
    except ImportError:
        sys.stderr.write("Missing dependency pyyaml. Install with:\n  pip install pyyaml\n")
        sys.exit(3)
    if not path.exists():
        raise SystemExit(f"ERROR: artifact not found at {path}.")
    text = path.read_text()
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit(f"ERROR: {path} has no YAML frontmatter — not an artifact.")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"ERROR: unparseable frontmatter in {path}: {e}")
    body = m.group(2)
    sql_m = SQL_FENCE_RE.search(body)
    sql = sql_m.group(1).strip() if sql_m else None
    params: dict = {}
    if sql_m:
        json_m = JSON_FENCE_RE.search(body, sql_m.end())
        if json_m:
            try:
                params = json.loads(json_m.group(1)) or {}
            except json.JSONDecodeError as e:
                raise SystemExit(f"ERROR: unparseable bound-parameters JSON in {path}: {e}")
    return fm, sql, params
