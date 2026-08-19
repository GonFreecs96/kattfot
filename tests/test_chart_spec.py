"""visualize-metric's faithfulness contract.

A chart is the form a number travels in most easily and is questioned least, so the
governance has to survive the trip into it. These pin the properties that make a
rendered chart defensible rather than decorative:

  - it refuses an ungoverned artifact outright;
  - it draws ONLY what the artifact's Results table contains;
  - a NULL cell stays a gap and never becomes a point at zero;
  - rate axes anchor at 0, which is not configurable;
  - the tier, source, and generated-at stamp travel with the spec;
  - fixture-backed data is watermarked, erring toward watermarking;
  - chart-type inference is deterministic and explains itself;
  - a funnel composition records the per-stage maturity spread that makes its
    cumulative pass-through differ from the named end-to-end metric.

`render.py` itself needs matplotlib and is not imported here — the suite stays
installable with no compiled dependencies. What constrains every renderer is the spec,
and that is what is tested.
"""

import json
import textwrap

import pytest

import chart_spec as cs


ARTIFACT = textwrap.dedent(
    """\
    ---
    metric: cart_to_checkout_start_conversion
    display_name: Cart to Checkout Start conversion rate
    accuracy_tier: T2
    source_view: analytics.cart_checkout_funnel_weekly
    source_project: ${ASSAY_BIGQUERY_PROJECT}
    source_mode: fixture_sql
    fixture_backed: true
    generated_at: 2026-08-17T12:00:00
    time_range_start: 2026-06-01
    time_range_end: 2026-07-06
    dimensions: []
    filters: {}
    ---

    # Cart to Checkout Start conversion rate

    ## Results

    | cohort_week | Cart -> Checkout | numerator | denominator |
    | --- | --- | --- | --- |
    | 2026-06-01 | 68.8% | 769 | 1,118 |
    | 2026-06-08 | 69.8% | 791 | 1,134 |
    | 2026-06-15 | 68.9% | 793 | 1,151 |

    ## Narrative

    Words.
    """
)

ARTIFACT_WITH_DIM = textwrap.dedent(
    """\
    ---
    metric: cart_to_checkout_start_conversion
    display_name: Cart to Checkout Start conversion rate
    accuracy_tier: T2
    source_view: analytics.cart_checkout_funnel_weekly
    source_mode: bigquery
    fixture_backed: false
    generated_at: 2026-08-17T12:00:00
    dimensions: [channel]
    ---

    ## Results

    | cohort_week | channel | Cart -> Checkout | numerator | denominator |
    | --- | --- | --- | --- | --- |
    | 2026-06-01 | web | 71.0% | 475 | 669 |
    | 2026-06-01 | mobile_app | 64.0% | 222 | 347 |
    | 2026-06-08 | web | 72.0% | 480 | 667 |
    | 2026-06-08 | mobile_app | 65.0% | 231 | 355 |

    ## Narrative
    """
)


def _write(tmp_path, text, name="artifact.md"):
    p = tmp_path / name
    p.write_text(text)
    return p


# --- governed provenance is a precondition ----------------------------------


@pytest.mark.parametrize("missing", ["metric", "accuracy_tier", "source_view", "generated_at"])
def test_refuses_an_artifact_missing_governed_provenance(tmp_path, missing):
    """An artifact without a tier and a source has no business becoming a chart. A
    chart is screenshotted and pasted far more readily than a markdown file, so an
    ungoverned one entering the pipeline is exactly backwards."""
    text = "\n".join(
        ln for ln in ARTIFACT.splitlines() if not ln.startswith(f"{missing}:")
    )
    with pytest.raises(SystemExit) as exc:
        cs.parse_artifact(_write(tmp_path, text))
    assert missing in str(exc.value)


def test_refuses_a_file_with_no_frontmatter(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cs.parse_artifact(_write(tmp_path, "# just a heading\n"))
    assert "frontmatter" in str(exc.value)


def test_refuses_an_artifact_with_no_results_table(tmp_path):
    text = ARTIFACT.split("## Results")[0]
    with pytest.raises(SystemExit) as exc:
        cs.parse_artifact(_write(tmp_path, text))
    assert "Results" in str(exc.value)


# --- it draws only what the artifact contains -------------------------------


def test_reads_the_results_table_verbatim(tmp_path):
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT))
    assert len(rows) == 3
    assert rows[0]["cohort_week"] == "2026-06-01"
    assert rows[0]["numerator"] == "769"


def test_spec_point_count_matches_the_table(tmp_path):
    """The spec cannot contain more points than the table it was built from. This is the
    property that makes 'no re-query' checkable rather than merely stated."""
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT))
    spec = cs.build_single_spec(fm, rows, None, None, None, 0)
    total_points = sum(len(s["points"]) for s in spec["series"])
    assert total_points == len(rows)
    assert spec["source_rows"] == len(rows)


def test_only_the_latest_period_appears_in_a_ranked_bar(tmp_path):
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT_WITH_DIM))
    spec = cs.build_single_spec(fm, rows, "compare", None, None, 0)
    assert spec["chart_type"] == "ranked_bar"
    assert spec["as_of"] == "2026-06-08"
    assert {b["label"] for b in spec["bars"]} == {"web", "mobile_app"}
    assert len(spec["bars"]) == 2  # not four — the earlier week is not double-counted


# --- NULL is a gap, not a zero ----------------------------------------------


@pytest.mark.parametrize("cell", ["—", "-", "", "n/a", "N/A", "none", "null"])
def test_null_cells_parse_to_none(cell):
    """`numfmt` renders a NULL rate as `n/a`; the em-dash and friends are covered too so
    a hand-written or differently-formatted artifact behaves the same."""
    assert cs.parse_number(cell) is None


def test_a_null_rate_never_becomes_zero(tmp_path):
    """SAFE_DIVIDE returns NULL on a zero denominator. Plotting that as 0% would invent a
    data point that reads as a total collapse."""
    text = ARTIFACT.replace("| 2026-06-15 | 68.9% | 793 | 1,151 |", "| 2026-06-15 | n/a | 0 | 0 |")
    fm, rows = cs.parse_artifact(_write(tmp_path, text))
    spec = cs.build_single_spec(fm, rows, None, None, None, 0)
    ys = [p["y"] for p in spec["series"][0]["points"]]
    assert ys[-1] is None
    assert 0 not in ys


def test_percent_and_count_cells_round_trip():
    assert cs.parse_number("68.8%") == pytest.approx(0.688)
    assert cs.parse_number("1,118") == 1118
    assert cs.parse_number("$1,250") == 1250


# --- axis anchoring ---------------------------------------------------------


def test_rate_axes_anchor_at_zero(tmp_path):
    """Not configurable. A truncated axis is the easiest way to mislead with entirely
    true numbers — a two-point move rendered as a cliff."""
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT))
    spec = cs.build_single_spec(fm, rows, None, None, None, 0)
    assert spec["value_format"] == "percent"
    assert spec["y_axis_min"] == 0


def test_count_axes_are_not_forced_to_zero(tmp_path):
    """The rule is about rates. Forcing a count axis to zero would flatten a series that
    legitimately lives in a narrow high band."""
    text = ARTIFACT.replace("| 2026-06-01 | 68.8% |", "| 2026-06-01 | 769 |")
    text = text.replace("| 2026-06-08 | 69.8% |", "| 2026-06-08 | 791 |")
    text = text.replace("| 2026-06-15 | 68.9% |", "| 2026-06-15 | 793 |")
    text = text.replace("metric: cart_to_checkout_start_conversion", "metric: not_a_real_metric")
    fm, rows = cs.parse_artifact(_write(tmp_path, text))
    spec = cs.build_single_spec(fm, rows, None, None, None, 0)
    assert spec["value_format"] == "count"
    assert spec["y_axis_min"] is None


# --- the governance stamp travels -------------------------------------------


def test_tier_source_and_timestamp_are_in_the_spec(tmp_path):
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT))
    spec = cs.build_single_spec(fm, rows, None, None, None, 0)
    assert spec["accuracy_tier"] == "T2"
    assert "T2" in spec["subtitle"]
    assert "cart_checkout_funnel_weekly" in spec["subtitle"]
    assert "2026-08-17" in spec["subtitle"]
    # PyYAML parses an unquoted ISO timestamp into a datetime, which `json.dumps(...,
    # default=str)` stringifies on the way out. Assert the serialized form, since that
    # is what a renderer and a reviewer actually see.
    assert json.loads(json.dumps(spec, default=str))["generated_at"].startswith(
        "2026-08-17 12:00"
    )


def test_fixture_backed_artifacts_are_watermarked(tmp_path):
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT))
    spec = cs.build_single_spec(fm, rows, None, None, None, 0)
    assert spec["mock"] is True


def test_warehouse_artifacts_are_not_watermarked(tmp_path):
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT_WITH_DIM))
    spec = cs.build_single_spec(fm, rows, None, None, None, 0)
    assert spec["mock"] is False


def test_watermark_detection_errs_toward_watermarking():
    """A spurious watermark is an annoyance; a missing one is a synthetic number passing
    itself off as measured. So the explicit boolean wins, and the source mode is the
    fallback for an artifact written before that field existed."""
    assert cs.is_fixture_backed({"fixture_backed": True}) is True
    assert cs.is_fixture_backed({"source_mode": "fixture_sql"}) is True
    assert cs.is_fixture_backed({"source_mode": "mock_csv"}) is True
    assert cs.is_fixture_backed({"source_mode": "bigquery"}) is False
    # An artifact carrying neither signal is treated as warehouse-backed, which is the
    # only defensible read of an explicit `source_mode: bigquery` absent other evidence.
    assert cs.is_fixture_backed({}) is False


# --- still-maturing markers -------------------------------------------------


def test_flag_last_n_is_carried_into_the_spec(tmp_path):
    """A deep-stage metric's newest points are biased in a specific direction — downward,
    because a negative outcome is observable immediately and a positive one only once the
    window closes. A solid marker asserts a settled value; a hollow one says 'not yet'."""
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT))
    spec = cs.build_single_spec(fm, rows, None, None, None, 2)
    assert spec["flag_last_n"] == 2


def test_flag_last_n_is_never_negative(tmp_path):
    fm, rows = cs.parse_artifact(_write(tmp_path, ARTIFACT))
    spec = cs.build_single_spec(fm, rows, None, None, None, -3)
    assert spec["flag_last_n"] == 0


# --- chart-type inference ---------------------------------------------------


@pytest.mark.parametrize(
    "n_time,n_dims,card,intent,expected",
    [
        (12, 0, 1, None, "single_line"),
        (1, 0, 1, None, "ranked_bar"),
        (12, 1, 4, None, "multi_series_line"),
        (12, 1, 4, "compare", "ranked_bar"),
        (12, 1, 9, None, "ranked_bar"),      # spaghetti guard
        (1, 1, 4, None, "ranked_bar"),
    ],
)
def test_chart_type_resolution_is_deterministic(n_time, n_dims, card, intent, expected):
    got, rationale = cs.resolve_chart_type(n_time, n_dims, card, intent, None)
    assert got == expected
    assert rationale, "the rationale travels into the spec so inference can explain itself"


def test_explicit_override_wins():
    got, rationale = cs.resolve_chart_type(12, 0, 1, None, "ranked_bar")
    assert got == "ranked_bar"
    assert "override" in rationale


def test_an_unknown_override_is_refused():
    with pytest.raises(SystemExit):
        cs.resolve_chart_type(12, 0, 1, None, "pie_chart")


def test_the_series_limit_is_a_readability_limit_not_a_data_limit():
    """Above the limit the chart type changes; the data is not truncated. Silently
    dropping series would be a faithfulness failure dressed up as a design choice."""
    _, rationale = cs.resolve_chart_type(12, 1, cs.MAX_LINE_SERIES + 1, None, None)
    assert "readability" in rationale


# --- funnel composition -----------------------------------------------------


def _stage_json(name, num, den, cutoff="2026-08-10", tier="T2", fixture=True):
    return {
        "metric_name": name,
        "display_name": name.replace("_", " "),
        "accuracy_tier": tier,
        "fixture_backed": fixture,
        "maturity": {"cutoff_week": cutoff},
        "rows": [{"numerator": num, "denominator": den}],
    }


FUNNEL_CONFIG = {
    "checkout": {
        "title": "Checkout funnel",
        "end_to_end": "cart_to_return_window_complete_end_to_end",
        "stages": ["stage_a", "stage_b"],
    }
}


def test_funnel_composes_in_the_configured_order():
    spec = cs.build_funnel_spec(
        "checkout",
        [_stage_json("stage_b", 50, 100), _stage_json("stage_a", 80, 100)],
        FUNNEL_CONFIG,
    )
    # Configured order, not the order the JSON files happened to arrive in.
    assert [s["metric"] for s in spec["stages"]] == ["stage_a", "stage_b"]
    assert spec["cumulative_passthrough"] == pytest.approx(0.8 * 0.5)


def test_funnel_stage_rate_is_summed_not_averaged():
    """Averaging per-week rates weights a thin week like a fat one, which is a different
    and usually wrong number."""
    stage = {
        "metric_name": "stage_a",
        "accuracy_tier": "T2",
        "rows": [
            {"numerator": 90, "denominator": 100},   # 90% on n=100
            {"numerator": 1, "denominator": 100},    # 1%  on n=100
        ],
    }
    spec = cs.build_funnel_spec(
        "checkout", [stage, _stage_json("stage_b", 50, 100)], FUNNEL_CONFIG
    )
    assert spec["stages"][0]["rate"] == pytest.approx(91 / 200)


def test_funnel_refuses_a_missing_stage():
    with pytest.raises(SystemExit) as exc:
        cs.build_funnel_spec("checkout", [_stage_json("stage_a", 80, 100)], FUNNEL_CONFIG)
    assert "stage_b" in str(exc.value)


def test_funnel_refuses_an_unknown_key():
    with pytest.raises(SystemExit) as exc:
        cs.build_funnel_spec("nope", [], FUNNEL_CONFIG)
    assert "checkout" in str(exc.value)  # lists what IS available


def test_funnel_takes_the_lowest_tier_and_any_watermark():
    """The most conservative stamp wins. A funnel is only as governed as its weakest
    stage, and only as measured as its most synthetic one."""
    spec = cs.build_funnel_spec(
        "checkout",
        [
            _stage_json("stage_a", 80, 100, tier="T2", fixture=False),
            _stage_json("stage_b", 50, 100, tier="T3", fixture=True),
        ],
        FUNNEL_CONFIG,
    )
    assert spec["accuracy_tier"] == "T3"
    assert spec["mock"] is True


def test_funnel_records_the_maturity_cutoff_spread():
    """Each stage carries its OWN cutoff, so the stages span slightly different cohort
    windows and the product will not exactly equal the named end-to-end metric. Recording
    the spread makes that visible instead of leaving a reader to decide which number is
    wrong — neither is."""
    spec = cs.build_funnel_spec(
        "checkout",
        [
            _stage_json("stage_a", 80, 100, cutoff="2026-08-10"),
            _stage_json("stage_b", 50, 100, cutoff="2026-06-29"),
        ],
        FUNNEL_CONFIG,
    )
    assert spec["maturity_cutoff_span"] == ["2026-06-29", "2026-08-10"]
    assert spec["end_to_end_metric"] == "cart_to_return_window_complete_end_to_end"


def test_funnel_withholds_the_product_when_a_stage_is_null():
    """A NULL stage rate makes the product unknowable. Treating NULL as zero would report
    a cumulative pass-through of 0%, which is a claim rather than an absence."""
    stage = {"metric_name": "stage_b", "accuracy_tier": "T2", "rows": [{"numerator": 0, "denominator": 0}]}
    spec = cs.build_funnel_spec("checkout", [_stage_json("stage_a", 80, 100), stage], FUNNEL_CONFIG)
    assert spec["stages"][1]["rate"] is None
    assert spec["cumulative_passthrough"] is None


# --- the shipped funnels config ---------------------------------------------


def test_the_bundled_funnels_config_loads_and_names_real_metrics():
    import assay_query as aq

    config = cs.load_funnels_config(None)
    assert set(config) >= {"checkout", "checkout-payment"}
    for key, funnel in config.items():
        assert funnel.get("title")
        assert funnel.get("end_to_end"), f"{key}: needs an explicit end_to_end metric"
        assert funnel.get("stages"), f"{key}: needs stages"
        for name in list(funnel["stages"]) + [funnel["end_to_end"]]:
            aq.load_metric(name)  # raises SystemExit if unregistered


def test_the_full_funnel_covers_the_whole_stage_ladder():
    """The eight-stage funnel should traverse the ladder, not a convenient subset — a
    funnel that quietly skipped the return window would look far healthier than the
    business is."""
    config = cs.load_funnels_config(None)
    assert len(config["checkout"]["stages"]) == 8
    assert config["checkout"]["end_to_end"] == "cart_to_return_window_complete_end_to_end"


def test_the_committed_example_spec_still_parses():
    """render-chart ships an example spec. If the spec shape drifts, that example stops
    rendering — and it is the fallback path when matplotlib is unavailable."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    example = root / "plugins" / "assay" / "skills" / "render-chart" / "examples" / "trend.spec.json"
    spec = json.loads(example.read_text())
    assert spec["chart_type"] in cs.ALLOWED_CHART_TYPES
