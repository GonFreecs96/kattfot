"""The committed fixtures: deterministic, ISO-dated, and genuinely immature at the tail.

Everything offline in this repo rests on the committed CSV, so its properties are
load-bearing rather than incidental:

  1. **Deterministic.** The same arguments produce byte-identical output. A fixture
     that drifted between runs could not be a test oracle, and every golden in the
     suite would fail intermittently for no reason anyone could reproduce.
  2. **ISO-8601 dates as text.** sqlite has no date type, so every offline date
     comparison is a string comparison. This is asserted at the generator's output as
     well as at the adapter, because the generator is where it would be broken.
  3. **A real maturation tail.** The deep stages of recent cohorts must be genuinely
     incomplete. Without that, the per-stage maturity machinery, the still-maturing
     chart markers, and half the caveats in the catalog would be tested against data
     that does not exercise them — passing tests describing a mechanism that never
     fires.
  4. **Internally consistent.** Stage counts must nest (you cannot ship more orders
     than you confirmed), or the funnel arithmetic is meaningless.
  5. **Wide enough, and no wider.** The session spine is committed TRIMMED to a
     cohort-week window, because one row per cart is ~280 bytes and the whole book is
     ~22 MB. The trim is sized by what the spine skills actually read at the funnel
     spec's own defaults, and that requirement is recomputed here from the spec
     rather than trusted to a constant — a trim one week too short would give
     `funnel-pulse` a silently truncated baseline.
"""

import csv
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_fixtures.py"
FIXTURE_DIR = ROOT / "data" / "kattfot" / "sample"
WEEKLY_CSV = FIXTURE_DIR / "cart_checkout_funnel_weekly.csv"
SPINE_CSV = FIXTURE_DIR / "cart_checkout_session_spine.csv"
SPEC_PATH = ROOT / "plugins" / "kattfot" / "config" / "checkout-funnel-spec.yaml"

DIMENSIONS = ("channel", "payment_method", "market", "category", "checkout_version")

#: The instant the fixtures were generated for, recorded in FIXTURES.md. Every
#: maturity assertion below is relative to this, not to the real today.
AS_OF = dt.date(2026, 8, 17)


@pytest.fixture(scope="module")
def weekly_rows():
    assert WEEKLY_CSV.exists(), (
        f"{WEEKLY_CSV} is missing — run `python3 scripts/generate_fixtures.py`"
    )
    with WEEKLY_CSV.open() as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def spine_rows():
    assert SPINE_CSV.exists(), (
        f"{SPINE_CSV} is missing — run `python3 scripts/generate_fixtures.py`"
    )
    with SPINE_CSV.open() as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load(SPEC_PATH.read_text())


# --- 1. determinism ---------------------------------------------------------


def test_generator_is_deterministic(tmp_path):
    """Two runs with the same arguments produce byte-identical CSVs. A diff in the
    committed fixture therefore always means a PARAMETER changed, which is what makes
    the file reviewable."""
    outs = []
    for i in range(2):
        d = tmp_path / f"run{i}"
        subprocess.run(
            [sys.executable, str(GENERATOR), "--out-dir", str(d),
             "--sessions", "4000", "--weeks", "20", "--quiet"],
            check=True, cwd=ROOT, capture_output=True,
        )
        outs.append((d / "cart_checkout_funnel_weekly.csv").read_bytes())
    assert outs[0] == outs[1]
    assert len(outs[0]) > 1000


def test_a_different_seed_produces_different_data(tmp_path):
    """Guards against the determinism above being achieved by the generator ignoring
    randomness altogether — which would pass test one and produce a useless fixture."""
    outs = []
    for seed in (1, 2):
        d = tmp_path / f"seed{seed}"
        subprocess.run(
            [sys.executable, str(GENERATOR), "--out-dir", str(d), "--seed", str(seed),
             "--sessions", "4000", "--weeks", "20", "--quiet"],
            check=True, cwd=ROOT, capture_output=True,
        )
        outs.append((d / "cart_checkout_funnel_weekly.csv").read_bytes())
    assert outs[0] != outs[1]


def test_committed_csvs_match_a_fresh_run_of_the_committed_generator(tmp_path):
    """BOTH committed fixtures must be reproducible from the committed generator with
    its default arguments. If they drift apart, the CSVs become unexplained artifacts
    nobody can regenerate or review — and for the spine, whose default emit is
    trimmed, that includes the trim width."""
    subprocess.run(
        [sys.executable, str(GENERATOR), "--out-dir", str(tmp_path), "--quiet"],
        check=True, cwd=ROOT, capture_output=True,
    )
    for name, committed in (
        ("cart_checkout_funnel_weekly.csv", WEEKLY_CSV),
        ("cart_checkout_session_spine.csv", SPINE_CSV),
    ):
        assert (tmp_path / name).read_bytes() == committed.read_bytes(), (
            f"committed {name} does not match a default run of the committed "
            f"generator — regenerate (`python3 scripts/generate_fixtures.py`) or "
            f"explain the drift"
        )


def test_the_full_spine_is_a_superset_of_the_committed_trim(tmp_path):
    """`--spine-weeks 0` must regrow exactly what the trim removed, and nothing else.

    This is what makes the trim safe to commit: the large version is not a different
    fixture, it is the same book with more cohort history, so a skill that needs more
    weeks gets them without any number changing on the weeks both cover.
    """
    subprocess.run(
        [sys.executable, str(GENERATOR), "--out-dir", str(tmp_path),
         "--emit", "spine", "--spine-weeks", "0", "--quiet"],
        check=True, cwd=ROOT, capture_output=True,
    )
    with (tmp_path / "cart_checkout_session_spine.csv").open() as f:
        full = list(csv.DictReader(f))
    with SPINE_CSV.open() as f:
        trimmed = list(csv.DictReader(f))

    assert len(full) > len(trimmed) * 2, "the full spine should be much larger"
    kept_weeks = {r["cohort_week"] for r in trimmed}
    # Same rows, byte for byte, on the weeks the trim kept.
    full_in_window = [r for r in full if r["cohort_week"] in kept_weeks]
    assert full_in_window == trimmed, (
        "the trimmed spine is not a strict row-for-row subset of the full one — the "
        "trim is doing something other than selecting whole cohort weeks"
    )


# --- 2. ISO-8601 dates ------------------------------------------------------


def test_cohort_week_is_iso_8601(weekly_rows):
    for r in weekly_rows:
        v = r["cohort_week"]
        assert dt.date.fromisoformat(v).isoformat() == v, f"non-ISO cohort_week {v!r}"


def test_cohort_week_is_always_a_monday(weekly_rows):
    """ISO-Monday weeks. A mixed-anchor cohort column would make the maturity cutoff
    arithmetic — which always returns a Monday — silently exclude everything."""
    for r in weekly_rows:
        assert dt.date.fromisoformat(r["cohort_week"]).weekday() == 0


def test_measure_columns_are_plain_integers(weekly_rows):
    """No thousands separators, no floats, no empty strings in a count column. Any of
    those would load as TEXT and make SUM() concatenate instead of add."""
    for r in weekly_rows[:200]:
        for col, v in r.items():
            if col in DIMENSIONS or col == "cohort_week":
                continue
            assert v.isdigit(), f"{col}={v!r} is not a bare integer"


# --- 3. the maturation tail -------------------------------------------------


def _by_week(rows, col):
    out: dict[str, int] = {}
    for r in rows:
        out[r["cohort_week"]] = out.get(r["cohort_week"], 0) + int(r[col])
    return out


def test_recent_cohorts_are_visibly_immature_at_the_deepest_stage(weekly_rows):
    """The property the whole maturity apparatus exists to handle. The newest cohorts
    must have essentially no settled return windows, because 30 days after delivery has
    not elapsed — not because of a generator quirk."""
    created = _by_week(weekly_rows, "carts_created")
    complete = _by_week(weekly_rows, "return_windows_complete")
    weeks = sorted(created)
    assert len(weeks) >= 50, "need a long enough window to have both settled and fresh cohorts"

    newest = weeks[-3:]
    settled = weeks[-30:-12]
    for w in newest:
        assert created[w] > 0, f"{w} has no carts at all"
        assert complete[w] == 0, (
            f"{w} has {complete[w]} settled return windows. The newest cohorts must be "
            f"unsettled, or the fixtures do not exercise the maturity cutoffs."
        )
    assert sum(complete[w] for w in settled) > 0, "older cohorts should have settled"


def test_the_tail_is_graded_not_a_cliff(weekly_rows):
    """Delivery precedes the return window, so the immaturity should arrive in the right
    order: the newest cohorts lose their delivered count too, but later and less
    sharply than their settled-window count."""
    shipped = _by_week(weekly_rows, "orders_shipped")
    delivered = _by_week(weekly_rows, "orders_delivered")
    weeks = sorted(shipped)
    newest = weeks[-1]
    mid = weeks[-20]
    newest_ratio = delivered[newest] / shipped[newest]
    mid_ratio = delivered[mid] / shipped[mid]
    assert newest_ratio < mid_ratio, (
        "the newest cohort should have a lower delivered/shipped ratio — its shipments "
        "are still in transit"
    )
    assert mid_ratio > 0.9, "a settled cohort should have nearly all shipments delivered"


def test_the_legacy_cohort_exists_and_then_stops(weekly_rows):
    """The retired checkout version must occupy an early stretch of the window and then
    disappear, so `legacy_exclude_column` / `legacy_exclude_value` has something real to
    exclude and so a cutover-crossing window is testable."""
    versions_by_week: dict[str, set] = {}
    for r in weekly_rows:
        versions_by_week.setdefault(r["cohort_week"], set()).add(r["checkout_version"])
    weeks = sorted(versions_by_week)
    assert versions_by_week[weeks[0]] == {"v1-legacy"}
    assert versions_by_week[weeks[-1]] == {"v2"}


def test_the_slow_payment_method_is_present_and_thin(weekly_rows):
    """`bank_transfer_review` is what the per-group maturity override is keyed on. It has
    to exist, and it has to be a minority — a book-wide lag being wrong for a small slow
    segment is the entire reason overrides exist."""
    totals: dict[str, int] = {}
    for r in weekly_rows:
        totals[r["payment_method"]] = totals.get(r["payment_method"], 0) + int(
            r["payments_submitted"]
        )
    assert "bank_transfer_review" in totals
    share = totals["bank_transfer_review"] / sum(totals.values())
    assert 0.01 < share < 0.20, f"bank_transfer_review share is {share:.1%}"


# --- 4. internal consistency ------------------------------------------------


@pytest.mark.parametrize(
    "inner,outer",
    [
        ("checkouts_started", "carts_created"),
        ("payments_submitted", "checkouts_started"),
        ("payments_decisioned", "payments_submitted"),
        ("payments_approved", "payments_decisioned"),
        ("payments_declined", "payments_decisioned"),
        ("fraud_failed", "payments_submitted"),
        ("orders_confirmed", "payments_approved"),
        ("orders_shipped", "orders_confirmed"),
        ("orders_delivered", "orders_shipped"),
        ("orders_returned", "orders_delivered"),
        ("return_windows_complete", "orders_delivered"),
        ("carts_stale", "carts_created"),
        ("orders_confirmed_not_shipped", "orders_confirmed"),
    ],
)
def test_stage_counts_nest(weekly_rows, inner, outer):
    """Every stage count must be <= its denominator, in every cell. A single cell where
    it is not would produce a conversion rate above 100% and make the funnel nonsense."""
    for r in weekly_rows:
        assert int(r[inner]) <= int(r[outer]), (
            f"{r['cohort_week']} {r['channel']}/{r['payment_method']}: "
            f"{inner}={r[inner]} > {outer}={r[outer]}"
        )


def test_approved_and_declined_partition_the_decisioned(weekly_rows):
    """Every decision is either an approval or a decline, exactly. If these did not sum,
    `payment_approval_rate` and `payment_decline_rate` would not be complements and the
    catalog's claim that they are would be wrong."""
    for r in weekly_rows:
        assert int(r["payments_approved"]) + int(r["payments_declined"]) == int(
            r["payments_decisioned"]
        ), f"{r['cohort_week']}: approved + declined != decisioned"


def test_confirmed_not_shipped_is_exactly_the_unshipped_confirmed(weekly_rows):
    """The out-of-stock leak is defined by an absence, so it must reconcile arithmetically
    against the two counts it is derived from."""
    for r in weekly_rows:
        assert int(r["orders_confirmed_not_shipped"]) == int(r["orders_confirmed"]) - int(
            r["orders_shipped"]
        ), f"{r['cohort_week']}: confirmed_not_shipped != confirmed - shipped"


def test_returned_and_complete_do_not_overlap(weekly_rows):
    """A kept order and a returned one are mutually exclusive, so together they cannot
    exceed the delivered count."""
    for r in weekly_rows:
        assert int(r["orders_returned"]) + int(r["return_windows_complete"]) <= int(
            r["orders_delivered"]
        )


def test_every_dimension_value_is_from_the_governed_enum(weekly_rows):
    """The fixture must not invent a dimension value outside the enum the entity registry
    declares, or a filter the catalog documents would return nothing."""
    expected = {
        "channel": {"web", "mobile_app", "marketplace", "social"},
        "payment_method": {"card_instant", "wallet", "bank_transfer_review"},
        "market": {"us", "ca", "uk"},
        "category": {"apparel", "electronics", "home", "beauty"},
        "checkout_version": {"v2", "v1-legacy"},
    }
    seen = {d: set() for d in expected}
    for r in weekly_rows:
        for d in expected:
            seen[d].add(r[d])
    for d, allowed in expected.items():
        assert seen[d] <= allowed, f"{d}: unexpected values {seen[d] - allowed}"
        assert seen[d] == allowed, f"{d}: missing values {allowed - seen[d]}"


# --- 5. the trimmed session spine -------------------------------------------


def test_spine_cohort_weeks_are_a_contiguous_recent_window(spine_rows):
    """The trim keeps the most recent N whole cohort weeks — contiguous, no holes.

    A gap would be far worse than a shorter window: every window-based read would
    silently span the hole and divide by a denominator that is missing a week.
    """
    weeks = sorted({dt.date.fromisoformat(r["cohort_week"]) for r in spine_rows})
    assert len(weeks) >= 12, f"only {len(weeks)} cohort weeks in the committed spine"
    for a, b in zip(weeks, weeks[1:]):
        assert (b - a).days == 7, f"gap in the spine's cohort weeks between {a} and {b}"
    assert all(w.weekday() == 0 for w in weeks), "cohort weeks must be ISO Mondays"


def test_trimmed_spine_covers_every_pulse_window(spine_rows, spec):
    """The trim width is derived, not asserted: recompute what `funnel-pulse` reads at
    the spec's OWN defaults and check the fixture reaches back that far.

    This is the test that keeps the trim honest. `funnel-pulse` compares a recent
    window against the baseline before it, per stage, each lagged by that stage's
    settle time — so the deepest stage's baseline reaches back much further than the
    monitor's nominal 12 weeks. A trim one week too short does not fail loudly; it
    gives the deepest stage a truncated baseline and a plausible-looking wrong delta.
    Per-group override windows are included, since an override can reach back further
    than the stage default it replaces.
    """
    from _shared import spine as spine_lib

    pulse = spec["pulse"]
    recent_weeks = int(pulse["recent_weeks"])
    baseline_weeks = int(pulse["baseline_weeks"])

    needed_start, needed_end = None, None
    for s in spec["stages"]:
        mstage = s.get("maturity_stage", s["key"])
        end = spine_lib.maturity_cutoff_monday(AS_OF, spine_lib.stage_lag_days(spec, mstage))
        rs = end - dt.timedelta(weeks=recent_weeks - 1)
        bs = rs - dt.timedelta(weeks=baseline_weeks)
        windows = [(bs, end)]
        gw = spine_lib.group_windows_for_stage(
            spec, mstage, pulse["by"], AS_OF, recent_weeks, baseline_weeks
        ) or {}
        windows += [(w["baseline_start"], w["end"]) for w in gw.values()]
        # Also the override dimension, which is what --by would be set to for the
        # override to engage at all.
        for dim in {pulse["by"], "payment_method"}:
            for w in (spine_lib.group_windows_for_stage(
                spec, mstage, dim, AS_OF, recent_weeks, baseline_weeks
            ) or {}).values():
                windows.append((w["baseline_start"], w["end"]))
        for bs_i, end_i in windows:
            needed_start = bs_i if needed_start is None else min(needed_start, bs_i)
            needed_end = end_i if needed_end is None else max(needed_end, end_i)

    weeks = sorted({dt.date.fromisoformat(r["cohort_week"]) for r in spine_rows})
    assert weeks[0] <= needed_start, (
        f"the committed spine starts at {weeks[0]} but funnel-pulse's deepest baseline "
        f"reaches back to {needed_start}. Raise TRIMMED_SPINE_WEEKS in "
        f"scripts/generate_fixtures.py and regenerate."
    )
    assert weeks[-1] >= needed_end, (
        f"the committed spine ends at {weeks[-1]} but the freshest stage's window runs "
        f"to {needed_end}"
    )


def test_spine_reconciles_with_the_weekly_aggregate(spine_rows, weekly_rows, spec):
    """Over the weeks both cover, the two views must agree exactly.

    They are two projections of one book — the whole claim of the LINEAGE note — and
    the trim is a cohort-week window rather than a row sample specifically so this
    stays checkable. If it ever fails, one of the two views has drifted, and every
    cross-check between a governed metric and a spine skill is invalid.
    """
    flag_to_measure = {
        "cart_created_flag": "carts_created",
        "checkout_started_flag": "checkouts_started",
        "payment_submitted_flag": "payments_submitted",
        "payment_decisioned_flag": "payments_decisioned",
        "order_confirmed_flag": "orders_confirmed",
        "shipped_flag": "orders_shipped",
        "delivered_flag": "orders_delivered",
        "return_window_complete_flag": "return_windows_complete",
    }
    weeks = {r["cohort_week"] for r in spine_rows}
    from_spine = {m: 0 for m in flag_to_measure.values()}
    for r in spine_rows:
        for flag, measure in flag_to_measure.items():
            if r[flag] == "true":
                from_spine[measure] += 1
    from_weekly = {m: 0 for m in flag_to_measure.values()}
    for r in weekly_rows:
        if r["cohort_week"] not in weeks:
            continue
        for m in from_weekly:
            from_weekly[m] += int(r[m])
    assert from_spine == from_weekly, (
        "the session spine and the weekly aggregate disagree over the overlapping "
        f"cohort weeks: {from_spine} vs {from_weekly}"
    )


def test_terminal_branch_is_exclusive_exhaustive_and_fully_declared(spine_rows, spec):
    """Every cart carries exactly one branch, and every branch the spine emits is one
    the spec lists.

    This is the precondition for `funnel-dropoff`'s coherence check to mean anything.
    Exclusivity and exhaustiveness are structural (one column, never empty); what
    could actually rot is the spec's `terminal_branches:` list falling behind the
    view's CASE, which would push a real branch into the "(other)" bucket and out of
    the curated ordering.
    """
    declared = set(spec["terminal_branches"])
    seen: dict[str, int] = {}
    for r in spine_rows:
        b = r["terminal_branch"]
        assert b, f"{r['checkout_session_id']} has no terminal_branch"
        seen[b] = seen.get(b, 0) + 1
    undeclared = sorted(set(seen) - declared)
    assert not undeclared, f"the spine emits branches the spec does not list: {undeclared}"
    assert sum(seen.values()) == len(spine_rows), "one branch per row, exactly"
    # And the branch counts sum to the carts created, which is what funnel-dropoff
    # asserts at query time over a cohort window.
    created = sum(1 for r in spine_rows if r["cart_created_flag"] == "true")
    assert created == len(spine_rows) == sum(seen.values())


def test_trimmed_spine_still_has_a_real_maturation_tail(spine_rows):
    """Trimming must not remove the thing the trim exists to preserve.

    The newest cohorts have to be genuinely unsettled at the deep stages, or every
    maturity cutoff in the suite is exercised against data where it makes no
    difference — passing tests describing a mechanism that never fires.
    """
    by_week: dict[str, dict] = {}
    for r in spine_rows:
        w = by_week.setdefault(r["cohort_week"], {"n": 0, "delivered": 0, "settled": 0})
        w["n"] += 1
        w["delivered"] += r["delivered_flag"] == "true"
        w["settled"] += r["return_window_complete_flag"] == "true"
    weeks = sorted(by_week)
    newest, oldest = by_week[weeks[-1]], by_week[weeks[0]]
    assert newest["n"] > 0
    assert newest["settled"] == 0, (
        "the newest committed cohort has settled return windows — the trim kept only "
        "old, fully-settled weeks and the maturity machinery has nothing to withhold"
    )
    assert oldest["settled"] > 0, (
        "the oldest committed cohort has no settled return windows — the trim is too "
        "narrow to contain a settled cohort at all"
    )
    assert newest["delivered"] / newest["n"] < oldest["delivered"] / oldest["n"], (
        "the newest cohort should have a lower delivered share; its shipments are "
        "still in transit"
    )


def test_the_fixture_manifest_records_its_provenance():
    """A reader should be able to see, without running anything, that the numbers came
    from a seeded generator and at what observation instant — the two facts that make
    the immature tail reproducible."""
    manifest = FIXTURE_DIR / "FIXTURES.md"
    assert manifest.exists()
    text = manifest.read_text()
    assert "Synthetic data" in text
    assert "Seed" in text
    assert "as_of" in text
