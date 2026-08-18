#!/usr/bin/env python3
"""Cursor beforeShellExecution adapter — deny shell commands outside vetted assay scripts.

Contract (Cursor beforeShellExecution "command" hook):
  stdin:  JSON {"command": "...", ...}
  stdout: JSON {"permission": "allow"} or
          {"permission": "deny", "user_message": "...", "agent_message": "..."}
  exit:   0 on success (deny is signaled in JSON, not via exit code 2).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_MATCHER_DIR = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "assay"
    / "hooks"
    / "scripts"
)
sys.path.insert(0, str(_MATCHER_DIR))
from bq_path_matcher import deny_reason  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(json.dumps({"permission": "allow"}))
        return 0

    command = payload.get("command") or ""
    reason = deny_reason(command)
    if reason is None:
        print(json.dumps({"permission": "allow"}))
        return 0

    print(json.dumps({
        "permission": "deny",
        "user_message": reason,
        "agent_message": reason,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
