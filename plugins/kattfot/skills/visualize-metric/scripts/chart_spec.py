#!/usr/bin/env python3
"""chart_spec.py — build a reviewable chart spec from a governed artifact.

Reads a `query-metric` artifact (frontmatter + the Results table), computes the
result *shape* deterministically, resolves a chart type through an
(intent × shape) rule table, and emits a **chart spec JSON**. The spec embeds the
points it was built from plus a governance stamp — accuracy tier, source view,
fixture flag, generated-at — so a renderer can produce a faithful image and a
reviewer can audit exactly what was plotted.

This helper never queries anything and never re-fetches. It renders only what the
artifact already contains, which is the same posture as the governed
`query-metric` run it sits downstream of. That is not a limitation to work around:
a visualization that could reach past its artifact to "fill in" a point would break
the one property that makes the artifact worth keeping.

Two modes:

  Single metric (default):
    chart_spec.py --artifact outputs/kattfot/2026-08-17-foo.md \
                  [--intent trend|compare|breakdown|share] \
                  [--chart-type single_line|multi_series_line|ranked_bar] \
                  [--baseline 0.35] [--flag-last-n 1]

  Funnel (compose ordered stage metrics into one chart):
    chart_spec.py --funnel checkout \
                  --stage-json stage1.json stage2.json ... \
                  [--funnels-config path/to/funnels.yaml]

  where each --stage-json is the JSON `kattfot_query.py --output-format json` emitted
  for one stage metric.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "Missing dependencies. Install with:\n"
        "  pip install -r scripts/requirements.txt\n"
    )
    sys.exit(3)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)

ALLOWED_CHART_TYPES = {"single_line", "multi_series_line", "ranked_bar", "funnel"}

# Above this many distinct series a multi-series line becomes spaghetti; fall back
# to a ranked bar of the most recent period. A chart nobody can read is not a more
# faithful chart for having included everything.
MAX_LINE_SERIES = 6

#: Source modes whose numbers come from committed fixtures. Kept in step with
#: kattfot_query.FIXTURE_SOURCE_MODES; duplicated rather than imported so this
#: helper stays runnable against any conforming artifact, including one produced
#: by a consumer's own tooling.
FIXTURE_SOURCE_MODES = frozenset({"fixture_sql", "mock_csv"})


def is_fixture_backed(fm: dict) -> bool:
    """Whether this artifact's numbers are synthetic, and must be watermarked.

    Checks the explicit boolean first and falls back to the source mode, so an
    artifact written before `fixture_backed` existed still watermarks correctly.
    Erring toward watermarking is the right direction for this check: a spurious
    watermark is an annoyance, a missing one is a number passing itself off as
    measured.
    """
    if fm.get("fixture_backed") is not None:
        return bool(fm["fixture_backed"])
    return fm.get("source_mode") in FIXTURE_SOURCE_MODES


# ---------------------------------------------------------------------------
# Artifact parsing
# ---------------------------------------------------------------------------


def parse_artifact(path: Path) -> tuple[dict, list[dict]]:
    """Return (frontmatter, results_table_rows) from a query-metric artifact.

    Each row is `{column_header: raw_cell_string}` — the *rendered* cells, not the
    raw query output, because the rendered table is what a reader saw. A chart
    built from the raw JSON could legitimately disagree with the table beside it.
    """
    text = path.read_text()
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit(
            f"ERROR: {path} has no YAML frontmatter — not a query-metric artifact."
        )
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)

    # Confirm governed provenance before visualizing anything. An artifact without
    # a tier and a source has no business becoming a chart somebody screenshots.
    for required in ("metric", "accuracy_tier", "source_view", "generated_at"):
        if required not in fm:
            raise SystemExit(
                f"ERROR: artifact frontmatter missing '{required}'. "
                f"Only governed query-metric artifacts can be visualized."
            )

    return fm, _parse_results_table(body, path)


def _parse_results_table(body: str, path: Path) -> list[dict]:
    """Pull the markdown table under the '## Results' header."""
    table_lines: list[str] = []
    in_results = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_results = stripped.lower().startswith("## results")
            continue
        if in_results and stripped.startswith("|"):
            table_lines.append(stripped)

    if len(table_lines) < 3:
        raise SystemExit(
            f"ERROR: no Results table found in {path}. Expected a markdown table "
            f"under a '## Results' header."
        )

    header = _split_row(table_lines[0])
    # table_lines[1] is the |---|---| separator.
    rows = []
    for raw in table_lines[2:]:
        cells = _split_row(raw)
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    if not rows:
        raise SystemExit(f"ERROR: Results table in {path} has no data rows.")
    return rows


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


# ---------------------------------------------------------------------------
# Cell value parsing
# ---------------------------------------------------------------------------

_NULL_CELLS = {"—", "-", "", "n/a", "none", "null"}


def parse_number(cell: str):
    """Rendered cell -> float/int, or None for NULL.

    `'28.54%'` -> 0.2854, `'6,877'` -> 6877, `'—'` -> None. NULL stays None rather
    than becoming 0: an absent rate and a zero rate are different findings, and
    plotting the first as the second invents a data point.
    """
    c = cell.strip()
    if c.lower() in _NULL_CELLS:
        return None
    if c.endswith("%"):
        return float(c[:-1].replace(",", "").strip()) / 100.0
    c = c.replace(",", "").replace("$", "").strip()
    try:
        return float(c) if "." in c else int(c)
    except ValueError:
        return None


def detect_value_format(cell: str) -> str:
    c = cell.strip()
    if c.endswith("%"):
        return "percent"
    if c.startswith("$"):
        return "currency"
    return "count"


# ---------------------------------------------------------------------------
# Metric-definition lookup (best-effort, for value_format + baseline)
# ---------------------------------------------------------------------------


def _metric_dirs() -> list[Path]:
    """Consumer dir first, then the plugin-bundled query-metric catalog."""
    dirs = []
    consumer = os.environ.get("KATTFOT_METRICS_DIR")
    dirs.append(Path(consumer) if consumer else Path.cwd() / "context" / "kattfot" / "metrics")
    # scripts -> visualize-metric -> skills
    dirs.append(Path(__file__).resolve().parents[1].parent / "query-metric" / "metrics")
    return dirs


def load_metric_definition(metric_name: str) -> dict | None:
    """Best-effort lookup of the definition, for `value_format` and a baseline.

    None when not found — the caller then infers the format from the rendered
    cells, so a chart still works without the definition. Deliberately soft: a
    consumer may visualize an artifact whose metric they no longer have on disk,
    and refusing would make the artifact less portable than the number in it.
    """
    for root in _metric_dirs():
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.md")):
            m = FRONTMATTER_RE.match(p.read_text())
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            if fm.get("name") == metric_name:
                return fm
    return None


# ---------------------------------------------------------------------------
# Shape + chart-type resolution
# ---------------------------------------------------------------------------


def resolve_chart_type(
    n_time_points: int,
    n_dims: int,
    series_cardinality: int,
    intent: str | None,
    override: str | None,
) -> tuple[str, str]:
    """Return (chart_type, rationale). Deterministic given the inputs.

    The rationale travels into the spec so a reviewer can see *why* this chart type
    and not another — inference that cannot explain itself is indistinguishable
    from a guess.
    """
    if override:
        if override not in ALLOWED_CHART_TYPES:
            raise SystemExit(
                f"ERROR: --chart-type '{override}' not allowed. "
                f"Choose from {sorted(ALLOWED_CHART_TYPES - {'funnel'})}."
            )
        return override, f"explicit --chart-type override ({override})."

    if intent == "compare" and n_dims >= 1:
        return (
            "ranked_bar",
            "intent 'compare' with a categorical breakdown → ranked bar of the "
            "most recent period.",
        )

    if n_dims == 0:
        if n_time_points > 1:
            return "single_line", (
                f"0 dimensions over {n_time_points} time points → single trend line."
            )
        return "ranked_bar", "single value, no breakdown → single labelled bar."

    if n_time_points > 1:
        if series_cardinality <= MAX_LINE_SERIES:
            return "multi_series_line", (
                f"{series_cardinality} series over {n_time_points} time points "
                f"(≤{MAX_LINE_SERIES}) → multi-series trend line."
            )
        return "ranked_bar", (
            f"{series_cardinality} series exceeds the {MAX_LINE_SERIES}-line "
            f"readability limit → ranked bar of the most recent period."
        )
    return "ranked_bar", (
        f"{series_cardinality} categories at a single time point → ranked bar."
    )


# ---------------------------------------------------------------------------
# Single-metric spec
# ---------------------------------------------------------------------------


def build_single_spec(
    fm: dict,
    rows: list[dict],
    intent: str | None,
    override: str | None,
    baseline: float | None,
    flag_last_n: int,
) -> dict:
    metric_name = fm["metric"]
    dimensions = fm.get("dimensions") or []
    if isinstance(dimensions, str):
        dimensions = [d.strip() for d in dimensions.strip("[]").split(",") if d.strip()]

    header = list(rows[0].keys())
    time_col = header[0]
    n_dims = len(dimensions)
    value_col = header[1 + n_dims]  # template order: time, *dims, value, num, den
    num_col = header[2 + n_dims] if len(header) > 2 + n_dims else None
    den_col = header[3 + n_dims] if len(header) > 3 + n_dims else None

    # value_format: the authoritative definition first, else inferred from cells.
    metric_def = load_metric_definition(metric_name)
    value_format = (metric_def or {}).get("value_format") or detect_value_format(
        rows[0][value_col]
    )
    if baseline is None and metric_def:
        baseline = metric_def.get("steady_state_baseline")

    time_values = []
    for r in rows:
        if r[time_col] not in time_values:
            time_values.append(r[time_col])
    n_time_points = len(time_values)

    def series_key(r):
        return " · ".join(r[d] for d in dimensions) if dimensions else "all"

    series_names = []
    for r in rows:
        k = series_key(r)
        if k not in series_names:
            series_names.append(k)
    series_cardinality = len(series_names)

    chart_type, rationale = resolve_chart_type(
        n_time_points, n_dims, series_cardinality, intent, override
    )

    spec = {
        "chart_type": chart_type,
        "metric": metric_name,
        "title": _title(fm),
        "subtitle": _subtitle(fm),
        "x_label": time_col,
        "y_label": fm.get("display_name", metric_name),
        "value_format": value_format,
        # Rate axes anchor at 0, always. A truncated axis turns a two-point move
        # into a cliff, which is the single easiest way to mislead with a chart
        # that contains only true numbers.
        "y_axis_min": 0 if value_format == "percent" else None,
        "baseline": baseline,
        "mock": is_fixture_backed(fm),
        "accuracy_tier": fm.get("accuracy_tier"),
        "rationale": rationale,
        "flag_last_n": max(0, int(flag_last_n)),
        "source_rows": len(rows),
        "generated_at": fm.get("generated_at"),
    }

    if chart_type in ("single_line", "multi_series_line"):
        spec["series"] = _build_series(
            rows, time_col, value_col, num_col, den_col, dimensions, series_key
        )
    else:
        spec["bars"] = _build_bars(
            rows, time_col, value_col, num_col, den_col, dimensions, series_key,
            time_values,
        )
        spec["as_of"] = time_values[-1]

    return spec


def _build_series(rows, time_col, value_col, num_col, den_col, dimensions, series_key):
    by_series: dict[str, list] = {}
    for r in rows:
        by_series.setdefault(series_key(r), []).append(
            {
                "x": r[time_col],
                "y": parse_number(r[value_col]),
                "num": parse_number(r[num_col]) if num_col else None,
                "den": parse_number(r[den_col]) if den_col else None,
            }
        )
    series = []
    for name, pts in by_series.items():
        pts.sort(key=lambda p: p["x"])
        series.append({"name": name, "points": pts})
    return series


def _build_bars(
    rows, time_col, value_col, num_col, den_col, dimensions, series_key, time_values
):
    latest = time_values[-1]
    bars = []
    for r in rows:
        if r[time_col] != latest:
            continue
        bars.append(
            {
                "label": series_key(r) if dimensions else r[time_col],
                "y": parse_number(r[value_col]),
                "num": parse_number(r[num_col]) if num_col else None,
                "den": parse_number(r[den_col]) if den_col else None,
            }
        )
    bars.sort(key=lambda b: (b["y"] is not None, b["y"]), reverse=True)
    return bars


def _title(fm: dict) -> str:
    return fm.get("display_name", fm.get("metric", "Metric"))


def _subtitle(fm: dict) -> str:
    parts = [
        fm.get("accuracy_tier", ""),
        f"{fm.get('source_project', '')}.{fm.get('source_view', '')}".strip("."),
        f"generated {fm.get('generated_at', '')}",
    ]
    return " · ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Funnel spec
# ---------------------------------------------------------------------------


def load_funnels_config(explicit: str | None) -> dict:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("KATTFOT_FUNNELS_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "config" / "funnels.yaml")
    # scripts -> visualize-metric -> skills -> <plugin root>
    candidates.append(
        Path(__file__).resolve().parents[2].parent / "config" / "funnels.yaml"
    )
    for c in candidates:
        if c.exists():
            return yaml.safe_load(c.read_text()) or {}
    raise SystemExit(
        "ERROR: no funnels.yaml found. Looked in config/funnels.yaml, "
        "$KATTFOT_FUNNELS_CONFIG, and the plugin-bundled default."
    )


def _aggregate_stage(helper_json: dict) -> dict:
    """Collapse a stage's rows to one overall rate = sum(num)/sum(den).

    Summed, not averaged: averaging per-week rates weights a thin week the same as
    a fat one, which is a different (and usually wrong) number.
    """
    num = 0
    den = 0
    for row in helper_json.get("rows", []):
        n = row.get("numerator")
        d = row.get("denominator")
        if n is not None:
            num += n
        if d is not None:
            den += d
    return {
        "metric": helper_json.get("metric_name"),
        "label": helper_json.get("display_name", helper_json.get("metric_name")),
        "rate": (num / den) if den else None,
        "num": num,
        "den": den,
        "mock": is_fixture_backed(helper_json),
        "accuracy_tier": helper_json.get("accuracy_tier"),
        "maturity_cutoff": (helper_json.get("maturity") or {}).get("cutoff_week"),
    }


# Lowest tier wins when stages disagree — the most conservative stamp.
_TIER_ORDER = {"T3": 0, "T1": 1, "T2": 2}


def build_funnel_spec(funnel_key: str, stage_jsons: list[dict], config: dict) -> dict:
    if funnel_key not in config:
        raise SystemExit(
            f"ERROR: funnel '{funnel_key}' not in config. "
            f"Available: {sorted(config.keys())}"
        )
    funnel = config[funnel_key]
    ordered = funnel.get("stages", [])

    by_metric = {}
    for hj in stage_jsons:
        agg = _aggregate_stage(hj)
        by_metric[agg["metric"]] = agg

    stages = []
    missing = []
    for metric_name in ordered:
        if metric_name not in by_metric:
            missing.append(metric_name)
            continue
        stages.append(by_metric[metric_name])
    if missing:
        raise SystemExit(
            f"ERROR: funnel '{funnel_key}' is missing stage JSON for: {missing}. "
            f"Run query-metric for each stage and pass every --stage-json."
        )

    # Cumulative pass-through = the product of the stage rates.
    cumulative = 1.0
    have_all = True
    for s in stages:
        if s["rate"] is None:
            have_all = False
            break
        cumulative *= s["rate"]

    tiers = [s.get("accuracy_tier") for s in stages if s.get("accuracy_tier")]
    lowest_tier = min(tiers, key=lambda t: _TIER_ORDER.get(t, 9)) if tiers else None
    any_mock = any(s["mock"] for s in stages)

    # Each stage carries its OWN maturity cutoff, so the stages are computed over
    # slightly different cohort windows and the product will not exactly equal the
    # named end-to-end metric. Recording the span makes that visible instead of
    # leaving a reader to wonder which number is wrong. Neither is.
    cutoffs = sorted({s["maturity_cutoff"] for s in stages if s.get("maturity_cutoff")})

    return {
        "chart_type": "funnel",
        "title": funnel.get("title", funnel_key),
        "subtitle": " · ".join(
            p for p in [lowest_tier, f"{len(stages)} stages"] if p
        ),
        "value_format": "percent",
        "mock": any_mock,
        "accuracy_tier": lowest_tier,
        "stages": stages,
        "cumulative_passthrough": cumulative if have_all else None,
        "end_to_end_metric": funnel.get("end_to_end"),
        "maturity_cutoff_span": cutoffs,
        "rationale": (
            f"funnel '{funnel_key}': {len(stages)} ordered stage metrics composed "
            f"into one pass-through chart."
        ),
        "source_rows": sum(len(hj.get("rows", [])) for hj in stage_jsons),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--artifact", help="path to a query-metric output artifact (.md)")
    p.add_argument(
        "--intent",
        choices=["trend", "compare", "breakdown", "share"],
        default=None,
        help="analytical intent extracted from the user's prompt",
    )
    p.add_argument(
        "--chart-type",
        default=None,
        help="explicit override (single_line|multi_series_line|ranked_bar)",
    )
    p.add_argument(
        "--baseline",
        type=float,
        default=None,
        help="optional steady-state reference line (a fraction, e.g. 0.35)",
    )
    p.add_argument(
        "--flag-last-n",
        type=int,
        default=0,
        help="flag the trailing N time points as still-maturing (hollow markers)",
    )
    p.add_argument("--funnel", default=None, help="funnel key from funnels.yaml")
    p.add_argument(
        "--stage-json",
        nargs="+",
        default=[],
        help="per-stage kattfot_query.py JSON files (funnel mode)",
    )
    p.add_argument("--funnels-config", default=None, help="path to funnels.yaml")
    p.add_argument("--out", default=None, help="write the spec here as well as stdout")
    args = p.parse_args()

    if args.funnel:
        if not args.stage_json:
            raise SystemExit("ERROR: --funnel requires one or more --stage-json files.")
        config = load_funnels_config(args.funnels_config)
        stage_jsons = [json.loads(Path(f).read_text()) for f in args.stage_json]
        spec = build_funnel_spec(args.funnel, stage_jsons, config)
    else:
        if not args.artifact:
            raise SystemExit("ERROR: provide --artifact or --funnel.")
        fm, rows = parse_artifact(Path(args.artifact))
        spec = build_single_spec(
            fm, rows, args.intent, args.chart_type, args.baseline, args.flag_last_n
        )

    text = json.dumps(spec, indent=2, default=str)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
