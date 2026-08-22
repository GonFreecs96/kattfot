"""The audit log's hash chain: tamper evidence, not just an append convention.

Covers `_shared/audit_chain.py` (link/replay/advance/verify) and its wiring
into `append_audit`. The properties worth pinning:

  1. An untouched log verifies CHAIN_OK against its committed head.
  2. Every way history can be falsified — an edited line, a deleted line, a
     reordered pair, a truncation, an insertion — flips verify to
     CHAIN_HEAD_MISMATCH. A chain that missed any one of these would be
     decoration, so each shape gets its own test.
  3. A log with no committed head is CHAIN_HEAD_ABSENT, not OK — a missing
     commitment is a finding (per Crosby & Wallach, a tamper-evident log
     without auditing proves nothing).
  4. Lines appended *without* chaining are never silently blessed: the next
     chained append advances incrementally from the recorded head, so the
     unattested lines surface as a mismatch instead of being adopted.
  5. The chain changes nothing about the records themselves — a written line
     round-trips to exactly the dict that was logged (the trace-not-a-copy
     contract is untouched).

No warehouse access.
"""

import json

import assay_query as aq
from _shared import audit_chain as ac


def _append_n(log, n, start=0):
    for i in range(start, start + n):
        assert aq.append_audit({"i": i, "metric": f"m{i}"}, log) is True


def test_untouched_log_verifies_ok(tmp_path):
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 5)
    result = ac.verify_chain(log)
    assert result["status"] == ac.CHAIN_OK
    assert result["replayed"]["entries"] == 5


def test_empty_state_is_chain_absent(tmp_path):
    result = ac.verify_chain(tmp_path / "audit-log.jsonl")
    assert result["status"] == ac.CHAIN_ABSENT


def test_edited_line_is_detected(tmp_path):
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 5)
    lines = log.read_text().splitlines()
    lines[2] = lines[2].replace("m2", "m2-falsified")
    log.write_text("\n".join(lines) + "\n")
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_MISMATCH


def test_deleted_line_is_detected(tmp_path):
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 5)
    lines = log.read_text().splitlines()
    del lines[1]
    log.write_text("\n".join(lines) + "\n")
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_MISMATCH


def test_reordered_lines_are_detected(tmp_path):
    """Same multiset of lines, different order. An order-insensitive digest
    would pass this; a chain must not, because the log's order *is* part of
    its claim (who ran what, and in what sequence)."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 5)
    lines = log.read_text().splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    log.write_text("\n".join(lines) + "\n")
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_MISMATCH


def test_truncation_is_detected(tmp_path):
    """Dropping the tail is the laziest way to erase history, and the one an
    'append-only convention' is most blind to."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 5)
    lines = log.read_text().splitlines()
    log.write_text("\n".join(lines[:3]) + "\n")
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_MISMATCH


def test_midregion_insertion_is_mismatch(tmp_path):
    """A line inserted INTO the committed region shifts it, so the prefix no
    longer replays to the committed head — CHAIN_HEAD_MISMATCH. (An unchained
    append at the END is a different, weaker signal — see the BEHIND tests.)"""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    lines = log.read_text().splitlines()
    lines.insert(1, json.dumps({"i": 99, "metric": "planted"}))
    log.write_text("\n".join(lines) + "\n")
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_MISMATCH


def test_unchained_append_at_end_is_behind_not_mismatch(tmp_path):
    """An unchained line appended at the end leaves the committed prefix intact,
    so it is the honest unattested-tail signal, not a tampering scream."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    with log.open("a") as f:
        f.write(json.dumps({"i": 99, "metric": "planted"}) + "\n")
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_BEHIND


def test_missing_head_is_reported_not_ok(tmp_path):
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    ac.head_path(log).unlink()
    result = ac.verify_chain(log)
    assert result["status"] == ac.CHAIN_HEAD_ABSENT
    assert result["replayed"]["entries"] == 3


def test_unchained_appends_are_not_blessed_with_a_live_sidecar(tmp_path):
    """Sneak two lines in without chaining, then make one legitimate chained
    append. advance_head extends from the *recorded* head, and the committed
    prefix (2) is intact while the log now has more lines — so this is the
    unattested-tail signal, not a clean OK. The lines are NOT blessed."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 2)
    with log.open("a") as f:
        f.write(json.dumps({"sneaked": 1}) + "\n")
        f.write(json.dumps({"sneaked": 2}) + "\n")
    _append_n(log, 1, start=2)
    assert ac.verify_chain(log)["status"] != ac.CHAIN_OK


def test_corrupt_sidecar_plus_planted_lines_is_not_laundered(tmp_path):
    """F1: the attack the old code failed. Corrupt the sidecar (so read_head
    returns None), append planted lines, then trigger one legit append. The
    old advance_head replayed the whole file and blessed everything — CHAIN_OK.
    Now, adoption is genesis-only, so the planted lines are never laundered:
    the state verifies as CHAIN_HEAD_ABSENT (no readable commitment), not OK."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    assert ac.verify_chain(log)["status"] == ac.CHAIN_OK
    ac.head_path(log).write_text("{}")  # corrupt, not delete — read_head -> None
    with log.open("a") as f:
        f.write(json.dumps({"planted": 1}) + "\n")
        f.write(json.dumps({"planted": 2}) + "\n")
    _append_n(log, 1, start=3)
    assert ac.verify_chain(log)["status"] != ac.CHAIN_OK
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_ABSENT


def test_deleted_sidecar_plus_planted_lines_is_not_laundered(tmp_path):
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    ac.head_path(log).unlink()
    with log.open("a") as f:
        f.write(json.dumps({"planted": 1}) + "\n")
    _append_n(log, 1, start=3)
    assert ac.verify_chain(log)["status"] != ac.CHAIN_OK


def test_legacy_log_is_not_silently_adopted(tmp_path):
    """A non-empty log with no sidecar (predates chaining, OR a deleted one —
    indistinguishable locally) is NOT adopted by the next append: adopting
    wholesale is exactly the laundering hole. It stays CHAIN_HEAD_ABSENT until
    an explicit adopt_log."""
    log = tmp_path / "audit-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as f:
        f.write(json.dumps({"legacy": 1}) + "\n")
        f.write(json.dumps({"legacy": 2}) + "\n")
    _append_n(log, 1)
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_ABSENT


def test_adopt_log_is_the_explicit_recommit(tmp_path):
    """The deliberate operator action advance_head refuses to do silently:
    (re)commit the current log in full. After it, the log verifies OK."""
    log = tmp_path / "audit-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as f:
        f.write(json.dumps({"legacy": 1}) + "\n")
        f.write(json.dumps({"legacy": 2}) + "\n")
    state = ac.adopt_log(log)
    assert state["entries"] == 2
    assert ac.verify_chain(log)["status"] == ac.CHAIN_OK


def test_missed_sidecar_write_reports_behind_not_tampering(tmp_path):
    """F2: a dropped sidecar write (log lands, head doesn't) must not cry wolf.
    Simulate one missed advance, then keep appending normally. The committed
    prefix stays intact, so verify reports CHAIN_HEAD_BEHIND (unattested tail),
    never the CHAIN_HEAD_MISMATCH a real edit raises."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    # A write whose sidecar advance is dropped: append the line only.
    with log.open("a") as f:
        f.write(json.dumps({"i": 3, "metric": "m3"}) + "\n")
    # ...and a couple more ordinary chained appends afterwards.
    _append_n(log, 2, start=4)
    result = ac.verify_chain(log)
    assert result["status"] == ac.CHAIN_HEAD_BEHIND
    # And it is recoverable by an explicit re-commit.
    ac.adopt_log(log)
    assert ac.verify_chain(log)["status"] == ac.CHAIN_OK


def test_behind_still_screams_mismatch_on_a_committed_region_edit(tmp_path):
    """The BEHIND path must not soften a real edit: change a line INSIDE the
    committed prefix and it is CHAIN_HEAD_MISMATCH, not BEHIND, even if the log
    also has an unattested tail."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 4)
    # Drop one sidecar advance to create an unattested tail...
    with log.open("a") as f:
        f.write(json.dumps({"i": 4, "metric": "m4"}) + "\n")
    # ...AND edit a line inside the committed region.
    lines = log.read_text().splitlines()
    lines[1] = lines[1].replace("m1", "m1-falsified")
    log.write_text("\n".join(lines) + "\n")
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_MISMATCH


def test_replay_is_utf8_regardless_of_records(tmp_path):
    """F4: a non-ASCII record must chain and verify identically — the fold is
    over UTF-8 bytes, not the locale default."""
    log = tmp_path / "audit-log.jsonl"
    assert aq.append_audit({"market": "Kööping", "metric": "m"}, log) is True
    assert aq.append_audit({"market": "Málaga", "metric": "m"}, log) is True
    assert ac.verify_chain(log)["status"] == ac.CHAIN_OK


def test_records_round_trip_unchanged(tmp_path):
    """Chaining lives in the sidecar; the log lines stay exactly the records
    that were appended. (This is the same contract test_audit_log pins — held
    here too because the chain is the thing most tempted to break it.)"""
    log = tmp_path / "audit-log.jsonl"
    record = {"metric": "m", "filters": {"market": "us"}}
    assert aq.append_audit(record, log) is True
    assert json.loads(log.read_text().strip()) == record


def test_replay_is_deterministic_from_bytes(tmp_path):
    """The chain is a pure function of GENESIS and the file bytes — a copied
    log replays to the identical head, so a head recorded anywhere pins any
    faithful copy."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 4)
    copy = tmp_path / "copy.jsonl"
    copy.write_text(log.read_text())
    assert ac.replay(log)["head"] == ac.replay(copy)["head"]


def test_advance_head_never_raises(tmp_path):
    """Same trade as append_audit: an unwritable sidecar must not break a
    query. It returns False and the gap shows up at verify time instead."""
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    # head_path would live under a file-as-directory -> both read and write fail.
    bad_log = blocker / "audit.jsonl"
    assert ac.advance_head(bad_log, "{}") is False


def test_verify_cli_covers_the_chain(tmp_path, monkeypatch, capsys):
    """The verify skill's CLI exposes the chain check: OK on an intact log,
    exit 1 on a falsified one."""
    import sys
    from pathlib import Path

    scripts = (
        Path(__file__).resolve().parents[1]
        / "plugins" / "assay" / "skills" / "verify" / "scripts"
    )
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import verify_artifact

    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)

    monkeypatch.setattr(
        "sys.argv", ["verify_artifact.py", "--audit-log", str(log)]
    )
    assert verify_artifact.main() == 0
    assert ac.CHAIN_OK in capsys.readouterr().out

    lines = log.read_text().splitlines()
    lines[0] = lines[0].replace("m0", "m0-falsified")
    log.write_text("\n".join(lines) + "\n")
    assert verify_artifact.main() == 1
    assert ac.CHAIN_HEAD_MISMATCH in capsys.readouterr().out


def test_verify_cli_detected_tamper_outranks_cannot_verify(tmp_path, monkeypatch, capsys):
    """F3: a confirmed audit-log tamper (exit 1) must not be masked by a
    co-occurring 'cannot verify' artifact (exit 2). An artifact with no
    manifest + a tampered log exits 1 (detected), not 2 (inconclusive) —
    severity, not numeric max over exit codes."""
    import sys
    from pathlib import Path

    scripts = (
        Path(__file__).resolve().parents[1]
        / "plugins" / "assay" / "skills" / "verify" / "scripts"
    )
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import verify_artifact

    # An artifact whose frontmatter has no manifest -> MANIFEST_ABSENT (exit 2).
    artifact = tmp_path / "art.md"
    artifact.write_text("---\nmetric: m\n---\n\nbody\n")
    # A tampered audit log -> CHAIN_HEAD_MISMATCH (exit 1, detected).
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    lines = log.read_text().splitlines()
    lines[1] = lines[1].replace("m1", "m1-falsified")
    log.write_text("\n".join(lines) + "\n")

    monkeypatch.setattr(
        "sys.argv",
        ["verify_artifact.py", str(artifact), "--audit-log", str(log)],
    )
    assert verify_artifact.main() == 1  # detected wins over cannot-verify
    out = capsys.readouterr().out
    assert ac.CHAIN_HEAD_MISMATCH in out and "MANIFEST_ABSENT" in out
