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
"""

import csv
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_fixtures.py"
FIXTURE_DIR = ROOT / "data" / "assay" / "sample"
WEEKLY_CSV = FIXTURE_DIR / "cart_checkout_funnel_weekly.csv"

DIMENSIONS = ("channel", "payment_method", "market", "category", "checkout_version")


@pytest.fixture(scope="module")
def weekly_rows():
    assert WEEKLY_CSV.exists(), (
        f"{WEEKLY_CSV} is missing — run `python3 scripts/generate_fixtures.py`"
    )
    with WEEKLY_CSV.open() as f:
        return list(csv.DictReader(f))


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


def test_committed_csv_matches_a_fresh_run_of_the_committed_generator(tmp_path):
    """The committed fixture must be reproducible from the committed generator with its
    default arguments. If they drift apart, the CSV becomes an unexplained artifact
    nobody can regenerate or review."""
    subprocess.run(
        [sys.executable, str(GENERATOR), "--out-dir", str(tmp_path), "--quiet"],
        check=True, cwd=ROOT, capture_output=True,
    )
    fresh = (tmp_path / "cart_checkout_funnel_weekly.csv").read_bytes()
    assert fresh == WEEKLY_CSV.read_bytes(), (
        "the committed CSV does not match a default run of the committed generator — "
        "regenerate it (`python3 scripts/generate_fixtures.py`) or explain the drift"
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
