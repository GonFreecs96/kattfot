"""numfmt.py — the single, tested authority for how a governed number is displayed.

Formatting used to be free-handed by the agent ("19.4%" with no codified rule),
which is exactly the kind of thing that drifts silently. This module pins it:
``format_value(value, value_format)`` is pure, deterministic, and golden-tested,
and the query helper emits the formatted strings so the rendered artifact never
depends on the model getting rounding right.

Rounding is **half-up** (the spreadsheet-intuitive direction), applied via
``Decimal`` at the display boundary only — never in any computation. The raw
float still travels in the result; this is purely the reporting-edge view.

Supported ``value_format`` values (the metric-definition enum):
  - ``percent``  — a ratio (0.841) shown as ``84.1%``  (1 decimal place)
  - ``count``    — an integer shown comma-grouped (``20,661``)
  - ``currency`` — USD, 2 decimals, comma-grouped (``$1,234.56``)
  - ``duration`` — a count of **seconds** humanized (``45s`` / ``12.5m`` / ``3.2h`` / ``4.1d``)

``duration`` assumes the underlying value is in **seconds**. That is an explicit,
documented convention: if a metric stores another unit, it must convert to
seconds upstream or declare a different format, rather than relying on a guess.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PERCENT_DP = 1
CURRENCY_DP = 2

NA = "n/a"


def _to_decimal(value):
    """Best-effort Decimal, or None if the value isn't a finite number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _half_up(d: Decimal, places: int) -> Decimal:
    quantum = Decimal(1).scaleb(-places)
    return d.quantize(quantum, rounding=ROUND_HALF_UP)


def _grouped(d: Decimal, places: int) -> str:
    """Comma-group an already-rounded Decimal to a fixed number of places."""
    sign = "-" if d < 0 else ""
    d = abs(d)
    if places == 0:
        return f"{sign}{int(d):,}"
    whole, _, frac = f"{d:.{places}f}".partition(".")
    return f"{sign}{int(whole):,}.{frac}"


def format_percent(value) -> str:
    d = _to_decimal(value)
    if d is None:
        return NA
    return f"{_half_up(d * 100, PERCENT_DP)}%"


def format_count(value) -> str:
    d = _to_decimal(value)
    if d is None:
        return NA
    return _grouped(_half_up(d, 0), 0)


def format_currency(value) -> str:
    d = _to_decimal(value)
    if d is None:
        return NA
    rounded = _half_up(d, CURRENCY_DP)
    sign = "-" if rounded < 0 else ""
    return f"{sign}${_grouped(abs(rounded), CURRENCY_DP)}"


def format_duration(value) -> str:
    """Humanize a duration given in **seconds**."""
    d = _to_decimal(value)
    if d is None:
        return NA
    secs = float(d)
    minute, hour, day = 60.0, 3600.0, 86400.0
    if abs(secs) < minute:
        return f"{int(round(secs))}s"
    if abs(secs) < hour:
        return f"{secs / minute:.1f}m"
    if abs(secs) < day:
        return f"{secs / hour:.1f}h"
    return f"{secs / day:.1f}d"


_FORMATTERS = {
    "percent": format_percent,
    "count": format_count,
    "currency": format_currency,
    "duration": format_duration,
}


def format_value(value, value_format: str | None) -> str:
    """Format ``value`` per the metric's ``value_format``.

    An unknown / missing format falls back to a plain string (a number is shown
    as-is rather than mangled), so a new format never silently renders wrong.
    """
    fmt = _FORMATTERS.get((value_format or "").lower())
    if fmt is None:
        return NA if value is None else str(value)
    return fmt(value)
