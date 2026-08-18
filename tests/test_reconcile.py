"""Reconcile core: tolerance diffing, and a classifier that recovers an injected
cause from a stubbed runner — the same shape (and >=95% bar) as a valuation
engine's equivalence classifier. No warehouse access: run_value is a stub.
"""

import datetime as dt

import pytest

from _shared import reconcile as rc

BASE = rc.Request(
    metric="cart_to_checkout_started_conversion",
    start=dt.date(2026, 3, 2),
    end=dt.date(2026, 4, 6),
    dimensions=(),
    filters=(("channel", "paid_social"),),
)
TODAY = dt.date(2026, 6, 13)
OURS = 0.84
THEIRS = 0.81


# --- diff / within ---------------------------------------------------------


def test_within_tolerance():
    assert rc.within(0.840, 0.8405)
    assert not rc.within(0.84, 0.81)


def test_diff_reports_gap_and_membership():
    d = rc.diff(0.84, 0.81)
    assert d["gap"] == pytest.approx(0.03)
    assert d["within"] is False


# --- classify: agreement ---------------------------------------------------


def test_agree_when_base_matches():
    res = rc.classify(0.8405, BASE, run_value=lambda req: 0.84)
    assert res["cause"] == rc.AGREE


# --- classify: one stub per injected cause --------------------------------


def _runner_reproducing(target_request):
    """A runner where ONLY the target request yields THEIRS; everything else
    yields OURS. So exactly the injected cause's variant reproduces theirs."""

    def run_value(req):
        return THEIRS if req == target_request else OURS

    return run_value


def _inject(cause, **hints):
    """Return (target_request, hints) for the named cause off BASE."""
    cands = rc.candidate_hypotheses(BASE, **hints)
    for c, req, _ in cands:
        if c == cause:
            return req
    raise AssertionError(f"cause {cause} not generated for hints {hints}")


def test_recovers_date_window():
    tw = (dt.date(2026, 3, 1), dt.date(2026, 4, 13))
    target = _inject(rc.DATE_WINDOW, their_window=tw)
    res = rc.classify(THEIRS, BASE, _runner_reproducing(target), their_window=tw)
    assert res["cause"] == rc.DATE_WINDOW


def test_recovers_maturity_cutoff():
    target = _inject(rc.MATURITY_CUTOFF, today=TODAY)
    res = rc.classify(THEIRS, BASE, _runner_reproducing(target), today=TODAY)
    assert res["cause"] == rc.MATURITY_CUTOFF


def test_recovers_timezone():
    # the "earlier day-cut" TIMEZONE variant is generated first
    cands = rc.candidate_hypotheses(BASE)
    tz_target = next(req for c, req, _ in cands if c == rc.TIMEZONE)
    res = rc.classify(THEIRS, BASE, _runner_reproducing(tz_target))
    assert res["cause"] == rc.TIMEZONE


def test_recovers_filter_drop():
    target = _inject(rc.FILTER_DIFF)  # drop the channel=paid_social filter we applied
    res = rc.classify(THEIRS, BASE, _runner_reproducing(target))
    assert res["cause"] == rc.FILTER_DIFF


def test_recovers_grain_metric_swap():
    """Their figure measured customers, ours measured checkout sessions — a
    different grain expressed as a different metric."""
    target = _inject(rc.GRAIN, their_metric="customer_cart_to_checkout_conversion")
    res = rc.classify(
        THEIRS, BASE, _runner_reproducing(target),
        their_metric="customer_cart_to_checkout_conversion",
    )
    assert res["cause"] == rc.GRAIN


# --- classify: honest UNKNOWN + skip None ---------------------------------


def test_unknown_when_nothing_reproduces():
    res = rc.classify(THEIRS, BASE, run_value=lambda req: OURS, today=TODAY)
    assert res["cause"] == rc.UNKNOWN
    assert res["tried"], "should record the variants it attempted"


def test_none_variant_is_skipped_not_matched():
    """A runner that can't compute a variant returns None; it must never count
    as a match even if theirs would be 'close to None'."""
    target = _inject(rc.MATURITY_CUTOFF, today=TODAY)

    def run_value(req):
        if req == BASE:
            return OURS
        if req == target:
            return None  # can't compute this variant
        return OURS

    res = rc.classify(THEIRS, BASE, run_value, today=TODAY)
    assert res["cause"] == rc.UNKNOWN


# --- the >=95% recovery bar -----------------------------------------------


def test_cause_recovery_rate_at_least_95pct():
    """Across many base requests x injected causes, the classifier recovers the
    injected cause from a clean stub at least 95% of the time."""
    starts = [dt.date(2026, 1, 5), dt.date(2026, 2, 2), dt.date(2026, 3, 2), dt.date(2026, 4, 6)]
    filtersets = [(), (("channel", "paid_social"),), (("payment_method", "card"),)]
    scenarios, recovered = 0, 0

    for s in starts:
        for fs in filtersets:
            base = rc.Request("m", s, s + dt.timedelta(days=28), filters=fs)
            tw = (s - dt.timedelta(days=7), s + dt.timedelta(days=35))
            hints = dict(today=TODAY, their_window=tw, their_metric="m_customer")
            for cause, req, _ in rc.candidate_hypotheses(base, **hints):
                scenarios += 1
                res = rc.classify(THEIRS, base, _runner_reproducing(req), **hints)
                # recovered if the reported cause matches the injected one
                if res["cause"] == cause:
                    recovered += 1

    assert scenarios > 30
    assert recovered / scenarios >= 0.95, f"only {recovered}/{scenarios} recovered"


# --- definition drift (offline) -------------------------------------------


def test_definition_drift_detected():
    out = rc.definition_drift("oldsha0000000000", "newsha1111111111")
    assert out["drifted"] and out["cause"] == rc.DEFINITION_DRIFT


def test_definition_drift_absent_when_equal():
    out = rc.definition_drift("samesha", "samesha")
    assert not out["drifted"]
