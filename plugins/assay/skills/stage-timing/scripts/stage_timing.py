#!/usr/bin/env python3
"""stage_timing.py — percentile stage latency over a session-grain spine.

Computes per-transition latency distributions — p50 / p90 / p99, plus mean and n
— from a module's spine (resolved through `config/modules.yaml`; the `checkout`
module's is `analytics.cart_checkout_session_spine`). This is the capability the
governed `query-metric` model structurally cannot express: its contract is
SUM(numerator) / SUM(denominator), and a median is not a ratio of two sums.

Each transition's statistics are over the population that reached **both** of its
endpoints. A row where either timestamp is NULL contributes nothing rather than
contributing a zero, so "time to ship" is the time taken by the orders that
shipped — and `n` alongside it is how the reader sees how much of the funnel that
was.

Percentiles are an adapter CAPABILITY, not a translated construct
----------------------------------------------------------------
The helper never writes `APPROX_QUANTILES` itself. It asks
`adapter.percentiles(...)`, which pushes the aggregate into BigQuery where that
exists and computes it client-side over the returned column on sqlite, where no
percentile function exists at all. Both use the same nearest-rank definition, so
the two engines agree on *what* is being computed; `percentile_basis` in the
output records *where* it was computed, because "approximate at scale" and
"exact" are a difference a reader is entitled to see rather than a detail to
paper over. `_shared/dialect.py` refuses APPROX_QUANTILES outright for the same
reason: a fake percentile in SQL would be slow and subtly wrong on ties.

Governance via `_shared/spine.py`: allow-listed group-by dimensions and filters,
bound parameter values, per-transition maturity cutoffs gated on each
transition's ARRIVAL stage, and — on the warehouse engine — the query running as
the individual human user via ADC.

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


def resolve_dimensions(spec: dict, raw: str | None) -> list:
    """Validate a comma-separated `--group-by` into a list of allow-listed dims.

    More than one is genuinely useful here — "time to ship by market and
    category" is a real question — and the adapter's percentile call returns one
    row per group tuple with each dimension under its own key, so a two-dimension
    result needs no unpacking downstream.
    """
    dims = [d.strip() for d in (raw or "").split(",") if d.strip()]
    for d in dims:
        spine.validate_dimension(spec, d)
    if len(set(dims)) != len(dims):
        spine.refuse(f"--group-by repeats a dimension: {raw!r}")
    return dims


def transition_stats(
    spec: dict,
    transition: dict,
    dims: list,
    where: str,
    where_params: dict,
    cutoffs: dict,
    pcts: list,
    *,
    engine=None,
) -> tuple:
    """One transition's percentile rows, plus the SQL-shaped provenance.

    Returns `(rows, value_expr)`. `rows` is what the adapter produced: one dict
    per group tuple carrying the group columns plus `n`, `mean`, `p{N}`, and
    `_percentile_basis`.
    """
    adapter = spine.active_adapter(engine)
    value_expr = spine.gated_diff(spec, transition, cutoffs)
    rows = adapter.percentiles(
        spec["source_project"],
        from_sql=f"`{spine.fq_view(spec, engine=engine)}`",
        where_sql=where,
        value_expr=value_expr,
        percentiles=pcts,
        params=spine.percentile_params(where_params, transition, cutoffs),
        group_by={d: spine.col_expr(spec, d) for d in dims} or None,
    )
    return rows, value_expr


def group_key(row: dict, dims: list) -> tuple:
    return tuple(row.get(d) for d in dims)


def shape_transition(
    transition: dict, row: dict | None, pcts: list, meta: dict, report_unit: str | None = None
) -> dict:
    """One (group, transition) cell of the output table.

    A group with no qualifying rows for this transition is emitted with `n: 0`
    and NULL statistics rather than omitted — the absence is the finding (nothing
    in this segment reached both endpoints), and dropping the row would make it
    look like the transition was never asked for.
    """
    k = transition["key"]
    psm = meta.get("per_stage", {}).get(k, {})
    row = row or {}
    return {
        "transition": k,
        "label": transition["label"],
        "unit": transition.get("unit"),
        "n": int(row.get("n") or 0),
        "mean": row.get("mean"),
        "percentiles": {f"p{p}": row.get(f"p{p}") for p in pcts},
        "maturity_stage": psm.get("maturity_stage", transition.get("maturity_stage")),
        "lag_days": psm.get("lag_days"),
        "mature_through": psm.get("cutoff_week"),
        "percentile_basis": row.get("_percentile_basis"),
    }


def kpi_index(spec: dict) -> dict:
    """`{transition_key: [kpi, ...]}` for the spec's named latency KPIs.

    A KPI is a thin alias over a transition that pins its headline percentiles,
    tier, and owner so it is citable by name. Surfacing them on the transition
    they measure is what makes `assay:catalog`'s KPI table and this skill's output
    describe the same thing.
    """
    out: dict = {}
    for kpi in spec.get("kpis") or []:
        out.setdefault(kpi.get("transition"), []).append({
            "key": kpi.get("key"),
            "display_name": kpi.get("display_name"),
            "percentiles": kpi.get("percentiles"),
            "accuracy_tier": kpi.get("accuracy_tier"),
            "report_unit": kpi.get("report_unit"),
            "owner": kpi.get("owner"),
            "leak_guarded": kpi.get("leak_guarded"),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    spine.add_common_args(
        ap,
        group_by_help="allow-listed dimension(s), comma-separated — one output row "
                      "per group tuple, each dimension under its own key",
    )
    ap.add_argument(
        "--transitions", default=None,
        help="comma-separated transition keys from the spec (default: all)",
    )
    ap.add_argument(
        "--percentiles", default="50,90,99",
        help="comma-separated 0-100 (default 50,90,99)",
    )
    args = ap.parse_args()

    spec = spine.load_spec(module=args.module, spine=args.spine)
    dims = resolve_dimensions(spec, args.group_by)
    filters = spine.filters_from_args(args.filter)
    spine.validate_filters(spec, filters)

    keys = [k.strip() for k in args.transitions.split(",")] if args.transitions else None
    transitions = spine.select_transitions(spec, keys)
    pcts = spine.parse_percentiles(args.percentiles)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    start, end = spine.parse_time_range(spec, args.time_range, today)
    mode = spine.resolve_maturity_mode(args, spec)

    cutoffs, _cut_params, maturity = spine.resolve_stage_cutoffs(
        spec, transitions, today, mode=mode
    )
    where, where_params = spine.build_where(
        spec, filters, start, end, maturity_mode=mode, include_legacy=args.include_legacy
    )

    # One percentile call per transition. The adapter builds its own SELECT, so
    # each call gets only the cutoff parameter its own value expression names.
    by_transition: dict = {}
    value_exprs: dict = {}
    order: list = []
    seen: set = set()
    basis = None
    for t in transitions:
        rows, value_expr = transition_stats(
            spec, t, dims, where, where_params, cutoffs, pcts, engine=args.engine
        )
        value_exprs[t["key"]] = value_expr
        for r in rows:
            key = group_key(r, dims)
            if key not in seen:
                seen.add(key)
                order.append(key)
            by_transition.setdefault(key, {})[t["key"]] = r
            basis = basis or r.get("_percentile_basis")

    kpis = kpi_index(spec)
    groups = []
    for key in order:
        cells = by_transition.get(key, {})
        groups.append({
            "group": dict(zip(dims, key)) if dims else None,
            "transitions": [
                shape_transition(t, cells.get(t["key"]), pcts, maturity) for t in transitions
            ],
        })
    if not groups:
        groups.append({
            "group": None,
            "transitions": [shape_transition(t, None, pcts, maturity) for t in transitions],
        })

    warnings = spine.fixture_warning(args.engine)
    warnings += spine.maturity_warning(spec, maturity)
    warnings.append(
        "PERCENTILE BASIS: " + (basis or "no rows reached both endpoints of any "
                                        "requested transition, so nothing was computed")
    )
    empty = [
        t["label"]
        for t in transitions
        if all(
            cell["n"] == 0
            for g in groups
            for cell in g["transitions"]
            if cell["transition"] == t["key"]
        )
    ]
    if empty:
        warnings.append(
            f"EMPTY TRANSITIONS: no rows reached both endpoints for {', '.join(empty)}. "
            f"For a deep transition that usually means the window holds no cohort "
            f"mature for its arrival stage, not that the stage never happens."
        )
    warnings += spine.guardrail_warnings(spec, start, end, columns=dims + list(filters))

    out = {
        "skill": "stage-timing",
        "module": args.module or spine.DEFAULT_MODULE,
        "spine": args.spine,
        "source_view": spec["source_view"],
        "source_project": spec["source_project"],
        "accuracy_tier": spec["accuracy_tier"],
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
        "segment_filters": filters,
        "group_by": dims,
        "percentiles": pcts,
        "percentile_basis": basis,
        "maturity_mode": mode,
        "mature_only": mode != "off",
        "maturity": maturity,
        "maturity_cutoff": maturity.get("deep_cohort_through"),
        "legacy": spine.legacy_status(spec, args.include_legacy),
        "kpis": kpis,
        "groups": groups,
        "sql": {
            "where": where,
            "value_expressions": value_exprs,
            "note": (
                "the SELECT is built by the adapter's percentiles() call — pushed "
                "down as APPROX_QUANTILES on BigQuery, computed client-side over "
                "the value expression on sqlite"
            ),
        },
        "warnings": warnings,
    }
    out.update(spine.fixture_provenance(args.engine))
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
