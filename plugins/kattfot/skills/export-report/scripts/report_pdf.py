#!/usr/bin/env python3
"""report_pdf.py — render a governed artifact into a PDF.

Produces a single, self-contained, stakeholder-ready PDF from a governed
output artifact: the chart (inline, when present), the Results table, the
Narrative, the verbatim Caveats, and the governance stamp (accuracy tier,
source view, generated-at, and a MOCK DATA / FIXTURE DATA banner when
applicable). The PDF is written locally for the user to download and
distribute as-is — no upload, no publishing pipeline (the `export-report`
skill).

Accepts two artifact shapes, both governed: a `query-metric` /
`visualize-metric` artifact (keyed by `metric` in frontmatter, usually
carrying a chart, its table under `## Results`) and a spine-analysis artifact
from `funnel-dropoff`, `stage-timing`, `segment-compare`, or `funnel-pulse`
(keyed by `skill` instead, chart-less, its table(s) under a skill-specific
heading such as `## Stage funnel` or `## Latency by transition`). Every
table-bearing section is rendered in the artifact's own order — see
`table_sections()` — so both shapes export uniformly with no per-skill
heading map to maintain. An artifact with neither `metric` nor `skill` is
refused — that is what stops an arbitrary markdown file from being
PDF-exported as though it were governed.

Faithful by construction: every element is read from the artifact the
governed run already wrote. This helper adds no numbers and re-queries
nothing. The chart image, when present, is the one `visualize-metric`
rendered.

Usage:
  report_pdf.py --artifact outputs/kattfot/2026-06-01-foo.md \
                [--out outputs/kattfot/2026-06-01-foo.pdf] \
                [--published-by "Your Name"]

Prints JSON {"pdf_path": ..., "title": ...} on success.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
    from fpdf import FPDF
except ImportError:
    sys.stderr.write(
        "Missing dependencies. Install with:\n"
        "  pip install -r scripts/requirements.txt\n"
    )
    sys.exit(3)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)
IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# Page geometry (A4 portrait, mm).
MARGIN = 18
INK = (20, 20, 20)
MUTED = (90, 90, 90)
RULE = (210, 210, 210)
WARN_BG = (255, 244, 229)
WARN_INK = (140, 60, 0)


# ---------------------------------------------------------------------------
# Glyph sanitation — DejaVu covers →·≈≤—…✓ but not color emoji.
# ---------------------------------------------------------------------------

# The PDF uses fpdf2 core fonts (Helvetica) — zero embedded font bytes, so the
# whole report stays a few KB and uploads as inline base64 with room to spare.
# Core fonts are Latin-1 only, so transliterate the glyphs the governed
# artifacts actually use down to ASCII/Latin-1.
_TRANSLIT = {
    "⚠️": "(!)", "⚠": "(!)", "📊": "", "📋": "", "🔗": "", "✅": "[ok]",
    "→": "->", "←": "<-", "↗": "", "≈": "~", "≤": "<=", "≥": ">=",
    "—": "-", "–": "-", "·": "-", "•": "-", "…": "...", "×": "x",
    "’": "'", "‘": "'", "“": '"', "”": '"', "→": "->",
}
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # [text](url) -> text


def clean(text: str) -> str:
    text = MD_LINK_RE.sub(r"\1", text)
    for k, v in _TRANSLIT.items():
        text = text.replace(k, v)
    # Guarantee the core font can encode every remaining char.
    text = text.encode("latin-1", "ignore").decode("latin-1")
    return text.strip()


# ---------------------------------------------------------------------------
# Artifact parsing
# ---------------------------------------------------------------------------


def parse_artifact(path: Path) -> tuple[dict, dict]:
    """Parse a governed artifact's frontmatter + body.

    Two shapes are accepted, both governed and both produced by this template's
    compose chain: a metric artifact (`query-metric` / `visualize-metric`,
    keyed by `metric`) and a spine-analysis artifact (`funnel-dropoff`,
    `stage-timing`, `segment-compare`, `funnel-pulse`, keyed by `skill`). Both
    carry `accuracy_tier`, `source_view`, and `generated_at`, and both produce
    one or more results table(s), caveats, and a maturity/tier stamp — the
    shared shape this exporter actually depends on, even though the two kinds
    name their table sections differently (see `table_sections()`). An
    artifact with NEITHER `metric` nor `skill` is refused: that check is what
    stops an arbitrary markdown file from being PDF-exported as if it were
    governed.
    """
    text = path.read_text()
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit(f"ERROR: {path} has no frontmatter — not a governed artifact.")
    fm = yaml.safe_load(m.group(1)) or {}
    if "metric" not in fm and "skill" not in fm:
        raise SystemExit(
            "ERROR: artifact missing 'metric' or 'skill' — only governed artifacts."
        )
    for req in ("accuracy_tier", "source_view", "generated_at"):
        if req not in fm:
            raise SystemExit(f"ERROR: artifact missing '{req}' — only governed artifacts.")
    return fm, split_sections(m.group(2))


def split_sections(body: str) -> dict:
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            sections[current] = buf
            current = line[3:].strip().lower()
            buf = []
        else:
            buf.append(line)
    sections[current] = buf
    return sections


def parse_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    rows = [ln.strip() for ln in lines if ln.strip().startswith("|")]
    if len(rows) < 2:
        return [], []
    def split(r): return [c.strip() for c in r.strip().strip("|").split("|")]
    header = split(rows[0])
    data = [split(r) for r in rows[2:]]  # rows[1] is the |---| separator
    return header, [r for r in data if len(r) == len(header)]


#: Sections that are prose (or otherwise not a results table) even though a
#: section-by-section scan would otherwise consider them eligible.
NON_TABLE_SECTIONS = {
    "_preamble", "narrative", "caveats", "provenance", "visualization",
    "run warnings", "about this artifact",
}


def table_sections(sections: dict) -> list[tuple[str, list[str], list[list[str]]]]:
    """Every section, in the artifact's own document order, that contains a
    Markdown table.

    A query-metric artifact has exactly one: `## Results`. A spine-analysis
    artifact (funnel-dropoff, stage-timing, segment-compare, funnel-pulse) has
    no section named "Results" at all — each has its own skill-specific
    heading instead (`## Stage funnel` + `## Where it leaks (terminal
    branch)`, `## Latency by transition`, `## Funnel (cumulative from
    start)` + `## Timing (p{N})`, …), per that skill's own documented Output
    format. Scanning every section for a table, rather than looking up one
    hardcoded name, is what lets both shapes render their tables uniformly
    without a maintained per-skill heading map.
    """
    out = []
    for key, lines in sections.items():
        if key in NON_TABLE_SECTIONS:
            continue
        header, data = parse_table(lines)
        if header:
            out.append((key, header, data))
    return out


def paragraphs(lines: list[str]) -> list[str]:
    out, buf = [], []
    for ln in lines:
        if ln.strip():
            buf.append(ln.strip())
        elif buf:
            out.append(" ".join(buf)); buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def bullets(lines: list[str]) -> list[str]:
    """Bullet items from a Markdown list, rejoining soft-wrapped continuation lines.

    Every caveat in this repo is hand-wrapped at ~80 characters across several
    physical lines with no blank line in between, e.g.:

        - **Channel mix moves this number without anything changing.** The channels
          converting between 55% and 76% means a shift in traffic mix alone will move
          the blended rate.
        - **A cart is not an intent to buy.** ...

    A line that is non-blank and does not start with "- " is a continuation of the
    bullet directly above it, and gets joined onto it with a single space — the
    naive version that kept only lines starting with "- " silently truncated every
    multi-line bullet to its first physical line, which is exactly the kind of
    silent loss this artifact's own footer ("reproduced verbatim") promises does
    not happen.

    A blank line still closes the current bullet: a later non-bullet line (for
    example a "### " sub-heading swept into this section by the coarse "## " split
    in `split_sections`) is not glued onto an unrelated, already-finished bullet.
    """
    out: list[str] = []
    continuing = False
    for ln in lines:
        s = ln.strip()
        if not s:
            continuing = False
            continue
        if s.startswith("- "):
            # strip markdown bold/inline-code markers for clean PDF text
            out.append(s[2:].replace("**", "").replace("`", ""))
            continuing = True
        elif continuing and out:
            out[-1] = out[-1] + " " + s.replace("**", "").replace("`", "")
    return out


def find_chart(sections: dict, artifact_dir: Path) -> tuple[Path | None, str]:
    """The chart image from the Visualization section, plus a one-line caption.

    The caption is the first prose line after the image, with markdown emphasis
    stripped — NOT just the first `**bold**` run. `visualize-metric` writes that line
    as `**Chart type:** {type} — {rationale}`, so pulling only the bold span would
    yield the literal caption "Chart type:" and drop the part that says anything.
    Blockquote lines are skipped: those are the faithfulness notes, which belong in
    the artifact but read as noise under a figure.
    """
    lines = sections.get("visualization", [])
    img_path, caption = None, ""
    for ln in lines:
        mi = IMG_RE.search(ln)
        if mi and img_path is None:
            cand = (artifact_dir / mi.group(1)).resolve()
            img_path = cand if cand.exists() else None
            continue
        s = ln.strip()
        if caption or not s or s.startswith(">") or s.startswith("!["):
            continue
        caption = BOLD_RE.sub(r"\1", s).replace("`", "")
    return img_path, caption


# ---------------------------------------------------------------------------
# PDF construction
# ---------------------------------------------------------------------------


def _quantized(img_path: Path) -> str:
    """Write an optimized palette PNG to a temp file and return its path.

    Line/bar charts use few colors, so an adaptive 64-colour palette is visually
    lossless while cutting the file ~8x (e.g. 63KB → ~3KB). Embedding the
    optimized file (not a PIL object, which fpdf2 re-expands) keeps the PDF
    small enough to upload as inline base64.
    """
    try:
        from PIL import Image
    except ImportError:
        return str(img_path)
    im = Image.open(img_path).convert("RGB")
    # Downscale to a sensible print width before quantizing — 900px is sharp at
    # the ~180mm page width and far smaller than the 1350px source.
    max_w = 900
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    im = im.convert("P", palette=Image.ADAPTIVE, colors=32)
    import tempfile

    tmp = Path(tempfile.gettempdir()) / (img_path.stem + ".q.png")
    im.save(tmp, format="PNG", optimize=True)
    return str(tmp)


def build_pdf(fm: dict, sections: dict, artifact_dir: Path, out: Path, published_by: str) -> None:
    title = fm.get("display_name") or fm.get("metric") or fm.get("skill")
    tier = fm.get("accuracy_tier", "")
    src = f"{fm.get('source_project','')}.{fm.get('source_view','')}".strip(".")
    generated = str(fm.get("generated_at", ""))
    # Fixture-backed numbers get the banner. The explicit boolean wins; the source
    # mode is the fallback so an artifact written before that field existed still
    # warns. Erring toward showing the banner is the right direction — a spurious
    # one is an annoyance, a missing one is a synthetic number in a stakeholder's
    # PDF with nothing saying so.
    mock = bool(
        fm.get("fixture_backed")
        or fm.get("source_mode") in ("mock_csv", "fixture_sql")
    )

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=MARGIN)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.add_page()
    width = pdf.epw  # effective page width

    # Title + stamp
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 17)
    pdf.multi_cell(width, 8, clean(title))
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    stamp = f"Accuracy tier {tier}   ·   {src}   ·   generated {generated}"
    if published_by:
        stamp += f"   ·   shared by {published_by}"
    pdf.multi_cell(width, 5, clean(stamp))
    pdf.ln(2)

    if mock:
        pdf.set_fill_color(*WARN_BG)
        pdf.set_text_color(*WARN_INK)
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(
            width, 7,
            clean("(!) FIXTURE DATA — these numbers come from committed synthetic "
                  "fixtures, not from a warehouse. They describe a seeded random "
                  "process and are not measurements."),
            fill=True, border=1,
        )
        pdf.ln(2)

    # Chart (inline, full content width). Quantize the PNG first — it shrinks
    # the embedded image ~8x (e.g. 63KB → ~3KB) with no visible loss, keeping
    # the whole PDF small enough to upload as inline base64.
    img_path, caption = find_chart(sections, artifact_dir)
    if img_path:
        pdf.image(_quantized(img_path), w=width)
        if caption:
            pdf.ln(1)
            pdf.set_font("Helvetica", "I", 9.5)
            pdf.set_text_color(*INK)
            pdf.multi_cell(width, 5, clean(caption))
        pdf.ln(3)

    # Results table(s) — "Results" for a metric artifact; one or more
    # skill-specific tables for a spine-analysis artifact (see table_sections()).
    for key, header, data in table_sections(sections):
        _section_heading(pdf, key.capitalize(), width)
        _render_table(pdf, header, data, width)
        pdf.ln(2)

    # Narrative
    paras = paragraphs(sections.get("narrative", []))
    if paras:
        _section_heading(pdf, "Narrative", width)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*INK)
        for p in paras:
            pdf.multi_cell(width, 5.4, clean(p.replace("**", "")))
            pdf.ln(1.5)

    # Caveats (verbatim)
    cavs = bullets(sections.get("caveats", []))
    if cavs:
        _section_heading(pdf, "Caveats", width)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*INK)
        for c in cavs:
            pdf.set_x(MARGIN)
            pdf.multi_cell(width, 5.0, clean("•  " + c))
            pdf.ln(0.8)

    # Footer note
    pdf.ln(3)
    pdf.set_draw_color(*RULE)
    pdf.line(MARGIN, pdf.get_y(), MARGIN + width, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*MUTED)
    if "metric" in fm:
        origin = (
            f"metric `{fm.get('metric')}` · tier {tier}. Generated by query-metric, "
            f"charted by visualize-metric."
        )
    else:
        origin = f"skill `{fm.get('skill')}` · tier {tier}. Generated by kattfot:{fm.get('skill')}."
    pdf.multi_cell(
        width, 4.5,
        clean(f"Governed artifact from kattfot · {origin} "
              f"Numbers and caveats are reproduced verbatim from the source run."),
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))


def _section_heading(pdf: FPDF, text: str, width: float) -> None:
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*INK)
    pdf.multi_cell(width, 6, text)
    pdf.set_draw_color(*RULE)
    pdf.line(MARGIN, pdf.get_y(), MARGIN + width, pdf.get_y())
    pdf.ln(2)


def _render_table(pdf: FPDF, header: list[str], data: list[list[str]], width: float) -> None:
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*INK)
    with pdf.table(
        width=width,
        text_align="LEFT",
        headings_style=__import__("fpdf").fonts.FontFace(emphasis="BOLD", fill_color=(238, 238, 238)),
        line_height=5.5,
    ) as table:
        hrow = table.row()
        for h in header:
            hrow.cell(clean(h))
        for r in data:
            row = table.row()
            for c in r:
                row.cell(clean(c))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifact", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--published-by", default="")
    args = p.parse_args()

    artifact = Path(args.artifact)
    fm, sections = parse_artifact(artifact)
    out = Path(args.out) if args.out else artifact.with_suffix(".pdf")
    build_pdf(fm, sections, artifact.parent, out, args.published_by)
    try:
        rel = str(out.relative_to(Path.cwd()))
    except ValueError:
        rel = str(out)
    title = fm.get("display_name") or fm.get("metric") or fm.get("skill")
    print(json.dumps({"pdf_path": rel, "title": title}))


if __name__ == "__main__":
    main()
