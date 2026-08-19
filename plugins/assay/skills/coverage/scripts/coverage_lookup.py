#!/usr/bin/env python3
"""coverage_lookup.py — "can I measure X yet?" against the instrumentation register.

Reads `config/coverage.yaml` (override: `ASSAY_COVERAGE_FILE`) through the shared
matcher in `_shared/coverage.py` and answers one of three ways:

    no argument   list everything, grouped by status — the triage view
    --term        match a stakeholder's phrasing against ids, asks, fields, streams
    --id          resolve one entry exactly

The point of the register is that "we can't measure that yet, here is why and who
owns it" is a *governed answer*, not an analyst's recollection. The catalog says
what you CAN ask; this says what you cannot yet, and stops the same question
re-routing every quarter.

Never queries anything. A missing or unparseable register yields an empty result
rather than an error — coverage is advisory metadata, and advisory metadata that
can break a caller is worse than none. That does mean a typo'd register degrades
to silence, which is why `tests/test_coverage.py` asserts the shipped file parses
and is non-empty rather than only testing the matchers in isolation.

Exit codes: 0 on a hit, 1 on a clean no-match (so a script can branch), 3 on a
missing dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _shared import coverage as cov  # noqa: E402


def entry_view(e: dict) -> dict:
    """One entry, flattened for rendering. `queryable` is derived from the status
    rather than stored, so the enum stays the single source of truth."""
    status = e.get("status")
    return {
        "id": e.get("id"),
        "status": status,
        "queryable": status in cov.QUERYABLE,
        "asks": e.get("asks") or [],
        "fields": e.get("fields") or [],
        "streams": e.get("streams") or [],
        "serve": (e.get("serve") or "").strip() or None,
        "blocker": (e.get("blocker") or "").strip() or None,
        "blocker_ref": e.get("blocker_ref"),
        "owner": e.get("owner"),
        "verified": e.get("verified"),
    }


VERDICTS = {
    "live": "Yes — queryable now.",
    "partial": "Yes, but — queryable with a known coverage hole; it is a subset, not the whole population.",
    "empty": "Not yet — the schema is wired but production has no rows.",
    "blocked": "No — not instrumented.",
    "declined": "No, and it is not coming — the instrumentation was declined.",
    "flag_gated": "Not yet — gated on a feature flag or a dated launch.",
}


def render(payload: dict) -> str:
    lines = []
    if payload["mode"] == "list":
        lines += ["# Instrumentation coverage", ""]
        for status, entries in payload["by_status"].items():
            lines += [
                f"## {status} — {VERDICTS.get(status, '')}".rstrip(),
                "",
                "| Capability | What / blocker | Owner | Ref |",
                "|---|---|---|---|",
            ]
            for e in entries:
                detail = (e["serve"] or e["blocker"] or "").split(".")[0].strip()
                lines.append(
                    f"| `{e['id']}` | {detail} | {e['owner'] or '—'} | "
                    f"{e['blocker_ref'] or '—'} |"
                )
            lines.append("")
        return "\n".join(lines)

    if not payload["matches"]:
        return (
            f"NO_COVERAGE_ENTRY -- nothing in the register matches "
            f"{payload['query']!r}.\n\n"
            f"That is not a verdict — it means the question has not been registered. "
            f"Two real routes: `assay:catalog` for the governed metrics that ARE "
            f"available, or the exploratory lane for an ad-hoc look (whose output is "
            f"unverified and untiered). Do not read this as 'not measurable'.\n"
        )
    for e in payload["matches"]:
        lines += [
            f"## `{e['id']}` — {e['status']}",
            "",
            VERDICTS.get(e["status"], ""),
            "",
        ]
        if e["serve"]:
            lines += ["**How it is served:** " + e["serve"], ""]
        if e["blocker"]:
            lines += ["**Blocker (verbatim):** " + e["blocker"], ""]
        meta = [f"owner: {e['owner']}" if e["owner"] else None,
                f"ref: {e['blocker_ref']}" if e["blocker_ref"] else None,
                f"verified: {e['verified']}" if e["verified"] else None]
        lines += ["_" + " · ".join(m for m in meta if m) + "_", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--term", default=None, help="phrase to match against the register")
    ap.add_argument("--id", default=None, help="resolve one entry by its exact id")
    ap.add_argument("--format", default="json", choices=["json", "markdown"])
    args = ap.parse_args()

    register = cov.load_coverage()
    all_entries = cov.entries(register)

    if args.id:
        hit = cov.by_id(register, args.id)
        payload = {
            "mode": "id",
            "query": args.id,
            "matches": [entry_view(hit)] if hit else [],
            "register_size": len(all_entries),
        }
    elif args.term:
        payload = {
            "mode": "term",
            "query": args.term,
            "matches": [entry_view(e) for e in cov.find(register, args.term)],
            "register_size": len(all_entries),
        }
    else:
        payload = {
            "mode": "list",
            "query": None,
            "register_size": len(all_entries),
            "by_status": {
                status: [entry_view(e) for e in entries]
                for status, entries in cov.summary(register).items()
            },
        }
    payload["skill"] = "coverage"
    payload["register_path"] = str(cov.coverage_path())
    payload["queryable_statuses"] = sorted(cov.QUERYABLE)

    if args.format == "markdown":
        sys.stdout.write(render(payload))
    else:
        print(json.dumps(payload, indent=2, default=str))

    if payload["mode"] == "list":
        return 0 if all_entries else 1
    return 0 if payload["matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
