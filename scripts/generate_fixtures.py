#!/usr/bin/env python3
"""generate_fixtures.py — the synthetic checkout book this template runs on.

Standard library only, one fixed seed, fully deterministic: running this twice
produces byte-identical CSVs, so a regenerated fixture shows up in `git diff`
only when a *parameter* changed. That property is why the committed CSV can be
trusted as a test oracle at all.

What it models
--------------
~80,000 checkout sessions (one row per cart) across ~18 monthly cohorts,
progressing through the eight-stage ladder in
`plugins/assay/config/checkout-funnel-spec.yaml`:

    Cart Created -> Checkout Started -> Payment Submitted -> Payment Decisioned
    -> Order Confirmed -> Shipped -> Delivered -> Return Window Complete

The point of the domain is the **return window**. A delivered order is not a
settled order: the customer has 30 more days to send it back. So the deepest
stage of a cohort created five weeks ago genuinely has not happened yet, and no
amount of querying will make it appear. The generator reproduces that honestly by
fixing an `as_of` instant and setting each stage flag only when that stage's
timestamp actually falls on or before it. Recent cohorts therefore come out
**visibly immature** — which is what gives the per-stage maturity cutoffs, the
still-maturing chart markers, and the "withheld as not-yet-settled" warnings
something real to act on rather than a synthetic flag to trust.

It also plants three things worth being able to see:

  * a **trend** — cart-to-checkout conversion drifts up over the window, so a
    trend chart has a slope rather than noise;
  * a **cutover** — the `v1-legacy` checkout version runs for the first stretch
    and is then retired, giving the spec's `legacy_exclude_column` /
    `legacy_exclude_value` pair a real cohort to exclude;
  * a **capability difference** — `bank_transfer_review` payments decide days
    later than `card_instant` ones, which is what the per-group maturity
    override in the spec exists to handle.

Outputs
-------
  data/assay/sample/cart_checkout_funnel_weekly.csv   (committed — the default)
      The weekly aggregate the governed query-metric catalog reads: one row per
      cohort_week x channel x payment_method x market x category, with a count
      column per stage.

  data/assay/sample/cart_checkout_session_spine.csv   (--emit spine, not committed)
      The session-grain spine the deep skills read: one row per cart with stage
      flags, stage timestamps, and a terminal-branch classification. Not
      committed because nothing in this phase queries it and it is ~20x the size;
      regenerate with one command when a spine-backed skill lands.

Usage
-----
    python3 scripts/generate_fixtures.py                  # weekly only
    python3 scripts/generate_fixtures.py --emit both      # weekly + spine
    python3 scripts/generate_fixtures.py --sessions 200000 --weeks 104
    python3 scripts/generate_fixtures.py --seed 7          # a different book

Reshaping: the segment mixes, stage probabilities, and timing distributions are
all module-level constants below, named for what they mean. Change one, re-run,
and the committed CSV moves — that diff is the review surface.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "assay" / "sample"

WEEKLY_VIEW = "cart_checkout_funnel_weekly"
SPINE_VIEW = "cart_checkout_session_spine"

# ---------------------------------------------------------------------------
# Determinism anchors
# ---------------------------------------------------------------------------
#: The instant the book is observed. Every "has this stage happened yet?" test is
#: against this, so the immature tail is reproducible rather than dependent on
#: when the generator ran. Pass `--today 2026-08-17` to the skills to read the
#: fixtures at the same instant they were generated for.
DEFAULT_AS_OF = dt.date(2026, 8, 17)
DEFAULT_SEED = 20260817
DEFAULT_SESSIONS = 80_000
DEFAULT_WEEKS = 78  # ~18 monthly cohorts

#: The week the v2 checkout replaced v1. Sessions before it are the legacy
#: cohort the funnel spec excludes by default.
LEGACY_CUTOVER_WEEK_INDEX = 12
LEGACY_VERSION = "v1-legacy"
CURRENT_VERSION = "v2"

RETURN_WINDOW_DAYS = 30
ABANDON_AFTER_DAYS = 30  # a cart with no progress this long is called stale

# ---------------------------------------------------------------------------
# Segment mixes. Heavily skewed on purpose: a real book has a dominant channel
# and payment method plus a long thin tail, and thin cells are exactly where
# rate reporting misleads. Keeping the tail thin means the zero-denominator path
# (SAFE_DIVIDE -> NULL, rendered "-") and the volume-alongside-rate discipline
# both get exercised by the shipped fixtures.
# ---------------------------------------------------------------------------
CHANNELS = (("web", 0.55), ("mobile_app", 0.30), ("marketplace", 0.10), ("social", 0.05))
PAYMENT_METHODS = (("card_instant", 0.82), ("wallet", 0.12), ("bank_transfer_review", 0.06))
MARKETS = (("us", 0.70), ("ca", 0.18), ("uk", 0.12))
CATEGORIES = (("apparel", 0.40), ("electronics", 0.25), ("home", 0.20), ("beauty", 0.15))

# ---------------------------------------------------------------------------
# Stage probabilities. Base rate plus additive per-segment deltas (in absolute
# probability), clamped to a sane band.
# ---------------------------------------------------------------------------
P_CHECKOUT_START = 0.620
CHECKOUT_START_BY_CHANNEL = {
    "web": 0.000, "mobile_app": -0.045, "marketplace": 0.075, "social": -0.070,
}
#: The visible trend: conversion improves ~5pp across the whole window.
CHECKOUT_START_TREND_PP = 0.050
#: The visible cutover: v2 checkout is a step better than v1.
CHECKOUT_START_V2_LIFT = 0.035

P_PAYMENT_SUBMIT = 0.780
PAYMENT_SUBMIT_BY_CHANNEL = {
    "web": 0.000, "mobile_app": 0.020, "marketplace": -0.030, "social": -0.015,
}

#: Not every submitted payment gets a decision: some sit in a review queue and
#: never resolve. This is the "3r Payment Under Review (Unresolved)" branch.
P_PAYMENT_DECISION = {"card_instant": 0.995, "wallet": 0.990, "bank_transfer_review": 0.905}

P_PAYMENT_APPROVE = {"card_instant": 0.930, "wallet": 0.952, "bank_transfer_review": 0.862}
APPROVE_BY_MARKET = {"us": 0.000, "ca": -0.012, "uk": -0.020}

#: Fraud / identity failure, as a share of submitted payments. Marketplace and
#: social traffic carries more of it.
P_FRAUD_FAIL = 0.012
FRAUD_BY_CHANNEL = {"web": 0.000, "mobile_app": 0.002, "marketplace": 0.014, "social": 0.010}

P_ORDER_CONFIRM = 0.985      # an approved payment almost always confirms
P_SHIP = 0.958               # the rest is out-of-stock or a post-confirm cancel
SHIP_BY_CATEGORY = {"apparel": 0.010, "electronics": -0.028, "home": -0.010, "beauty": 0.008}
P_CANCEL_GIVEN_UNSHIPPED = 0.35  # split of unshipped into cancelled vs stuck-out-of-stock

P_DELIVER = 0.986            # carrier loss / refused delivery is the remainder

P_RETURN = 0.090             # share of delivered orders sent back
RETURN_BY_CATEGORY = {"apparel": 0.095, "electronics": -0.020, "home": -0.040, "beauty": -0.030}

# ---------------------------------------------------------------------------
# Timing distributions, as (mu, sigma) for a lognormal in the stated unit. Chosen
# so the medians read plausibly and the tails are long enough that p90/p99 differ
# from p50 — which is the whole reason a percentile engine exists.
# ---------------------------------------------------------------------------
T_CART_TO_CHECKOUT_MIN = (1.6, 1.3)        # median ~5 min, long tail into days
T_CHECKOUT_TO_PAYMENT_MIN = (1.1, 0.9)     # median ~3 min
T_DECISION_MIN = {                          # submit -> decision
    "card_instant": (-0.7, 0.6),           # median ~0.5 min
    "wallet": (1.0, 1.1),                  # median ~3 min (provider callback)
    "bank_transfer_review": (7.6, 1.1),    # median ~1.4 days (clearing + review)
}
T_CONFIRM_TO_SHIP_MIN = (7.3, 0.8)         # median ~1 day
T_SHIP_TO_DELIVER_DAY = {
    "us": (1.0, 0.5), "ca": (1.4, 0.55), "uk": (1.5, 0.6),
}
T_RETURN_INITIATED_DAY = (2.1, 0.7)        # days after delivery, capped at the window


def _clamp(p: float, lo: float = 0.001, hi: float = 0.999) -> float:
    return max(lo, min(hi, p))


def _pick(rng: random.Random, weighted: tuple) -> str:
    r = rng.random()
    cum = 0.0
    for value, weight in weighted:
        cum += weight
        if r < cum:
            return value
    return weighted[-1][0]


def _lognormal_capped(rng: random.Random, mu: float, sigma: float, cap: float) -> float:
    return min(rng.lognormvariate(mu, sigma), cap)


# ---------------------------------------------------------------------------
# One session
# ---------------------------------------------------------------------------

#: The measure columns on the weekly aggregate, in emit order. Every one is the
#: numerator or denominator of at least one registered metric — an unused count
#: column on a curated view is a maintenance liability, not a convenience.
MEASURES = (
    "carts_created",
    "checkouts_started",
    "payments_submitted",
    "payments_decisioned",
    "payments_approved",
    "payments_declined",
    "fraud_failed",
    "orders_confirmed",
    "orders_confirmed_not_shipped",
    "orders_shipped",
    "orders_delivered",
    "orders_returned",
    "return_windows_complete",
    "carts_stale",
)

# The dimensions the weekly aggregate is cut by, and therefore the exact set the
# metric catalog can allow-list. `checkout_version` is included because the legacy
# cohort has to be filterable at the metric layer too, not only by the
# spine-backed skills — and because it is a function of the cohort week here, it
# costs no extra rows. `is_first_order` is deliberately NOT here: it lives on the
# session spine only, since carrying it would double the cell count and halve the
# volume behind every rate.
DIMENSIONS = ("channel", "payment_method", "market", "category", "checkout_version")

SPINE_COLUMNS = (
    "checkout_session_id",
    "customer_token",
    "order_id",
    "cohort_week",
    "channel",
    "payment_method",
    "market",
    "category",
    "checkout_version",
    "is_first_order",
    "cart_created_flag",
    "checkout_started_flag",
    "payment_submitted_flag",
    "payment_decisioned_flag",
    "payment_approved_flag",
    "payment_declined_flag",
    "fraud_failed_flag",
    "order_confirmed_flag",
    "shipped_flag",
    "delivered_flag",
    "returned_flag",
    "return_window_complete_flag",
    "cart_stale_flag",
    "terminal_branch",
    "funnel_stage_reached",
    "cart_created_at",
    "checkout_started_at",
    "payment_submitted_at",
    "payment_decisioned_at",
    "order_confirmed_at",
    "shipped_at",
    "delivered_at",
    "return_window_ends_at",
    "is_mature_45d",
    "days_since_created",
)


def _iso_ts(base: dt.datetime | None) -> str:
    """ISO-8601 text, or empty for NULL.

    Text, and specifically ISO-8601, is not a formatting preference. sqlite has no
    date type, so every date comparison in the offline path is a string
    comparison — and ISO-8601 is the format whose lexicographic order matches
    chronological order. Epoch integers or MM/DD/YYYY would make
    `cohort_week BETWEEN :start AND :end` return the wrong rows *silently*.
    tests/test_adapters.py asserts this end to end.
    """
    return "" if base is None else base.isoformat(sep=" ", timespec="seconds")


def simulate_session(rng: random.Random, idx: int, created_at: dt.datetime,
                     week_index: int, n_weeks: int, as_of_ts: dt.datetime) -> dict:
    """One cart's whole life. Returns a spine-shaped record."""
    channel = _pick(rng, CHANNELS)
    payment_method = _pick(rng, PAYMENT_METHODS)
    market = _pick(rng, MARKETS)
    category = _pick(rng, CATEGORIES)
    version = LEGACY_VERSION if week_index < LEGACY_CUTOVER_WEEK_INDEX else CURRENT_VERSION
    is_first_order = rng.random() < 0.38

    progress = week_index / max(1, n_weeks - 1)

    # -- stage 2: checkout started ---------------------------------------
    p_start = (
        P_CHECKOUT_START
        + CHECKOUT_START_BY_CHANNEL[channel]
        + CHECKOUT_START_TREND_PP * progress
        + (CHECKOUT_START_V2_LIFT if version == CURRENT_VERSION else 0.0)
    )
    reached_checkout = rng.random() < _clamp(p_start)

    checkout_started_at = payment_submitted_at = payment_decisioned_at = None
    order_confirmed_at = shipped_at = delivered_at = return_window_ends_at = None
    approved = declined = fraud_failed = returned = False
    cancelled = False

    if reached_checkout:
        checkout_started_at = created_at + dt.timedelta(
            minutes=_lognormal_capped(rng, *T_CART_TO_CHECKOUT_MIN, cap=60 * 24 * 3)
        )

        # -- stage 3: payment submitted -----------------------------------
        p_submit = _clamp(P_PAYMENT_SUBMIT + PAYMENT_SUBMIT_BY_CHANNEL[channel])
        if rng.random() < p_submit:
            payment_submitted_at = checkout_started_at + dt.timedelta(
                minutes=_lognormal_capped(rng, *T_CHECKOUT_TO_PAYMENT_MIN, cap=60 * 24)
            )
            fraud_failed = rng.random() < _clamp(
                P_FRAUD_FAIL + FRAUD_BY_CHANNEL[channel], lo=0.0
            )

            # -- stage 4: payment decisioned ------------------------------
            if not fraud_failed and rng.random() < P_PAYMENT_DECISION[payment_method]:
                payment_decisioned_at = payment_submitted_at + dt.timedelta(
                    minutes=_lognormal_capped(
                        rng, *T_DECISION_MIN[payment_method], cap=60 * 24 * 21
                    )
                )
                p_approve = _clamp(
                    P_PAYMENT_APPROVE[payment_method] + APPROVE_BY_MARKET[market]
                )
                approved = rng.random() < p_approve
                declined = not approved

                # -- stage 5: order confirmed -----------------------------
                if approved and rng.random() < P_ORDER_CONFIRM:
                    order_confirmed_at = payment_decisioned_at + dt.timedelta(
                        minutes=rng.uniform(0.2, 12.0)
                    )

                    # -- stage 6: shipped --------------------------------
                    p_ship = _clamp(P_SHIP + SHIP_BY_CATEGORY[category])
                    if rng.random() < p_ship:
                        shipped_at = order_confirmed_at + dt.timedelta(
                            minutes=_lognormal_capped(
                                rng, *T_CONFIRM_TO_SHIP_MIN, cap=60 * 24 * 21
                            )
                        )

                        # -- stage 7: delivered --------------------------
                        if rng.random() < P_DELIVER:
                            delivered_at = shipped_at + dt.timedelta(
                                days=_lognormal_capped(
                                    rng, *T_SHIP_TO_DELIVER_DAY[market], cap=30
                                )
                            )
                            return_window_ends_at = delivered_at + dt.timedelta(
                                days=RETURN_WINDOW_DAYS
                            )
                            p_return = _clamp(
                                P_RETURN + RETURN_BY_CATEGORY[category], lo=0.0
                            )
                            returned = rng.random() < p_return
                    else:
                        cancelled = rng.random() < P_CANCEL_GIVEN_UNSHIPPED

    # -- observation: a stage counts only once it has actually happened ---
    # This is where the maturation tail comes from. Nothing is hidden; the deep
    # stages of recent cohorts simply have not occurred yet.
    def seen(ts: dt.datetime | None) -> bool:
        return ts is not None and ts <= as_of_ts

    f_checkout = seen(checkout_started_at)
    f_submit = seen(payment_submitted_at)
    f_fraud = fraud_failed and f_submit
    f_decision = seen(payment_decisioned_at)
    f_approved = approved and f_decision
    f_declined = declined and f_decision
    f_confirm = seen(order_confirmed_at)
    f_ship = seen(shipped_at)
    f_deliver = seen(delivered_at)
    window_closed = (
        return_window_ends_at is not None and return_window_ends_at <= as_of_ts
    )
    # A return is only observable after it is initiated, which is itself somewhere
    # inside the window — so a returned order becomes visible before the window
    # closes, while a *kept* order is only confirmed kept once the window shuts.
    return_initiated_at = None
    if returned and delivered_at is not None:
        return_initiated_at = delivered_at + dt.timedelta(
            days=_lognormal_capped(rng, *T_RETURN_INITIATED_DAY, cap=RETURN_WINDOW_DAYS)
        )
    f_returned = seen(return_initiated_at)
    f_window_complete = f_deliver and window_closed and not returned

    # A cart that stopped making progress and is now older than the abandonment
    # threshold is called stale, which is the terminal state of an abandoned cart
    # rather than a separate event.
    age_days = (as_of_ts - created_at).days
    stalled_pre_checkout = not f_submit and age_days >= ABANDON_AFTER_DAYS
    stalled_post_submit = f_submit and not f_decision and age_days >= ABANDON_AFTER_DAYS
    f_stale = stalled_pre_checkout or stalled_post_submit

    # -- terminal branch, deepest-first (mirrors the view DDL's CASE) -----
    if f_window_complete:
        branch, reached = "8 Return Window Complete", 8
    elif f_returned:
        branch, reached = "7r Delivered then Returned", 7
    elif f_deliver:
        branch, reached = "7 Delivered (Return Window Open)", 7
    elif f_ship:
        branch, reached = "6 Shipped (In Transit)", 6
    elif f_confirm and cancelled:
        branch, reached = "5x Cancelled after Confirmation", 5
    elif f_confirm:
        branch, reached = "5 Confirmed not Shipped (Out of Stock)", 5
    elif f_declined:
        branch, reached = "4d Payment Declined", 4
    elif f_fraud:
        branch, reached = "4f Fraud / Identity Check Failed", 3
    elif f_submit and stalled_post_submit:
        branch, reached = "3z Stale (Post-Submit)", 3
    elif f_submit and not f_decision:
        branch, reached = "3r Payment Under Review (Unresolved)", 3
    elif f_submit:
        branch, reached = "3 Payment Submitted, Undecided", 3
    elif f_checkout:
        branch, reached = "2 Checkout Started, No Payment", 2
    elif stalled_pre_checkout:
        branch, reached = "1z Stale (Pre-Checkout)", 1
    else:
        branch, reached = "1 Cart Abandoned", 1

    cohort_week = created_at.date() - dt.timedelta(days=created_at.weekday())

    return {
        "checkout_session_id": f"cs_{idx:07d}",
        # An opaque surrogate key, not a person. `_shared/pii.py` allow-lists it
        # explicitly so customer-level linkage stays possible without ever
        # exposing a name, an email, or an address.
        "customer_token": f"ct_{rng.randrange(10 ** 6):06d}",
        "order_id": f"ord_{idx:07d}" if f_confirm else "",
        "cohort_week": cohort_week.isoformat(),
        "channel": channel,
        "payment_method": payment_method,
        "market": market,
        "category": category,
        "checkout_version": version,
        "is_first_order": is_first_order,
        "cart_created_flag": True,
        "checkout_started_flag": f_checkout,
        "payment_submitted_flag": f_submit,
        "payment_decisioned_flag": f_decision,
        "payment_approved_flag": f_approved,
        "payment_declined_flag": f_declined,
        "fraud_failed_flag": f_fraud,
        "order_confirmed_flag": f_confirm,
        "shipped_flag": f_ship,
        "delivered_flag": f_deliver,
        "returned_flag": f_returned,
        "return_window_complete_flag": f_window_complete,
        "cart_stale_flag": f_stale,
        "terminal_branch": branch,
        "funnel_stage_reached": reached,
        "cart_created_at": _iso_ts(created_at),
        "checkout_started_at": _iso_ts(checkout_started_at if f_checkout else None),
        "payment_submitted_at": _iso_ts(payment_submitted_at if f_submit else None),
        "payment_decisioned_at": _iso_ts(payment_decisioned_at if f_decision else None),
        "order_confirmed_at": _iso_ts(order_confirmed_at if f_confirm else None),
        "shipped_at": _iso_ts(shipped_at if f_ship else None),
        "delivered_at": _iso_ts(delivered_at if f_deliver else None),
        "return_window_ends_at": _iso_ts(return_window_ends_at if f_deliver else None),
        "is_mature_45d": age_days >= 45,
        "days_since_created": age_days,
    }


# ---------------------------------------------------------------------------
# The book
# ---------------------------------------------------------------------------


def generate(sessions: int, weeks: int, as_of: dt.date, seed: int) -> list[dict]:
    rng = random.Random(seed)
    as_of_ts = dt.datetime.combine(as_of, dt.time(23, 59, 59))
    # `as_of` is a Monday; the window is the `weeks` complete weeks before it.
    first_monday = as_of - dt.timedelta(days=as_of.weekday() + 7 * weeks)

    # Weekly volume: gentle growth plus a shallow seasonal wave, so the trend in
    # the rates is not confounded with a flat denominator.
    weights = []
    for w in range(weeks):
        growth = 1.0 + 0.45 * (w / max(1, weeks - 1))
        seasonal = 1.0 + 0.12 * ((w % 13) / 12.0 - 0.5)
        weights.append(growth * seasonal)
    total_weight = sum(weights)
    per_week = [max(1, round(sessions * (x / total_weight))) for x in weights]

    records: list[dict] = []
    idx = 0
    for w, count in enumerate(per_week):
        monday = first_monday + dt.timedelta(weeks=w)
        for _ in range(count):
            created_at = dt.datetime.combine(monday, dt.time.min) + dt.timedelta(
                days=rng.randrange(7),
                minutes=rng.randrange(24 * 60),
                seconds=rng.randrange(60),
            )
            if created_at > as_of_ts:
                continue
            idx += 1
            records.append(
                simulate_session(rng, idx, created_at, w, weeks, as_of_ts)
            )
    return records


def aggregate_weekly(records: list[dict]) -> list[dict]:
    """Roll the session grain up to the weekly aggregate the metrics read."""
    flag_to_measure = {
        "cart_created_flag": "carts_created",
        "checkout_started_flag": "checkouts_started",
        "payment_submitted_flag": "payments_submitted",
        "payment_decisioned_flag": "payments_decisioned",
        "payment_approved_flag": "payments_approved",
        "payment_declined_flag": "payments_declined",
        "fraud_failed_flag": "fraud_failed",
        "order_confirmed_flag": "orders_confirmed",
        "shipped_flag": "orders_shipped",
        "delivered_flag": "orders_delivered",
        "returned_flag": "orders_returned",
        "return_window_complete_flag": "return_windows_complete",
        "cart_stale_flag": "carts_stale",
    }
    cells: dict[tuple, dict] = {}
    for r in records:
        key = (r["cohort_week"],) + tuple(r[d] for d in DIMENSIONS)
        cell = cells.get(key)
        if cell is None:
            cell = dict.fromkeys(MEASURES, 0)
            cells[key] = cell
        for flag, measure in flag_to_measure.items():
            if r[flag]:
                cell[measure] += 1
        # The out-of-stock leak: confirmed, and still not shipped. Derived here
        # rather than from a flag because it is the *absence* of a later stage.
        if r["order_confirmed_flag"] and not r["shipped_flag"]:
            cell["orders_confirmed_not_shipped"] += 1

    rows = []
    for key in sorted(cells):
        row = dict(zip(("cohort_week",) + DIMENSIONS, key))
        row.update(cells[key])
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------


def write_csv(path: Path, columns, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({c: _csv_value(r[c]) for c in columns})
    return len(rows)


def _csv_value(v):
    """Booleans as lowercase `true`/`false` — what the sqlite fixture loader and
    BigQuery's own CSV export both read back as BOOL."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def write_manifest(out_dir: Path, meta: dict) -> Path:
    """A short provenance note next to the CSVs.

    Anyone reading a fixture-backed number should be able to see, without running
    anything, that it came from a seeded generator and at what observation
    instant — the two facts that make the immature tail reproducible.
    """
    path = out_dir / "FIXTURES.md"
    lines = [
        "# Fixture provenance",
        "",
        "Generated by `scripts/generate_fixtures.py`. **Synthetic data — these",
        "numbers describe a seeded random process, not any real business.** Every",
        "artifact built from them is watermarked accordingly.",
        "",
        "| Parameter | Value |",
        "|---|---|",
        f"| Seed | `{meta['seed']}` |",
        f"| Observation instant (`as_of`) | `{meta['as_of']}` |",
        f"| Cohort weeks | {meta['weeks']} |",
        f"| Sessions simulated | {meta['sessions']:,} |",
        f"| Return window | {RETURN_WINDOW_DAYS} days |",
        f"| Legacy cohort | `{LEGACY_VERSION}` for the first {LEGACY_CUTOVER_WEEK_INDEX} weeks |",
        "",
        "Rows written:",
        "",
    ]
    for name, n in meta["outputs"].items():
        lines.append(f"- `{name}` — {n:,} rows")
    lines += [
        "",
        "## Reading these at the right instant",
        "",
        f"Pass `--today {meta['as_of']}` to the skills. The maturity cutoffs are",
        "computed from *today*, so reading a fixed fixture with a drifting real",
        "date would keep withholding more of the tail every week until the whole",
        "book looked immature.",
        "",
        "## Regenerating",
        "",
        "```bash",
        "python3 scripts/generate_fixtures.py              # weekly aggregate only",
        "python3 scripts/generate_fixtures.py --emit both  # + the session spine",
        "```",
        "",
        "Deterministic: the same arguments produce byte-identical files, so a diff",
        "here always means a parameter changed.",
        "",
    ]
    path.write_text("\n".join(lines))
    return path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)
    p.add_argument("--weeks", type=int, default=DEFAULT_WEEKS)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument(
        "--as-of",
        default=DEFAULT_AS_OF.isoformat(),
        help="observation instant (ISO date); the immature tail is relative to this",
    )
    p.add_argument(
        "--emit",
        default="weekly",
        choices=["weekly", "spine", "both"],
        help="weekly aggregate (committed), session spine, or both",
    )
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    as_of = dt.date.fromisoformat(args.as_of)
    out_dir = Path(args.out_dir)

    records = generate(args.sessions, args.weeks, as_of, args.seed)
    outputs: dict[str, int] = {}

    if args.emit in ("weekly", "both"):
        weekly = aggregate_weekly(records)
        n = write_csv(
            out_dir / f"{WEEKLY_VIEW}.csv", ("cohort_week",) + DIMENSIONS + MEASURES, weekly
        )
        outputs[f"{WEEKLY_VIEW}.csv"] = n
    if args.emit in ("spine", "both"):
        n = write_csv(out_dir / f"{SPINE_VIEW}.csv", SPINE_COLUMNS, records)
        outputs[f"{SPINE_VIEW}.csv"] = n

    meta = {
        "seed": args.seed,
        "as_of": as_of.isoformat(),
        "weeks": args.weeks,
        "sessions": len(records),
        "outputs": outputs,
    }
    write_manifest(out_dir, meta)

    if not args.quiet:
        print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
