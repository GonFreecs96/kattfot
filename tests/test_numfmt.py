"""Value formatting is codified and golden — the rendered number never depends
on the agent getting rounding right.
"""

import pytest

from _shared import numfmt as nf


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.841, "84.1%"),
        (0.8449, "84.5%"),   # half-up at the 1-dp boundary
        (0.8445, "84.5%"),   # .5 rounds up, not banker's-even down
        (1.0, "100.0%"),
        (0, "0.0%"),
        (-0.123, "-12.3%"),
    ],
)
def test_percent(value, expected):
    assert nf.format_percent(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (20661, "20,661"),
        (20661.0, "20,661"),   # integral float collapses
        (999, "999"),
        (1234567, "1,234,567"),
        (0, "0"),
        (-2500, "-2,500"),
    ],
)
def test_count(value, expected):
    assert nf.format_count(value) == expected


def test_count_rounds_half_up():
    assert nf.format_count(2.5) == "3"   # not banker's-even (which would give 2)


@pytest.mark.parametrize(
    "value,expected",
    [
        (1234.5, "$1,234.50"),
        (1234.567, "$1,234.57"),
        (0, "$0.00"),
        (-9.005, "-$9.01"),    # half-up
        (1000000, "$1,000,000.00"),
    ],
)
def test_currency(value, expected):
    assert nf.format_currency(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (5, "5s"),
        (45, "45s"),
        (90, "1.5m"),
        (3600, "1.0h"),     # exactly one hour rolls to hours
        (90000, "1.0d"),    # > 24h rolls to days
    ],
)
def test_duration_assumes_seconds(value, expected):
    assert nf.format_duration(value) == expected


def test_none_is_na_everywhere():
    for fmt in ("percent", "count", "currency", "duration"):
        assert nf.format_value(None, fmt) == "n/a"


def test_unknown_format_falls_back_to_plain_string():
    assert nf.format_value(0.841, "bogus") == "0.841"
    assert nf.format_value(None, "bogus") == "n/a"


def test_format_value_dispatches():
    assert nf.format_value(0.841, "percent") == "84.1%"
    assert nf.format_value(20661, "count") == "20,661"
    assert nf.format_value(12.5, "currency") == "$12.50"
