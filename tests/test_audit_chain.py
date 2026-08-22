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


def test_inserted_line_is_detected(tmp_path):
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    with log.open("a") as f:
        f.write(json.dumps({"i": 99, "metric": "planted"}) + "\n")
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_MISMATCH


def test_missing_head_is_reported_not_ok(tmp_path):
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 3)
    ac.head_path(log).unlink()
    result = ac.verify_chain(log)
    assert result["status"] == ac.CHAIN_HEAD_ABSENT
    assert result["replayed"]["entries"] == 3


def test_unchained_appends_are_not_blessed(tmp_path):
    """Sneak two lines in without chaining, then make one legitimate chained
    append. advance_head must extend from the *recorded* head — adopting the
    file wholesale would launder the sneaked lines into attested history."""
    log = tmp_path / "audit-log.jsonl"
    _append_n(log, 2)
    with log.open("a") as f:
        f.write(json.dumps({"sneaked": 1}) + "\n")
        f.write(json.dumps({"sneaked": 2}) + "\n")
    _append_n(log, 1, start=2)
    assert ac.verify_chain(log)["status"] == ac.CHAIN_HEAD_MISMATCH


def test_legacy_log_is_adopted_on_first_chained_append(tmp_path):
    """A log that predates chaining (lines, no sidecar) gets a full replay on
    the first chained append — adoption, not blessing: with no recorded head
    there is no earlier commitment to contradict."""
    log = tmp_path / "audit-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as f:
        f.write(json.dumps({"legacy": 1}) + "\n")
        f.write(json.dumps({"legacy": 2}) + "\n")
    _append_n(log, 1)
    result = ac.verify_chain(log)
    assert result["status"] == ac.CHAIN_OK
    assert result["replayed"]["entries"] == 3


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
