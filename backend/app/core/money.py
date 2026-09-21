"""Exact money handling.

Every dollar amount in the system is an ``int`` counting ten-thousandths of a
dollar ("units"). $4.50 is 45_000; $0.0125 is 125.

Why not floats: 0.1 + 0.2 != 0.3 in binary floating point, so a 5,000-line
total can drift by a cent and the screen stops agreeing with the export.
Why not cents: supplier costs such as $0.125 per piece are real; four decimal
places covers them without rounding anyone's price.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

UNITS_PER_DOLLAR = 10_000
MAX_DECIMAL_PLACES = 4

_UNIT = Decimal(1) / UNITS_PER_DOLLAR  # 0.0001
_CENT = Decimal("0.01")


class MoneyPrecisionError(ValueError):
    """Raised when an amount has more precision than we can store exactly."""


def decimal_places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def to_units(value: Decimal) -> int:
    """Convert an exact Decimal dollar amount to integer units.

    Refuses to round: an amount like 1.23456 is a data problem to surface,
    not something to silently change.
    """
    if not value.is_finite():
        raise MoneyPrecisionError(f"{value} is not a finite amount")
    if decimal_places(value) > MAX_DECIMAL_PLACES:
        raise MoneyPrecisionError(
            f"{value} has more than {MAX_DECIMAL_PLACES} decimal places"
        )
    return int(value * UNITS_PER_DOLLAR)


def to_decimal(units: int) -> Decimal:
    return (Decimal(units) * _UNIT).quantize(_UNIT)


def format_amount(units: int) -> str:
    """Plain decimal string for files and APIs: at least 2 places, at most 4.

    45000 -> "4.50"; 125 -> "0.0125"; 1872145000 -> "187214.50".
    """
    exact = to_decimal(units)
    if exact == exact.quantize(_CENT):
        return str(exact.quantize(_CENT))
    return str(exact.normalize())


def format_usd(units: int) -> str:
    """Human display, rounded to cents: 1872145000 -> "$187,214.50"."""
    rounded = to_decimal(units).quantize(_CENT, rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    return f"{sign}${abs(rounded):,.2f}"
