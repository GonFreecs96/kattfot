"""The audit / trace log: append-only, best-effort, a trace and not a copy.

Covers `build_audit_record` (which fields, and that row data is excluded) and
`append_audit` (one JSON line per call, never raises on an unwritable path).

The "never raises" property is the one worth being deliberate about. An audit-log
write failure must not break a query: a read-only filesystem or a full disk is an
operational problem, and turning it into a governance outage would tempt someone to
disable the logging rather than fix the disk. The trade-off is that a silent failure
means a missing trace entry, which is why the function returns a boolean the caller
can act on rather than swallowing the outcome entirely.

No warehouse access.
"""

import json

import assay_query as aq


SAMPLE_RESULT = {
    "generated_at": "2026-08-17T12:00:00",
    "metric_name": "cart_to_checkout_start_conversion",
    "accuracy_tier": "T2",
    "source_mode": "fixture_sql",
    "engine": "sqlite",
    "fixture_backed": True,
    "source_view": "analytics.cart_checkout_funnel_weekly",
    "source_project": "${ASSAY_BIGQUERY_PROJECT}",
    "dimensions": ["channel"],
    "filters": {"market": "us"},
    "time_range": {"start": "2026-04-27", "end": "2026-08-10"},
    "row_count": 64,
    "manifest": {"plugin_version": "0.1.0", "query_sha": "abc123"},
    "rows": [{"x": 1}],  # must NOT leak into the audit record
    "query": "SELECT ...",  # nor the SQL body
}


def test_build_audit_record_shape():
    rec = aq.build_audit_record(SAMPLE_RESULT, "analyst@example.com")
    assert rec["metric"] == "cart_to_checkout_start_conversion"
    assert rec["user_email"] == "analyst@example.com"
    assert rec["row_count"] == 64
    assert rec["filters"] == {"market": "us"}
    assert rec["manifest"]["query_sha"] == "abc123"


def test_the_record_is_a_trace_not_a_copy():
    """The row data is never logged. An audit log that accumulated result sets would
    become a second, ungoverned copy of the warehouse — with none of the allow-list or
    PII controls that govern the first."""
    rec = aq.build_audit_record(SAMPLE_RESULT, None)
    assert "rows" not in rec
    assert "query" not in rec


def test_the_record_captures_which_engine_served_it():
    """Which engine ran a query is part of reconstructing it: a fixture-backed number
    and a warehouse number are not interchangeable, and six months later the artifact
    may be gone while the log remains."""
    rec = aq.build_audit_record(SAMPLE_RESULT, None)
    assert rec["engine"] == "sqlite"
    assert rec["fixture_backed"] is True
    assert rec["source_mode"] == "fixture_sql"


def test_missing_fields_do_not_raise():
    """A partial result (an early failure, a caller passing a subset) still produces a
    record. The log is best-effort by design, and a KeyError here would take down the
    query it was meant to record."""
    rec = aq.build_audit_record({}, None)
    assert rec["metric"] is None
    assert rec["user_email"] is None


def test_append_audit_writes_one_line_per_call(tmp_path):
    log = tmp_path / "nested" / "audit-log.jsonl"
    assert aq.append_audit({"a": 1}, log) is True
    assert aq.append_audit({"b": 2}, log) is True
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": 2}


def test_append_audit_creates_parent_directories(tmp_path):
    log = tmp_path / "a" / "b" / "c" / "audit-log.jsonl"
    assert aq.append_audit({"x": 1}, log) is True
    assert log.exists()


def test_append_audit_never_raises_on_a_bad_path(tmp_path):
    """A path whose parent is a file, so mkdir and open both fail internally. Returns
    False rather than raising — the caller can log the miss, and the query survives."""
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad = blocker / "audit.jsonl"
    assert aq.append_audit({"a": 1}, bad) is False


def test_append_audit_serializes_dates(tmp_path):
    """Records carry dates. `json.dumps(default=str)` handles them, and without it every
    real record would fail to serialize while the unit tests above passed."""
    import datetime as dt

    log = tmp_path / "audit-log.jsonl"
    assert aq.append_audit({"d": dt.date(2026, 8, 17)}, log) is True
    assert json.loads(log.read_text())["d"] == "2026-08-17"


def test_audit_log_path_honours_the_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSAY_AUDIT_LOG", str(tmp_path / "custom.jsonl"))
    assert aq.audit_log_path() == tmp_path / "custom.jsonl"

    monkeypatch.delenv("ASSAY_AUDIT_LOG")
    monkeypatch.setenv("ASSAY_OUTPUTS_DIR", str(tmp_path / "out"))
    assert aq.audit_log_path() == tmp_path / "out" / "audit-log.jsonl"
