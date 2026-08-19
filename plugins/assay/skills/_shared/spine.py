"""Shared governance + SQL helpers for the spine-backed analytics skills.

Used by `funnel-dropoff` and `stage-timing`, which query a module's spine view
(resolved per-module/per-spine via config/modules.yaml; the `checkout` module's
is something like `analytics.checkout_session_spine`) directly to compute things
the SUM/SUM query-metric model cannot (per-stage drop-off + terminal-branch
attribution, and percentile stage timing).

Governance mirrors query-metric:
  * runs as the individual human user via ADC (assert_user_identity); service
    accounts refused.
  * group-by dims and filters are allow-listed in the resolved funnel spec;
    anything outside refuses (R2 / R3 analogues).
  * filter values are bound as query parameters, never interpolated.

Exit codes (consumed by the SKILL.md flows): identical to identity.py —
  2 = ADC missing, 3 = deps missing, 4 = service-account refused, plus
  5 = governance refusal (dim/filter not in allow-list, or bad input).
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

# Reuse the user-identity guard + PII denylist shared across the plugin.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import assert_user_identity  # noqa: E402,F401  (re-exported)
from pii import is_pii_column, pii_reason  # noqa: E402
import guardrails as _guardrails  # noqa: E402

# The adapter registry is imported through the `_shared` PACKAGE name rather than
# bare, unlike its siblings above. This module is reachable both as `spine` and as
# `_shared.spine`, which is harmless for stateless helpers but not for the adapter
# cache: two module identities would mean two caches. Importing it one way from
# everywhere keeps it a single object.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import adapters  # noqa: E402

REFUSAL_EXIT = 5


# ---------------------------------------------------------------------------
# Spec loading
# ---------------------------------------------------------------------------

DEFAULT_MODULE = "checkout"


def _plugin_root() -> Path:
    # this file: <plugin_root>/skills/_shared/spine.py
    return Path(__file__).resolve().parents[2]


def _registry_path() -> Path:
    return _plugin_root() / "config" / "modules.yaml"


def load_registry() -> dict:
    """Parse config/modules.yaml. Every module/spine lookup goes through this,
    so there is exactly one place that knows the on-disk module -> spine ->
    spec-file shape."""
    try:
        import yaml
    except ImportError:
        sys.stderr.write(
            "Missing dependency pyyaml. Install with:\n  pip install pyyaml\n"
        )
        sys.exit(3)
    p = _registry_path()
    if not p.exists():
        sys.stderr.write(f"Module registry not found at {p}.\n")
        sys.exit(1)
    with open(p) as f:
        return yaml.safe_load(f) or {}


def resolve_spine(module: str, spine: str | None, registry: dict | None = None) -> dict:
    """Resolve a (module, spine) pair to its registry entry (the dict carrying
    `spec`, `primary_entity`, `skills`, `excluded`, etc.). `spine=None` resolves
    to whichever spine the module marks `default: true`."""
    registry = registry if registry is not None else load_registry()
    modules = registry.get("modules") or {}
    mod = modules.get(module)
    if mod is None:
        refuse(f"Unknown module '{module}'. Known: {', '.join(sorted(modules))}")
    spines = mod.get("spines") or {}
    if spine is None:
        spine = next((k for k, v in spines.items() if v.get("default")), None)
        if spine is None:
            refuse(f"Module '{module}' has no default spine and none was specified.")
    cfg = spines.get(spine)
    if cfg is None:
        refuse(
            f"Unknown spine '{spine}' for module '{module}'. "
            f"Known: {', '.join(sorted(spines))}"
        )
    return cfg


def spine_for_entity(module: str, entity: str | None, registry: dict | None = None) -> str | None:
    """Spine name within `module` whose `primary_entity` == `entity`, or None
    if `entity` is falsy or no spine matches. Metrics declare `entity:`, not a
    spine name directly, so this is the lookup a metric's own frontmatter
    needs — mirrors `resolve_spine()`'s registry access, keyed the other way."""
    if not entity:
        return None
    registry = registry if registry is not None else load_registry()
    mod = (registry.get("modules") or {}).get(module) or {}
    spines = mod.get("spines") or {}
    return next((name for name, cfg in spines.items() if cfg.get("primary_entity") == entity), None)


def spec_path(module: str | None = None, spine: str | None = None) -> Path:
    """Resolve the funnel-spec YAML for a module/spine. `ASSAY_FUNNEL_SPEC`
    (test override) wins outright. Bare `spec_path()` resolves to the default
    module's default spine, i.e. `checkout` -> `config/checkout-funnel-spec.yaml`."""
    env = os.environ.get("ASSAY_FUNNEL_SPEC")
    if env:
        return Path(env)
    cfg = resolve_spine(module or DEFAULT_MODULE, spine)
    return _plugin_root() / "config" / cfg["spec"]


def load_spec(module: str | None = None, spine: str | None = None) -> dict:
    try:
        import yaml
    except ImportError:
        sys.stderr.write(
            "Missing dependency pyyaml. Install with:\n  pip install pyyaml\n"
        )
        sys.exit(3)
    p = spec_path(module, spine)
    if not p.exists():
        sys.stderr.write(
            f"Funnel spec not found at {p}. Set ASSAY_FUNNEL_SPEC or restore "
            f"the spec file the module registry points at.\n"
        )
        sys.exit(1)
    with open(p) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------

def refuse(message: str) -> None:
    """Emit a doctrine-style refusal and exit. No workaround proposed."""
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(REFUSAL_EXIT)


def validate_dimension(spec: dict, dim: str | None) -> None:
    if dim is None:
        return
    if is_pii_column(dim):
        refuse(
            f"R6: dimension '{dim}' is personal PII ({pii_reason(dim)}) and is "
            f"refused by design. No override."
        )
    allowed = spec.get("allowed_dimensions") or []
    if dim not in allowed:
        refuse(
            f"R2: dimension '{dim}' isn't in the allow-list. "
            f"Allowed: {', '.join(allowed)}."
        )


def validate_filters(spec: dict, filters: dict) -> None:
    allowed = spec.get("allowed_filters") or []
    for col in filters:
        if is_pii_column(col):
            refuse(
                f"R6: filter on '{col}' is personal PII ({pii_reason(col)}) and "
                f"is refused by design. No override."
            )
        if col not in allowed:
            refuse(
                f"R3: filter on '{col}' isn't allowed. "
                f"Allowed: {', '.join(allowed)}."
            )


def col_expr(spec: dict, col: str) -> str:
    """SQL expression for a dimension/filter column, casting where required.

    Emits canonical BigQuery (`AS STRING`) unconditionally — `_shared/dialect.py`
    rewrites it to `AS TEXT` for sqlite. Branching on the engine here instead
    would put dialect knowledge in a governance module and make the generated SQL
    engine-dependent; see `fq_view` for the same reasoning.
    """
    if col in (spec.get("string_cast") or []):
        return f"CAST({col} AS STRING)"
    return col


def guardrail_warnings(spec: dict, start, end, columns=None) -> list:
    """Warn-level guardrails (feed-cutover discontinuities + view/column notes)
    for a spine query window. Thin wrapper over the shared guardrails layer so
    each spine skill can add `out["warnings"] = spine.guardrail_warnings(...)`.

    Pass the columns the query actually referenced — the group-by dimension and
    the filter keys — or column-scoped notes cannot fire and the caveat layer
    only ever surfaces feed cutovers.
    """
    return _guardrails.warnings_for(spec.get("source_view"), columns or [], start, end)


def legacy_status(spec: dict, include_legacy: bool) -> dict:
    """What this read did about the spine's legacy cohort, in a shape an artifact
    header can print without knowing whether one is declared.

    Three states, and the distinction matters: a spine with a legacy cohort that
    was excluded, one whose legacy cohort was deliberately included, and one that
    declares none at all. Collapsing the third into "included" would report a
    legacy cohort as present on every spine that has none — a claim about the
    data that happens to be false.
    """
    col = spec.get("legacy_exclude_column")
    val = spec.get("legacy_exclude_value")
    if not col or val is None:
        return {
            "declared": False,
            "excluded": False,
            "detail": "this spine declares no legacy cohort, so nothing was excluded",
        }
    excluded = not include_legacy
    return {
        "declared": True,
        "excluded": excluded,
        "column": col,
        "value": val,
        "detail": (
            f"legacy cohort {col}={val!r} "
            + ("excluded (the default)" if excluded else "INCLUDED via --include-legacy")
        ),
    }


def fixture_provenance(engine: str | None = None) -> dict:
    """Engine + fixture-backing stamp for a spine artifact.

    Spine skills carry no metric definition and so no `manifest`, which makes
    this the only place a reader learns whether the numbers came from a warehouse
    or from committed synthetic CSVs. `fixture_backed` is what every renderer
    turns into the MOCK DATA watermark, so it has to reach the output.
    """
    adapter = active_adapter(engine)
    return {
        "engine": adapter.name,
        "fixture_backed": bool(adapter.fixture_backed),
        "engine_capabilities": sorted(adapter.capabilities()),
    }


def fixture_warning(engine: str | None = None) -> list:
    """The FIXTURE DATA warning line, or nothing on a warehouse engine."""
    adapter = active_adapter(engine)
    if not adapter.fixture_backed:
        return []
    return [
        "FIXTURE DATA: this read ran on the "
        f"{adapter.name} adapter against the committed synthetic fixtures. The "
        "numbers describe a seeded random process, not any real business; every "
        "downstream chart, dashboard, and PDF is watermarked accordingly."
    ]


def maturity_warning(spec: dict, meta: dict) -> list:
    """The MATURITY line for a spine artifact, mirroring query-metric's.

    Says which cohorts were withheld and — when the spec admits it — that the
    lags were hand-authored rather than measured. Implying measurement where
    there was none is the one thing this machinery must not do.
    """
    if meta.get("mode") != "stage":
        return [
            f"MATURITY: mode is {meta.get('mode')!r}, so no per-stage cutoff was "
            f"applied. Deep stages of recent cohorts have not settled and will "
            f"read low."
        ]
    table = stage_maturity_table(spec)
    illustrative = (
        " The lags themselves are hand-authored and illustrative, NOT measured — a "
        "real deployment regenerates the stage_maturity table by measuring "
        "open-anchored settle time on the spine."
        if table.get("illustrative")
        else ""
    )
    return [
        "MATURITY: each stage is gated to the cohort weeks mature FOR THAT STAGE; "
        f"the deepest fully-settled cohort is {meta.get('deep_cohort_through')}, "
        f"which is what the terminal-branch mix and any single-cohort funnel use."
        + illustrative
    ]


# ---------------------------------------------------------------------------
# Timing transitions (shared by stage-timing and segment-compare)
# ---------------------------------------------------------------------------

#: BigQuery `TIMESTAMP_DIFF` units the specs may name. `dialect.py` rewrites the
#: call for sqlite, scaling by the same unit table.
UNIT_SQL = {"minute": "MINUTE", "hour": "HOUR", "day": "DAY", "second": "SECOND"}


def select_transitions(spec: dict, keys: list | None) -> list:
    """The spec's `transitions`, or the named subset, refusing an unknown key.

    Shared rather than duplicated in the two skills that read timing, because a
    divergence here would mean two different answers to "which transitions
    exist" for the same spine.
    """
    by_key = {t["key"]: t for t in spec.get("transitions") or []}
    if not by_key:
        refuse(
            f"the spec sourced from {spec.get('source_view', '?')} declares no "
            f"`transitions:`, so it cannot serve stage timing."
        )
    if not keys:
        return list(spec["transitions"])
    out = []
    for k in keys:
        if k not in by_key:
            refuse(f"Unknown transition '{k}'. Available: {', '.join(by_key)}.")
        out.append(by_key[k])
    return out


def parse_percentiles(raw: str) -> list:
    pcts = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            p = int(part)
        except ValueError:
            refuse(f"Bad --percentiles '{raw}'. Use comma-separated 0-100, e.g. 50,90,99.")
        if not 0 <= p <= 100:
            refuse(f"Percentile {p} is outside 0-100.")
        pcts.append(p)
    if not pcts:
        refuse(f"Bad --percentiles '{raw}'. Use comma-separated 0-100, e.g. 50,90,99.")
    return pcts


def gated_diff(spec: dict, transition: dict, cutoffs: dict) -> str:
    """`to - from` duration, NULLed for cohorts not yet mature for the
    transition's ARRIVAL stage.

    Percentile and mean aggregates drop NULLs, so this excludes recent cohorts
    whose slow members have not arrived yet — without a separate WHERE, and
    therefore without changing the denominator of anything else in the same
    query. Gating on arrival rather than departure is the point: a transition
    read over cohorts that have not finished arriving is biased *fast*, which
    looks like an improvement.
    """
    unit = UNIT_SQL.get(transition.get("unit"), "MINUTE")
    diff = f"TIMESTAMP_DIFF({transition['to']}, {transition['from']}, {unit})"
    cut = cutoffs.get(transition["key"])
    if cut is not None:
        return f"IF({spec['cohort_column']} <= @cut_{transition['key']}, {diff}, NULL)"
    return diff


def percentile_params(where_params: dict, transition: dict, cutoffs: dict) -> dict:
    """Bind values for ONE transition's percentile query.

    Scoped to that transition's own cutoff rather than passing every stage's,
    because `adapter.percentiles()` builds its own SQL and a parameter the SQL
    never names is at best noise in the artifact's provenance and at worst an
    error on a stricter driver.
    """
    params = dict(where_params)
    cut = cutoffs.get(transition["key"])
    if cut is not None:
        params[f"cut_{transition['key']}"] = cut
    return params


# ---------------------------------------------------------------------------
# Shared CLI surface
# ---------------------------------------------------------------------------
# Four skills take the same governance-relevant arguments. Declared once so they
# cannot drift into meaning slightly different things per skill — which is how a
# `--maturity` flag ends up defaulting differently in two places.


def add_common_args(ap, *, group_by_help: str | None = None) -> None:
    ap.add_argument(
        "--module", default=None,
        help=f"registry module in config/modules.yaml (default: {DEFAULT_MODULE})",
    )
    ap.add_argument(
        "--spine", default=None,
        help="spine within --module (default: that module's default spine). "
             "ASSAY_FUNNEL_SPEC overrides both.",
    )
    if group_by_help:
        ap.add_argument("--group-by", default=None, help=group_by_help)
    ap.add_argument(
        "--filter", action="append", default=[],
        help="allow-listed col=value, repeatable; values are bound, never interpolated",
    )
    ap.add_argument(
        "--time-range", default=None,
        help="last_N_weeks | YYYY-MM-DD:YYYY-MM-DD (default: the spec's lookback)",
    )
    ap.add_argument(
        "--today", default=None,
        help="ISO date override, for a deterministic window (fixtures: 2026-08-17)",
    )
    ap.add_argument(
        "--include-legacy", action="store_true",
        help="include the spine's declared legacy cohort (excluded by default)",
    )
    ap.add_argument(
        "--maturity", choices=list(MATURITY_MODES), default=None,
        help="stage (per-stage cutoffs, the default) | flag (whole-funnel legacy "
             "gate) | off (no gate at all)",
    )
    ap.add_argument("--no-mature", action="store_true", help="alias for --maturity off")
    ap.add_argument("--engine", default=None, help="override ASSAY_ENGINE (sqlite | bigquery)")
    ap.add_argument("--output-format", default="json", choices=["json"])


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

def last_completed_monday(today: dt.date) -> dt.date:
    return today - dt.timedelta(days=today.weekday() + 7)


def parse_time_range(spec: dict, spec_arg: str | None, today: dt.date) -> tuple[dt.date, dt.date]:
    """Return (start_cohort_week, end_cohort_week) as DATEs.

    Accepts: None (default lookback), 'last_N_weeks', or 'YYYY-MM-DD:YYYY-MM-DD'.
    The end is clamped to the last completed ISO week.
    """
    end = last_completed_monday(today)
    if not spec_arg:
        weeks = int(spec.get("defaults", {}).get("lookback_weeks", 26))
        return end - dt.timedelta(weeks=weeks - 1), end
    spec_arg = spec_arg.strip()
    if spec_arg.startswith("last_") and spec_arg.endswith("_weeks"):
        try:
            weeks = int(spec_arg[len("last_"):-len("_weeks")])
        except ValueError:
            refuse(f"Bad time range '{spec_arg}'. Use last_N_weeks or YYYY-MM-DD:YYYY-MM-DD.")
        return end - dt.timedelta(weeks=weeks - 1), end
    if ":" in spec_arg:
        a, b = spec_arg.split(":", 1)
        try:
            start = dt.date.fromisoformat(a)
            end = dt.date.fromisoformat(b)
        except ValueError:
            refuse(f"Bad date range '{spec_arg}'. Use YYYY-MM-DD:YYYY-MM-DD.")
        return start, end
    refuse(f"Unrecognized time range '{spec_arg}'.")


# ---------------------------------------------------------------------------
# Per-stage maturity (stage-aware cohort cutoffs)
# ---------------------------------------------------------------------------
# A cohort is mature for a (stage) pair, not the cohort alone: a week that has
# settled its cart->checkout-started outcome (minutes) must NOT be withheld for
# the weeks a cart->delivered outcome needs. These helpers mirror query-metric:
# each spine stage / timing transition declares a `maturity_stage`, whose
# calibrated open-anchored settle lag lives in the funnel spec's
# `stage_maturity` table (regenerate via
# stage-timing/scripts/calibrate_maturity.py). Each stage column is then gated
# to the cohort weeks mature FOR THAT STAGE, so an early-funnel conversion stays
# fresh while a deep one withholds.

MATURITY_MODES = ("stage", "flag", "off")


def stage_maturity_table(spec: dict) -> dict:
    return spec.get("stage_maturity") or {}


def maturity_cutoff_monday(today: dt.date, lag_days: int) -> dt.date:
    """Latest cohort-week Monday whose ENTIRE week (open..open+6d) is at least
    `lag_days` old — the newest week fully mature for a stage with this lag.
    Whole-week maturity avoids partially-counted boundary weeks in a rate."""
    cap = today - dt.timedelta(days=int(lag_days) + 6)
    return cap - dt.timedelta(days=cap.weekday())


def stage_lag_days(spec: dict, maturity_stage: str | None) -> int | None:
    """Calibrated lag for a `maturity_stage`. 'started' is the open anchor (0);
    any other stage must exist in the calibrated table or returns None (the
    caller decides whether that's a hard error or a no-gate fallback)."""
    if maturity_stage in (None, "started"):
        return 0 if maturity_stage == "started" else None
    lags = stage_maturity_table(spec).get("lag_days") or {}
    v = lags.get(maturity_stage)
    return int(v) if v is not None else None


def cutoff_for_stage(spec: dict, maturity_stage: str | None, today: dt.date) -> dt.date | None:
    lag = stage_lag_days(spec, maturity_stage)
    return None if lag is None else maturity_cutoff_monday(today, lag)


# ---------------------------------------------------------------------------
# Per-group maturity overrides (decision_path-sensitive stages)
# ---------------------------------------------------------------------------
# Some stages settle at wildly different speeds depending on a dimension the
# book-wide lag averages away. A payment decision, for instance, settles in
# seconds for instant card authorization but in days for a bank transfer or a
# manual-review queue; a single lag is swamped by the fast majority and is badly
# wrong for the slow segments. calibrate_maturity.py's
# calibrate_group_overrides() derives a per-group lag for those segments and
# writes it to stage_maturity.lag_days_overrides.<stage> as
# {"by": <dimension>, "values": {<value>: lag_days}}. A group value with no
# entry keeps the book-wide lag_days default -- this is additive, never a
# regression versus the pre-override behavior.

def stage_lag_overrides(spec: dict, maturity_stage: str | None) -> dict | None:
    """{"by": dim, "values": {value: lag_days}} for a maturity_stage, or None
    if it has no per-group overrides."""
    if maturity_stage is None:
        return None
    overrides = stage_maturity_table(spec).get("lag_days_overrides") or {}
    cfg = overrides.get(maturity_stage)
    if not cfg or not cfg.get("values"):
        return None
    return cfg


def group_cutoffs_for_stage(
    spec: dict, maturity_stage: str | None, group_dim: str | None, today: dt.date
) -> dict[str, dt.date] | None:
    """Per-value maturity cutoffs for a stage's overrides, when the query's
    OWN group-by/filter dimension (`group_dim`) matches the override's `by`.
    Returns None when there's no override, or the active dimension doesn't
    match it (the caller then falls back to the single default cutoff)."""
    cfg = stage_lag_overrides(spec, maturity_stage)
    if not cfg or cfg.get("by") != group_dim:
        return None
    return {value: maturity_cutoff_monday(today, lag) for value, lag in cfg["values"].items()}


def group_windows_for_stage(
    spec: dict, maturity_stage: str | None, group_dim: str | None, today: dt.date,
    recent_weeks: int, baseline_weeks: int,
) -> dict[str, dict] | None:
    """Per-value {end, recent_start, baseline_start, lag_days} windows for a
    stage's overrides, when `group_dim` matches the override's `by` -- mirrors
    funnel_pulse.stage_windows()'s per-stage window shape, but one per
    override value. None when there's no matching override (caller keeps the
    single shared window)."""
    cfg = stage_lag_overrides(spec, maturity_stage)
    if not cfg or cfg.get("by") != group_dim:
        return None
    out = {}
    for value, lag in cfg["values"].items():
        end = maturity_cutoff_monday(today, lag)
        rs = end - dt.timedelta(weeks=recent_weeks - 1)
        bs = rs - dt.timedelta(weeks=baseline_weeks)
        out[value] = {"end": end, "recent_start": rs, "baseline_start": bs, "lag_days": lag}
    return out


def windowed_count_expr(
    flag_or_true: str, cc: str, key: str, default_window: dict, group_expr: str | None,
    group_windows: dict[str, dict] | None, which: str,
) -> str:
    """COUNTIF condition for a stage's recent/baseline window (`which` is
    "recent" or "baseline"), falling back to a per-group CASE when
    `group_windows` has overrides for this stage. `default_window` carries
    this stage's plain {end, recent_start, baseline_start} (bound as
    @rs_{key}/@end_{key}); override windows are bound as @rs_{key}_ov{i}/
    @end_{key}_ov{i} by windowed_count_params()."""
    if which == "recent":
        default_cond = f"{cc} >= @rs_{key} AND {cc} <= @end_{key}"
    else:
        default_cond = f"{cc} >= @bs_{key} AND {cc} < @rs_{key}"
    if not group_windows:
        return f"{flag_or_true} AND {default_cond}"
    when_clauses = []
    for i, value in enumerate(group_windows):
        if which == "recent":
            cond = f"{cc} >= @rs_{key}_ov{i} AND {cc} <= @end_{key}_ov{i}"
        else:
            cond = f"{cc} >= @bs_{key}_ov{i} AND {cc} < @rs_{key}_ov{i}"
        when_clauses.append(f"WHEN {group_expr} = @grp_{key}_ov{i} THEN ({cond})")
    case_sql = "CASE " + " ".join(when_clauses) + f" ELSE ({default_cond}) END"
    return f"{flag_or_true} AND ({case_sql})"


def windowed_count_params(key: str, group_windows: dict[str, dict] | None) -> dict:
    """Bind params for windowed_count_expr()'s override WHEN clauses."""
    if not group_windows:
        return {}
    params: dict = {}
    for i, (value, w) in enumerate(group_windows.items()):
        params[f"grp_{key}_ov{i}"] = value
        params[f"rs_{key}_ov{i}"] = w["recent_start"]
        params[f"end_{key}_ov{i}"] = w["end"]
        params[f"bs_{key}_ov{i}"] = w["baseline_start"]
    return params


def resolve_stage_cutoffs(
    spec: dict, stage_defs: list, today: dt.date, *, mode: str
) -> tuple[dict, dict, dict]:
    """Resolve per-stage maturity cutoffs for a set of stages/transitions.

    `stage_defs`: list of dicts each carrying a `key` and (preferably) a
    `maturity_stage` (defaults to `key`). Used for both the funnel `stages` and
    the timing `transitions`.

    Returns (cutoffs, params, meta):
      cutoffs : {key: dt.date | None}     — None means "no per-stage cap" (the
                column is counted over the full window). Always None in flag/off.
      params  : {f"cut_{key}": date, ..., "cut_deep": date}  — DATE bind values.
      meta    : {"mode", "percentile", "calibrated_at", "basis",
                 "deep_cohort_through", "per_stage": {key: {...}}}

    In `stage` mode a stage that declares a `maturity_stage` with no calibrated
    lag is a hard error (a governance gap a reviewer must see), matching
    query-metric's resolve_maturity.
    """
    table = stage_maturity_table(spec)
    meta: dict = {
        "mode": mode,
        "percentile": table.get("percentile"),
        "calibrated_at": table.get("calibrated_at"),
        "basis": table.get("basis"),
        "per_stage": {},
        "deep_cohort_through": None,
    }
    cutoffs: dict = {}
    params: dict = {}
    if mode != "stage":
        for sd in stage_defs:
            cutoffs[sd["key"]] = None
        return cutoffs, params, meta

    deepest: dt.date | None = None
    for sd in stage_defs:
        key = sd["key"]
        mstage = sd.get("maturity_stage", key)
        lag = stage_lag_days(spec, mstage)
        if lag is None:
            refuse(
                f"metric/stage '{key}' declares maturity_stage '{mstage}' but no "
                f"calibrated lag exists for it in the spec sourced from "
                f"{spec.get('source_view', '?')} (stage_maturity.lag_days). "
                f"Re-run calibrate_maturity.py --module/--spine for that spine, or fix the spec."
            )
        cut = maturity_cutoff_monday(today, lag)
        cutoffs[key] = cut
        params[f"cut_{key}"] = cut
        meta["per_stage"][key] = {
            "maturity_stage": mstage,
            "lag_days": lag,
            "cutoff_week": cut.isoformat(),
        }
        if deepest is None or cut < deepest:   # the most-conservative (oldest) cut
            deepest = cut
    if deepest is not None:
        params["cut_deep"] = deepest
        meta["deep_cohort_through"] = deepest.isoformat()
    return cutoffs, params, meta


def gate(cohort_col: str, key: str, cutoffs: dict) -> str:
    """SQL fragment capping a COUNTIF/IF to a stage's mature cohort window.
    Returns '' when there is no cap (flag/off mode, or an ungated stage)."""
    cut = cutoffs.get(key)
    return f" AND {cohort_col} <= @cut_{key}" if cut is not None else ""


def deep_gate(cohort_col: str, params: dict) -> str:
    """SQL fragment capping to the single deepest (fully-settled) cohort window,
    used for the terminal-branch mix and the coherent funnel chart."""
    return f" AND {cohort_col} <= @cut_deep" if "cut_deep" in params else ""


def resolve_maturity_mode(args, spec: dict) -> str:
    """Translate CLI flags + spec defaults into a maturity mode.
    --no-mature wins (off); else --maturity if given; else the spec default
    (mature_only:true -> stage, false -> off)."""
    if getattr(args, "no_mature", False):
        return "off"
    m = getattr(args, "maturity", None)
    if m:
        return m
    return "stage" if spec.get("defaults", {}).get("mature_only", True) else "off"


# ---------------------------------------------------------------------------
# WHERE builder
# ---------------------------------------------------------------------------

def build_where(
    spec: dict,
    filters: dict,
    start: dt.date,
    end: dt.date,
    *,
    maturity_mode: str = "stage",
    include_legacy: bool,
) -> tuple[str, dict]:
    """Return (where_sql, params). Identifiers are allow-listed; values bound.

    `maturity_mode`:
      "stage" — no whole-funnel gate here; per-stage caps are applied in the
                skill's SELECT via gate()/deep_gate() (the stage-aware default).
      "flag"  — the whole-funnel maturity-flag gate named by
                `spec['maturity_flag']` (a boolean column on the spine view).
      "off"   — no maturity gate at all.
    """
    cohort = spec["cohort_column"]
    parts = [f"{cohort} BETWEEN @start_date AND @end_date"]
    params: dict = {"start_date": start, "end_date": end}

    if maturity_mode == "flag":
        parts.append(f"{spec['maturity_flag']}")
    # A spine may declare one legacy cohort that every default read excludes —
    # a retired checkout version, a deprecated channel — as
    # `legacy_exclude_column` + `legacy_exclude_value` in its spec. BOTH keys
    # must be present or the clause is omitted entirely: `col != NULL` evaluates
    # to NULL, never TRUE, so binding a missing value would silently zero every
    # row rather than exclude nothing. A spine with no legacy cohort (or one
    # whose column is sparse/NULL-heavy) simply leaves the keys out.
    legacy_col = spec.get("legacy_exclude_column")
    legacy_val = spec.get("legacy_exclude_value")
    if not include_legacy and legacy_col and legacy_val is not None:
        parts.append(f"{col_expr(spec, legacy_col)} != @legacy_excluded")
        params["legacy_excluded"] = legacy_val

    for i, (col, val) in enumerate(filters.items()):
        pname = f"filter_{i}"
        parts.append(f"{col_expr(spec, col)} = @{pname}")
        params[pname] = val

    return "WHERE " + "\n  AND ".join(parts), params


# ---------------------------------------------------------------------------
# Execution (the single warehouse contact point, behind an adapter)
# ---------------------------------------------------------------------------
# Everything above this line is pure string building over ONE canonical dialect
# (BigQuery). This is the only function in the plugin that reaches a database,
# which is why swapping engines is a dispatch rather than a rewrite: the
# `bigquery` adapter asserts per-user identity and binds typed parameters; the
# `sqlite` adapter loads the committed fixture CSVs into an in-memory database,
# translates the canonical SQL once (`_shared/dialect.py`), and runs it. Engine
# comes from `ASSAY_ENGINE`, defaulting to sqlite so a fresh clone works.

def run_query(project: str, sql: str, params: dict, *, engine: str | None = None) -> list[dict]:
    """Execute `sql` with bound `params` on the resolved engine.

    Signature is unchanged from the pre-adapter version (`engine` is keyword-only
    and optional), so every existing caller is untouched.
    """
    return adapters.get_adapter(engine).run_query(project, sql, params)


def active_adapter(engine: str | None = None):
    """The adapter a query would run on. Skills use this to stamp provenance —
    chiefly `fixture_backed`, which becomes the renderers' `mock: true`
    watermark — and to check capabilities (percentile push-down) before building
    a query."""
    return adapters.get_adapter(engine)


def fq_view(spec: dict, *, engine: str | None = None) -> str:
    """The spine view's reference in **canonical** (BigQuery) SQL.

    Adapter-resolved, but both adapters intentionally return the same
    `project.dataset.view` form. sqlite has no such namespace, and the obvious
    fix — have this return a bare table name under sqlite — would make the
    generated SQL, and therefore the artifact's `query_sha` receipt, depend on
    which engine served it. Instead the SQL stays dialect-stable and the
    flattening to a physical table name happens in exactly one place,
    `dialect.translate_sql`. Ask the adapter for `table_name()` when you need the
    physical relation (the fixture loader does).
    """
    return adapters.get_adapter(engine).qualify_view(
        spec["source_project"], spec["source_view"]
    )


def filters_from_args(filter_args: list[str]) -> dict:
    out: dict = {}
    for f in filter_args or []:
        if "=" not in f:
            refuse(f"Bad --filter '{f}'. Use col=value.")
        k, v = f.split("=", 1)
        out[k.strip()] = v.strip()
    return out
