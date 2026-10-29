#!/usr/bin/env python3
"""Claude Code PreToolUse adapter — deny Bash calls outside vetted kattfot scripts.

Contract (Claude Code PreToolUse "command" hook):
  stdin:  JSON {"tool_name": "Bash", "tool_input": {"command": "..."}, ...}
  stdout: on deny, JSON {"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": "deny", "permissionDecisionReason": "..."}}.
          On allow, print nothing.
  exit:   always 0. Denial is signaled via the JSON body, not the exit code.

Fail-closed, two layers. If the matcher itself raises, this adapter DENIES with
the error in the reason rather than letting an unchecked command through. And
`hooks.json` sets `"onFailure": "block"` (Claude Code >= 2.1.295), so a hook that
times out, fails to start, or exits non-zero blocks the tool call too — without
it, Claude Code treats a timed-out or crashed PreToolUse hook as "no decision"
and the call proceeds. Unparseable stdin is the one deliberate exception: there
is no command to judge, so it exits 0 with no decision (pinned in
`tests/test_bq_bypass_hook.py`).
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
    try:
        reason = deny_reason(command)
    except Exception as exc:  # noqa: BLE001 — any internal failure fails closed
        reason = (
            f"kattfot access-path hook failed while checking this command "
            f"({type(exc).__name__}: {exc}); denying rather than allowing an "
            f"unchecked warehouse route. Fix the hook, then retry."
        )
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
