#!/usr/bin/env python3
"""Claude Code PreToolUse adapter — deny Bash calls outside vetted kattfot scripts.

Contract (Claude Code PreToolUse "command" hook):
  stdin:  JSON {"tool_name": "Bash", "tool_input": {"command": "..."}, ...}
  stdout: on deny, JSON {"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": "deny", "permissionDecisionReason": "..."}}.
          On allow, print nothing.
  exit:   always 0. Denial is signaled via the JSON body, not the exit code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Import shared matcher from the same directory (works when invoked as a script).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bq_path_matcher import deny_reason  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    reason = deny_reason(command)
    if reason is None:
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
