"""Warn-level guardrails: the data-quality caveat layer.

Covers the shared library (`_shared/guardrails.py`) — cutover-window matching,
column and view note matching, the reference-only exclusion, the formatted output —
and **lints the bundled `config/guardrails.yaml`**.

The linting half is the part worth being deliberate about. `load_guardrails()`
returns `{}` for a file that is missing *or unparseable*, which is the right
behaviour for advisory metadata — a caveat layer that can break a query is worse
than no caveat layer. But it means a typo'd register produces **zero warnings
instead of an error**, and every matcher test would still pass against the empty
dict it degrades to. So the first assertions here are that the shipped file actually
parses and is non-empty; without them this file would test the matchers in isolation
and certify a register that had silently stopped existing.

Pure and in-process — never touches a warehouse.
"""

import datetime as dt
from pathlib import Path

import pytest

from _shared import guardrails as gr

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "plugins" / "assay" / "config" / "guardrails.yaml"


@pytest.fixture(scope="module")
def bundled():
    return gr.load_guardrails(BUNDLED)


# --- the register exists and parses (see the module docstring) ---------------


def test_the_bundled_register_is_present_and_non_empty(bundled):
    """The assertion that stops every test below passing vacuously.

    A missing or malformed guardrails.yaml degrades to `{}`, and `{}` satisfies every
    matcher contract perfectly. This is the only test that can tell the difference
    between "the caveat layer works" and "the caveat layer has nothing in it".
    """
    assert BUNDLED.exists(), f"{BUNDLED} is missing"
    assert bundled, (
        f"{BUNDLED} loaded as empty. load_guardrails() swallows a YAML error by "
        f"design, so a syntax mistake here would otherwise show up as silence — no "
        f"caveats on any artifact, and no failing test."
    )
    assert bundled.get("feed_cutovers"), "expected a non-empty feed_cutovers list"
    assert bundled.get("notes"), "expected a non-empty notes list"


def test_the_register_is_reached_by_default_without_an_explicit_path():
    """The bundled file must be what the *default* resolution finds. Passing an
    explicit path everywhere else in this file would let a broken default — a moved
    config directory, a bad `_plugin_root()` — pass unnoticed."""
    assert gr.guardrails_path() == BUNDLED
    assert gr.load_guardrails(), "the default path resolved to an empty register"


def test_cutovers_are_well_formed(bundled):
    ids = set()
    for c in bundled["feed_cutovers"]:
        assert c.get("id"), f"cutover missing id: {c}"
        assert c["id"] not in ids, f"duplicate cutover id {c['id']}"
        ids.add(c["id"])
        assert c.get("label"), f"{c['id']}: needs a label"
        assert c.get("effect"), f"{c['id']}: needs an effect — a date with no stated"
        assert gr._as_date(c.get("date")) is not None, f"{c['id']}: bad date {c.get('date')!r}"
        assert c.get("entity"), f"{c['id']}: needs an entity anchor"


def test_notes_are_well_formed(bundled):
    ids = set()
    for n in bundled["notes"]:
        assert n.get("id"), f"note missing id: {n}"
        assert n["id"] not in ids, f"duplicate note id {n['id']}"
        ids.add(n["id"])
        assert n.get("message"), f"{n['id']}: needs a message"
        assert n.get("entity"), f"{n['id']}: needs an entity anchor"


def test_the_register_exercises_both_scoping_mechanisms(bundled):
    """Column-scoped, view-scoped, and reference-only notes must all be present, or
    a matcher branch is untested against real content."""
    kinds = {"columns": 0, "views": 0, "reference_only": 0}
    for n in bundled["notes"]:
        if n.get("applies_to_columns"):
            kinds["columns"] += 1
        if n.get("applies_to_views"):
            kinds["views"] += 1
        if not n.get("applies_to_columns") and not n.get("applies_to_views"):
            kinds["reference_only"] += 1
    for kind, count in kinds.items():
        assert count, f"the register has no {kind} notes, so that branch is untested"


def test_cutover_dates_fall_inside_the_fixture_window(bundled):
    """A cutover outside the fixtures' cohort range can never fire offline, so it
    would be documentation masquerading as a checked mechanism."""
    dates = [gr._as_date(c["date"]) for c in bundled["feed_cutovers"]]
    assert min(dates) >= dt.date(2025, 2, 17), "a cutover predates the fixture window"
    assert max(dates) <= dt.date(2026, 8, 17), "a cutover postdates the fixture window"


# --- cutover window matching ------------------------------------------------


def test_cutover_in_window_is_matched(bundled):
    hits = gr.cutovers_in_window(bundled, dt.date(2026, 5, 25), dt.date(2026, 6, 29))
    assert "carrier_scan_feed_switch" in {c["id"] for c in hits}


def test_cutover_window_misses_when_nothing_is_crossed(bundled):
    assert gr.cutovers_in_window(bundled, dt.date(2026, 7, 6), dt.date(2026, 7, 27)) == []


def test_cutover_bounds_are_inclusive(bundled):
    exact = gr.cutovers_in_window(bundled, dt.date(2026, 6, 1), dt.date(2026, 6, 1))
    assert any(c["id"] == "carrier_scan_feed_switch" for c in exact)


def test_the_checkout_version_cutover_matches_the_generator(bundled):
    """The v1 -> v2 cutover date has to be the generator's real one, or the largest
    discontinuity in the fixtures is caveated on the wrong week — and a window that
    genuinely crosses it gets no warning."""
    c = next(c for c in bundled["feed_cutovers"] if c["id"] == "checkout_v2_rollout")
    assert gr._as_date(c["date"]) == dt.date(2025, 5, 12)


def test_cutover_warnings_are_prefixed_and_dated(bundled):
    w = gr.cutover_warnings(bundled, dt.date(2026, 5, 25), dt.date(2026, 6, 29))
    assert w and all(s.startswith("DISCONTINUITY:") for s in w)
    assert any("2026-06-01" in s for s in w)


# --- note matching ----------------------------------------------------------


def test_note_matches_on_a_referenced_column(bundled):
    notes = gr.applicable_notes(bundled, source_view=None, columns=["payment_method"])
    assert any(n["id"] == "payment_method_drives_maturity_not_only_rate" for n in notes)


def test_note_matches_on_the_source_view_by_bare_table_name(bundled):
    """`source_view` arrives as `dataset.table`, and the register lists the bare
    table — both forms must match, or every note scoped to a view is dead."""
    notes = gr.applicable_notes(
        bundled, source_view="analytics.cart_checkout_session_spine", columns=[]
    )
    assert any(n["id"] == "spine_timestamps_are_utc_and_cohorts_are_iso_monday" for n in notes)


def test_reference_only_notes_are_never_injected(bundled):
    """A note with no `applies_to_*` is captured, not fired. Asserted with a
    deliberately broad query — a real view and several real columns — so the test
    fails if scoping is ever loosened to 'match everything'."""
    notes = gr.applicable_notes(
        bundled,
        source_view="analytics.cart_checkout_session_spine",
        columns=["payment_method", "category", "order_id", "terminal_branch"],
    )
    ids = {n["id"] for n in notes}
    assert "marketplace_channel_is_a_reseller_rollup" not in ids
    assert "returns_are_only_knowable_after_the_window" not in ids
    assert "customer_token_is_opaque_not_a_person" not in ids


def test_note_warnings_are_prefixed_with_their_id(bundled):
    w = gr.note_warnings(bundled, source_view=None, columns=["order_id"])
    assert any(s.startswith("CAVEAT (order_id_is_null_before_confirmation):") for s in w)


# --- end to end -------------------------------------------------------------


def test_warnings_for_combines_cutovers_and_notes():
    w = gr.warnings_for(
        source_view="analytics.cart_checkout_funnel_weekly",
        columns=["payment_method"],
        start=dt.date(2026, 5, 25),
        end=dt.date(2026, 6, 29),
        path=BUNDLED,
    )
    assert any(s.startswith("DISCONTINUITY:") for s in w)
    assert any(s.startswith("CAVEAT (payment_method_drives_maturity_not_only_rate):") for s in w)
    assert any(s.startswith("CAVEAT (weekly_aggregate_is_a_narrower_interface):") for s in w)


def test_a_query_touching_nothing_gets_no_caveats():
    """The other half of the contract: guardrails must not be noise. A window that
    crosses no cutover and references no scoped column gets an empty list."""
    assert gr.warnings_for(
        source_view="analytics.some_unrelated_view",
        columns=["nothing_scoped"],
        start=dt.date(2026, 7, 6),
        end=dt.date(2026, 7, 27),
        path=BUNDLED,
    ) == []


# --- robustness -------------------------------------------------------------


def test_a_missing_file_returns_empty_and_never_raises(tmp_path):
    assert gr.load_guardrails(tmp_path / "nope.yaml") == {}
    assert gr.cutovers_in_window({}, dt.date(2026, 1, 1), dt.date(2026, 12, 31)) == []
    assert gr.applicable_notes({}, None, ["payment_method"]) == []
    assert gr.warnings_for("v", ["c"], dt.date(2026, 1, 1), dt.date(2026, 2, 1),
                           path=tmp_path / "nope.yaml") == []


def test_a_malformed_file_degrades_to_empty_rather_than_raising(tmp_path):
    """The documented degradation, pinned so it is a decision rather than an
    accident — and so the reason the non-empty assertions above exist is visible
    right next to the behaviour that makes them necessary."""
    bad = tmp_path / "broken.yaml"
    bad.write_text("feed_cutovers: [unclosed\n")
    assert gr.load_guardrails(bad) == {}


def test_a_bad_date_is_skipped_not_crashed(tmp_path):
    """One unparseable date must not take the whole register down with it."""
    p = tmp_path / "g.yaml"
    p.write_text(
        "feed_cutovers:\n"
        "  - {id: good, date: 2026-06-01, label: l, effect: e}\n"
        "  - {id: bad, date: not-a-date, label: l, effect: e}\n"
    )
    gr_cfg = gr.load_guardrails(p)
    hits = gr.cutovers_in_window(gr_cfg, dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert [c["id"] for c in hits] == ["good"]
