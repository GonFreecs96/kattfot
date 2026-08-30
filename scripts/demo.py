#!/usr/bin/env python3
"""demo.py — run the whole compose chain offline, end to end.

    query-metric  ->  artifact.md  ->  chart_spec.py  ->  render.py     (SVG + PNG)
                                                      ->  render_html.py (interactive HTML)
                                                      ->  report_pdf.py  (PDF)

No network, no cloud account, no credentials: `KATTFOT_ENGINE` defaults to `sqlite`,
so every metric's real SQL runs against the committed fixture CSVs. This is the
fastest way to see what the plugin actually produces.

For the *gate* — every one of the fifteen skills, run in an environment scrubbed of
credentials, failing if any cannot — use `scripts/demo_skills.py`. This script walks
one chain in depth; that one covers the whole surface in breadth.

**What this script is, and is not.** In normal use the *agent* composes the artifact
markdown — that is Step 8 of `query-metric`'s SKILL.md, and it is a judgment step
(what to lead with, how to summarise a large result, how to narrate a zero-row
answer). This script scripts a deliberately plain version of that step so the chain
is mechanically runnable and testable. It is not a substitute for the skill, and the
narrative it writes is intentionally bland — a script cannot know what the question
was.

Usage
-----
    python3 scripts/demo.py                      # the default single-metric run
    python3 scripts/demo.py --metric payment_approval_rate --dimensions payment_method
    python3 scripts/demo.py --funnel checkout    # compose the eight-stage funnel
    python3 scripts/demo.py --all                # metric run, then funnel run
    python3 scripts/demo.py --skip-pdf           # when fpdf2 isn't installed

Outputs land in `outputs/kattfot/` (override with `KATTFOT_OUTPUTS_DIR`), which is
git-ignored — generated artifacts are reproducible, so committing them would only
create diff noise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS = REPO_ROOT / "plugins" / "kattfot" / "skills"
QUERY_HELPER = SKILLS / "query-metric" / "scripts" / "kattfot_query.py"
CHART_SPEC = SKILLS / "visualize-metric" / "scripts" / "chart_spec.py"
RENDER_MPL = SKILLS / "visualize-metric" / "scripts" / "render.py"
RENDER_HTML = SKILLS / "render-chart" / "scripts" / "render_html.py"
REPORT_PDF = SKILLS / "export-report" / "scripts" / "report_pdf.py"
FUNNELS_CONFIG = REPO_ROOT / "plugins" / "kattfot" / "config" / "funnels.yaml"

#: The fixtures were generated for a fixed observation instant (recorded in
#: data/kattfot/sample/FIXTURES.md). Reading them at the real "today" would keep
#: withholding more of the tail every week until the whole book looked immature, so
#: the demo pins it. A real deployment does not pass --today.
FIXTURE_AS_OF = "2026-08-17"

DEFAULT_METRIC = "cart_to_checkout_start_conversion"


def outputs_dir() -> Path:
    return Path(os.environ.get("KATTFOT_OUTPUTS_DIR") or REPO_ROOT / "outputs" / "kattfot")


def rel(path) -> str:
    """Repo-relative when possible, absolute otherwise.

    `KATTFOT_OUTPUTS_DIR` can legitimately point outside the repo — a scratch directory,
    a shared drive, a pytest tmp_path — and `Path.relative_to` raises rather than
    falling back, so printing a friendly path has to tolerate that.
    """
    try:
        return str(Path(path).relative_to(REPO_ROOT))
    except (ValueError, TypeError):
        return str(path)


def run(cmd: list[str], *, stdin_text: str | None = None) -> str:
    """Run a step and surface its stderr verbatim on failure.

    The helpers use exit codes as their governance interface (2 = no ADC,
    3 = missing deps, 4 = service account refused, 5 = governance refusal), so a
    swallowed stderr would turn a meaningful refusal into a bare traceback.
    """
    proc = subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True, cwd=REPO_ROOT
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(
            f"\nStep failed (exit {proc.returncode}): {' '.join(str(c) for c in cmd)}"
        )
    return proc.stdout


# ---------------------------------------------------------------------------
# Step 1 — the governed query
# ---------------------------------------------------------------------------


def query_metric(metric: str, dimensions: str, time_range: str, filters: list[str]) -> dict:
    cmd = [
        sys.executable, str(QUERY_HELPER),
        "--metric", metric,
        "--dimensions", dimensions,
        "--time-range", time_range,
        "--today", FIXTURE_AS_OF,
        "--output-format", "json",
    ]
    for f in filters:
        cmd += ["--filter", f]
    return json.loads(run(cmd))


# ---------------------------------------------------------------------------
# Step 2 — compose the artifact (the agent's job, scripted plainly)
# ---------------------------------------------------------------------------


def extract_caveats(metric_def_path: Path) -> list[str]:
    """The metric definition's `## Caveats` section, verbatim.

    Verbatim is the contract: a caveat that gets summarised on its way into an
    artifact is a caveat that will eventually get summarised away.
    """
    if not metric_def_path.exists():
        return ["_(metric definition not found — caveats unavailable)_"]
    lines = metric_def_path.read_text().splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            inside = line.strip().lower() == "## caveats"
            continue
        if inside:
            out.append(line)
    return [ln for ln in out] or ["_(no caveats section)_"]


def compose_artifact(result: dict, out_path: Path) -> Path:
    """Write the markdown artifact `query-metric` Step 8 describes."""
    fm = result
    dims = fm["dimensions"]
    value_col = fm["column_label"]
    tier = fm["accuracy_tier"]

    header = ["cohort_week" if not dims else fm["rows"][0] and "cohort_week"]
    time_col = "cohort_week"
    cols = [time_col] + list(dims) + [value_col, "numerator", "denominator"]

    body_rows = []
    for r in fm["rows"]:
        disp = r.get("_display", {})
        cells = [str(r.get(time_col, ""))]
        cells += [str(r.get(d, "")) for d in dims]
        cells += [
            disp.get(fm["metric_name"], "—"),
            disp.get("numerator", "—"),
            disp.get("denominator", "—"),
        ]
        body_rows.append("| " + " | ".join(cells) + " |")

    banner = ""
    if fm.get("fixture_backed"):
        banner = (
            "> ⚠️ **FIXTURE DATA** — these numbers come from committed synthetic\n"
            "> fixtures, not from a warehouse. They describe a seeded random process.\n"
            f"> ({fm['source_mode_reason']})\n\n"
        )

    warnings_md = "\n".join(f"- {w}" for w in fm.get("warnings", [])) or "- _(none)_"
    caveats_md = "\n".join(extract_caveats(REPO_ROOT / fm["metric_definition_path"]))

    mat = fm.get("maturity") or {}
    mat_line = (
        f"stage `{mat.get('stage')}`, lag {mat.get('lag_days')}d, cohorts through "
        f"{mat.get('cutoff_week')} ({mat.get('source')})"
        if mat else "no per-metric maturity gate"
    )
    # The cutoff goes in the frontmatter, not only in the prose: it is an INPUT to the
    # query, so `kattfot:verify` needs it to rebuild the same SQL. Omitted entirely when
    # there is no gate, which is how verify tells the two cases apart.
    mat_fm = f"\nmaturity_cutoff: {mat['cutoff_week']}" if mat.get("cutoff_week") else ""

    slice_note = f", split by {', '.join(dims)}" if dims else ""
    text = f"""---
metric: {fm['metric_name']}
display_name: {fm['display_name']}
accuracy_tier: {tier}
source_view: {fm['source_view']}
source_project: {fm['source_project']}
source_mode: {fm['source_mode']}
engine: {fm['engine']}
fixture_backed: {str(fm['fixture_backed']).lower()}
generated_at: {fm['generated_at']}
time_range_start: {fm['time_range']['start']}
time_range_end: {fm['time_range']['end']}{mat_fm}
dimensions: {dims}
filters: {fm['filters']}
manifest:
  plugin_version: {fm['manifest']['plugin_version']}
  metric_def_sha: {fm['manifest']['metric_def_sha']}
  query_sha: {fm['manifest']['query_sha']}
  result_sha: {fm['manifest']['result_sha']}
---

# {fm['display_name']} — {fm['time_range']['start']} to {fm['time_range']['end']}

**Accuracy tier:** {tier} (operational — decision-grade for product and process
discussions, not regulator-facing)
**Source view:** `{fm['source_project']}.{fm['source_view']}` ({fm['source_mode']})
**Engine:** {fm['engine']} — capabilities: {', '.join(fm['engine_capabilities'])}
**Generated:** {fm['generated_at']}

{banner}**Volume:** {fm['total_numerator_display']} / {fm['total_denominator_display']} over the period ({fm['row_count']} rows).

## Results

| {' | '.join(cols)} |
| {' | '.join(['---'] * len(cols))} |
{chr(10).join(body_rows)}

## Narrative

`{fm['metric_name']}` over {fm['time_range']['start']} to {fm['time_range']['end']}{slice_note},
computed as SUM(numerator) / SUM(denominator) per cohort week. The headline volume is
{fm['total_numerator_display']} of {fm['total_denominator_display']}.

This artifact was composed by `scripts/demo.py`, which scripts a deliberately plain
version of the narrative step so the compose chain is mechanically runnable. In normal
use the agent writes this section against the actual question asked. The numbers are
fixture-backed and describe a seeded random process, not a business.

## Caveats
{caveats_md}

### Run warnings
{warnings_md}

## Provenance

- **Metric definition:** `{fm['metric_definition_path']}`
- **Source mode:** {fm['source_mode']} ({fm['source_mode_reason']})
- **Maturity:** {mat_line}
- **Query (parameterized):**

```sql
{fm['query']}
```

- **Parameters:** `{json.dumps(fm['query_params'])}`
- **Manifest (receipt):** plugin `{fm['manifest']['plugin_version']}` · metric-def `{fm['manifest']['metric_def_sha']}` · query `{fm['manifest']['query_sha']}` · result `{fm['manifest']['result_sha']}` — a faithful re-run reproduces these; check with `kattfot:verify`.
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    return out_path


def inject_visualization(
    artifact: Path, png: Path, spec: Path, chart_type: str, rationale: str,
    renderer: str, flag_last_n: int, fixture_backed: bool,
) -> None:
    """Insert `## Visualization` immediately after `## Results`.

    Nothing else in the artifact is touched. The manifest fingerprints the
    definition, the query, and the result — not the prose — so this does not
    invalidate the receipt; rewriting the Results table would make the chart and the
    receipt disagree, which is why only an insertion happens here.
    """
    text = artifact.read_text()
    rel_png = os.path.relpath(png, artifact.parent)
    rel_spec = os.path.relpath(spec, artifact.parent)

    extra = ""
    if flag_last_n:
        extra += (
            "> Hollow markers mark cohort weeks that are still maturing — see Caveats.\n"
        )
    if fixture_backed:
        extra += (
            "> The MOCK DATA watermark is not decorative: these numbers come from\n"
            "> committed synthetic fixtures.\n"
        )

    section = f"""## Visualization

![chart]({rel_png})

**Chart type:** {chart_type} — {rationale}
**Spec:** `{rel_spec}` (the exact points plotted)
**Renderer:** {renderer}

> Drawn only from the Results table above. No re-query, no smoothing, no
> extrapolation. Rate axes anchor at 0.
{extra}
"""
    marker = "\n## Narrative"
    if marker in text:
        text = text.replace(marker, "\n" + section + marker, 1)
    else:
        text += "\n" + section
    artifact.write_text(text)


# ---------------------------------------------------------------------------
# Steps 3-5 — chart, interactive chart, PDF
# ---------------------------------------------------------------------------


def build_chart(artifact: Path, slug: str, intent: str | None, flag_last_n: int,
                skip_html: bool) -> dict:
    charts = outputs_dir() / "charts"
    spec_path = charts / f"{slug}.spec.json"
    cmd = [
        sys.executable, str(CHART_SPEC),
        "--artifact", str(artifact),
        "--flag-last-n", str(flag_last_n),
        "--out", str(spec_path),
    ]
    if intent:
        cmd += ["--intent", intent]
    spec = json.loads(run(cmd))

    out = {"spec_path": spec_path, "spec": spec}
    render_out = json.loads(
        run([sys.executable, str(RENDER_MPL), "--spec", str(spec_path),
             "--out-base", str(charts / slug)])
    )
    out.update(render_out)

    if not skip_html:
        html = charts / f"{slug}.html"
        run([sys.executable, str(RENDER_HTML), "--spec", str(spec_path),
             "--out", str(html)])
        out["html"] = str(html)
    return out


def build_pdf(artifact: Path, slug: str) -> str:
    pdf_path = outputs_dir() / f"{slug}.pdf"
    result = json.loads(
        run([sys.executable, str(REPORT_PDF), "--artifact", str(artifact),
             "--out", str(pdf_path), "--published-by", "kattfot demo"])
    )
    return result.get("pdf_path", str(pdf_path))


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def metric_run(args) -> dict:
    print(f"[1/5] query-metric: {args.metric}")
    result = query_metric(args.metric, args.dimensions, args.time_range, args.filter)
    print(
        f"      {result['row_count']} rows · {result['source_mode']} · "
        f"volume {result['total_numerator_display']}/"
        f"{result['total_denominator_display']}"
    )

    slug = f"{FIXTURE_AS_OF}-{args.metric.replace('_', '-')}"
    artifact = outputs_dir() / f"{slug}.md"
    print(f"[2/5] compose artifact -> {rel(artifact)}")
    compose_artifact(result, artifact)

    print("[3/5] chart_spec + render (SVG/PNG" + ("" if args.skip_html else " + HTML") + ")")
    chart = build_chart(artifact, slug, args.intent, args.flag_last_n, args.skip_html)
    print(f"      {chart['chart_type']}: {chart['spec']['rationale']}")

    inject_visualization(
        artifact, Path(chart["png"]), chart["spec_path"], chart["chart_type"],
        chart["spec"]["rationale"], chart["renderer"], args.flag_last_n,
        bool(result.get("fixture_backed")),
    )
    print("[4/5] injected ## Visualization into the artifact")

    outputs = {
        "artifact": str(artifact),
        "spec": str(chart["spec_path"]),
        "svg": chart["svg"],
        "png": chart["png"],
    }
    if "html" in chart:
        outputs["html"] = chart["html"]

    if args.skip_pdf:
        print("[5/5] PDF skipped (--skip-pdf)")
    else:
        print("[5/5] export-report -> PDF")
        outputs["pdf"] = build_pdf(artifact, slug)
    return outputs


def funnel_run(args) -> dict:
    import yaml

    config = yaml.safe_load(FUNNELS_CONFIG.read_text()) or {}
    key = args.funnel
    if key not in config:
        raise SystemExit(
            f"ERROR: funnel {key!r} not in {FUNNELS_CONFIG}. "
            f"Available: {sorted(config)}"
        )
    stages = config[key]["stages"]
    print(f"[1/3] query-metric x {len(stages)} stages for funnel '{key}'")

    stage_files = []
    tmpdir = Path(tempfile.mkdtemp(prefix="kattfot-funnel-"))
    for i, metric in enumerate(stages, start=1):
        res = query_metric(metric, "", args.time_range, args.filter)
        p = tmpdir / f"stage{i}.json"
        p.write_text(json.dumps(res))
        stage_files.append(str(p))
        rate = (
            res["total_numerator"] / res["total_denominator"]
            if res["total_denominator"] else None
        )
        shown = f"{rate:.1%}" if rate is not None else "—"
        print(f"      {i}. {metric}: {shown}")

    slug = f"{FIXTURE_AS_OF}-{key}-funnel"
    charts = outputs_dir() / "charts"
    spec_path = charts / f"{slug}.spec.json"
    print("[2/3] compose funnel spec")
    spec = json.loads(
        run([sys.executable, str(CHART_SPEC), "--funnel", key,
             "--stage-json", *stage_files, "--out", str(spec_path)])
    )
    cum = spec.get("cumulative_passthrough")
    print(f"      cumulative pass-through ≈ {cum:.2%}" if cum else "      pass-through: —")

    print("[3/3] render funnel chart")
    render_out = json.loads(
        run([sys.executable, str(RENDER_MPL), "--spec", str(spec_path),
             "--out-base", str(charts / slug)])
    )
    out = {"spec": str(spec_path), "svg": render_out["svg"], "png": render_out["png"]}
    if not args.skip_html:
        html = charts / f"{slug}.html"
        run([sys.executable, str(RENDER_HTML), "--spec", str(spec_path),
             "--out", str(html)])
        out["html"] = str(html)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--metric", default=DEFAULT_METRIC)
    p.add_argument("--dimensions", default="channel")
    p.add_argument("--filter", action="append", default=[])
    p.add_argument("--time-range", default="last_16_weeks")
    p.add_argument("--intent", default=None,
                   choices=["trend", "compare", "breakdown", "share"])
    p.add_argument("--flag-last-n", type=int, default=1,
                   help="render the trailing N cohort weeks as still-maturing")
    p.add_argument("--funnel", default=None, help="also compose this funnel")
    p.add_argument("--all", action="store_true",
                   help="run the metric chain and the default funnel")
    p.add_argument("--skip-pdf", action="store_true")
    p.add_argument("--skip-html", action="store_true")
    args = p.parse_args()

    # Belt and braces: the whole point of the demo is that it works with no cloud
    # account, so pin the engine rather than inheriting a stray KATTFOT_ENGINE.
    os.environ.setdefault("KATTFOT_ENGINE", "sqlite")

    all_outputs: dict = {}
    if args.funnel and not args.all:
        all_outputs["funnel"] = funnel_run(args)
    else:
        all_outputs["metric"] = metric_run(args)
        if args.all:
            print()
            args.funnel = args.funnel or "checkout"
            all_outputs["funnel"] = funnel_run(args)

    print("\nOutputs:")
    for group, files in all_outputs.items():
        for kind, path in files.items():
            print(f"  {group:7s} {kind:9s} {rel(path)}")
    print(
        f"\nGenerated {dt.datetime.now().isoformat(timespec='seconds')}. "
        f"All numbers are fixture-backed and watermarked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
