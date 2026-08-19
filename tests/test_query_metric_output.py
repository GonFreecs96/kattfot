"""Output ergonomics: the helper's display and safe-failure contract.

  - nearest-match "did you mean?" on a metric-name miss (R1 stays a refusal, but a
    useful one)
  - `column_label` resolution (a friendly value-column header, display-only)
  - `volume_summary` (surface the n a ratio sits on)
  - `decorate_display` (formatting codified in tested code, not left to the agent)
  - `post_run_warnings` (zero-row and typo'd-filter detection)

All pure functions — no warehouse, no CSV — so they run in CI exactly as the
governance suite does.
"""

import pytest

import assay_query as aq


# --- nearest-match suggestion ------------------------------------------------


def test_suggest_close_names_fuzzy_typo():
    available = ["payment_approval_rate", "payment_decline_rate", "cart_to_stale_rate"]
    out = aq.suggest_close_names("payment_aproval_rate", available)
    assert "payment_approval_rate" in out


def test_suggest_close_names_promotes_substring():
    available = ["payment_approval_rate", "payment_decline_rate", "cart_to_stale_rate"]
    out = aq.suggest_close_names("payment", available)
    assert out[0] in {"payment_approval_rate", "payment_decline_rate"}
    assert "cart_to_stale_rate" not in out


def test_suggest_close_names_empty_registry():
    assert aq.suggest_close_names("anything", []) == []


def test_suggest_close_names_caps_the_list():
    available = [f"metric_{i}" for i in range(20)]
    assert len(aq.suggest_close_names("metric", available)) <= 3


def test_find_metric_file_miss_suggests_a_close_match():
    """A near-typo of a real bundled metric refuses (R1) *and* names the metric."""
    with pytest.raises(SystemExit) as exc:
        aq.find_metric_file("payment_aproval_rate")
    assert "payment_approval_rate" in str(exc.value)


# --- column_label -----------------------------------------------------------


def test_resolve_column_label_uses_label_when_present():
    assert aq.resolve_column_label({"name": "x_rate", "column_label": "X Rate"}) == "X Rate"


def test_resolve_column_label_falls_back_to_name():
    assert aq.resolve_column_label({"name": "x_rate"}) == "x_rate"
    assert aq.resolve_column_label({"name": "x_rate", "column_label": "  "}) == "x_rate"
    assert aq.resolve_column_label({"name": "x_rate", "column_label": None}) == "x_rate"


# --- volume_summary ---------------------------------------------------------


def test_volume_summary_totals_and_count():
    rows = [
        {"numerator": 10, "denominator": 100},
        {"numerator": 5, "denominator": 50},
    ]
    assert aq.volume_summary(rows) == {
        "row_count": 2, "total_numerator": 15, "total_denominator": 150,
    }


def test_volume_summary_tolerates_none_and_nonnumeric():
    """A NULL cell must not crash the summary. The volume line is what stops a rate
    being read without its n, so it has to survive a ragged result."""
    rows = [
        {"numerator": None, "denominator": 100},
        {"numerator": 5, "denominator": "n/a"},
    ]
    assert aq.volume_summary(rows) == {
        "row_count": 2, "total_numerator": 5, "total_denominator": 100,
    }


def test_volume_summary_empty():
    assert aq.volume_summary([]) == {
        "row_count": 0, "total_numerator": 0, "total_denominator": 0,
    }


# --- decorate_display -------------------------------------------------------


def test_decorate_display_formats_the_value_and_the_counts():
    """Formatting happens in tested code rather than being left to the agent, so a
    rendered number cannot drift from a codified rule."""
    rows = [{"m": 0.8412, "numerator": 3401, "denominator": 4043}]
    aq.decorate_display(rows, "m", "percent")
    d = rows[0]["_display"]
    assert d["m"] == "84.1%"
    assert d["numerator"] == "3,401"
    assert d["denominator"] == "4,043"
    # The raw values stay untouched; _display is the reporting-boundary view.
    assert rows[0]["m"] == 0.8412


def test_decorate_display_never_renders_null_as_zero():
    """A NULL rate and a 0% rate are different findings. `SAFE_DIVIDE` returns NULL on
    a zero denominator, and rendering that as 0% would report a catastrophe where
    there is simply no data.

    The exact marker is `numfmt`'s business (it uses `n/a`); what this pins is the
    property that matters — whatever it is, it is not a number, and specifically not
    zero. `chart_spec`'s null-cell set recognises it, so a NULL survives the round
    trip into a chart as a gap rather than as a point at the origin.
    """
    from chart_spec import parse_number

    rows = [{"m": None, "numerator": 0, "denominator": 0}]
    aq.decorate_display(rows, "m", "percent")
    shown = rows[0]["_display"]["m"]
    assert shown not in ("0", "0%", "0.0%", "0.00%")
    assert parse_number(shown) is None


# --- post_run_warnings ------------------------------------------------------


def test_zero_rows_with_filters_flags_a_typo():
    """Filters bind as exact string matches, so a typo'd value returns nothing. Left
    unflagged, that reads as 'the rate is empty' rather than 'you asked for a value
    that does not exist'."""
    w = aq.post_run_warnings([], {"channel": "mobile_ap"})
    assert len(w) == 1
    assert "channel=mobile_ap" in w[0]
    assert "typo" in w[0].lower()


def test_zero_rows_without_filters_generic():
    w = aq.post_run_warnings([], {})
    assert len(w) == 1
    assert "Zero rows" in w[0]


def test_nonempty_rows_no_warning():
    assert aq.post_run_warnings([{"numerator": 1, "denominator": 2}], {}) == []
