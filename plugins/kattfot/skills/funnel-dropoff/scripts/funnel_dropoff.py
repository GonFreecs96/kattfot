#!/usr/bin/env python3
"""funnel_dropoff.py — spine-backed funnel + terminal-branch drop-off attribution.

Queries a module's session-grain spine (resolved per-module/per-spine through
`config/modules.yaml`; the `checkout` module's is
`analytics.cart_checkout_session_spine`) to produce, for one segment:

  1. the **ordered stage funnel** — counts, stage-to-stage conversion, and
     cumulative pass-through from the first stage; and
  2. the **terminal-branch decomposition** — of all carts created, the share that
     ENDED in each mutually-exclusive branch.

The second is the reason this skill exists. A governed `query-metric` is a ratio
of two pre-aggregated columns, which can say that 38% of carts that started
checkout never submitted a payment but cannot say where those carts ended up.
Drop-off attribution needs one row per instance, so it needs the spine.

Coherence is checked rather than assumed
----------------------------------------
The branches are mutually exclusive and exhaustive by construction, so under a
single cohort cutoff they must sum to exactly the carts created in that window.
The output carries that check (`branch_coherence`) and the skill says so out
loud, because the assertion is cheap and it is the thing that catches the two
bugs this shape actually has: a branch query gated to a different cohort window
than the stage query, and a deepest-first `CASE` in the view that leaves a row
unclassified.

Governance via `_shared/spine.py`: allow-listed group-by and filters, values
bound as parameters, per-stage maturity cutoffs, and — on the warehouse engine —
the query running as the individual human user via ADC.

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


def stage_columns(spec: dict, cutoffs: dict, params: dict) -> list:
    """Per-stage count columns, each gated to the cohorts mature FOR THAT STAGE.

    For every stage, over that stage's own mature window:

        n_{k}     — reached the stage (the numerator)
        base0_{k} — reached the FIRST stage (the cumulative-from-start denominator)
        basep_{k} — reached the PREVIOUS stage (the stage-to-stage denominator)

    plus `deep_{k}`, the same count over the single deepest fully-settled cohort,
    which is what the branch denominators and the funnel chart use. The three
    per-stage columns share one window each, so every ratio built from them is
    population-consistent — that is the whole reason `base0_`/`basep_` are
    re-counted per stage instead of being read once from the first stage.

    In flag/off mode the gates are empty strings and all four collapse to the
    same unwindowed count.
    """
    cc = spec["cohort_column"]
    first_flag = spec["stages"][0]["flag"]
    cols: list = []
    prev_flag = None
    for s in spec["stages"]:
        k, flag = s["key"], s["flag"]
        g = spine.gate(cc, k, cutoffs)
        cols.append(f"COUNTIF({flag}{g}) AS n_{k}")
        cols.append(f"COUNTIF({first_flag}{g}) AS base0_{k}")
        if prev_flag is not None:
            cols.append(f"COUNTIF({prev_flag}{g}) AS basep_{k}")
        cols.append(f"COUNTIF({flag}{spine.deep_gate(cc, params)}) AS deep_{k}")
        prev_flag = flag
    return cols


def build_stage_sql(spec, dim, where, cutoffs, params, *, engine=None) -> str:
    view = spine.fq_view(spec, engine=engine)
    counts = ",\n  ".join(stage_columns(spec, cutoffs, params))
    if dim:
        dexpr = spine.col_expr(spec, dim)
        return (
            f"SELECT\n  {dexpr} AS grp,\n  {counts}\n"
            f"FROM `{view}`\n{where}\nGROUP BY grp\nORDER BY grp"
        )
    return f"SELECT\n  {counts}\nFROM `{view}`\n{where}"


def build_branch_sql(spec, dim, where, params, *, engine=None) -> str:
    """Branch mix over the single deepest fully-settled cohort.

    Not per-stage, deliberately. `terminal_branch` is a settled-state property of
    a whole cart — "where did this one end up" — so mixing cohort windows across
    branches would produce shares that do not sum to anything.
    """
    view = spine.fq_view(spec, engine=engine)
    bwhere = where + spine.deep_gate(spec["cohort_column"], params)
    if dim and dim != "terminal_branch":
        dexpr = spine.col_expr(spec, dim)
        return (
            f"SELECT\n  {dexpr} AS grp,\n  terminal_branch,\n  COUNT(*) AS n\n"
            f"FROM `{view}`\n{bwhere}\nGROUP BY grp, terminal_branch\n"
            f"ORDER BY grp, terminal_branch"
        )
    return (
        f"SELECT\n  terminal_branch,\n  COUNT(*) AS n\n"
        f"FROM `{view}`\n{bwhere}\nGROUP BY terminal_branch\nORDER BY terminal_branch"
    )


def order_branches(spec: dict, counts: dict) -> list:
    """Branches in the spec's curated deepest-first order, with anything the
    spine emitted that the spec does not list appended under `(other)`.

    Appended rather than dropped: an unlisted branch means the view and the spec
    have diverged, and silently discarding it would break the coherence check
    below — which is exactly the signal worth keeping.
    """
    ordered = spec.get("terminal_branches") or []
    out = [(b, counts.get(b, 0)) for b in ordered]
    seen = set(ordered)
    for b, n in counts.items():
        if b not in seen:
            out.append((f"{b} (other)", n))
    return out


def check_branch_coherence(branches: list, deep_started: int) -> dict:
    """Do the branch counts sum to the carts created in the same window?

    They must: every row on the spine lands in exactly one branch, and both
    queries are gated to the same deepest cohort. A mismatch is not a rounding
    artifact, it is one of two real bugs — the two queries disagreeing about the
    window, or the view's `CASE` failing to classify some rows — so this is
    reported as a hard field rather than a warning to skim past.
    """
    total = sum(n for _, n in branches)
    return {
        "branch_total": total,
        "carts_in_deep_window": deep_started,
        "coherent": total == deep_started,
        "difference": total - deep_started,
        "basis": (
            "terminal_branch is mutually exclusive and exhaustive, and both queries "
            "are gated to the same deepest settled cohort, so these must be equal"
        ),
    }


def assemble_group(spec: dict, stage_row: dict, branch_counts: dict, meta: dict) -> dict:
    """Compose one group's funnel + branch mix from the two query rows.

    Each stage's rates are population-consistent *within its own mature window*:
    `cumulative` is n/base0 and `stage_conversion` is n/basep, both counted over
    the same window. Across rows the windows differ by design, which is why the
    chart is built from `deep_*` instead.
    """
    def gi(name: str) -> int:
        return int(stage_row.get(name, 0) or 0)

    per_stage = meta.get("per_stage", {})
    # The first stage's OWN key, never a hardcoded literal. `stage_columns()`
    # emits `n_{key}` for whatever the ladder calls its first stage (`created`
    # here), so a hardcoded name would read back 0 for every group on any spine
    # that named it differently — a silent zero, not an error.
    first_key = spec["stages"][0]["key"]
    started = gi(f"n_{first_key}")
    deep_started = gi(f"deep_{first_key}")

    stages = []
    prev_key = None
    for s in spec["stages"]:
        k = s["key"]
        n, base0, deep = gi(f"n_{k}"), gi(f"base0_{k}"), gi(f"deep_{k}")
        basep = gi(f"basep_{k}") if prev_key is not None else None
        psm = per_stage.get(k, {})
        stages.append({
            "key": k,
            "label": s["label"],
            "count": n,
            "stage_conversion": (n / basep) if basep else None,
            "cumulative": (n / base0) if base0 else None,
            "start_base": base0,
            "maturity_stage": psm.get("maturity_stage", s.get("maturity_stage", k)),
            "lag_days": psm.get("lag_days"),
            "mature_through": psm.get("cutoff_week"),
            "deep_count": deep,
            "deep_cumulative": (deep / deep_started) if deep_started else None,
        })
        prev_key = k

    ordered = order_branches(spec, branch_counts)
    branches = [
        {
            "terminal_branch": b,
            "count": n,
            "pct_of_started": (n / deep_started) if deep_started else None,
        }
        for b, n in ordered
        if n
    ]
    return {
        "started": started,
        "deep_started": deep_started,
        "stages": stages,
        "branches": branches,
        "branch_coherence": check_branch_coherence(ordered, deep_started),
    }


def chart_spec_for(spec: dict, group: dict, title: str, meta: dict, *, mock: bool) -> dict:
    """A render-chart funnel spec for one group's stage funnel.

    Built over the SINGLE deepest settled cohort (`deep_*`) so it narrows
    monotonically from 100% and reads as one coherent funnel — unlike the
    artifact table, whose rows each use their own fresher window. Two different
    honest views of the same ladder; conflating them is what makes a funnel chart
    go back up in the middle.
    """
    deep_started = group["deep_started"]
    stages = [
        {
            "metric": st["key"],
            "label": st["label"],
            "rate": round(st["deep_cumulative"], 4) if st["deep_cumulative"] is not None else None,
            "num": st["deep_count"],
            "den": deep_started,
        }
        for st in group["stages"]
    ]
    final = group["stages"][-1]["deep_count"]
    through = meta.get("deep_cohort_through")
    subtitle = f"{spec['accuracy_tier']} · {len(spec['stages'])} stages"
    if through:
        subtitle += f" · single settled cohort through {through}"
    return {
        "chart_type": "funnel",
        "title": title,
        "subtitle": subtitle,
        "value_format": "percent",
        "mock": mock,
        "accuracy_tier": spec["accuracy_tier"],
        "rationale": (
            "funnel-dropoff: spine stage counts composed into a pass-through funnel "
            "over a single settled cohort."
        ),
        "generated_at": dt.date.today().isoformat(),
        "cumulative_passthrough": round(final / deep_started, 4) if deep_started else None,
        "stages": stages,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    spine.add_common_args(
        ap, group_by_help="single allow-listed dimension to split the funnel by"
    )
    args = ap.parse_args()

    spec = spine.load_spec(module=args.module, spine=args.spine)
    if not spec.get("stages"):
        spine.refuse(
            f"the spec sourced from {spec.get('source_view', '?')} declares no "
            f"`stages:`, so it has no funnel to decompose."
        )
    dim = args.group_by
    spine.validate_dimension(spec, dim)
    filters = spine.filters_from_args(args.filter)
    spine.validate_filters(spec, filters)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    start, end = spine.parse_time_range(spec, args.time_range, today)
    mode = spine.resolve_maturity_mode(args, spec)

    cutoffs, cut_params, maturity = spine.resolve_stage_cutoffs(
        spec, spec["stages"], today, mode=mode
    )
    where, params = spine.build_where(
        spec, filters, start, end, maturity_mode=mode, include_legacy=args.include_legacy
    )
    params.update(cut_params)

    stage_sql = build_stage_sql(spec, dim, where, cutoffs, params, engine=args.engine)
    branch_sql = build_branch_sql(spec, dim, where, params, engine=args.engine)
    project = spec["source_project"]
    stage_rows = spine.run_query(project, stage_sql, params, engine=args.engine)
    branch_rows = spine.run_query(project, branch_sql, params, engine=args.engine)

    groups = []
    if dim:
        branch_by_grp: dict = {}
        for r in branch_rows:
            branch_by_grp.setdefault(str(r.get("grp")), {})[r["terminal_branch"]] = int(r["n"])
        for sr in stage_rows:
            g = assemble_group(spec, sr, branch_by_grp.get(str(sr.get("grp")), {}), maturity)
            g["group"] = {dim: sr.get("grp")}
            groups.append(g)
    else:
        sr = stage_rows[0] if stage_rows else {}
        counts = {r["terminal_branch"]: int(r["n"]) for r in branch_rows}
        g = assemble_group(spec, sr, counts, maturity)
        g["group"] = None
        groups.append(g)

    warnings = spine.fixture_warning(args.engine)
    warnings += spine.maturity_warning(spec, maturity)
    incoherent = [g for g in groups if not g["branch_coherence"]["coherent"]]
    for g in incoherent:
        c = g["branch_coherence"]
        warnings.append(
            f"BRANCH INCOHERENCE ({g['group'] or 'all'}): terminal branches sum to "
            f"{c['branch_total']} but {c['carts_in_deep_window']} carts were created "
            f"in the same settled window (difference {c['difference']}). The branch "
            f"shares below do not decompose the funnel — treat them as unusable "
            f"until the view's terminal_branch CASE or the two queries' cohort gates "
            f"are reconciled."
        )
    if not groups[0]["deep_started"]:
        warnings.append(
            "Zero carts in the deepest settled cohort window, so the branch mix and "
            "the funnel chart are empty. Widen --time-range, or check that the "
            "fixture/view covers cohorts old enough to have settled."
        )
    warnings += spine.guardrail_warnings(
        spec, start, end, columns=([dim] if dim else []) + list(filters)
    )

    out = {
        "skill": "funnel-dropoff",
        "module": args.module or spine.DEFAULT_MODULE,
        "spine": args.spine,
        "source_view": spec["source_view"],
        "source_project": project,
        "accuracy_tier": spec["accuracy_tier"],
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
        "segment_filters": filters,
        "group_by": dim,
        "maturity_mode": mode,
        "mature_only": mode != "off",
        "maturity": maturity,
        "maturity_cutoff": maturity.get("deep_cohort_through"),
        "legacy": spine.legacy_status(spec, args.include_legacy),
        "branch_coherent": not incoherent,
        "groups": groups,
        "chart_spec": chart_spec_for(
            spec,
            groups[0],
            f"{spec.get('primary_entity', 'session')} funnel — drop-off",
            maturity,
            mock=spine.active_adapter(args.engine).fixture_backed,
        ) if groups else None,
        "sql": {"stages": stage_sql, "branches": branch_sql},
        "warnings": warnings,
    }
    out.update(spine.fixture_provenance(args.engine))
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
