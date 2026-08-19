"""reconcile.py — explain *why* a governed number differs from an external one.

This mirrors a valuation engine's equivalence harness: a ``diff`` that says
whether two numbers agree within tolerance, and a ``classify`` that attributes a
material gap to a **cause** by recomputing our number under one tweaked
convention at a time and seeing which reproduces theirs. When nothing does, it
says ``UNKNOWN`` honestly rather than inventing an explanation.

The core is **runner-agnostic and pure**: ``classify`` is handed a
``run_value(request) -> float | None`` callable, so the cause logic is unit
tested with a stub and driven against BigQuery in production by the reconcile
skill's CLI. A runner returning ``None`` means "this variant can't be computed"
(e.g. a grain swap with no sibling metric) — that candidate is skipped, never
counted as a match.

Cause catalog (the analytics conventions two correct pipelines most often
disagree on):
  - ``DATE_WINDOW``      — they measured a different explicit window.
  - ``MATURITY_CUTOFF``  — their range reaches into the recent, still-immature
                           cohorts our per-stage cutoff excludes (folds in the
                           partial-week case).
  - ``TIMEZONE``         — a one-day boundary shift (UTC vs local day-cut).
  - ``FILTER_DIFF``      — they applied / omitted a filter we didn't.
  - ``GRAIN``            — they measured a different grain (e.g. customer vs
                           checkout session), expressed as a different metric.
``DEFINITION_DRIFT`` (their figure came from an older artifact built on a
different metric definition) is detected separately via the manifest — see
``definition_drift``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

# --- causes ---------------------------------------------------------------
AGREE = "AGREE"
UNKNOWN = "UNKNOWN"
DATE_WINDOW = "DATE_WINDOW"
MATURITY_CUTOFF = "MATURITY_CUTOFF"
TIMEZONE = "TIMEZONE"
FILTER_DIFF = "FILTER_DIFF"
GRAIN = "GRAIN"
DEFINITION_DRIFT = "DEFINITION_DRIFT"


@dataclass(frozen=True)
class Request:
    """A recomputable description of one governed number. Frozen + hashable so it
    can key a stub runner in tests and be cheaply tweaked with ``replace``.

    ``mature`` is what makes the ``MATURITY_CUTOFF`` cause reachable at all. A
    governed run applies the metric's per-stage cutoff, so a hypothesis that only
    widened the window would be clamped straight back to our own number and could
    never reproduce theirs — the cause would be permanently unreachable in the
    live path even though it is the most common real explanation. Setting it False
    says "and they did not withhold the immature tail", which is the other half of
    the same hypothesis. Trailing field with a default, so every positional
    construction and every ``replace`` call predating it is unaffected.
    """

    metric: str
    start: dt.date
    end: dt.date
    dimensions: tuple = ()
    filters: tuple = ()  # tuple of (col, val) pairs
    mature: bool = True  # apply the metric's maturity cutoff


def within(ours: float, theirs: float, *, rtol: float = 0.01, atol: float = 0.0005) -> bool:
    """True if the two values agree within ``atol + rtol * |theirs|``."""
    return abs(ours - theirs) <= atol + rtol * abs(theirs)


def diff(ours: float, theirs: float, *, rtol: float = 0.01, atol: float = 0.0005) -> dict:
    return {
        "ours": ours,
        "theirs": theirs,
        "gap": ours - theirs,
        "within": within(ours, theirs, rtol=rtol, atol=atol),
    }


def candidate_hypotheses(
    base: Request,
    *,
    today: dt.date | None = None,
    their_window: tuple | None = None,
    their_filters: tuple | None = None,
    their_metric: str | None = None,
) -> list:
    """Ordered (cause, tweaked_request, human_explanation) candidates to test.

    Order matters: the first candidate whose recomputed value reproduces theirs
    wins, so more specific / likely causes come first.
    """
    out = []

    # DATE_WINDOW — they stated a different explicit window.
    if their_window and tuple(their_window) != (base.start, base.end):
        ws, we = their_window
        out.append(
            (DATE_WINDOW, replace(base, start=ws, end=we),
             f"their window {ws}..{we} differs from {base.start}..{base.end}")
        )

    # MATURITY_CUTOFF — their range reaches into the immature recent cohorts.
    # Both halves of the hypothesis: a wider window AND no cutoff. Widening alone
    # would be clamped back by the governed cutoff and could never reproduce their
    # number, which would make this cause unreachable in the live path.
    if today and today > base.end:
        out.append(
            (MATURITY_CUTOFF, replace(base, end=today, mature=False),
             f"their range extends to {today} and does not withhold the immature "
             f"tail, so it includes cohorts our per-stage maturity cutoff excludes")
        )

    # TIMEZONE — one-day boundary shift either direction.
    out.append(
        (TIMEZONE,
         replace(base, start=base.start - dt.timedelta(days=1), end=base.end - dt.timedelta(days=1)),
         "a one-day timezone boundary shift (earlier day-cut)")
    )
    out.append(
        (TIMEZONE,
         replace(base, start=base.start + dt.timedelta(days=1), end=base.end + dt.timedelta(days=1)),
         "a one-day timezone boundary shift (later day-cut)")
    )

    # FILTER_DIFF — drop a filter we applied that they may not have.
    for i, (col, val) in enumerate(base.filters):
        kept = base.filters[:i] + base.filters[i + 1 :]
        out.append(
            (FILTER_DIFF, replace(base, filters=kept),
             f"they did not apply the {col}={val} filter")
        )
    # FILTER_DIFF — add a filter they applied that we didn't.
    for col, val in their_filters or ():
        if (col, val) not in base.filters:
            out.append(
                (FILTER_DIFF, replace(base, filters=base.filters + ((col, val),)),
                 f"they additionally filtered {col}={val}")
            )

    # GRAIN — they measured a different grain, expressed as a sibling metric.
    if their_metric and their_metric != base.metric:
        out.append(
            (GRAIN, replace(base, metric=their_metric),
             f"they measured the '{their_metric}' grain, not '{base.metric}'")
        )

    return out


def classify(
    theirs: float,
    base: Request,
    run_value,
    *,
    rtol: float = 0.01,
    atol: float = 0.0005,
    **hints,
) -> dict:
    """Attribute the gap between ours (``run_value(base)``) and ``theirs`` to a cause.

    ``run_value(request) -> float | None``. Returns a dict with the cause, both
    values, the gap, the reproducing value (when found), an explanation, and the
    list of variants tried. ``UNKNOWN`` when no single convention reproduces theirs.
    """
    ours = run_value(base)
    if ours is None:
        return {"cause": UNKNOWN, "ours": None, "theirs": theirs,
                "detail": "could not compute the governed number for the base request.",
                "tried": []}

    base_diff = diff(ours, theirs, rtol=rtol, atol=atol)
    if base_diff["within"]:
        return {"cause": AGREE, "ours": ours, "theirs": theirs, "gap": base_diff["gap"],
                "detail": "the governed number already matches theirs within tolerance."}

    tried = []
    for cause, req, explanation in candidate_hypotheses(base, **hints):
        val = run_value(req)
        tried.append({"cause": cause, "value": val, "explanation": explanation})
        if val is None:
            continue
        if within(val, theirs, rtol=rtol, atol=atol):
            return {"cause": cause, "ours": ours, "theirs": theirs, "gap": base_diff["gap"],
                    "reproduced": val, "detail": explanation, "tried": tried}

    return {"cause": UNKNOWN, "ours": ours, "theirs": theirs, "gap": base_diff["gap"],
            "detail": "no single known convention reproduced their number; the gap "
                      "needs manual investigation.", "tried": tried}


def definition_drift(their_metric_def_sha: str | None, current_metric_def_sha: str) -> dict:
    """Detect that an external figure came from a different metric *definition*.

    Used when 'theirs' is an older governed artifact: compare the manifest's
    ``metric_def_sha`` to the current one. This is an offline, exact check — no
    re-run needed — and is reported independently of the numeric classification.
    """
    if not their_metric_def_sha:
        return {"drifted": False, "detail": "no metric_def_sha on the external figure to compare."}
    drifted = their_metric_def_sha != current_metric_def_sha
    return {
        "drifted": drifted,
        "cause": DEFINITION_DRIFT if drifted else AGREE,
        "detail": (
            f"their figure was built on a different metric definition "
            f"({their_metric_def_sha} vs current {current_metric_def_sha})"
            if drifted else
            "their figure was built on the current metric definition."
        ),
    }
