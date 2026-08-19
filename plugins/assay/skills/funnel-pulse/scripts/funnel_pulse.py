#!/usr/bin/env python3
"""funnel_pulse.py — recent-vs-baseline funnel regression monitor, stage-aware.

Scans every value of one dimension (`pulse.by` in the spec, or `--by`) on a
session-grain spine and flags stage-conversion and terminal-branch shifts between
a recent window and the baseline window immediately before it, ranked by impact
(carts affected x delta). Built to run unattended and emit a digest for review;
it never posts anywhere.

Stage-aware windows
-------------------
Each stage gets its OWN recent and baseline windows, lagged by that stage's
settle time. Cart-to-checkout-start settles in minutes, so its recent window ends
at the last complete week and a regression there surfaces within days. Return
Window Complete needs ~44 days, so its recent window is lagged that far — because
you genuinely cannot measure it on last week's carts. Forcing the whole funnel to
the slowest stage's lag, which is what a single mature-cohort gate does, means an
early-funnel regression sits undetected for six weeks.

This is still a **cohort** monitor, not a within-day alarm: every window is a
settled cohort for its own stage.

Per-group maturity overrides
----------------------------
A stage whose settle time varies by the dimension being monitored gets a per-group
window. `payment_decisioned` is the live example: book-wide it is dominated by
instant card authorization that decides in seconds, so the default 2-day lag is
badly wrong for a bank transfer routed through a clearing cycle and a review
queue. When `--by` matches the override's `by`, that group is compared over its
own longer window — on BOTH sides of the ratio, numerator and denominator, or the
rate would be measured over two different populations. A group with no override
entry keeps the shared window, so this is purely additive.

Why the refusing cutoff resolver
--------------------------------
Windows are built from `spine.resolve_stage_cutoffs()`, which HARD REFUSES when a
stage declares a `maturity_stage` with no calibrated lag. The convenient
alternative (`cutoff_for_stage()`) returns None for a missing lag, which here
would silently produce an ungated window — a monitor comparing two windows that
are not settled, reporting shifts that are just the tail arriving, and saying
nothing about it. A monitor that runs ungated without announcing it is worse than
one that refuses to start.

Output: JSON on stdout for the SKILL.md flow to render into a digest artifact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _shared import spine  # noqa: E402


def stage_windows(
    spec: dict, today: dt.date, recent_weeks: int, baseline_weeks: int, mode: str, by: str
) -> tuple:
    """Per-stage recent/baseline windows, plus the maturity meta they came from.

    recent   = [end - (recent_weeks-1)w, end]
    baseline = [recent_start - baseline_weeks*w, recent_start)
    end      = the stage's mature-through Monday.

    `resolve_stage_cutoffs(..., mode="stage")` is called unconditionally, even in
    `flag` mode, precisely so its refusal on a missing calibrated lag always
    fires: `flag` mode then collapses every stage to the deepest cutoff, which is
    a deliberate narrowing of a known-good set rather than an absence of gating.
    """
    cutoffs, _params, meta = spine.resolve_stage_cutoffs(
        spec, spec["stages"], today, mode="stage"
    )
    if mode != "stage":
        deep = min(c for c in cutoffs.values() if c is not None)
        cutoffs = {k: deep for k in cutoffs}

    wins: dict = {}
    for s in spec["stages"]:
        k = s["key"]
        mstage = s.get("maturity_stage", k)
        end = cutoffs[k]
        rs = end - dt.timedelta(weeks=recent_weeks - 1)
        bs = rs - dt.timedelta(weeks=baseline_weeks)
        group_windows = None
        if mode == "stage":
            group_windows = spine.group_windows_for_stage(
                spec, mstage, by, today, recent_weeks, baseline_weeks
            )
        wins[k] = {
            "end": end,
            "recent_start": rs,
            "baseline_start": bs,
            "lag_days": spine.stage_lag_days(spec, mstage),
            "maturity_stage": mstage,
            "group_windows": group_windows,
        }
    return wins, meta


def window_params(wins: dict) -> dict:
    """Bind values for every stage window, including override windows."""
    p: dict = {}
    for k, w in wins.items():
        p[f"end_{k}"] = w["end"]
        p[f"rs_{k}"] = w["recent_start"]
        p[f"bs_{k}"] = w["baseline_start"]
        p.update(spine.windowed_count_params(k, w.get("group_windows")))
    return p


def scan_bounds(wins: dict) -> tuple:
    """The widest [start, end] any window touches.

    A per-group override window can reach further back than its stage's default
    baseline, so the outer WHERE has to be widened or those rows are filtered out
    before the per-row CASE ever sees them — the override would then silently
    return a truncated baseline for exactly the groups it exists to serve.
    """
    starts = [w["baseline_start"] for w in wins.values()]
    ends = [w["end"] for w in wins.values()]
    for w in wins.values():
        for gw in (w.get("group_windows") or {}).values():
            starts.append(gw["baseline_start"])
            ends.append(gw["end"])
    return min(starts), max(ends)


def stage_sql(spec: dict, by: str, where: str, wins: dict, *, engine=None) -> str:
    """Recent and baseline counts per group, per stage, over each stage's window.

    The first-stage count (the denominator) is re-counted over each stage's OWN
    windows, so a stage's rate has matching populations top and bottom. Where a
    per-group override applies, `windowed_count_expr` emits a CASE keyed on the
    group column with the shared window as the ELSE — applied to the denominator
    as well as the numerator, for the same reason.
    """
    view = spine.fq_view(spec, engine=engine)
    cc = spec["cohort_column"]
    first_key = spec["stages"][0]["key"]
    first_flag = spec["stages"][0]["flag"]
    dexpr = spine.col_expr(spec, by)
    cols = []
    for s in spec["stages"]:
        k = s["key"]
        gw = wins[k].get("group_windows")
        base_recent = spine.windowed_count_expr(first_flag, cc, k, wins[k], dexpr, gw, "recent")
        base_base = spine.windowed_count_expr(first_flag, cc, k, wins[k], dexpr, gw, "baseline")
        cols.append(f"COUNTIF({base_recent}) AS started_recent_{k}")
        cols.append(f"COUNTIF({base_base}) AS started_base_{k}")
        if k != first_key:
            n_recent = spine.windowed_count_expr(s["flag"], cc, k, wins[k], dexpr, gw, "recent")
            n_base = spine.windowed_count_expr(s["flag"], cc, k, wins[k], dexpr, gw, "baseline")
            cols.append(f"COUNTIF({n_recent}) AS {k}_recent")
            cols.append(f"COUNTIF({n_base}) AS {k}_base")
    return (
        f"SELECT\n  {dexpr} AS grp,\n  " + ",\n  ".join(cols)
        + f"\nFROM `{view}`\n{where}\nGROUP BY grp\nORDER BY grp"
    )


def branch_sql(spec: dict, by: str, where: str, deep_key: str, *, engine=None) -> str:
    """Branch mix over the deepest (fully settled) stage's windows.

    A terminal branch is a settled-state property of a whole cart, so it is read
    over one coherent cohort per side rather than per-stage.
    """
    view = spine.fq_view(spec, engine=engine)
    cc = spec["cohort_column"]
    dexpr = spine.col_expr(spec, by)
    return (
        f"SELECT\n  {dexpr} AS grp,\n  terminal_branch,\n"
        f"  COUNTIF({cc} >= @rs_{deep_key} AND {cc} <= @end_{deep_key}) AS n_recent,\n"
        f"  COUNTIF({cc} >= @bs_{deep_key} AND {cc} <  @rs_{deep_key}) AS n_baseline\n"
        f"FROM `{view}`\n{where}\nGROUP BY grp, terminal_branch\n"
        f"ORDER BY grp, terminal_branch"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    spine.add_common_args(ap)
    ap.add_argument("--by", default=None,
                    help="monitoring dimension (default: the spec's pulse.by)")
    ap.add_argument("--recent-weeks", type=int, default=None)
    ap.add_argument("--baseline-weeks", type=int, default=None)
    ap.add_argument("--min-started", type=int, default=None,
                    help="skip a group below this cart count in EITHER window")
    ap.add_argument("--threshold-pp", type=float, default=None,
                    help="flag a stage or branch share move of at least this many pp")
    args = ap.parse_args()

    spec = spine.load_spec(module=args.module, spine=args.spine)
    pulse = spec.get("pulse") or {}
    if not pulse and args.by is None:
        spine.refuse(
            f"the spec sourced from {spec.get('source_view', '?')} has no `pulse:` "
            f"block, so there is no default monitoring dimension or window. Pass "
            f"--by explicitly, or author the block."
        )
    by = args.by or pulse.get("by")
    spine.validate_dimension(spec, by)
    filters = spine.filters_from_args(args.filter)
    spine.validate_filters(spec, filters)

    recent_weeks = args.recent_weeks or int(pulse.get("recent_weeks", 4))
    baseline_weeks = args.baseline_weeks or int(pulse.get("baseline_weeks", 8))
    min_started = args.min_started if args.min_started is not None else int(pulse.get("min_started", 200))
    threshold = args.threshold_pp if args.threshold_pp is not None else float(pulse.get("flag_threshold_pp", 3.0))

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    mode = spine.resolve_maturity_mode(args, spec)
    if mode == "off":
        # A pulse without a settle lag compares two windows that have not
        # finished arriving, so "off" is not a coherent request here. Honour the
        # intent to see everything by narrowing to `flag` (one deepest cohort)
        # and say so, rather than silently running ungated.
        mode = "flag"
        off_note = (
            "--no-mature / --maturity off is not available for a cohort monitor: "
            "comparing two unsettled windows measures the tail arriving, not a "
            "change. Fell back to `flag` (every stage on the deepest settled "
            "cohort's window)."
        )
    else:
        off_note = None

    wins, maturity_meta = stage_windows(spec, today, recent_weeks, baseline_weeks, mode, by)
    win_params = window_params(wins)
    first_key = spec["stages"][0]["key"]
    deep_key = spec["stages"][-1]["key"]
    overall_start, overall_end = scan_bounds(wins)

    # The window boundaries carry maturity; the WHERE only bounds the scan.
    where, params = spine.build_where(
        spec, filters, overall_start, overall_end,
        maturity_mode="off", include_legacy=args.include_legacy,
    )
    params.update(win_params)

    project = spec["source_project"]
    s_sql = stage_sql(spec, by, where, wins, engine=args.engine)
    b_sql = branch_sql(spec, by, where, deep_key, engine=args.engine)
    stage_rows = spine.run_query(project, s_sql, params, engine=args.engine)
    branch_rows = spine.run_query(project, b_sql, params, engine=args.engine)

    branch_by_grp: dict = {}
    for r in branch_rows:
        branch_by_grp.setdefault(str(r.get("grp")), []).append(r)

    best_branch = (spec.get("terminal_branches") or [None])[0]
    flows, flags, skipped = [], [], []
    override_groups = set()

    for sr in stage_rows:
        grp = sr.get("grp")
        # The volume gate uses the FIRST stage's own (freshest) windows.
        sr_n = int(sr.get(f"started_recent_{first_key}", 0) or 0)
        sb_n = int(sr.get(f"started_base_{first_key}", 0) or 0)
        if sr_n < min_started or sb_n < min_started:
            skipped.append({"grp": grp, "started_recent": sr_n, "started_baseline": sb_n})
            continue

        stage_deltas = []
        for s in spec["stages"]:
            k = s["key"]
            srk = int(sr.get(f"started_recent_{k}", 0) or 0)
            sbk = int(sr.get(f"started_base_{k}", 0) or 0)
            if k == first_key:
                rr = br = 1.0
            else:
                rr = (int(sr.get(f"{k}_recent", 0) or 0) / srk) if srk else None
                br = (int(sr.get(f"{k}_base", 0) or 0) / sbk) if sbk else None
            d = ((rr - br) * 100) if (rr is not None and br is not None) else None
            # Report the window actually applied to THIS group's row: a per-group
            # override when one matched, else the stage default.
            applied = (wins[k].get("group_windows") or {}).get(grp)
            if applied is not None:
                override_groups.add(str(grp))
            stage_deltas.append({
                "key": k,
                "label": s["label"],
                "baseline_rate": br,
                "recent_rate": rr,
                "delta_pp": d,
                "recent_n": srk,
                "baseline_n": sbk,
                "mature_through": (applied or wins[k])["end"].isoformat(),
                "lag_days": (applied or wins[k])["lag_days"],
                "lag_override": applied is not None,
            })
            if k != first_key and d is not None and abs(d) >= threshold:
                flags.append({
                    "grp": grp,
                    "kind": "stage",
                    "name": s["label"],
                    "baseline_rate": br,
                    "recent_rate": rr,
                    "delta_pp": d,
                    "direction": "regression" if d < 0 else "improvement",
                    "recent_n": srk,
                    "impact": abs(d) * srk,
                    "lag_override": applied is not None,
                })

        e2e = next(x for x in stage_deltas if x["key"] == deep_key)
        flows.append({
            "grp": grp,
            "started_recent": sr_n,
            "started_baseline": sb_n,
            "e2e_baseline": e2e["baseline_rate"],
            "e2e_recent": e2e["recent_rate"],
            "e2e_delta_pp": e2e["delta_pp"],
            "stages": stage_deltas,
        })

        # Branch share shifts, over the deepest stage's settled windows.
        srd = int(sr.get(f"started_recent_{deep_key}", 0) or 0)
        sbd = int(sr.get(f"started_base_{deep_key}", 0) or 0)
        for b in branch_by_grp.get(str(grp), []):
            br = (int(b.get("n_baseline", 0) or 0) / sbd) if sbd else None
            rr = (int(b.get("n_recent", 0) or 0) / srd) if srd else None
            if rr is None or br is None:
                continue
            d = (rr - br) * 100
            if abs(d) >= threshold:
                # Rising into the spec's best-listed branch is an improvement;
                # rising into any other exit is a regression. Spec-driven rather
                # than keyed on a naming convention, so this generalizes to a
                # spine whose branches are labelled differently.
                good = b["terminal_branch"] == best_branch
                flags.append({
                    "grp": grp,
                    "kind": "branch",
                    "name": b["terminal_branch"],
                    "baseline_rate": br,
                    "recent_rate": rr,
                    "delta_pp": d,
                    "direction": "improvement" if (d > 0) == good else "regression",
                    "recent_n": srd,
                    "impact": abs(d) * srd,
                    "lag_override": False,
                })

    flags.sort(key=lambda x: x["impact"], reverse=True)
    flows.sort(key=lambda x: x["started_recent"], reverse=True)

    def _window_block(w, applied=None):
        src = applied or w
        return {
            "recent": {"start": src["recent_start"].isoformat(), "end": src["end"].isoformat()},
            "baseline": {
                "start": src["baseline_start"].isoformat(),
                "end": (src["recent_start"] - dt.timedelta(days=7)).isoformat(),
            },
            "lag_days": src["lag_days"],
        }

    warnings = spine.fixture_warning(args.engine)
    if off_note:
        warnings.append("MATURITY MODE: " + off_note)
    warnings.append(
        f"STAGE-AWARE WINDOWS: every stage is compared over its own settled window "
        f"— {first_key} through {wins[first_key]['end'].isoformat()}, {deep_key} "
        f"through {wins[deep_key]['end'].isoformat()}. A deep-stage shift cannot "
        f"surface until that stage settles, and an early-stage one is not withheld "
        f"waiting for it."
    )
    if spine.stage_maturity_table(spec).get("illustrative"):
        warnings.append(
            "MATURITY: the settle lags driving those windows are hand-authored and "
            "illustrative, NOT measured. A real deployment regenerates them from "
            "observed settle time on the spine."
        )
    if override_groups:
        warnings.append(
            f"PER-GROUP MATURITY OVERRIDE applied to {', '.join(sorted(override_groups))}: "
            f"those groups are compared over their own longer windows, on both sides "
            f"of each rate. Rows carrying `lag_override: true` are not on the same "
            f"window as their neighbours — which is the point, and is why the "
            f"per-row `mature_through` is reported rather than one window per stage."
        )
    warnings.append(
        "DIRECTION IS A HEURISTIC for intermediate stages. A fall in one stage paired "
        "with a rise in the next is usually a routing shift, not a regression. The "
        f"end-to-end ({deep_key}) delta is the unambiguous signal; treat the rest as "
        "pointers for where to look."
    )
    warnings.append(
        "DESCRIPTIVE ONLY: no significance test. Groups below the min_started floor "
        "are listed under skipped_low_n rather than flagged on noise."
    )
    warnings += spine.guardrail_warnings(
        spec, overall_start, overall_end, columns=[by] + list(filters)
    )

    out = {
        "skill": "funnel-pulse",
        "module": args.module or spine.DEFAULT_MODULE,
        "spine": args.spine,
        "source_view": spec["source_view"],
        "source_project": project,
        "accuracy_tier": spec["accuracy_tier"],
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "by": by,
        "scope_filters": filters,
        "maturity_mode": mode,
        "maturity_cutoff": wins[deep_key]["end"].isoformat(),
        "scan_window": {"start": overall_start.isoformat(), "end": overall_end.isoformat()},
        "legacy": spine.legacy_status(spec, args.include_legacy),
        "maturity": {
            "mode": mode,
            "basis": maturity_meta.get("basis"),
            "percentile": maturity_meta.get("percentile"),
            "calibrated_at": maturity_meta.get("calibrated_at"),
            "per_stage": {
                k: {
                    "maturity_stage": w["maturity_stage"],
                    **_window_block(w),
                    "group_overrides": {
                        by: {
                            value: {
                                "lag_days": gw["lag_days"],
                                "mature_through": gw["end"].isoformat(),
                            }
                            for value, gw in (w.get("group_windows") or {}).items()
                        }
                    } if w.get("group_windows") else None,
                }
                for k, w in wins.items()
            },
        },
        "windows": {
            "freshest": {"stage": first_key, **_window_block(wins[first_key])},
            "deepest": {"stage": deep_key, **_window_block(wins[deep_key])},
        },
        "thresholds": {
            "min_started": min_started,
            "flag_threshold_pp": threshold,
            "recent_weeks": recent_weeks,
            "baseline_weeks": baseline_weeks,
        },
        "scanned": len(stage_rows),
        "qualified": len(flows),
        "skipped_low_n": skipped,
        "flags": flags,
        "flows": flows,
        "sql": {"stages": s_sql, "branches": b_sql},
        "warnings": warnings,
    }
    out.update(spine.fixture_provenance(args.engine))
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
