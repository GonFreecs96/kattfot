#!/usr/bin/env python3
"""Cursor beforeShellExecution adapter — deny shell commands outside vetted kattfot scripts.

Contract (Cursor beforeShellExecution "command" hook):
  stdin:  JSON {"command": "...", ...}
  stdout: JSON {"permission": "allow"} or
          {"permission": "deny", "user_message": "...", "agent_message": "..."}
  exit:   0 on success (deny is signaled in JSON, not via exit code 2).

Fail-closed: if the matcher itself raises, this adapter DENIES with the error in
the message rather than letting an unchecked command through — the twin of the
Claude Code adapter's behaviour (that one also declares `onFailure: block` in
hooks.json; Cursor's hook schema has no documented equivalent, so the in-script
guard is the whole backstop here). Unparseable stdin is the one deliberate
exception: no command to judge, so it allows (pinned in
`tests/test_bq_bypass_hook.py`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_MATCHER_DIR = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "kattfot"
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
    try:
        reason = deny_reason(command)
    except Exception as exc:  # noqa: BLE001 — any internal failure fails closed
        reason = (
            f"kattfot access-path hook failed while checking this command "
            f"({type(exc).__name__}: {exc}); denying rather than allowing an "
            f"unchecked warehouse route. Fix the hook, then retry."
        )
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
