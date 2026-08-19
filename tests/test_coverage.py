"""The instrumentation coverage register: the "can I measure X yet?" layer.

Covers the shared library (`_shared/coverage.py`) — keyword matching, id and
status-summary resolution, the robustness contract — and **lints the bundled
`config/coverage.yaml`**.

Same deliberate structure as `test_guardrails.py`, for the same reason.
`load_coverage()` returns `{}` for a file that is missing *or unparseable*, which is
correct for advisory metadata but means a typo'd register produces **zero coverage
answers instead of an error**. Every matcher assertion below would still pass against
the empty dict it degrades to, so the first tests here assert the shipped file parses
and is non-empty. Without them this file would certify a register that had silently
stopped existing — and `assay:coverage` would then answer "not in the register" to
every question, which reads exactly like a legitimate no-match.

Pure and in-process — never touches a warehouse.
"""

from pathlib import Path

import pytest

from _shared import coverage as cov

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "plugins" / "assay" / "config" / "coverage.yaml"


@pytest.fixture(scope="module")
def bundled():
    return cov.load_coverage(BUNDLED)


# --- the register exists and parses (see the module docstring) ---------------


def test_the_bundled_register_is_present_and_non_empty(bundled):
    """The assertion that stops every test below passing vacuously."""
    assert BUNDLED.exists(), f"{BUNDLED} is missing"
    assert bundled, (
        f"{BUNDLED} loaded as empty. load_coverage() swallows a YAML error by design, "
        f"so a syntax mistake here would show up as `assay:coverage` no-matching every "
        f"question — indistinguishable from a genuine gap in the register."
    )
    entries = cov.entries(bundled)
    assert len(entries) >= 6, f"only {len(entries)} coverage entries; expected a real register"


def test_the_register_is_reached_by_default_without_an_explicit_path():
    assert cov.coverage_path() == BUNDLED
    assert cov.entries(cov.load_coverage()), "the default path resolved to an empty register"


# --- config integrity (the linter) ------------------------------------------


def test_required_keys_and_status_enum(bundled):
    for e in cov.entries(bundled):
        assert e.get("id"), f"entry missing id: {e}"
        assert e.get("asks"), f"{e['id']}: must carry at least one `asks` phrase"
        assert e.get("status") in cov.STATUSES, f"{e['id']}: bad status {e.get('status')!r}"


def test_ids_are_unique(bundled):
    ids = [e["id"] for e in cov.entries(bundled)]
    assert len(ids) == len(set(ids)), "coverage entry ids must be unique"


def test_non_queryable_entries_carry_a_blocker(bundled):
    """`live` and `partial` are queryable; everything else has to explain why not.
    A non-queryable entry with no blocker is a dead end — it tells a stakeholder no
    and gives them nowhere to go."""
    for e in cov.entries(bundled):
        if e["status"] not in cov.QUERYABLE:
            assert e.get("blocker"), f"{e['id']} ({e['status']}) must carry a blocker"


def test_live_entries_have_no_blocker_and_say_how_they_are_served(bundled):
    """A `live` thing is measurable now, so a blocker on it is a contradiction — and
    it must name the route, or "yes" is not actionable."""
    for e in cov.entries(bundled):
        if e["status"] == "live":
            assert not e.get("blocker"), f"{e['id']} is live but carries a blocker"
            assert e.get("serve"), f"{e['id']} is live but does not say how it is served"


def test_partial_entries_carry_both_halves(bundled):
    """`partial` is the dangerous status: there IS a number, and it looks complete.
    So it needs the route AND the hole — one without the other is either an
    unusable warning or an unqualified number."""
    partials = [e for e in cov.entries(bundled) if e["status"] == "partial"]
    assert partials, "expected at least one partial entry — it is the trickiest branch"
    for e in partials:
        assert e.get("blocker"), f"{e['id']} is partial but names no coverage hole"


def test_blockers_are_substantive_not_placeholders(bundled):
    """"TBD" is not a blocker. A blocker has to carry enough that a reader can tell
    a hard ceiling from a queued ticket — those need completely different responses."""
    for e in cov.entries(bundled):
        blocker = e.get("blocker")
        if not blocker:
            continue
        assert len(blocker.strip()) >= 80, f"{e['id']}: blocker is too thin to act on"
        assert "tbd" not in blocker.lower(), f"{e['id']}: blocker says TBD rather than why"


def test_every_entry_names_an_owner(bundled):
    """Placeholders in this template, a person in a real one — but never absent.
    "Who owns this" is the field that turns a coverage answer into a next step."""
    for e in cov.entries(bundled):
        assert e.get("owner"), f"{e['id']}: needs an owner"


def test_every_status_in_the_enum_is_exercised(bundled):
    """The register seeds all six statuses so every branch of the `coverage` skill's
    verdict table is real rather than hypothetical."""
    seen = {e["status"] for e in cov.entries(bundled)}
    missing = cov.STATUSES - seen
    assert not missing, f"no entry exercises status(es) {sorted(missing)}"


def test_the_browse_handoff_entry_exists_and_explains_the_denominator(bundled):
    """The registry's `browse/session -> checkout/session` handoff carries a coverage
    number that is a design-time estimate, and this is the entry a stakeholder reaches
    for before trusting it. If it ever goes missing, the edge's caveat exists only in
    a YAML comment."""
    e = cov.by_id(bundled, "browse_to_cart_handoff")
    assert e is not None, "the browse handoff has no coverage entry"
    assert e["status"] not in cov.QUERYABLE
    blocker = e["blocker"].lower()
    assert "denominator" in blocker
    assert "conversion rate" in blocker


def test_the_contract_documented_example_id_resolves(bundled):
    """`contracts/consumer-api.md` cites `coverage/cart_abandon_reason` as the worked
    example of the scope. A contract that names an id which does not exist is a
    documentation bug that only a consumer would discover."""
    assert cov.by_id(bundled, "cart_abandon_reason") is not None


# --- matching ---------------------------------------------------------------


def test_find_matches_on_an_asks_phrase(bundled):
    hits = cov.find(bundled, "why was the cart abandoned")
    assert any(e["id"] == "cart_abandon_reason" for e in hits)


def test_find_matches_on_a_field_name(bundled):
    hits = cov.find(bundled, "wallet_provider")
    assert any(e["id"] == "wallet_provider_breakdown" for e in hits)


def test_find_matches_on_a_stream_name(bundled):
    hits = cov.find(bundled, "raw_payments.authorization_attempts")
    assert any(e["id"] == "payment_retry_attempts" for e in hits)


def test_find_matches_on_an_id(bundled):
    assert any(e["id"] == "return_reason_codes" for e in cov.find(bundled, "return_reason"))


def test_find_is_case_insensitive(bundled):
    assert cov.find(bundled, "Promo Code") == cov.find(bundled, "promo code")
    assert cov.find(bundled, "Promo Code"), "expected the case-folded match to hit"


def test_find_empty_term_matches_nothing(bundled):
    """An empty term must not match everything. A skill that passes through a blank
    ask would otherwise dump the whole register as though it had answered."""
    assert cov.find(bundled, "") == []
    assert cov.find(bundled, "   ") == []
    assert cov.find(bundled, None) == []


def test_find_unknown_term_returns_empty(bundled):
    assert cov.find(bundled, "quantum widget throughput") == []


def test_by_id(bundled):
    e = cov.by_id(bundled, "payment_retry_attempts")
    assert e and e["status"] == "empty"
    assert cov.by_id(bundled, "does_not_exist") is None


# --- summary ----------------------------------------------------------------


def test_summary_groups_by_status_with_queryable_first(bundled):
    s = cov.summary(bundled)
    order = list(s)
    assert order.index("live") < order.index("blocked")
    assert order.index("partial") < order.index("declined")
    total = sum(len(v) for v in s.values())
    assert total == len(cov.entries(bundled)), "every entry accounted for exactly once"


def test_summary_of_an_empty_register_is_empty():
    assert cov.summary({}) == {}


# --- robustness -------------------------------------------------------------


def test_a_missing_file_returns_empty_and_every_accessor_tolerates_it(tmp_path):
    assert cov.load_coverage(tmp_path / "nope.yaml") == {}
    assert cov.entries({}) == []
    assert cov.by_id({}, "anything") is None
    assert cov.find({}, "cart events") == []


def test_a_malformed_file_degrades_to_empty_rather_than_raising(tmp_path):
    """The documented degradation, pinned next to the non-empty assertions that exist
    because of it."""
    bad = tmp_path / "broken.yaml"
    bad.write_text("coverage: [unclosed\n")
    assert cov.load_coverage(bad) == {}


def test_queryable_is_a_strict_subset_of_the_status_enum():
    """A status added to QUERYABLE but not to STATUSES (or vice versa) would make the
    skill's verdict table and the linter disagree about what 'measurable' means."""
    assert cov.QUERYABLE < cov.STATUSES
    assert cov.QUERYABLE == {"live", "partial"}
