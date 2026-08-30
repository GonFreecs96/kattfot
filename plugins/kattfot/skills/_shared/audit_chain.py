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
- **Unchained lines are never adopted.** ``advance_head`` extends the chain
  only from a valid, exactly-up-to-date sidecar (or starts one on a genuinely
  empty log); it never re-derives a head by replaying an existing non-empty
  file. So deleting or corrupting the sidecar and appending planted lines does
  not launder them into a valid head on the next append — that state verifies
  as CHAIN_HEAD_ABSENT (no readable commitment), never CHAIN_OK. Re-committing
  an existing log is the explicit ``adopt_log``, a deliberate operator action
  off the append path.
- **A missed sidecar write does not cry wolf.** If the log write lands but the
  sidecar write is dropped once, the sidecar falls behind rather than
  diverging; ``verify_chain`` reports CHAIN_HEAD_BEHIND ("committed history
  intact through entry N; later entries unattested"), distinct from the
  CHAIN_HEAD_MISMATCH it raises when a line *within* the committed region was
  altered. A tampering alarm that fired on an ordinary I/O hiccup would train
  operators to ignore it; this keeps the scream for real tampering.
- A head recorded anywhere else — a git commit, a printed artifact, a weekly
  report — pins the entire log prefix behind it. Committing heads externally is
  the auditing half of the mechanism, and per Crosby & Wallach a tamper-evident
  log **without** that auditing step proves nothing: an attacker who can
  rewrite the log *and* the sidecar in one motion is only caught by a head they
  cannot reach. This template ships the chain and the sidecar; where the
  external commitment lives is a deployment decision.
- Appends are assumed single-writer, same as the log itself. Concurrent
  writers can interleave correctly-chained lines out of order; that surfaces
  as CHAIN_HEAD_BEHIND or a mismatch, which is the honest outcome.

Pure stdlib, no dependency on the rest of the engine; `kattfot_query.append_audit`
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
CHAIN_HEAD_BEHIND = "CHAIN_HEAD_BEHIND"
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


def replay(log_path: Path, *, limit: int | None = None) -> dict:
    """Fold the chain over the log file (optionally only its first ``limit``
    non-blank lines).

    Returns ``{"head": <hex>, "entries": <int>}`` — GENESIS/0 for an empty or
    missing file. Blank lines are skipped, matching what a JSONL reader would
    tolerate; a blank line therefore cannot carry (or hide) an entry. Reads as
    UTF-8 explicitly so the fold is a pure function of the file's bytes rather
    than of the verifier's locale (``link`` encodes UTF-8; the read must match,
    or an accented value round-trips to different bytes under a different
    ``LANG`` and reports a false mismatch).

    ``limit`` folds only the first N entries — used to check whether a
    committed head still describes the log's *prefix*, which is what separates
    an unattested tail (CHAIN_HEAD_BEHIND) from tampering in the attested
    region (CHAIN_HEAD_MISMATCH).
    """
    head = GENESIS
    entries = 0
    p = Path(log_path)
    if not p.exists():
        return {"head": head, "entries": entries}
    with p.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if limit is not None and entries >= limit:
                break
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


def _line_count(log_path: Path) -> int:
    """Non-blank lines currently in the log — the file's own notion of how many
    entries exist, independent of what any sidecar claims."""
    p = Path(log_path)
    if not p.exists():
        return 0
    with p.open(encoding="utf-8") as f:
        return sum(1 for raw in f if raw.strip())


def advance_head(log_path: Path, line: str) -> bool:
    """Advance the committed head after one line was appended to the log.

    Advances in exactly two safe states, and refuses to write in every other:

    - **Incremental** — a valid sidecar exists AND it accounted for every line
      that preceded this append (``entries == line_count - 1``): chain from the
      *committed* head, ``new = link(old, line)``. Chaining from the committed
      head (not from a replay of the file) means a tampered earlier line is
      never blessed — it still surfaces as a mismatch at verify time.
    - **Genesis** — no sidecar and this line is the whole log
      (``line_count == 1``): start the chain.

    Every other state writes nothing and returns ``False``:

    - **No sidecar over a non-empty log.** Either the log predates chaining or
      its sidecar was deleted/corrupted to force adoption — indistinguishable
      locally, so adopting wholesale would launder unchained (or planted) lines
      into a valid-looking head. Left uncommitted, ``verify`` reports
      CHAIN_HEAD_ABSENT. Re-establishing a commitment over an existing log is
      the explicit, deliberate ``adopt_log`` below, never a silent side effect.
    - **A stale sidecar** (``entries != line_count - 1``, e.g. a previous
      sidecar write was missed). Advancing would chain the new line onto a head
      that skips the gap, producing a commitment matching no prefix of the
      file. Left as-is, ``verify`` reports CHAIN_HEAD_BEHIND — an honest
      "committed through entry N; later entries unattested" — rather than a
      false tampering alarm.

    Best-effort like the log write itself: returns False rather than raising.

    Cost: one line-count scan of the log per append — O(n) each, O(n²) over the
    log's lifetime. Negligible at this template's scale (a demo writes a
    handful of lines); a real deployment that lets the log grow unbounded
    should rotate it or move to a transparency-log backend (RFC 6962), which is
    the same deployment step the module docstring names for external witnessing.
    """
    try:
        current = read_head(log_path)
        n_lines = _line_count(log_path)  # includes the just-appended line
        if current is None:
            if n_lines == 1:  # genesis only — never adopt a non-empty log
                state = replay(log_path)
                write_head(log_path, state["head"], state["entries"])
                return True
            return False
        if current["entries"] == n_lines - 1:  # sidecar is exactly up to date
            write_head(log_path, link(current["head"], line), current["entries"] + 1)
            return True
        return False  # stale sidecar: leave it for an honest CHAIN_HEAD_BEHIND
    except Exception:
        return False


def adopt_log(log_path: Path) -> dict:
    """Explicitly (re)commit the current log in full — the deliberate operation
    that ``advance_head`` refuses to do silently.

    For migrating a log that predates chaining, or re-committing after a
    missed sidecar write (CHAIN_HEAD_BEHIND) once a human has confirmed the
    unattested tail is legitimate. Because this trusts the file as it stands,
    it must be an intentional human/operator action, never reachable from the
    append path — that separation is exactly what closes the laundering hole.
    Returns the committed ``{"head", "entries"}``.
    """
    state = replay(log_path)
    write_head(log_path, state["head"], state["entries"])
    return state


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
    # Distinguish an unattested TAIL from tampering in the attested region.
    # If the committed head still describes the log's first `recorded[entries]`
    # lines, that prefix is intact and only later lines are unaccounted for —
    # a missed sidecar write or an unchained append, not evidence the committed
    # history was altered. That is a weaker, honest signal (CHAIN_HEAD_BEHIND),
    # not the tampering scream, and it exists so an ordinary I/O hiccup does not
    # cry wolf. Any change WITHIN the first N lines fails this prefix check and
    # stays CHAIN_HEAD_MISMATCH.
    if state["entries"] > recorded["entries"]:
        prefix = replay(p, limit=recorded["entries"])
        if prefix["head"] == recorded["head"]:
            return {
                "status": CHAIN_HEAD_BEHIND,
                "detail": (
                    f"the committed head verifies the first {recorded['entries']} "
                    f"entries, but the log now has {state['entries']} — the "
                    f"{state['entries'] - recorded['entries']} later entries are "
                    "unattested (a missed sidecar write, or lines appended "
                    "without chaining). The committed history is intact; confirm "
                    "the tail and re-commit with adopt_log to extend coverage."
                ),
                "replayed": state,
            }
    return {
        "status": CHAIN_HEAD_MISMATCH,
        "detail": (
            f"replayed head over {state['entries']} entries is "
            f"{state['head'][:16]}… but the committed head says "
            f"{str(recorded['head'])[:16]}… over {recorded['entries']} entries "
            "— a line in the committed region was edited, reordered, or removed."
        ),
        "replayed": state,
    }
