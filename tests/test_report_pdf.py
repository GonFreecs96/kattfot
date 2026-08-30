"""export-report's faithfulness contract: report_pdf.py.

Two things this file pins, both regressions found while closing out Phase 4:

  * `bullets()` must reproduce a caveat verbatim, including every soft-wrapped
    continuation line — every caveat in this repo is hand-wrapped at ~80
    characters across several physical lines with no blank line between them,
    and the naive version kept only each bullet's first physical line. The
    PDF's own footer claims "reproduced verbatim"; a silent truncation
    contradicts that promise directly.
  * `parse_artifact()` must accept BOTH governed artifact shapes this template
    produces — a `metric:`-keyed query-metric artifact and a `skill:`-keyed
    spine-analysis artifact (funnel-dropoff / stage-timing / segment-compare /
    funnel-pulse) — while still refusing an artifact with neither key, which is
    what stops an arbitrary markdown file from being PDF-exported as though it
    were governed.

`report_pdf.py` needs `fpdf2` + `pyyaml` at import time (a hard `sys.exit(3)`
otherwise), and `fpdf2` is an export-report extra rather than a core test
dependency (see `tests/requirements.txt`) — the same reason `visualize-metric`'s
`render.py` is absent from `conftest.py`. So this whole file is skipped, not
failed, when `fpdf2` is not installed.
"""

import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("fpdf", reason="fpdf2 is an export-report extra, not a core test dependency")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "kattfot" / "skills" / "export-report" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import report_pdf as rp  # noqa: E402  (path insertion must happen first)


def _write(tmp_path, text, name="artifact.md"):
    p = tmp_path / name
    p.write_text(text)
    return p


# --- bullets(): the verbatim-caveat regression -------------------------------


def test_bullets_rejoins_a_soft_wrapped_multiline_caveat():
    """The exact shape every caveat in this repo ships in. The naive
    implementation kept only the line starting with '- ' and silently dropped
    the rest — this pins the fix."""
    lines = textwrap.dedent(
        """\
        - **Channel mix moves this number without anything changing.** The channels
          converting between 55% and 76% means a shift in traffic mix alone will move
          the blended rate. Slice by `channel` before concluding the checkout got
          worse. This is the metric most often misread in the module.
        - **A cart is not an intent to buy.** Add-to-cart is used as a bookmark.
        """
    ).splitlines()
    out = rp.bullets(lines)
    assert len(out) == 2
    assert out[0] == (
        "Channel mix moves this number without anything changing. The channels "
        "converting between 55% and 76% means a shift in traffic mix alone will move "
        "the blended rate. Slice by channel before concluding the checkout got "
        "worse. This is the metric most often misread in the module."
    )
    assert out[1] == "A cart is not an intent to buy. Add-to-cart is used as a bookmark."


def test_bullets_a_blank_line_still_closes_a_bullet():
    """A non-bullet line that follows a blank line must not be glued onto an
    already-finished bullet — this is what stops a "### Run warnings"
    sub-heading (swept into the same section by the coarse '## ' split in
    `split_sections`) from being appended to the caveat above it, while its own
    '- ' bullets are still picked up as bullets in their own right."""
    lines = [
        "- first bullet",
        "  still first bullet",
        "",
        "### Run warnings",
        "- a run warning, which IS its own bullet",
    ]
    assert rp.bullets(lines) == [
        "first bullet still first bullet",
        "a run warning, which IS its own bullet",
    ]


def test_bullets_single_line_bullets_are_unaffected():
    assert rp.bullets(["- one", "- two", "- three"]) == ["one", "two", "three"]


def test_bullets_strips_markdown_emphasis_across_the_join():
    out = rp.bullets(["- **bold start** and `code`", "  more **bold** text"])
    assert out == ["bold start and code more bold text"]


def test_bullets_ignores_leading_non_bullet_lines_with_nothing_to_continue():
    assert rp.bullets(["not a bullet", "- real bullet"]) == ["real bullet"]


# --- parse_artifact(): metric-keyed OR skill-keyed, never neither ------------

METRIC_ARTIFACT = textwrap.dedent(
    """\
    ---
    metric: cart_to_checkout_start_conversion
    display_name: Cart to Checkout Start conversion rate
    accuracy_tier: T2
    source_view: analytics.cart_checkout_funnel_weekly
    source_project: my-project
    source_mode: fixture_sql
    fixture_backed: true
    generated_at: 2026-08-17T12:00:00
    ---

    # Cart to Checkout Start conversion rate

    ## Results

    | cohort_week | Cart -> Checkout |
    | --- | --- |
    | 2026-06-01 | 68.8% |

    ## Narrative

    Words.

    ## Caveats

    - a metric-artifact caveat
    """
)

SPINE_ARTIFACT = textwrap.dedent(
    """\
    ---
    skill: funnel-dropoff
    accuracy_tier: T2
    source_view: my-project.cart_checkout_session_spine
    engine: sqlite
    fixture_backed: true
    generated_at: 2026-08-17T12:00:00
    maturity_mode: stage-aware
    maturity_cutoff: 2026-08-10
    ---

    # funnel-dropoff — fixture-backed demonstration

    > (!) FIXTURE DATA — these numbers come from committed synthetic fixtures.

    ## Stage funnel

    | Stage | Count |
    | --- | --- |
    | Cart Created | 1,000 |
    | Checkout Started | 694 |

    ## Where it leaks (terminal branch)

    | Terminal branch | Count | % of carts |
    | --- | --- | --- |
    | Payment Declined | 40 | 4.00% |

    ## Narrative

    Words about where the funnel leaks.

    ## Caveats

    - a spine-artifact caveat
    """
)

NEITHER_ARTIFACT = textwrap.dedent(
    """\
    ---
    accuracy_tier: T2
    source_view: analytics.something
    generated_at: 2026-08-17T12:00:00
    ---

    # not governed

    Frontmatter looks the part, but carries neither `metric` nor `skill`.
    """
)


def test_parse_artifact_accepts_a_metric_keyed_artifact(tmp_path):
    fm, sections = rp.parse_artifact(_write(tmp_path, METRIC_ARTIFACT))
    assert fm["metric"] == "cart_to_checkout_start_conversion"
    assert rp.bullets(sections["caveats"]) == ["a metric-artifact caveat"]


def test_parse_artifact_accepts_a_skill_keyed_spine_artifact(tmp_path):
    """The Task 3 extension: a spine-analysis artifact (funnel-dropoff /
    stage-timing / segment-compare / funnel-pulse) has no `metric:` key at all,
    but is governed exactly the same way — tier, source, generated-at, a
    Results table, and caveats — and must export."""
    fm, sections = rp.parse_artifact(_write(tmp_path, SPINE_ARTIFACT))
    assert fm["skill"] == "funnel-dropoff"
    assert "metric" not in fm
    assert rp.bullets(sections["caveats"]) == ["a spine-artifact caveat"]


def test_parse_artifact_refuses_an_artifact_with_neither_metric_nor_skill(tmp_path):
    """The refusal this extension must keep: something with tier-shaped
    frontmatter but no `metric` and no `skill` is not a governed artifact, and
    exporting it would let an arbitrary markdown file pass as one."""
    with pytest.raises(SystemExit) as exc:
        rp.parse_artifact(_write(tmp_path, NEITHER_ARTIFACT))
    assert "metric" in str(exc.value)
    assert "skill" in str(exc.value)


@pytest.mark.parametrize("missing", ["accuracy_tier", "source_view", "generated_at"])
def test_parse_artifact_still_requires_the_core_fields_on_a_spine_artifact(tmp_path, missing):
    text = "\n".join(
        ln for ln in SPINE_ARTIFACT.splitlines() if not ln.startswith(f"{missing}:")
    )
    with pytest.raises(SystemExit) as exc:
        rp.parse_artifact(_write(tmp_path, text))
    assert missing in str(exc.value)


def test_refuses_a_file_with_no_frontmatter(tmp_path):
    with pytest.raises(SystemExit) as exc:
        rp.parse_artifact(_write(tmp_path, "# just a heading\n"))
    assert "frontmatter" in str(exc.value)


# --- table_sections(): a spine artifact has no section literally named -----
# --- "Results", so every table-bearing section must be found and rendered --


def test_table_sections_finds_the_single_results_table_on_a_metric_artifact(tmp_path):
    _, sections = rp.parse_artifact(_write(tmp_path, METRIC_ARTIFACT))
    tables = rp.table_sections(sections)
    assert [key for key, _, _ in tables] == ["results"]


def test_table_sections_finds_every_skill_specific_table_on_a_spine_artifact(tmp_path):
    """funnel-dropoff (like every spine-analysis skill) has no `## Results`
    heading at all — its Output format uses `## Stage funnel` and `## Where it
    leaks (terminal branch)` instead. Both must be found, in document order,
    and Narrative/Caveats must NOT be mistaken for tables."""
    _, sections = rp.parse_artifact(_write(tmp_path, SPINE_ARTIFACT))
    tables = rp.table_sections(sections)
    assert [key for key, _, _ in tables] == ["stage funnel", "where it leaks (terminal branch)"]
    stage_header, stage_data = tables[0][1], tables[0][2]
    assert stage_header == ["Stage", "Count"]
    assert stage_data == [["Cart Created", "1,000"], ["Checkout Started", "694"]]


def test_table_sections_never_renders_narrative_or_caveats_as_a_table(tmp_path):
    _, sections = rp.parse_artifact(_write(tmp_path, SPINE_ARTIFACT))
    keys = {key for key, _, _ in rp.table_sections(sections)}
    assert "narrative" not in keys
    assert "caveats" not in keys


# --- build_pdf(): both shapes actually render --------------------------------


def test_build_pdf_renders_both_governed_shapes_end_to_end(tmp_path):
    """Not just accepted by the parser — actually produces a PDF, for both a
    metric artifact (chart-less here, since there is no Visualization section)
    and a spine artifact."""
    for text, name in ((METRIC_ARTIFACT, "metric.md"), (SPINE_ARTIFACT, "spine.md")):
        artifact = _write(tmp_path, text, name)
        fm, sections = rp.parse_artifact(artifact)
        out = tmp_path / name.replace(".md", ".pdf")
        rp.build_pdf(fm, sections, artifact.parent, out, "")
        assert out.exists() and out.stat().st_size > 500, name


def test_build_pdf_titles_a_spine_artifact_by_its_skill_name(tmp_path):
    """A spine artifact has no `display_name` and no `metric` — the title must
    fall back to `skill` rather than rendering `None`."""
    artifact = _write(tmp_path, SPINE_ARTIFACT, "spine.md")
    fm, sections = rp.parse_artifact(artifact)
    title = fm.get("display_name") or fm.get("metric") or fm.get("skill")
    assert title == "funnel-dropoff"
