#!/usr/bin/env python3
"""funnel_nowcast.py — chain-ladder now-cast of an immature cohort's eventual rate.

Answers *"this recent cohort can't be measured on its deep stages yet — what
will its mature rate be?"* without waiting out the settle time. For a target
stage it builds the classic run-off triangle — cohort week x development week,
cumulative arrivals — from the session-grain spine, estimates volume-weighted
development factors from the cohorts old enough to inform them, projects each
immature cohort's ultimate arrivals, and reports P10/P50/P90 alongside.

The method is Mack's distribution-free chain ladder (Mack 1993, ASTIN
Bulletin 23(2)) — the actuarial standard for exactly this shape of problem
("a cohort is still reporting outcomes; I need its eventual total before it
has finished"), and literally the baseline method the epidemiological
nowcasting community measures newer models against. Three deliberate ports
from claims to funnels:

  1. **Factors are estimated, never authored.** Every development factor and
     every variance is computed from the triangle at run time. Nothing here
     ships a fitted curve, which is what lets this skill exist in a template
     whose fixtures are synthetic: on fixture data the *numbers* describe the
     generator (and are watermarked as such, like every other skill's), while
     the *mechanism* is exactly what a real deployment runs.
  2. **The ceiling is enforced.** Claims have no upper bound; a funnel does —
     a cohort cannot eventually convert more carts than it opened. Raw chain
     ladder can breach that ceiling, so ultimates and quantiles are capped at
     cohort size and floored at the already-observed count.
  3. **Calibration is checked, not assumed.** `--backtest` replays history:
     for every fully-developed cohort and every development age, it re-runs
     the estimator using only the data that was observable at that pseudo
     as-of date, then scores the interval against the realized outcome. The
     only preregistered real-time comparison of nowcasting methods (Wolffram
     et al. 2023) found model-based intervals almost universally too narrow —
     so the empirical coverage is reported next to the nominal one, and the
     artifact says which it achieved.

Intervals: Mack's closed-form mean squared error of prediction (process +
estimation variance, his Theorem 3 recursion) mapped through a lognormal so
quantiles respect the count's positivity, then clamped to [observed, cohort
size]. Every projected number is lane-stamped **FORECAST** — never a governed
T2 actual; it reconciles to one as the cohort matures.

Governance is the shared spine machinery: allow-listed group-by and filters,
values bound as parameters, legacy-cohort exclusion, per-user identity on the
warehouse engine. The target stage must be declared in the spec's `nowcast:`
block — fast-settling stages are refused, because a now-cast of a stage that
settles in a day is a worse way to read a governed actual.

Output: JSON on stdout for the SKILL.md flow to render into a markdown
artifact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _shared import spine  # noqa: E402

LANE = "FORECAST"


# ---------------------------------------------------------------------------
# Development ages and triangles
# ---------------------------------------------------------------------------


def dev_age_weeks(cohort_monday: dt.date, as_of: dt.date) -> int:
    """Completed development weeks for a cohort at `as_of`, anchored to the
    cohort week's END (Sunday): the youngest cart in the cohort has had this
    many full weeks to produce arrivals. Anchoring to the week's end rather
    than its Monday is the conservative choice — a development cell is only
    treated as observed once EVERY cart in the cohort has aged through it,
    which keeps the triangle's diagonal honest at the cost of up to six days
    of freshness."""
    return max(0, (as_of - (cohort_monday + dt.timedelta(days=6))).days // 7)


def cumulative_from_hist(hist: dict[int, int], j_weeks: int) -> int:
    """Arrivals within `j_weeks` development weeks: lag_days < 7*j."""
    return sum(n for lag, n in hist.items() if lag < 7 * j_weeks)


def build_group_data(size_rows, hist_rows, grouped: bool):
    """Fold the two query results into {group: {"sizes": {cohort: n},
    "hists": {cohort: {lag: n}}}}. Group key is "__all__" when ungrouped."""
    data: dict = {}

    def bucket(grp):
        return data.setdefault(
            str(grp) if grouped else "__all__", {"sizes": {}, "hists": {}}
        )

    for r in size_rows:
        b = bucket(r.get("grp"))
        b["sizes"][str(r["cohort_week"])] = int(r["n"])
    for r in hist_rows:
        lag = int(r["lag_days"])
        if lag < 0:
            continue  # arrival before the open anchor: a data bug, not a delay
        b = bucket(r.get("grp"))
        h = b["hists"].setdefault(str(r["cohort_week"]), {})
        h[lag] = h.get(lag, 0) + int(r["n"])
    return data


# ---------------------------------------------------------------------------
# Mack chain ladder
# ---------------------------------------------------------------------------


def estimate_factors(triangle: dict[str, dict[int, int]], ages: dict[str, int],
                     j_max: int, window: int | None) -> list[dict]:
    """Volume-weighted development factors f_j (j -> j+1) with Mack's variance.

    For each j, uses the cohorts whose age covers BOTH cells (and, when
    `window` is set, only the most recent `window` of them — the standard
    trailing-diagonals defense against old regimes leaking into current
    factors). f_j = sum(C_{i,j+1}) / sum(C_{i,j}) is Mack's minimum-variance
    estimator under his assumptions; sigma2_j is his unbiased variance
    estimate, extrapolated for the last age by his log-linear rule when only
    one cohort informs it.
    """
    out = []
    for j in range(1, j_max):
        cohorts = sorted(
            c for c, a in ages.items()
            if a >= j + 1 and triangle[c].get(j, 0) > 0
        )
        if window:
            cohorts = cohorts[-window:]
        S = sum(triangle[c][j] for c in cohorts)
        if not cohorts or S == 0:
            out.append({"j": j, "f": 1.0, "sigma2": 0.0, "S": 0, "n_cohorts": 0,
                        "ratio_min": None, "ratio_max": None})
            continue
        S_next = sum(triangle[c].get(j + 1, 0) for c in cohorts)
        f = S_next / S
        ratios = {c: triangle[c].get(j + 1, 0) / triangle[c][j] for c in cohorts}
        if len(cohorts) > 1:
            sigma2 = sum(
                triangle[c][j] * (ratios[c] - f) ** 2 for c in cohorts
            ) / (len(cohorts) - 1)
        else:
            sigma2 = None  # extrapolated below
        out.append({
            "j": j, "f": f, "sigma2": sigma2, "S": S, "n_cohorts": len(cohorts),
            "ratio_min": min(ratios.values()), "ratio_max": max(ratios.values()),
        })
    # Mack's extrapolation for a tail sigma2 estimated from <2 cohorts:
    # sigma2_{k} = min(sigma2_{k-1}^2 / sigma2_{k-2}, sigma2_{k-2}, sigma2_{k-1}).
    for idx, fac in enumerate(out):
        if fac["sigma2"] is None:
            prev = [f["sigma2"] for f in out[:idx] if f["sigma2"]]
            if len(prev) >= 2:
                fac["sigma2"] = min(prev[-1] ** 2 / prev[-2], prev[-2], prev[-1])
            elif prev:
                fac["sigma2"] = prev[-1]
            else:
                fac["sigma2"] = 0.0
    return out


def mack_project(observed: int, age: int, factors: list[dict], j_max: int) -> dict:
    """Project one immature cohort to its ultimate at j_max, with Mack's MSEP.

    The recursion accumulates, age by age, the process term (sigma2/f^2 * 1/C)
    and the estimation term (sigma2/f^2 * 1/S); the total scales by the
    squared ultimate. Distribution-free: no likelihood was assumed to get
    here, which is the reason this variance is the standard one."""
    c = float(observed)
    if c <= 0:
        return {"ultimate": 0.0, "sd": 0.0}
    acc = 0.0
    for fac in factors:
        j = fac["j"]
        if j < age or j >= j_max:
            continue
        f, sigma2, S = fac["f"], fac["sigma2"], fac["S"]
        if f > 0:
            term = (sigma2 / f ** 2) * (1.0 / c)
            if S > 0:
                term += (sigma2 / f ** 2) * (1.0 / S)
            acc += term
        c *= max(f, 0.0)
    ultimate = c
    msep = (ultimate ** 2) * acc
    return {"ultimate": ultimate, "sd": math.sqrt(msep) if msep > 0 else 0.0}


def lognormal_quantile(mean: float, sd: float, p: float) -> float:
    """Quantile of a lognormal matched to (mean, sd) by moments — the standard
    way to turn Mack's MSEP into positivity-respecting quantiles. Degenerates
    to the mean when sd is 0."""
    if mean <= 0 or sd <= 0:
        return mean
    sigma2 = math.log(1.0 + (sd / mean) ** 2)
    mu = math.log(mean) - sigma2 / 2.0
    z = statistics.NormalDist().inv_cdf(p / 100.0)
    return math.exp(mu + z * math.sqrt(sigma2))


def level_prior(mature_rates: list[float]) -> dict | None:
    """The cohort-to-cohort distribution of the *eventual* mature rate — the
    fallback for a cohort too young for its triangle row to say anything.

    A stage behind a long pure delay (a return window) shows ZERO arrivals for
    its first several weeks; a multiplicative chain ladder projects zero from
    zero and wraps it in a confident band, which is the worst kind of wrong.
    The honest read for such a cohort is the level: recent mature cohorts
    landed here, with this spread. This is the 'level dispersion' floor the
    method's own design notes call the term that must not vanish."""
    if len(mature_rates) < 2:
        return None
    return {
        "mean": statistics.fmean(mature_rates),
        "sd": statistics.stdev(mature_rates),
        "n_cohorts": len(mature_rates),
    }


def nowcast_cohort(cohort: str, triangle_row: dict[int, int], age: int, size: int,
                   factors: list[dict], j_max: int, quantiles: list[int],
                   prior: dict | None) -> dict:
    """One cohort's now-cast row: observed, projected ultimate, quantiles —
    capped at cohort size (a funnel has a ceiling claims do not) and floored
    at what is already observed (arrivals cannot un-arrive).

    A cohort with arrivals on the board projects by chain ladder; one with
    none yet falls back to the level prior (see `level_prior`), and one with
    neither reports itself unprojectable rather than manufacturing a zero."""
    a = min(age, j_max)
    observed = triangle_row.get(a, 0) if a >= 1 else 0
    mature = age >= j_max
    row = {
        "cohort_week": cohort,
        "age_weeks": age,
        "cohort_size": size,
        "observed": observed,
        "observed_rate": observed / size if size else None,
        "mature": mature,
    }
    if mature:
        row["ultimate"] = observed
        row["ultimate_rate"] = observed / size if size else None
        return row
    lo, hi = float(observed), float(size)
    if observed > 0:
        proj = mack_project(observed, a, factors, j_max)
        mean, sd, method = proj["ultimate"], proj["sd"], "chain_ladder"
    elif prior is not None:
        mean, sd = prior["mean"] * size, prior["sd"] * size
        method = "level_prior_too_young"
    else:
        row.update({"method": "unprojectable", "ultimate": None,
                    "ultimate_rate": None})
        return row
    ultimate = min(max(mean, lo), hi)
    q = {
        f"p{p}": min(max(lognormal_quantile(mean, sd, p), lo), hi)
        for p in quantiles
    }
    row.update({
        "method": method,
        "ultimate": round(ultimate, 1),
        "ultimate_rate": ultimate / size if size else None,
        "sd": round(sd, 2),
        "quantiles": {k: round(v, 1) for k, v in q.items()},
        "quantile_rates": {k: (v / size if size else None) for k, v in q.items()},
        "capped_at_cohort_size": mean > hi,
    })
    return row


# ---------------------------------------------------------------------------
# One estimation pass (shared by the live now-cast and every backtest replay)
# ---------------------------------------------------------------------------


def run_nowcast(hists: dict[str, dict[int, int]], sizes: dict[str, int],
                as_of: dt.date, j_max: int, window: int | None,
                quantiles: list[int], min_size: int) -> dict:
    """Build the triangle observable at `as_of` and now-cast every cohort.

    The full arrival histograms are masked by each cohort's development age —
    a cell C_{i,j} exists only once cohort i has aged through week j — which
    is exactly what makes the backtest's pseudo as-of replays honest: the
    estimator never sees an arrival the pseudo date could not have seen.
    """
    ages, triangle, skipped = {}, {}, []
    for cohort, hist in sorted(hists.items()):
        size = sizes.get(cohort, 0)
        if size < min_size:
            skipped.append(cohort)
            continue
        monday = dt.date.fromisoformat(cohort)
        age = dev_age_weeks(monday, as_of)
        if age < 1:
            skipped.append(cohort)
            continue
        # Mask: only development weeks this cohort has fully aged through, and
        # only arrivals observable by as_of (lag capped by the cohort's age).
        capped = {lag: n for lag, n in hist.items() if lag < 7 * min(age, j_max)}
        ages[cohort] = age
        triangle[cohort] = {
            j: cumulative_from_hist(capped, j) for j in range(1, min(age, j_max) + 1)
        }
    factors = estimate_factors(triangle, ages, j_max, window)
    mature_rates = [
        triangle[c].get(j_max, 0) / sizes[c]
        for c in sorted(triangle)
        if ages[c] >= j_max and sizes[c]
    ]
    prior = level_prior(mature_rates)
    rows = [
        nowcast_cohort(c, triangle[c], ages[c], sizes[c], factors, j_max,
                       quantiles, prior)
        for c in sorted(triangle)
    ]
    return {
        "factors": factors,
        "level_prior": prior,
        "cohorts": rows,
        "skipped_cohorts": skipped,
    }


def backtest(hists, sizes, as_of, j_max, window, quantiles, min_size,
             lo_q: int, hi_q: int) -> dict:
    """Replay every fully-developed cohort at every development age, using
    only then-observable data, and score the interval against the realized
    ultimate. Per-horizon empirical coverage next to the nominal interval is
    the Wolffram et al. (2023) discipline: model-based nowcast bands are
    almost always too narrow, and the honest response is to measure yours."""
    per_horizon: dict[int, dict] = {}
    full = run_nowcast(hists, sizes, as_of, j_max, window, quantiles, min_size)
    realized = {
        r["cohort_week"]: r["observed"] for r in full["cohorts"] if r["mature"]
    }
    for cohort, actual in realized.items():
        monday = dt.date.fromisoformat(cohort)
        for k in range(1, j_max):
            pseudo = monday + dt.timedelta(days=6 + 7 * k)
            replay = run_nowcast(hists, sizes, pseudo, j_max, window,
                                 sorted({lo_q, 50, hi_q}), min_size)
            row = next(
                (r for r in replay["cohorts"] if r["cohort_week"] == cohort), None
            )
            if row is None or row.get("mature") or "quantiles" not in row:
                continue
            h = per_horizon.setdefault(
                k, {"n": 0, "covered": 0, "abs_pct_errors": []}
            )
            h["n"] += 1
            q = row["quantiles"]
            if q[f"p{lo_q}"] <= actual <= q[f"p{hi_q}"]:
                h["covered"] += 1
            if actual:
                h["abs_pct_errors"].append(abs(q["p50"] - actual) / actual)
    horizons = []
    for k in sorted(per_horizon):
        h = per_horizon[k]
        horizons.append({
            "age_weeks": k,
            "n": h["n"],
            "empirical_coverage": h["covered"] / h["n"] if h["n"] else None,
            "median_abs_pct_error": (
                statistics.median(h["abs_pct_errors"]) if h["abs_pct_errors"] else None
            ),
        })
    nominal = (hi_q - lo_q) / 100.0
    covered = sum(h["covered"] for h in per_horizon.values())
    n = sum(h["n"] for h in per_horizon.values())
    return {
        "nominal_coverage": nominal,
        "overall_empirical_coverage": covered / n if n else None,
        "replays": n,
        "per_horizon": horizons,
    }


# ---------------------------------------------------------------------------
# Spec / target resolution
# ---------------------------------------------------------------------------


def resolve_target(spec: dict, arg: str | None) -> tuple[str, str, dict]:
    """(stage_key, arrival_column, nowcast_cfg). The target must be declared
    in the spec's `nowcast.stages` — a fast-settling stage is refused, since a
    now-cast of a stage that settles in a day is a worse way to read a
    governed actual (query-metric / stage-timing own that lane)."""
    cfg = spec.get("nowcast")
    if not cfg:
        spine.refuse(
            f"the spec sourced from {spec.get('source_view', '?')} declares no "
            f"`nowcast:` block, so no stage is authorized for now-casting."
        )
    allowed = list(cfg.get("stages") or [])
    stage = arg or cfg.get("default_stage") or (allowed[0] if allowed else None)
    if stage not in allowed:
        spine.refuse(
            f"stage '{stage}' is not a now-cast target. The nowcast block "
            f"authorizes: {', '.join(allowed)} — deep stages with a real "
            f"maturation tail. Fast-settling stages are read on governed "
            f"actuals instead (kattfot:query-metric / kattfot:stage-timing)."
        )
    transitions = {t.get("maturity_stage"): t["to"] for t in spec.get("transitions") or []}
    for s in spec.get("stages") or []:
        if s.get("settle_column"):
            transitions.setdefault(s.get("maturity_stage", s["key"]), s["settle_column"])
    col = transitions.get(stage)
    if not col:
        spine.refuse(
            f"stage '{stage}' has no arrival timestamp on the spine (no "
            f"transition arrives at it and no settle_column names one), so its "
            f"development triangle cannot be built."
        )
    return stage, col, cfg


def development_weeks(spec: dict, stage: str, cfg: dict) -> int:
    """The development horizon J: enough whole weeks to cover the stage's
    settle lag, from the spec's own stage_maturity table (an explicit
    `development_weeks` in the nowcast block overrides). Past J a cohort is
    mature and the governed actual takes over — the now-cast exists only
    inside the maturity blind spot."""
    explicit = (cfg.get("development_weeks") or {}).get(stage) if isinstance(
        cfg.get("development_weeks"), dict) else cfg.get("development_weeks")
    if explicit:
        return int(explicit)
    lag = spine.stage_lag_days(spec, stage)
    if lag is None:
        spine.refuse(
            f"stage '{stage}' has no calibrated lag in stage_maturity.lag_days, "
            f"so its development horizon cannot be derived. Re-run "
            f"calibrate_maturity.py, or set nowcast.development_weeks."
        )
    return max(2, math.ceil((lag + 1) / 7))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    spine.add_common_args(
        ap, group_by_help="single allow-listed dimension to now-cast per group "
        "(per-segment then re-aggregate: a blended curve drifts with mix)"
    )
    ap.add_argument("--stage", default=None,
                    help="target stage (must be in the spec's nowcast.stages)")
    ap.add_argument("--backtest", action="store_true",
                    help="replay fully-developed cohorts at every age and report "
                         "empirical interval coverage next to nominal")
    args = ap.parse_args()

    spec = spine.load_spec(module=args.module, spine=args.spine)
    stage, col, cfg = resolve_target(spec, args.stage)
    dim = args.group_by
    spine.validate_dimension(spec, dim)
    filters = spine.filters_from_args(args.filter)
    spine.validate_filters(spec, filters)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    start, end = spine.parse_time_range(spec, args.time_range, today)
    j_max = development_weeks(spec, stage, cfg)
    window = cfg.get("factor_window_cohorts")
    quantiles = [int(q) for q in (cfg.get("quantiles") or [10, 50, 90])]
    lo_q, hi_q = min(quantiles), max(quantiles)
    min_size = int(cfg.get("min_cohort_size") or 0)

    # Two queries, one triangle. Maturity mode is OFF on purpose: the entire
    # point is to read the immature cohorts the per-stage gates withhold; the
    # gate's job is done here by the FORECAST lane instead.
    where, params = spine.build_where(
        spec, filters, start, end, maturity_mode="off",
        include_legacy=args.include_legacy,
    )
    view = spine.fq_view(spec, engine=args.engine)
    cc = spec["cohort_column"]
    anchor = (spec.get("transitions") or [{}])[0].get("from")
    gsel = f"{spine.col_expr(spec, dim)} AS grp,\n  " if dim else ""
    ggrp = "grp, " if dim else ""
    size_sql = (
        f"SELECT\n  {gsel}{cc} AS cohort_week,\n  COUNT(*) AS n\n"
        f"FROM `{view}`\n{where}\nGROUP BY {ggrp}cohort_week\n"
        f"ORDER BY {ggrp}cohort_week"
    )
    hist_sql = (
        f"SELECT\n  {gsel}{cc} AS cohort_week,\n"
        f"  TIMESTAMP_DIFF({col}, {anchor}, DAY) AS lag_days,\n"
        f"  COUNT(*) AS n\n"
        f"FROM `{view}`\n{where}\n  AND {col} IS NOT NULL\n"
        f"GROUP BY {ggrp}cohort_week, lag_days\n"
        f"ORDER BY {ggrp}cohort_week, lag_days"
    )
    project = spec["source_project"]
    size_rows = spine.run_query(project, size_sql, params, engine=args.engine)
    hist_rows = spine.run_query(project, hist_sql, params, engine=args.engine)
    data = build_group_data(size_rows, hist_rows, grouped=bool(dim))

    groups = []
    for grp, d in sorted(data.items()):
        result = run_nowcast(
            d["hists"], d["sizes"], today, j_max, window, quantiles, min_size
        )
        if args.backtest:
            result["backtest"] = backtest(
                d["hists"], d["sizes"], today, j_max, window, quantiles,
                min_size, lo_q, hi_q,
            )
        result["group"] = None if grp == "__all__" else {dim: grp}
        groups.append(result)

    warnings = spine.fixture_warning(args.engine)
    if spine.active_adapter(args.engine).fixture_backed:
        warnings.append(
            "FIXTURE NOW-CAST: the development factors below are estimated from "
            "the committed fixture generator's delay distribution — the "
            "mechanism is real, the curve describes a seeded random process. "
            "Nothing about a fixture run calibrates this skill for any "
            "business."
        )
    warnings.append(
        f"{LANE} LANE: every projected value on this output is a now-cast, not "
        f"a governed actual. It reconciles to the T2 number once the cohort "
        f"matures (~{spine.stage_lag_days(spec, stage)}d for '{stage}'); quote "
        f"the interval, never the point estimate alone."
    )
    for g in groups:
        unstable = [
            f for f in g["factors"]
            if f["n_cohorts"] >= 2 and f["ratio_max"] and f["ratio_min"]
            and f["f"] > 0 and (f["ratio_max"] - f["ratio_min"]) / f["f"] > 0.10
        ]
        for f in unstable:
            warnings.append(
                f"FACTOR INSTABILITY{' ' + str(g['group']) if g['group'] else ''}: "
                f"development age {f['j']}w factor spans "
                f"{f['ratio_min']:.3f}..{f['ratio_max']:.3f} across cohorts "
                f"(pooled {f['f']:.3f}). Mack's stability assumption is doing "
                f"real work here — check for a regime change (release, carrier, "
                f"returns policy) inside the estimation window before trusting "
                f"the band."
            )
        bt = g.get("backtest")
        if bt and bt["overall_empirical_coverage"] is not None:
            if bt["overall_empirical_coverage"] < bt["nominal_coverage"] - 0.10:
                warnings.append(
                    f"INTERVALS UNDERCOVER{' ' + str(g['group']) if g['group'] else ''}: "
                    f"backtest coverage {bt['overall_empirical_coverage']:.0%} vs "
                    f"nominal {bt['nominal_coverage']:.0%} over {bt['replays']} "
                    f"replays — the documented failure mode of model-based "
                    f"nowcast bands (Wolffram et al. 2023). Widen the read: "
                    f"treat the band as optimistic, or inflate to the "
                    f"backtest's horizon-specific error quantiles."
                )
    warnings += spine.guardrail_warnings(
        spec, start, end, columns=([dim] if dim else []) + list(filters)
    )

    out = {
        "skill": "funnel-nowcast",
        "lane": LANE,
        "accuracy_tier": LANE,  # never T2: the tier IS the lane here
        "module": args.module or spine.DEFAULT_MODULE,
        "spine": args.spine,
        "source_view": spec["source_view"],
        "source_project": project,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "target_stage": stage,
        "arrival_column": col,
        "open_anchor_column": anchor,
        "development_weeks": j_max,
        "factor_window_cohorts": window,
        "quantiles": quantiles,
        "min_cohort_size": min_size,
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
        "as_of": today.isoformat(),
        # The gate is off BY DESIGN: reading the immature cohorts the per-stage
        # gates withhold is this skill's entire purpose, and the FORECAST lane
        # is what replaces the gate's protection. Present (with the shared
        # names) so the artifact contract renders it rather than omitting it.
        "maturity_mode": "off",
        "maturity_cutoff": None,
        "segment_filters": filters,
        "group_by": dim,
        "legacy": spine.legacy_status(spec, args.include_legacy),
        "method": {
            "estimator": "mack_chain_ladder",
            "intervals": "mack_msep_lognormal",
            "assumptions": [
                "development is multiplicative in expectation with factors "
                "depending only on age, not cohort (Mack assumption 1 — "
                "violated by mid-maturation regime changes; see any FACTOR "
                "INSTABILITY warning)",
                "cohorts develop independently (Mack assumption 3 — violated "
                "by calendar-time shocks that hit every open cohort at once)",
                "ultimates are capped at cohort size and floored at observed "
                "arrivals (the funnel ceiling raw chain ladder lacks)",
            ],
        },
        "groups": groups,
        "sql": {"cohort_sizes": size_sql, "arrivals": hist_sql},
        "warnings": warnings,
    }
    out.update(spine.fixture_provenance(args.engine))
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
