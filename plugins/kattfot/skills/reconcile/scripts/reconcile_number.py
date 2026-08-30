#!/usr/bin/env python3
"""reconcile_number.py — CLI for `kattfot:reconcile`: why does their number differ from ours?

Named `reconcile_number` rather than `reconcile` on purpose. `_shared/spine.py` puts
`_shared/` on `sys.path` (so its stateless siblings are importable bare), which makes
`_shared/reconcile.py` reachable as a top-level `reconcile` — and a second top-level
module of that name would resolve to whichever was imported first. Ambiguous imports
that depend on ordering are the kind of bug that only appears when two things are used
together, which is exactly when a reconciliation is being run.

Someone has a figure that disagrees with the governed one ("the dashboard says
81%, kattfot says 84%"). This recomputes the governed number and then attributes any
material gap to a **named cause**, by re-running the same metric under one tweaked
convention at a time until one reproduces their figure. When none does, it reports
`UNKNOWN` rather than inventing an explanation — an attributed cause that was not
reproduced is worse than an admitted gap, because it ends the investigation.

The mechanism is a valuation engine's equivalence harness: a tolerance check, then
a search over the conventions two correct pipelines most often disagree about.

Division of labour
------------------
The cause logic lives in `_shared/reconcile.py` and is pure: `classify()` is handed
a `run_value(request) -> float | None` callable, so it is unit-tested against a
stub and driven against real data here. This file is only the runner — it turns a
`Request` into a governed metric read on whichever engine is configured, and
returns None for a variant that cannot be computed (a grain swap naming a metric
that is not registered), so the classifier skips it rather than scoring it as a
match.

Every re-run goes through the same `query-metric` helper as a first-class query:
the same allow-lists, the same PII refusal, the same tier gate, the same identity
assertion on the warehouse path. A reconciliation that reached around governance
to "just check something" would be the most dangerous read in the system.

Output: a JSON verdict on stdout for the skill to narrate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS))
sys.path.insert(0, str(SKILLS / "query-metric" / "scripts"))

from _shared import adapters as adapters_lib  # noqa: E402
from _shared import reconcile as rc  # noqa: E402
import kattfot_query as aq  # noqa: E402

try:
    import yaml
except ImportError:
    sys.stderr.write("Missing dependency. Install with:\n  pip install pyyaml\n")
    sys.exit(3)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_filters(items) -> tuple:
    out = []
    for f in items or []:
        if "=" not in f:
            raise SystemExit(f"ERROR: filter '{f}' is not in col=value form")
        k, v = f.split("=", 1)
        out.append((k.strip(), v.strip()))
    return tuple(out)


def make_runner(today: dt.date, engine: str | None):
    """`run_value(request) -> float | None`, backed by the resolved engine.

    Computes the headline rate — SUM(numerator) / SUM(denominator) over the
    request's window and filters — which is the number a dashboard would show.
    Metric definitions are cached because the variant search re-reads the same one
    up to a dozen times, and a metric that cannot be loaded caches as None so a
    grain-swap hypothesis costs one lookup rather than one per attempt.
    """
    cache: dict = {}
    trace: list = []

    def _metric(name: str):
        if name not in cache:
            try:
                cache[name] = aq.load_metric(name)
            except SystemExit:
                cache[name] = None
        return cache[name]

    def run_value(req: rc.Request):
        metric = _metric(req.metric)
        if metric is None:
            trace.append({"metric": req.metric, "skipped": "not a registered metric"})
            return None
        aq.validate_tier(metric)
        aq.validate_request(metric, list(req.dimensions), dict(req.filters))

        mode, _reason = aq.resolve_source_mode(metric, engine)
        project = aq.resolve_project(metric, mode)
        maturity = aq.resolve_maturity(metric, today) if req.mature else None
        cutoff = dt.date.fromisoformat(maturity["cutoff_week"]) if maturity else None

        if mode == "mock_csv":
            rows, _csv = aq.run_query_mock(
                metric, list(req.dimensions), dict(req.filters), req.start, req.end, cutoff
            )
        else:
            sql, params = aq.build_sql(
                metric, project, list(req.dimensions), dict(req.filters), cutoff
            )
            rows = aq.run_query_adapter(
                metric, project, sql, params, req.start, req.end, cutoff, engine
            )
        summary = aq.volume_summary(rows)
        den = summary["total_denominator"]
        value = (summary["total_numerator"] / den) if den else None
        trace.append({
            "metric": req.metric,
            "window": [req.start.isoformat(), req.end.isoformat()],
            "filters": dict(req.filters),
            "mature": req.mature,
            "maturity_cutoff": cutoff.isoformat() if cutoff else None,
            "numerator": summary["total_numerator"],
            "denominator": den,
            "value": value,
        })
        return value

    return run_value, trace


def maybe_definition_drift(their_artifact: str | None, metric: dict) -> dict | None:
    """If 'theirs' is an kattfot artifact, compare its `metric_def_sha` to the
    current one — an exact, offline definition-drift check that needs no re-run.

    This is the cause the numeric search cannot find: two runs of a metric whose
    definition changed in between can agree numerically and still not mean the same
    thing.
    """
    if not their_artifact:
        return None
    p = Path(their_artifact)
    if not p.exists():
        raise SystemExit(f"ERROR: --their-artifact {their_artifact} does not exist")
    m = FRONTMATTER_RE.match(p.read_text())
    fm = (yaml.safe_load(m.group(1)) if m else {}) or {}
    their_sha = (fm.get("manifest") or {}).get("metric_def_sha")
    return rc.definition_drift(their_sha, metric["_def_sha"])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--metric", required=True)
    p.add_argument("--their-value", required=True, type=float,
                   help="the external number, as a rate (e.g. 0.81)")
    p.add_argument("--time-range", required=True,
                   help="OUR window: last_N_weeks or YYYY-MM-DD:YYYY-MM-DD")
    p.add_argument("--their-time-range", default="",
                   help="THEIR window, if known — the single most common cause")
    p.add_argument("--filter", action="append", default=[], help="our filter col=value")
    p.add_argument("--their-filter", action="append", default=[],
                   help="a filter they applied that we did not")
    p.add_argument("--their-metric", default="",
                   help="sibling metric name, for a suspected grain difference")
    p.add_argument("--their-artifact", default="",
                   help="path to an older kattfot artifact, for the definition-drift check")
    p.add_argument("--today", default="", help="ISO date override (fixtures: 2026-08-17)")
    p.add_argument("--engine", default=None, help="override KATTFOT_ENGINE (sqlite | bigquery)")
    p.add_argument("--rtol", type=float, default=0.01,
                   help="relative tolerance for 'these agree' (default 1%%)")
    p.add_argument("--atol", type=float, default=0.0005)
    args = p.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    metric = aq.load_metric(args.metric)
    aq.validate_tier(metric)
    lookback = metric.get("default_lookback_weeks", 8)

    start, end = aq.parse_time_range(args.time_range, lookback, today)
    base = rc.Request(
        metric=args.metric, start=start, end=end, filters=parse_filters(args.filter)
    )

    hints: dict = {"today": today}
    if args.their_time_range:
        hints["their_window"] = aq.parse_time_range(args.their_time_range, lookback, today)
    if args.their_filter:
        hints["their_filters"] = parse_filters(args.their_filter)
    if args.their_metric:
        hints["their_metric"] = args.their_metric

    run_value, trace = make_runner(today, args.engine)
    verdict = rc.classify(
        args.their_value, base, run_value, rtol=args.rtol, atol=args.atol, **hints
    )

    drift = maybe_definition_drift(args.their_artifact, metric)
    if drift is not None:
        verdict["definition_drift"] = drift

    adapter = adapters_lib.get_adapter(args.engine)
    verdict.update({
        "skill": "reconcile",
        "metric": args.metric,
        "our_window": {"start": start.isoformat(), "end": end.isoformat()},
        "our_filters": dict(base.filters),
        "tolerance": {"rtol": args.rtol, "atol": args.atol},
        "engine": adapter.name,
        "fixture_backed": bool(adapter.fixture_backed),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "runs": trace,
        "run_count": len(trace),
    })
    if adapter.fixture_backed:
        verdict.setdefault("warnings", []).append(
            "FIXTURE DATA: every re-run below ran against the committed synthetic "
            "fixtures, so the cause attribution is a demonstration of the mechanism "
            "rather than a statement about any real disagreement."
        )
    print(json.dumps(verdict, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
