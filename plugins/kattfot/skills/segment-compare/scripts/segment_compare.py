#!/usr/bin/env python3
"""segment_compare.py — diff two segments of a spine, as A | B | delta.

Compares two segments across the full funnel, the terminal-branch leak mix, and
stage timing. Two modes:

  filter-compare  Segment A = `--a-filter ...`, Segment B = `--b-filter ...`,
                  both optionally narrowed by a shared `--filter`, over one
                  window. e.g. within the web channel, card vs bank transfer.

  pre/post        One segment (`--filter ...`) split at `--split-date` into
                  pre (`cohort_week < date`) and post (`cohort_week >= date`).
                  e.g. did the checkout change move the funnel?

Both segments are resolved against **one** `today`, so every stage and transition
is gated to the same mature window on both sides and the deltas stay comparable.
That is the whole reason this is a skill rather than two runs of
`funnel-dropoff`: two separate invocations minutes apart can straddle a Monday
and silently compare different cohort windows.

What it deliberately does not do
--------------------------------
No significance test, no confidence interval, no causal claim. A pre/post delta
over a shipped change is a description of two windows, and treating it as a
measured effect ignores seasonality, mix, and everything else that moved in
between. The registry marks `release-scorecard` — the skill that would make a
causal claim — as excluded for exactly this reason.

Governance via `_shared/spine.py`: allow-listed filters, bound values, per-stage
and per-transition maturity cutoffs, human-user ADC on the warehouse engine.

Output: JSON on stdout for the SKILL.md flow to render into a markdown artifact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _shared import spine  # noqa: E402


def stage_sql(spec: dict, where: str, cutoffs: dict, params: dict, *, engine=None) -> str:
    """Per-stage gated counts plus the matching first-stage base, in one scan.

    `base0_{k}` is re-counted per stage on purpose: each stage's cumulative rate
    has to be measured against the starts over the SAME mature window, or a deep
    stage's smaller cohort is divided by a fresher, larger denominator and reads
    artificially low. `deep_started` is the single settled cohort the branch mix
    uses.
    """
    view = spine.fq_view(spec, engine=engine)
    cc = spec["cohort_column"]
    first_flag = spec["stages"][0]["flag"]
    cols = []
    for s in spec["stages"]:
        k = s["key"]
        g = spine.gate(cc, k, cutoffs)
        cols.append(f"COUNTIF({s['flag']}{g}) AS n_{k}")
        cols.append(f"COUNTIF({first_flag}{g}) AS base0_{k}")
    cols.append(f"COUNTIF({first_flag}{spine.deep_gate(cc, params)}) AS deep_started")
    return "SELECT\n  " + ",\n  ".join(cols) + f"\nFROM `{view}`\n{where}"


def branch_sql(spec: dict, where: str, params: dict, *, engine=None) -> str:
    view = spine.fq_view(spec, engine=engine)
    bwhere = where + spine.deep_gate(spec["cohort_column"], params)
    return (
        f"SELECT\n  terminal_branch,\n  COUNT(*) AS n\n"
        f"FROM `{view}`\n{bwhere}\nGROUP BY terminal_branch\nORDER BY terminal_branch"
    )


def run_segment(
    spec, label, filters, start, end, *, mode, include_legacy, transitions, pcts,
    cutoffs, cut_params, meta, engine,
) -> dict:
    """One side of the comparison: funnel, branch mix, and timing."""
    where, params = spine.build_where(
        spec, filters, start, end, maturity_mode=mode, include_legacy=include_legacy
    )
    params.update(cut_params)
    project = spec["source_project"]

    rows = spine.run_query(
        project, stage_sql(spec, where, cutoffs, params, engine=engine), params, engine=engine
    )
    row = rows[0] if rows else {}
    brows = spine.run_query(
        project, branch_sql(spec, where, params, engine=engine), params, engine=engine
    )

    def gi(name):
        return int(row.get(name, 0) or 0)

    per_stage = meta.get("per_stage", {})
    started = gi(f"n_{spec['stages'][0]['key']}")
    deep_started = gi("deep_started")

    stages = []
    for s in spec["stages"]:
        k = s["key"]
        cnt, base0 = gi(f"n_{k}"), gi(f"base0_{k}")
        stages.append({
            "key": k,
            "label": s["label"],
            "count": cnt,
            "cumulative": (cnt / base0) if base0 else None,
            "start_base": base0,
            "mature_through": per_stage.get(k, {}).get("cutoff_week"),
        })

    branch_counts = {r["terminal_branch"]: int(r["n"]) for r in brows}
    branches = {
        b: {"count": n, "pct": (n / deep_started) if deep_started else None}
        for b, n in branch_counts.items()
    }

    adapter = spine.active_adapter(engine)
    timing = []
    basis = None
    for t in transitions:
        prows = adapter.percentiles(
            project,
            from_sql=f"`{spine.fq_view(spec, engine=engine)}`",
            where_sql=where,
            value_expr=spine.gated_diff(spec, t, cutoffs),
            percentiles=pcts,
            params=spine.percentile_params(params, t, cutoffs),
        )
        prow = prows[0] if prows else {}
        basis = basis or prow.get("_percentile_basis")
        timing.append({
            "transition": t["key"],
            "label": t["label"],
            "unit": t.get("unit"),
            "n": int(prow.get("n") or 0),
            "mean": prow.get("mean"),
            "percentiles": {f"p{p}": prow.get(f"p{p}") for p in pcts},
            "mature_through": per_stage.get(t["key"], {}).get("cutoff_week"),
        })

    return {
        "label": label,
        "filters": filters,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "started": started,
        "deep_started": deep_started,
        "branch_total": sum(branch_counts.values()),
        "branch_coherent": sum(branch_counts.values()) == deep_started,
        "stages": stages,
        "branches": branches,
        "timing": timing,
        "percentile_basis": basis,
        "sql": {
            "stages": stage_sql(spec, where, cutoffs, params, engine=engine),
            "branches": branch_sql(spec, where, params, engine=engine),
        },
    }


def _delta(b, a):
    return (b - a) if (a is not None and b is not None) else None


def build_comparison(spec: dict, A: dict, B: dict, pcts: list) -> dict:
    """A | B | delta across the three views, branch shifts biggest-first."""
    stages = [
        {
            "key": sa["key"],
            "label": sa["label"],
            "a_cumulative": sa["cumulative"],
            "b_cumulative": sb["cumulative"],
            "delta_pp": (
                (_delta(sb["cumulative"], sa["cumulative"]) * 100)
                if _delta(sb["cumulative"], sa["cumulative"]) is not None else None
            ),
            "mature_through": sa["mature_through"],
        }
        for sa, sb in zip(A["stages"], B["stages"])
    ]

    all_branches = list(spec.get("terminal_branches") or [])
    for b in list(A["branches"]) + list(B["branches"]):
        if b not in all_branches:
            all_branches.append(b)
    branches = []
    for b in all_branches:
        ap = A["branches"].get(b, {}).get("pct")
        bp = B["branches"].get(b, {}).get("pct")
        if not ap and not bp:
            continue
        d = _delta(bp, ap)
        branches.append({
            "terminal_branch": b,
            "a_pct": ap,
            "b_pct": bp,
            "delta_pp": (d * 100) if d is not None else None,
        })
    branches.sort(key=lambda x: abs(x["delta_pp"]) if x["delta_pp"] is not None else 0, reverse=True)

    headline = pcts[0]
    timing = [
        {
            "transition": ta["transition"],
            "label": ta["label"],
            "unit": ta["unit"],
            "a_n": ta["n"],
            "b_n": tb["n"],
            f"a_p{headline}": ta["percentiles"].get(f"p{headline}"),
            f"b_p{headline}": tb["percentiles"].get(f"p{headline}"),
            "delta": _delta(
                tb["percentiles"].get(f"p{headline}"), ta["percentiles"].get(f"p{headline}")
            ),
        }
        for ta, tb in zip(A["timing"], B["timing"])
    ]
    return {
        "headline_percentile": headline,
        "stages": stages,
        "branches": branches,
        "timing": timing,
    }


def compact_label(filters: dict, fallback: str) -> str:
    return ", ".join(f"{k}={v}" for k, v in filters.items()) if filters else fallback


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    spine.add_common_args(ap)
    ap.add_argument("--a-filter", action="append", default=[],
                    help="Segment A filter col=value (filter-compare mode)")
    ap.add_argument("--b-filter", action="append", default=[],
                    help="Segment B filter col=value (filter-compare mode)")
    ap.add_argument("--a-label", default=None)
    ap.add_argument("--b-label", default=None)
    ap.add_argument("--split-date", default=None,
                    help="YYYY-MM-DD — pre/post mode, split on cohort_week")
    ap.add_argument("--transitions", default=None,
                    help="comma-separated transition keys (default: all)")
    ap.add_argument("--percentiles", default="50",
                    help="comma-separated 0-100; the first is the headline delta (default 50)")
    args = ap.parse_args()

    spec = spine.load_spec(module=args.module, spine=args.spine)
    shared = spine.filters_from_args(args.filter)
    a_only = spine.filters_from_args(args.a_filter)
    b_only = spine.filters_from_args(args.b_filter)
    for f in (shared, a_only, b_only):
        spine.validate_filters(spec, f)

    is_prepost = bool(args.split_date)
    is_filtercmp = bool(a_only or b_only)
    if is_prepost and is_filtercmp:
        spine.refuse(
            "Choose ONE mode: --split-date (pre/post) OR --a-filter/--b-filter "
            "(filter-compare). Doing both would compare a filter change and a time "
            "change at once, and the result could not be attributed to either."
        )
    if not is_prepost and not is_filtercmp:
        spine.refuse(
            "Need either --split-date (pre/post) or --a-filter/--b-filter "
            "(filter-compare); there is no default second segment."
        )

    keys = [k.strip() for k in args.transitions.split(",")] if args.transitions else None
    transitions = spine.select_transitions(spec, keys)
    pcts = spine.parse_percentiles(args.percentiles)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    start, end = spine.parse_time_range(spec, args.time_range, today)
    maturity_mode = spine.resolve_maturity_mode(args, spec)

    # One `today` for both segments, and one merged cutoff set: stage keys and
    # transition keys do not collide, so the two resolutions can share a
    # namespace and every gate is identical on both sides.
    s_cut, s_par, s_meta = spine.resolve_stage_cutoffs(spec, spec["stages"], today, mode=maturity_mode)
    t_cut, t_par, t_meta = spine.resolve_stage_cutoffs(spec, transitions, today, mode=maturity_mode)
    cutoffs = {**s_cut, **t_cut}
    cut_params = {**s_par, **t_par}
    maturity = s_meta
    maturity["per_stage"].update(t_meta["per_stage"])
    if t_par.get("cut_deep") and s_par.get("cut_deep"):
        deepest = min(s_par["cut_deep"], t_par["cut_deep"])
        cut_params["cut_deep"] = deepest
        maturity["deep_cohort_through"] = deepest.isoformat()

    seg = dict(
        mode=maturity_mode, include_legacy=args.include_legacy, transitions=transitions,
        pcts=pcts, cutoffs=cutoffs, cut_params=cut_params, meta=maturity, engine=args.engine,
    )

    if is_prepost:
        split = dt.date.fromisoformat(args.split_date)
        if not (start < split <= end):
            spine.refuse(
                f"--split-date {split} must fall inside the window {start}..{end}, "
                f"or one side of the comparison is empty."
            )
        mode = "pre_post"
        A = run_segment(spec, args.a_label or f"pre {split}", dict(shared),
                        start, split - dt.timedelta(days=1), **seg)
        B = run_segment(spec, args.b_label or f"post {split}", dict(shared), split, end, **seg)
    else:
        mode = "filter_compare"
        A = run_segment(spec, args.a_label or compact_label(a_only, "Segment A"),
                        {**shared, **a_only}, start, end, **seg)
        B = run_segment(spec, args.b_label or compact_label(b_only, "Segment B"),
                        {**shared, **b_only}, start, end, **seg)

    warnings = spine.fixture_warning(args.engine)
    warnings += spine.maturity_warning(spec, maturity)
    warnings.append(
        "DESCRIPTIVE ONLY: these deltas are not significance-tested and carry no "
        "confidence interval. Read a small delta on a small n as noise, and cite "
        "both sides' n."
    )
    if mode == "pre_post":
        warnings.append(
            "PRE/POST MATURITY: a deep stage in a recent post window may have little "
            "or no settled cohort behind it, which shows up as a low n rather than a "
            "low rate. That is correct withholding, not a regression — lean on the "
            "stages that mature in days for a recent change, and read the deep ones "
            "once the post cohort settles."
        )
    for seg_out in (A, B):
        if not seg_out["branch_coherent"]:
            warnings.append(
                f"BRANCH INCOHERENCE ({seg_out['label']}): branch counts sum to "
                f"{seg_out['branch_total']} against {seg_out['deep_started']} carts in "
                f"the same settled window — the leak mix for this side does not "
                f"decompose its funnel."
            )
        if not seg_out["started"]:
            warnings.append(
                f"EMPTY SEGMENT ({seg_out['label']}): zero carts matched. Filters bind "
                f"as exact values, so check {seg_out['filters']} for a typo or an "
                f"unseen category before reading any delta."
            )
    warnings += spine.guardrail_warnings(
        spec, start, end,
        columns=list(shared) + list(a_only) + list(b_only),
    )

    out = {
        "skill": "segment-compare",
        "mode": mode,
        "module": args.module or spine.DEFAULT_MODULE,
        "spine": args.spine,
        "source_view": spec["source_view"],
        "source_project": spec["source_project"],
        "accuracy_tier": spec["accuracy_tier"],
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
        "split_date": args.split_date,
        "maturity_mode": maturity_mode,
        "mature_only": maturity_mode != "off",
        "maturity": maturity,
        "maturity_cutoff": maturity.get("deep_cohort_through"),
        "legacy": spine.legacy_status(spec, args.include_legacy),
        "percentiles": pcts,
        "percentile_basis": A.get("percentile_basis") or B.get("percentile_basis"),
        "segment_a": A,
        "segment_b": B,
        "comparison": build_comparison(spec, A, B, pcts),
        "warnings": warnings,
    }
    out.update(spine.fixture_provenance(args.engine))
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
