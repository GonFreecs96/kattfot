"""audit_chain.py — a hash chain that makes the audit log tamper-evident.

The audit log (`audit-log.jsonl`) is append-only *by convention*: nothing about
a flat file stops a later writer from editing, reordering, or truncating it and
leaving no trace. The security literature is blunt that such a file is an event
log, not an audit log — tamper evidence is defined against a logger that is
itself untrusted (Crosby & Wallach, "Efficient Data Structures for
Tamper-Evident Logging", USENIX Security 2009), and the deployed instantiation
of that idea is the append-only Merkle log of Certificate Transparency
(RFC 6962).

This module is the smallest honest step toward that bar: a **hash chain** over
the log's exact line bytes,

    head_0 = GENESIS
    head_i = SHA-256(head_{i-1} || line_i)

with the current head kept in a sidecar file (`<log>.head`) that records the
head hash and the entry count. Any edit, reorder, insertion, or truncation of
the log changes the replayed head, so `verify_chain()` detects it by replaying
the file and comparing against the sidecar.

Chaining over the raw serialized line — rather than a canonicalized re-parse —
is deliberate: the record's JSON stays exactly what `append_audit` wrote (a
trace, not a copy, with no chain fields injected), and the verifier needs no
canonicalization rules, only the bytes on disk.

Hashes here are full 64-hex SHA-256, unlike the manifest's 16-hex eyeball-
diffable fingerprints: a manifest hash is compared by humans in a frontmatter
diff, while a chain head defends against a deliberate second-preimage search,
so it gets the full digest.

What this guarantees, and what it does not (state the limit or it gets trusted
past it):

- The log and its sidecar can no longer *silently* diverge — rewriting history
  means rewriting both, consistently.
- A head recorded anywhere else — a git commit, a printed artifact, a weekly
  report — pins the entire log prefix behind it. Committing heads externally is
  the auditing half of the mechanism, and per Crosby & Wallach a tamper-evident
  log **without** that auditing step proves nothing: an attacker who can
  rewrite the log *and* the sidecar in one motion is only caught by a head they
  cannot reach. This template ships the chain and the sidecar; where the
  external commitment lives is a deployment decision.
- Appends are assumed single-writer, same as the log itself. Concurrent
  writers can interleave correctly-chained lines out of order; that surfaces
  as a head mismatch, which is the honest outcome.

Pure stdlib, no dependency on the rest of the engine; `assay_query.append_audit`
calls `advance_head()` best-effort so an unwritable sidecar can never break a
query — the same trade the audit log itself makes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# The chain seed for an empty log. A fixed, obviously-artificial value rather
# than e.g. a hash of the path, so heads are comparable across relocated logs.
GENESIS = "0" * 64

# Single-line status sentinels, same style as verify.py / the consumer API.
CHAIN_OK = "CHAIN_OK"
CHAIN_HEAD_MISMATCH = "CHAIN_HEAD_MISMATCH"
CHAIN_HEAD_ABSENT = "CHAIN_HEAD_ABSENT"
CHAIN_ABSENT = "CHAIN_ABSENT"


def head_path(log_path: Path) -> Path:
    """The sidecar path for a log: `audit-log.jsonl` -> `audit-log.jsonl.head`."""
    log_path = Path(log_path)
    return log_path.with_name(log_path.name + ".head")


def link(prev_head: str, line: str) -> str:
    """One chain step: SHA-256 over the previous head and the exact line text.

    `line` is the serialized JSON record as written to the file, without its
    trailing newline. The previous head participates as its hex string, so the
    whole chain is reproducible from the file bytes plus GENESIS.
    """
    h = hashlib.sha256()
    h.update(prev_head.encode("ascii"))
    h.update(line.encode("utf-8"))
    return h.hexdigest()


def replay(log_path: Path) -> dict:
    """Fold the chain over every line of the log file.

    Returns ``{"head": <hex>, "entries": <int>}`` — GENESIS/0 for an empty or
    missing file. Blank lines are skipped, matching what a JSONL reader would
    tolerate; a blank line therefore cannot carry (or hide) an entry.
    """
    head = GENESIS
    entries = 0
    p = Path(log_path)
    if not p.exists():
        return {"head": head, "entries": entries}
    with p.open() as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            head = link(head, line)
            entries += 1
    return {"head": head, "entries": entries}


def read_head(log_path: Path) -> dict | None:
    """The sidecar's recorded state, or None if absent/unreadable."""
    p = head_path(log_path)
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "head" not in data or "entries" not in data:
        return None
    return {"head": data["head"], "entries": data["entries"]}


def write_head(log_path: Path, head: str, entries: int) -> None:
    head_path(log_path).write_text(
        json.dumps({"head": head, "entries": entries}) + "\n"
    )


def advance_head(log_path: Path, line: str) -> bool:
    """Advance the sidecar after one line was appended to the log.

    Incremental when a sidecar exists — ``new = link(old, line)`` — so lines
    that were appended *without* chaining are never silently blessed: they stay
    unattested and surface as a head mismatch at verify time. Only a log with
    no sidecar at all (one that predates chaining) is adopted wholesale by
    replaying the file, which includes the line just written.

    Best-effort like the log write itself: returns False rather than raising.
    """
    try:
        current = read_head(log_path)
        if current is None:
            state = replay(log_path)
            write_head(log_path, state["head"], state["entries"])
        else:
            write_head(
                log_path, link(current["head"], line), current["entries"] + 1
            )
        return True
    except Exception:
        return False


def verify_chain(log_path: Path) -> dict:
    """Replay the log and compare against the sidecar.

    Returns ``{"status": <SENTINEL>, "detail": str, "replayed": {...}}``:

    - CHAIN_OK            — replayed head and entry count match the sidecar.
    - CHAIN_HEAD_MISMATCH — they do not: the log was edited, reordered,
                            truncated, or extended without chaining. The chain
                            says *that* history diverged, not where — that is
                            what makes it a smoke alarm, not a floor plan.
    - CHAIN_HEAD_ABSENT   — the log exists but no sidecar does. Either the log
                            predates chaining or the sidecar was deleted; a
                            missing commitment is itself worth reporting, so
                            this is distinct from OK.
    - CHAIN_ABSENT        — no log file at all (nothing has been audited).
    """
    p = Path(log_path)
    if not p.exists():
        return {
            "status": CHAIN_ABSENT,
            "detail": f"no audit log at {p} — nothing to verify.",
            "replayed": {"head": GENESIS, "entries": 0},
        }
    state = replay(p)
    recorded = read_head(p)
    if recorded is None:
        return {
            "status": CHAIN_HEAD_ABSENT,
            "detail": (
                f"audit log has {state['entries']} entries but no committed "
                f"head ({head_path(p).name} missing or unreadable) — the log "
                "predates chaining, or its commitment was removed. Replayed "
                f"head: {state['head']}."
            ),
            "replayed": state,
        }
    if recorded == state:
        return {
            "status": CHAIN_OK,
            "detail": (
                f"replayed {state['entries']} entries; head matches the "
                f"committed head ({state['head'][:16]}…). A head recorded "
                "outside this filesystem pins this entire history; the local "
                "sidecar alone does not."
            ),
            "replayed": state,
        }
    return {
        "status": CHAIN_HEAD_MISMATCH,
        "detail": (
            f"replayed head over {state['entries']} entries is "
            f"{state['head'][:16]}… but the committed head says "
            f"{str(recorded['head'])[:16]}… over {recorded['entries']} entries "
            "— the log was edited, truncated, or extended without chaining."
        ),
        "replayed": state,
    }
