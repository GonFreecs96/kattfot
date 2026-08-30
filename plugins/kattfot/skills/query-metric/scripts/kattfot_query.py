#!/usr/bin/env python3
"""kattfot_query.py — the governed query helper behind `kattfot:query-metric`.

Reads a metric definition from a metrics directory, validates the request against
that metric's allow-list, builds a parameterized SQL string, executes it on the
resolved warehouse adapter, and prints a JSON blob for the skill to render into a
reviewable markdown artifact.

Governance, all of it in code rather than in a prompt:

  R1  unregistered metric              -> refuse, with a nearest-match suggestion
  R2  dimension not in allow-list      -> refuse, listing what is allowed
  R3  filter column not in allow-list  -> refuse, listing what is allowed
  R4  accuracy tier T1                 -> refuse (agent layer; catalog is T2-only)
  R5  individual-level request         -> refuse (agent layer)
  R6  PII dimension or filter          -> refuse, no override, even if the metric
                                          explicitly allow-lists the column

R2/R3/R6 run here, behind the agent, so a prompt cannot be talked out of them.
Filter values are always **bound as parameters**, never interpolated, so a
malicious filter value is data rather than SQL.

Execution modes
---------------
`source_mode` in the output is one of:

  `bigquery`     the warehouse, as the individual human user via ADC. Service
                 accounts are refused in `_shared/identity.py`. Missing
                 credentials or libraries are hard errors (exit 2/3/4) — never a
                 silent fall back to synthetic numbers.
  `fixture_sql`  the committed fixture CSVs loaded into an in-memory sqlite
                 database, running the SAME canonical SQL, translated once by
                 `_shared/dialect.py`. This is the default so a fresh clone works.
  `mock_csv`     only when the metric declares `status: dummy`: a Python
                 aggregation over a sample CSV. Kept because it is an
                 *independent* second opinion on the arithmetic — a reference
                 implementation that shares no code with the SQL path, which is
                 what makes it useful as a numeric oracle (tests/test_mock_oracle.py
                 checks it against both a committed golden and the sqlite path).

The last two are both fixture-backed and set `fixture_backed: true` in the output,
which the renderers turn into a MOCK DATA watermark. A fixture number can travel
into a chart, a PDF, or a Confluence page and still announce what it is.

Content discovery (env overrides)
  KATTFOT_METRICS_DIR   default <cwd>/context/kattfot/metrics    (consumer-authored)
  KATTFOT_SAMPLE_DIR    default <repo>/data/kattfot/sample       (fixture CSVs)
  KATTFOT_OUTPUTS_DIR   default <cwd>/outputs/kattfot            (artifacts + audit log)
  KATTFOT_ENGINE        default sqlite                         (sqlite | bigquery)
  KATTFOT_BIGQUERY_PROJECT                                     (no default, ever)

Metric resolution walks the consumer dir first, then the plugin-bundled catalog.
Consumer metrics win on a `name:` collision, so a team can override one metric
without forking the catalog.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import difflib
import json
import os
import re
import sys
from pathlib import Path

# Make `_shared` importable when this script is invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _shared import adapters as adapters_lib  # noqa: E402
from _shared import audit_chain as audit_chain_lib  # noqa: E402
from _shared.exploration import EXPLORABLE_HINT  # noqa: E402
from _shared import guardrails as guardrails_lib  # noqa: E402
from _shared import manifest as manifest_lib  # noqa: E402
from _shared import numfmt as numfmt_lib  # noqa: E402
from _shared import spine as spine_lib  # noqa: E402
from _shared.identity import assert_user_identity  # noqa: E402,F401  (re-exported)
from _shared.pii import is_pii_column, pii_reason  # noqa: E402

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "Missing dependencies. Install with:\n"
        "  pip install pyyaml\n"
        "(BigQuery mode additionally needs google-cloud-bigquery.)\n"
    )
    sys.exit(3)


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)

#: `${VAR}` in a metric's `source_project:`. The bundled catalog ships this
#: rather than a real project id — a template that baked one in would ship
#: somebody else's billing account. Expanded from the environment at query time.
ENV_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

#: Source modes whose numbers come from committed fixtures rather than a
#: warehouse. Both watermark.
FIXTURE_SOURCE_MODES = frozenset({"fixture_sql", "mock_csv"})


def _repo_root() -> Path:
    # this file: <repo>/plugins/kattfot/skills/query-metric/scripts/kattfot_query.py
    #            parents:      5      4      3          2         1        0
    return Path(__file__).resolve().parents[5]


def metrics_dir() -> Path:
    return Path(
        os.environ.get("KATTFOT_METRICS_DIR")
        or Path.cwd() / "context" / "kattfot" / "metrics"
    )


def bundled_metrics_dir() -> Path:
    """Plugin-shipped metrics catalog. Consumers override individual metrics by
    defining the same `name:` in their own metrics dir."""
    return Path(__file__).resolve().parent.parent / "metrics"


def sample_dir() -> Path:
    return Path(
        os.environ.get("KATTFOT_SAMPLE_DIR") or _repo_root() / "data" / "kattfot" / "sample"
    )


# ---------------------------------------------------------------------------
# Metric loading
# ---------------------------------------------------------------------------


def _scan_for_metric(root: Path, metric_name: str) -> Path | None:
    if not root.exists():
        return None
    for path in sorted(root.rglob("*.md")):
        text = path.read_text()
        m = FRONTMATTER_RE.match(text)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if fm.get("name") == metric_name:
            return path
    return None


def available_metric_names(*roots: Path) -> list:
    """Every registered `name:` across the given dirs, sorted and deduped.

    Powers the 'did you mean?' suggestion on a lookup miss — R1 stays a refusal,
    but a useful one.
    """
    names = set()
    for root in roots:
        if not root or not root.exists():
            continue
        for path in root.rglob("*.md"):
            m = FRONTMATTER_RE.match(path.read_text())
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            if fm.get("name"):
                names.add(fm["name"])
    return sorted(names)


def suggest_close_names(metric_name: str, available: list, n: int = 3) -> list:
    """Closest registered names to a miss — typo-tolerant.

    Pure (no I/O) so it is unit-testable. Substring matches are promoted ahead of
    fuzzy ones so 'payment' surfaces every metric whose name contains it.
    """
    if not available:
        return []
    substring = [x for x in available if metric_name and metric_name.lower() in x.lower()]
    fuzzy = difflib.get_close_matches(metric_name, available, n=n, cutoff=0.5)
    ordered = list(dict.fromkeys(substring + fuzzy))  # de-dupe, preserve order
    return ordered[:n]


#: Ambiguity thresholds for the R1 refusal below. A miss is AMBIGUOUS — refused
#: with every candidate named, never resolved by rank — when several registered
#: names contain the query as a substring, or when the two best fuzzy matches
#: are this close: both at least MATCH_FLOOR similar, separated by less than
#: MATCH_MARGIN. Tuned to the failure the text-to-SQL ambiguity literature
#: measures (AmbiQT, EMNLP 2023): models silently commit to ONE reading of an
#: ambiguous ask, and top-1 acceptance of a near-tie is this system's version
#: of that mistake.
AMBIGUITY_MATCH_FLOOR = 0.6
AMBIGUITY_MATCH_MARGIN = 0.08


def ambiguous_candidates(metric_name: str, available: list) -> list:
    """The registered names a miss matches ABOUT EQUALLY WELL, or [] when the
    miss is unambiguous (zero or one plausible reading).

    Two triggers, checked in order:
    - **Generic-term ambiguity**: the query is a substring of several names
      ('payment' — approval rate? decline rate? submit-to-decision?). Every
      such name is a valid reading; returning them all is the point.
    - **Near-tie ambiguity**: the two closest fuzzy matches sit within
      AMBIGUITY_MATCH_MARGIN of each other above AMBIGUITY_MATCH_FLOOR — close
      enough that picking the winner would answer a different question than
      the one asked roughly half the time.

    Pure, so the thresholds are unit-testable against real catalog names.
    """
    if not metric_name or not available:
        return []
    q = metric_name.lower()
    substring = [x for x in available if q in x.lower()]
    if len(substring) >= 2:
        return substring
    scored = sorted(
        ((difflib.SequenceMatcher(None, q, x.lower()).ratio(), x) for x in available),
        reverse=True,
    )
    if (
        len(scored) >= 2
        and scored[0][0] >= AMBIGUITY_MATCH_FLOOR
        and scored[1][0] >= AMBIGUITY_MATCH_FLOOR
        and scored[0][0] - scored[1][0] < AMBIGUITY_MATCH_MARGIN
    ):
        # Only the candidates genuinely tied with the top one — within the same
        # margin of it — belong in the refusal. A distant third would be noise
        # dressed as a rival reading.
        top = scored[0][0]
        return [
            name
            for score, name in scored[:3]
            if top - score < AMBIGUITY_MATCH_MARGIN
        ]
    return []


def find_metric_file(metric_name: str) -> Path:
    """Locate a metric definition by its frontmatter `name`.

    Consumer dir first, then the plugin-bundled catalog; consumer wins on a
    collision.
    """
    consumer_root = metrics_dir()
    bundled_root = bundled_metrics_dir()

    for root in (consumer_root, bundled_root):
        hit = _scan_for_metric(root, metric_name)
        if hit is not None:
            return hit

    available = available_metric_names(consumer_root, bundled_root)
    # Ambiguity outranks helpfulness: when a miss reads several registered
    # metrics about equally well, resolving it by rank would silently answer a
    # different question than the one asked (the top-1-commitment failure the
    # ambiguity literature measures). Name every reading and refuse to choose.
    ambiguous = ambiguous_candidates(metric_name, available)
    if ambiguous:
        raise SystemExit(
            f"ERROR (R1-AMBIGUOUS): '{metric_name}' matches multiple registered "
            f"metrics about equally well: {', '.join(ambiguous)}. Refusing to "
            f"guess between readings — they are different questions with "
            f"different denominators. Re-run with one exact name (or ask the "
            f"requester which they meant); kattfot:catalog shows each metric's "
            f"definition."
        )
    suggestions = suggest_close_names(metric_name, available)
    did_you_mean = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    registered = f" Registered metrics: {', '.join(available)}." if available else ""
    raise SystemExit(
        f"ERROR (R1): no metric definition found with name '{metric_name}' "
        f"in consumer dir ({consumer_root}) or plugin-bundled catalog "
        f"({bundled_root}).{did_you_mean}{registered}{EXPLORABLE_HINT}"
    )


def load_metric(metric_name: str) -> dict:
    path = find_metric_file(metric_name)
    text = path.read_text()
    m = FRONTMATTER_RE.match(text)
    fm = yaml.safe_load(m.group(1)) or {}
    try:
        fm["_path"] = str(path.relative_to(Path.cwd()))
    except ValueError:
        fm["_path"] = str(path)
    # Fingerprint the definition file AS USED, so the artifact can prove which
    # version of the metric produced its number (manifest.metric_def_sha). The
    # whole file is hashed: a change to any field — allow-lists, columns, a
    # caveat the SQL never reads — is a definition change a reviewer should be
    # able to detect.
    fm["_def_sha"] = manifest_lib.sha16(text)
    return fm


# ---------------------------------------------------------------------------
# Allow-list validation (defense in depth — the skill already validated)
# ---------------------------------------------------------------------------


def validate_request(metric: dict, dimensions: list, filters: dict) -> None:
    # R6 first, and hard: a PII column must never be grouped by or filtered on,
    # even if it somehow appears in the metric's own allow-list. Checked before
    # the allow-lists precisely so an allow-list cannot authorize it.
    for col in list(dimensions) + list(filters.keys()):
        if is_pii_column(col):
            raise SystemExit(
                f"ERROR (R6): column '{col}' is personal PII ({pii_reason(col)}) "
                f"and is refused by design — curated views exclude direct PII. "
                f"No override."
            )

    allowed_dims = set(metric.get("allowed_dimensions") or [])
    for d in dimensions:
        if d not in allowed_dims:
            raise SystemExit(
                f"ERROR (R2): dimension '{d}' is not in allowed_dimensions for "
                f"{metric['name']}. Allowed: {sorted(allowed_dims)}{EXPLORABLE_HINT}"
            )
    allowed_filters = set(metric.get("allowed_filters") or [])
    for col in filters:
        if col not in allowed_filters:
            raise SystemExit(
                f"ERROR (R3): filter column '{col}' is not in allowed_filters for "
                f"{metric['name']}. Allowed: {sorted(allowed_filters)}{EXPLORABLE_HINT}"
            )


def validate_tier(metric: dict) -> None:
    """R4: only T2 is served.

    The agent layer checks this too, but a helper invoked directly (a script, a
    scheduled job, another plugin) bypasses the agent entirely — so the tier gate
    has to exist in code as well or it is advisory.
    """
    tier = metric.get("accuracy_tier")
    if tier == "T1":
        raise SystemExit(
            f"ERROR (R4): '{metric['name']}' is T1 (regulator-grade). This "
            f"runtime does not serve T1 numbers; they come from the system of "
            f"record, not from a governed convenience layer. No override."
        )
    if tier == "T3":
        raise SystemExit(
            f"ERROR (R4): '{metric['name']}' is T3 (exploratory). T3 belongs in "
            f"the exploratory lane (kattfot:explore), not in the governed metric "
            f"runtime — promote it to T2 with a reviewed definition first "
            f"(kattfot:promote scaffolds the draft).{EXPLORABLE_HINT}"
        )
    if tier != "T2":
        raise SystemExit(
            f"ERROR (R4): '{metric['name']}' declares accuracy_tier "
            f"{tier!r}; expected one of T1, T2, T3."
        )


# ---------------------------------------------------------------------------
# Time range parsing
# ---------------------------------------------------------------------------


def last_completed_monday(today: dt.date) -> dt.date:
    """Monday of the most recent COMPLETED ISO week (never the partial one)."""
    return today - dt.timedelta(days=today.weekday()) - dt.timedelta(days=7)


def parse_time_range(spec: str, default_lookback_weeks: int, today: dt.date) -> tuple:
    if not spec:
        end = last_completed_monday(today)
        return (end - dt.timedelta(days=(default_lookback_weeks - 1) * 7), end)
    m = re.match(r"^last_(\d+)_weeks$", spec)
    if m:
        end = last_completed_monday(today)
        return (end - dt.timedelta(days=(int(m.group(1)) - 1) * 7), end)
    m = re.match(r"^(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$", spec)
    if m:
        return (dt.date.fromisoformat(m.group(1)), dt.date.fromisoformat(m.group(2)))
    raise SystemExit(
        f"ERROR: time-range '{spec}' not recognized. "
        f"Use 'last_N_weeks' or 'YYYY-MM-DD:YYYY-MM-DD'."
    )


# ---------------------------------------------------------------------------
# Per-metric maturity (the stage-aware cohort cutoff)
# ---------------------------------------------------------------------------
#
# Maturity is a property of a (cohort x stage) PAIR, not of the cohort alone. A
# week that has fully settled its cart-to-checkout-start outcome (minutes) must
# not be withheld for the six weeks a return-window outcome needs. Each metric
# declares its numerator's stage via `maturity_stage:`; the settle lag for that
# stage lives in the funnel spec's `stage_maturity` table. The cutoff is applied
# in the query layer, so the SAME view serves an early-stage and a late-stage
# metric at different freshness.

_STAGE_MATURITY_CACHE: dict[Path, dict] = {}


def funnel_spec_path(metric: dict | None = None) -> Path:
    """Resolve the funnel spec for THIS metric's own module/entity.

    Goes through the module registry so a metric in a second module resolves its
    own spec rather than silently reading the default module's. `KATTFOT_FUNNEL_SPEC`
    wins outright. `metric=None` resolves the default module's default spine.
    """
    env = os.environ.get("KATTFOT_FUNNEL_SPEC")
    if env:
        return Path(env)
    module = (metric or {}).get("module") or spine_lib.DEFAULT_MODULE
    entity = (metric or {}).get("entity")
    spine_name = spine_lib.spine_for_entity(module, entity)
    return spine_lib.spec_path(module=module, spine=spine_name)


def load_stage_maturity(metric: dict | None = None) -> dict:
    """The `stage_maturity` table from this metric's funnel spec (cached by
    resolved path, since different metrics can resolve different specs in one
    process). Returns {} when the spec or the block is absent."""
    p = funnel_spec_path(metric)
    if p in _STAGE_MATURITY_CACHE:
        return _STAGE_MATURITY_CACHE[p]
    table: dict = {}
    if p.exists():
        try:
            spec = yaml.safe_load(p.read_text()) or {}
            table = spec.get("stage_maturity") or {}
        except yaml.YAMLError:
            table = {}
    _STAGE_MATURITY_CACHE[p] = table
    return table


def maturity_cutoff_monday(today: dt.date, lag_days: int) -> dt.date:
    """Latest cohort-week Monday whose ENTIRE week (open..open+6d) is at least
    `lag_days` old — the newest week fully mature for a stage with this lag.

    Whole-week maturity, rather than per-row, avoids a partially-counted boundary
    week silently dragging a governed weekly rate down.
    """
    cap = today - dt.timedelta(days=int(lag_days) + 6)
    return cap - dt.timedelta(days=cap.weekday())


def resolve_maturity(metric: dict, today: dt.date) -> dict | None:
    """This metric's maturity cutoff, or None for no per-metric gate.

    Priority: an explicit `maturity_lag_days` on the metric, else `maturity_stage`
    resolved against the spec's `stage_maturity.lag_days`. A metric declaring a
    `maturity_stage` with no lag in the table is a HARD error — that is a
    governance gap a reviewer must see, not something to silently no-op past.
    """
    lag = metric.get("maturity_lag_days")
    stage = metric.get("maturity_stage")
    table = load_stage_maturity(metric) if stage else {}
    if lag is not None:
        lag_days = int(lag)
        percentile = None
        source = "metric.maturity_lag_days"
    elif stage:
        lags = (table or {}).get("lag_days") or {}
        if stage not in lags:
            raise SystemExit(
                f"ERROR: metric '{metric.get('name')}' declares maturity_stage "
                f"'{stage}' but no lag exists for it in "
                f"{funnel_spec_path(metric)} (stage_maturity.lag_days). Add the "
                f"stage to the spec or correct the metric."
            )
        lag_days = int(lags[stage])
        percentile = (table or {}).get("percentile")
        source = (
            f"stage_maturity[{stage}] p{percentile}" if percentile
            else f"stage_maturity[{stage}]"
        )
    else:
        return None
    cutoff = maturity_cutoff_monday(today, lag_days)
    return {
        "stage": stage,
        "lag_days": lag_days,
        "percentile": percentile,
        "cutoff_week": cutoff.isoformat(),
        "source": source,
        # Surfaced so an artifact can say the lag was hand-authored rather than
        # measured. Implying measurement where there was none is the one thing
        # this machinery must not do.
        "illustrative": bool((table or {}).get("illustrative")),
    }


# ---------------------------------------------------------------------------
# Source mode + project resolution
# ---------------------------------------------------------------------------


def resolve_source_mode(metric: dict, engine: str | None = None) -> tuple:
    """Return (mode, reason).

    `status: dummy` routes to the CSV-stub oracle — that is a property of the
    METRIC (its view is not provisioned anywhere), so it holds on every engine.
    Everything else routes to whatever engine is resolved. There is no silent
    fallback in either direction: an auth or dependency problem on the BigQuery
    path surfaces as an error, never as synthetic numbers.
    """
    if metric.get("status") == "dummy":
        return (
            "mock_csv",
            "metric declares status: dummy — its view is not provisioned, so the "
            "CSV stub is the only source",
        )
    name = adapters_lib.resolve_engine(engine)
    if name == "bigquery":
        return ("bigquery", "KATTFOT_ENGINE=bigquery — running against the live view")
    return (
        "fixture_sql",
        f"KATTFOT_ENGINE={name} — the metric's SQL is running against the committed "
        f"fixture CSVs loaded into in-memory sqlite",
    )


def resolve_project(metric: dict, mode: str = "bigquery") -> str:
    """The project the canonical SQL names.

    A `${VAR}` placeholder in the metric's `source_project:` is expanded from the
    environment. When it cannot be resolved, the behavior depends on the mode: on
    BigQuery that is a hard error (there is nowhere to send the query); on a
    fixture-backed mode the placeholder is kept verbatim in the SQL, which is
    both self-documenting and stable — the fixture result does not depend on it,
    and a stable string keeps the artifact's `query_sha` reproducible.
    """
    raw = metric.get("source_project")
    if raw:
        m = ENV_PLACEHOLDER_RE.match(str(raw).strip())
        if m:
            resolved = os.environ.get(m.group(1))
            if resolved:
                return resolved
            if mode in FIXTURE_SOURCE_MODES:
                return str(raw).strip()
            raise SystemExit(
                f"ERROR: metric '{metric.get('name')}' declares source_project "
                f"{raw!r}, but ${{{m.group(1)}}} is not set in the environment. "
                f"Export it, or set KATTFOT_ENGINE=sqlite to read the committed "
                f"fixtures."
            )
        return str(raw)
    env = os.environ.get("KATTFOT_BIGQUERY_PROJECT")
    if env:
        return env
    if mode in FIXTURE_SOURCE_MODES:
        return "${KATTFOT_BIGQUERY_PROJECT}"
    raise SystemExit(
        "ERROR: no warehouse project resolved. Either add `source_project:` to "
        "the metric YAML or set KATTFOT_BIGQUERY_PROJECT in the environment."
    )


# ---------------------------------------------------------------------------
# SQL builder (parameterized; built in every mode, for provenance)
# ---------------------------------------------------------------------------


def build_sql(
    metric: dict,
    project: str,
    dimensions: list,
    filters: dict,
    maturity_cutoff: dt.date | None = None,
) -> tuple:
    """Return (sql, params). Identifiers are allow-listed; values are bound.

    Always emits **canonical BigQuery dialect**, on every engine. The sqlite path
    translates this once in `_shared/dialect.py` rather than each builder
    branching on the engine — which keeps dialect knowledge out of the governance
    layer and means the artifact's `query_sha` receipt does not depend on where
    the query ran.

    `maturity_cutoff`, when given, adds `{time_col} <= @maturity_cutoff` so the
    result is restricted to cohort weeks that have settled for THIS metric's
    stage. It is bound as a DATE by the executor and deliberately kept out of the
    STRING-bound filter params.
    """
    time_col = metric["time_column"]
    num_col = metric["numerator_column"]
    den_col = metric["denominator_column"]
    metric_name = metric["name"]
    view = metric["source_view"]

    select_cols = [time_col] + list(dimensions)
    group_cols = ", ".join(select_cols)
    select_clause = ", ".join(select_cols)

    where_parts = [f"{time_col} BETWEEN @start_date AND @end_date"]
    if maturity_cutoff is not None:
        where_parts.append(f"{time_col} <= @maturity_cutoff")
    params = {}
    for i, (col, val) in enumerate(filters.items()):
        pname = f"filter_{i}"
        where_parts.append(f"{col} = @{pname}")
        params[pname] = val

    sql = (
        f"SELECT\n"
        f"  {select_clause},\n"
        f"  SAFE_DIVIDE(SUM({num_col}), SUM({den_col})) AS {metric_name},\n"
        f"  SUM({num_col}) AS numerator,\n"
        f"  SUM({den_col}) AS denominator\n"
        f"FROM `{project}.{view}`\n"
        f"WHERE " + " AND ".join(where_parts) + "\n"
        f"GROUP BY {group_cols}\n"
        f"ORDER BY {group_cols}"
    )
    return sql, params


# ---------------------------------------------------------------------------
# Execution paths
# ---------------------------------------------------------------------------


def run_query_adapter(
    metric: dict,
    project: str,
    sql: str,
    params: dict,
    start: dt.date,
    end: dt.date,
    maturity_cutoff: dt.date | None = None,
    engine: str | None = None,
) -> list:
    """Execute the canonical SQL on the resolved adapter.

    Time-range parameters are bound according to the metric's declared
    `time_column_type`, because a DATETIME cohort column needs the inclusive date
    range widened to span whole days or the last day is silently truncated to
    midnight. The adapter binds by Python type from there.
    """
    time_col_type = (metric.get("time_column_type") or "DATE").upper()
    if time_col_type not in {"DATE", "DATETIME", "TIMESTAMP"}:
        raise SystemExit(
            f"ERROR: unsupported time_column_type {time_col_type!r}. "
            f"Use DATE, DATETIME, or TIMESTAMP."
        )
    if time_col_type == "DATE":
        start_param, end_param = start, end
    else:
        start_param = dt.datetime.combine(start, dt.time.min)
        end_param = dt.datetime.combine(end, dt.time.max)

    bound = {"start_date": start_param, "end_date": end_param}
    if maturity_cutoff is not None:
        # The cohort column is a DATE and the maturity cutoff is always a DATE,
        # whatever the metric's own time_column_type.
        bound["maturity_cutoff"] = maturity_cutoff
    bound.update(params)

    adapter = adapters_lib.get_adapter(engine)
    return adapter.run_query(project, sql, bound)


def run_query_mock(
    metric: dict,
    dimensions: list,
    filters: dict,
    start: dt.date,
    end: dt.date,
    maturity_cutoff: dt.date | None = None,
) -> tuple:
    """Aggregate the CSV stub in Python: filter, time-window, group, sum.

    This is the one piece of arithmetic the repo owns outright (elsewhere the
    engine does the SUM/SUM), and it is kept deliberately as a *different idiom*
    from the SQL path so it can serve as an independent numeric oracle. It only
    expresses the SUM/SUM shape — which is exactly why it is not the production
    offline path; see the sqlite adapter's module docstring.
    """
    view_slug = metric["source_view"].split(".", 1)[-1]
    csv_path = sample_dir() / f"{view_slug}.csv"
    if not csv_path.exists():
        raise SystemExit(
            f"ERROR: sample CSV not found at {csv_path}. "
            f"Set KATTFOT_SAMPLE_DIR or generate fixtures with "
            f"`python3 scripts/generate_fixtures.py`."
        )

    time_col = metric["time_column"]
    num_col = metric["numerator_column"]
    den_col = metric["denominator_column"]
    metric_name = metric["name"]

    groups = collections.defaultdict(lambda: {"numerator": 0, "denominator": 0})
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            row_date = dt.date.fromisoformat(row[time_col])
            if not (start <= row_date <= end):
                continue
            if maturity_cutoff is not None and row_date > maturity_cutoff:
                continue
            if not all(row[k] == v for k, v in filters.items()):
                continue
            key = tuple([row[time_col]] + [row[d] for d in dimensions])
            groups[key]["numerator"] += int(row[num_col])
            groups[key]["denominator"] += int(row[den_col])

    rows = []
    for key in sorted(groups.keys()):
        agg = groups[key]
        record = dict(zip([time_col] + list(dimensions), key))
        record["numerator"] = agg["numerator"]
        record["denominator"] = agg["denominator"]
        record[metric_name] = (
            agg["numerator"] / agg["denominator"] if agg["denominator"] else None
        )
        rows.append(record)
    try:
        rel = str(csv_path.relative_to(Path.cwd()))
    except ValueError:
        rel = str(csv_path)
    return rows, rel


# ---------------------------------------------------------------------------
# Output ergonomics (pure helpers — unit-testable, no I/O)
# ---------------------------------------------------------------------------


def resolve_column_label(metric: dict) -> str:
    """Friendly header for the value column in rendered output.

    Falls back to the raw `name` (also the SQL alias) when `column_label` is
    absent. The SQL alias always stays the snake_case `name`, so a metric can read
    'Approval Rate' in a table without touching the parameterized query.
    """
    label = metric.get("column_label")
    return label if isinstance(label, str) and label.strip() else metric["name"]


def volume_summary(rows: list, num_col: str = "numerator", den_col: str = "denominator") -> dict:
    """Total numerator/denominator across the rows, plus the row count.

    Surfaces the volume a ratio sits on, so '19.4%' is never shown without
    revealing whether it rests on n=175 or n=3,401. Non-numeric and None cells are
    skipped rather than raising.
    """
    total_num = 0
    total_den = 0
    for r in rows:
        try:
            total_num += int(r.get(num_col) or 0)
        except (TypeError, ValueError):
            pass
        try:
            total_den += int(r.get(den_col) or 0)
        except (TypeError, ValueError):
            pass
    return {
        "row_count": len(rows),
        "total_numerator": total_num,
        "total_denominator": total_den,
    }


def decorate_display(rows: list, metric_name: str, value_format: str | None) -> list:
    """Attach a `_display` map to each row: the value column formatted per the
    metric's `value_format`, numerator/denominator as counts.

    Formatting happens here — in tested code — rather than being left to the
    agent, so a rendered number cannot drift from a codified rule. Raw values stay
    on the row untouched; `_display` is the reporting-boundary view.
    """
    for r in rows:
        disp = {}
        if metric_name in r:
            disp[metric_name] = numfmt_lib.format_value(r.get(metric_name), value_format)
        for col in ("numerator", "denominator"):
            if col in r:
                disp[col] = numfmt_lib.format_value(r.get(col), "count")
        r["_display"] = disp
    return rows


def post_run_warnings(rows: list, filters: dict) -> list:
    """Warnings derived from the result set itself.

    The important one is a zero-row result. Filters bind as exact string matches,
    so a typo'd value returns nothing — say so, rather than letting it read as
    'the rate is empty'.
    """
    warnings = []
    if not rows:
        if filters:
            applied = ", ".join(f"{k}={v}" for k, v in filters.items())
            warnings.append(
                "Zero rows returned. Filters are matched as exact values — check "
                f"the applied filter values for typos or unseen categories: {applied}."
            )
        else:
            warnings.append(
                "Zero rows returned for this metric over the requested time range. "
                "Widen the range or confirm the view has data for this period."
            )
    return warnings


# ---------------------------------------------------------------------------
# Audit / trace log (append-only; best-effort — never blocks a query)
# ---------------------------------------------------------------------------


def audit_log_path() -> Path:
    env = os.environ.get("KATTFOT_AUDIT_LOG")
    if env:
        return Path(env)
    base = os.environ.get("KATTFOT_OUTPUTS_DIR") or (Path.cwd() / "outputs" / "kattfot")
    return Path(base) / "audit-log.jsonl"


def build_audit_record(result: dict, user_email: str | None) -> dict:
    """The append-only trace entry: who ran what, against what, when — the fields
    a reviewer needs to reconstruct a query without the full artifact.
    Deliberately excludes the row data (a trace, not a copy).

    Attribution is recorded as a delegation pair, per the W3C PROV model of
    agency (prov:SoftwareAgent acting, `actedOnBehalfOf` a person): ``agent``
    is the software that executed the query — this skill, at this plugin
    version — and ``user_email`` is the human principal it acted on behalf of
    (resolved only on the BigQuery path, where identity is asserted; a fixture
    read has no principal and records null). In an *agentic* analytics system
    the acting-software half is the governance-relevant field: "who ran this"
    has two answers, and a record that keeps only the human one cannot
    distinguish an analyst's own query from one an agent ran in their name."""
    return {
        "generated_at": result.get("generated_at"),
        "agent": result.get("agent"),
        "user_email": user_email,
        "metric": result.get("metric_name"),
        "accuracy_tier": result.get("accuracy_tier"),
        "source_mode": result.get("source_mode"),
        "engine": result.get("engine"),
        "fixture_backed": result.get("fixture_backed"),
        "source_view": result.get("source_view"),
        "source_project": result.get("source_project"),
        "dimensions": result.get("dimensions"),
        "filters": result.get("filters"),
        "time_range": result.get("time_range"),
        "row_count": result.get("row_count"),
        "manifest": result.get("manifest"),
    }


def append_audit(record: dict, path: Path | None = None) -> bool:
    """Append one JSON line and advance the log's hash-chain head. Best-effort:
    never raises — an audit-log write failure must not break a query. Returns
    True on success (of the log write; the head sidecar is itself best-effort,
    and a missing head surfaces as CHAIN_HEAD_ABSENT at verify time rather
    than as a failure here)."""
    p = path or audit_log_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, default=str)
        # UTF-8 explicitly: the hash chain folds the line as UTF-8 bytes, so the
        # write must match or a non-ASCII value (an accented market/name) would
        # round-trip to different bytes under a different locale and read as a
        # false mismatch.
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        # Tamper evidence: fold this exact line into the chained head
        # (see _shared/audit_chain.py for what that does and does not claim).
        audit_chain_lib.advance_head(p, line)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_filters(filter_args: list) -> dict:
    out = {}
    for f in filter_args or []:
        if "=" not in f:
            raise SystemExit(f"ERROR: filter '{f}' is not in col=value form")
        k, v = f.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--metric", required=True, help="metric name (matches YAML 'name:')")
    p.add_argument("--dimensions", default="", help="comma-separated dimension columns")
    p.add_argument("--filter", action="append", default=[], help="col=value (repeatable)")
    p.add_argument("--time-range", default="", help="last_N_weeks or YYYY-MM-DD:YYYY-MM-DD")
    p.add_argument("--output-format", default="json", choices=["json"])
    p.add_argument(
        "--engine",
        default=None,
        help="override KATTFOT_ENGINE for this run (sqlite | bigquery)",
    )
    p.add_argument(
        "--today", default="", help="ISO date override (default: the real today)"
    )
    args = p.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    dimensions = [d.strip() for d in args.dimensions.split(",") if d.strip()]
    filters = parse_filters(args.filter)

    metric = load_metric(args.metric)
    validate_tier(metric)
    validate_request(metric, dimensions, filters)

    start, end = parse_time_range(
        args.time_range, metric.get("default_lookback_weeks", 8), today
    )

    maturity = resolve_maturity(metric, today)
    maturity_cutoff = (
        dt.date.fromisoformat(maturity["cutoff_week"]) if maturity else None
    )

    mode, reason = resolve_source_mode(metric, args.engine)
    engine = adapters_lib.resolve_engine(args.engine)
    project = resolve_project(metric, mode)

    sql, params = build_sql(metric, project, dimensions, filters, maturity_cutoff)
    params_for_output = {**params, "start_date": start.isoformat(), "end_date": end.isoformat()}
    if maturity_cutoff is not None:
        params_for_output["maturity_cutoff"] = maturity_cutoff.isoformat()

    warnings = []
    csv_path = None
    adapter = adapters_lib.get_adapter(args.engine)
    fixture_backed = mode in FIXTURE_SOURCE_MODES

    if mode == "mock_csv":
        rows, csv_path = run_query_mock(
            metric, dimensions, filters, start, end, maturity_cutoff
        )
    else:
        rows = run_query_adapter(
            metric, project, sql, params, start, end, maturity_cutoff, args.engine
        )

    if fixture_backed:
        warnings.append(
            f"FIXTURE DATA: source mode is {mode} — {reason}. These numbers come "
            f"from committed synthetic fixtures and describe a seeded random "
            f"process, not any real business. Every downstream chart, dashboard, "
            f"and PDF is watermarked accordingly."
        )
    if maturity is not None:
        illustrative = (
            " The lag itself is hand-authored and illustrative, NOT measured — a "
            "real deployment regenerates the stage_maturity table by measuring "
            "settle time on the spine."
            if maturity.get("illustrative")
            else ""
        )
        warnings.append(
            f"MATURITY: '{metric['name']}' uses a per-metric maturity lag of "
            f"{maturity['lag_days']}d ({maturity['source']}); cohort weeks after "
            f"{maturity['cutoff_week']} are withheld as not-yet-settled for stage "
            f"'{maturity['stage']}'.{illustrative}"
        )
    warnings.extend(post_run_warnings(rows, filters))
    # Warn-level guardrails: feed cutovers the window crosses, plus column/view
    # caveats. Data quality, not a block.
    warnings.extend(
        guardrails_lib.warnings_for(
            metric.get("source_view"),
            list(dimensions) + list(filters.keys()),
            start,
            end,
        )
    )
    summary = volume_summary(rows)
    decorate_display(rows, metric["name"], metric.get("value_format"))

    result = {
        "metric_name": metric["name"],
        "display_name": metric.get("display_name", metric["name"]),
        "column_label": resolve_column_label(metric),
        "accuracy_tier": metric.get("accuracy_tier"),
        "value_format": metric.get("value_format"),
        "source_view": metric.get("source_view"),
        "source_project": project,
        "source_mode": mode,
        "source_mode_reason": reason,
        "engine": engine,
        "fixture_backed": fixture_backed,
        "engine_capabilities": sorted(adapter.capabilities()),
        "query": sql,
        "query_params": params_for_output,
        "csv_path": csv_path,
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
        "maturity": maturity,
        "dimensions": dimensions,
        "filters": filters,
        "rows": rows,
        "row_count": summary["row_count"],
        "total_numerator": summary["total_numerator"],
        "total_denominator": summary["total_denominator"],
        "total_numerator_display": numfmt_lib.format_value(summary["total_numerator"], "count"),
        "total_denominator_display": numfmt_lib.format_value(summary["total_denominator"], "count"),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "metric_definition_path": metric["_path"],
        # The acting software agent (PROV: a SoftwareAgent that ran the
        # Activity). The human principal it acted on behalf of is recorded in
        # the audit trace, where identity is resolved; here the pair names
        # which code, at which version, produced this artifact.
        "agent": {
            "skill": "query-metric",
            "plugin_version": manifest_lib.plugin_version(),
        },
        "warnings": warnings,
    }

    # The receipt: content fingerprints that make the artifact replayable and
    # tamper-evident. `generated_at` is deliberately excluded from every hash
    # (see manifest.py) so a faithful re-run reproduces these exactly.
    result["manifest"] = manifest_lib.build_manifest(
        metric_def_sha=metric["_def_sha"],
        sql=sql,
        params=params_for_output,
        rows=rows,
        metric_name=metric["name"],
        plugin_ver=manifest_lib.plugin_version(),
    )

    # Append-only audit trace (best-effort; disable with KATTFOT_AUDIT_DISABLE=1).
    # The user email is resolved only on the BigQuery path, where identity is
    # already asserted — a fixture read has no identity to record and forcing an
    # ADC lookup for one would be theatre.
    if os.environ.get("KATTFOT_AUDIT_DISABLE") not in ("1", "true", "True"):
        user_email = None
        if mode == "bigquery":
            try:
                from _shared.identity import lookup_email

                user_email, _ = lookup_email()
            except Exception:
                user_email = None
        append_audit(build_audit_record(result, user_email))

    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
