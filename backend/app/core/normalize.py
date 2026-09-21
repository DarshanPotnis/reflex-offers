"""Turn raw spreadsheet cells into typed values, reporting every change.

A parser never guesses. It returns one of four outcomes:

  ok          the cell already held a clean value of the right type
  normalized  the meaning is certain but the form changed ("$3.25" -> 3.25);
              the note says exactly what changed so the user can see it
  missing     the cell is empty
  invalid     the cell holds something we will not interpret ("TBD", "12.5"
              pieces, "3 dollars"); the raw value is kept for display

Rules about whether a value is *acceptable* (negative quantity, zero cost)
live in rules.py. This module only answers "what does this cell say?".
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Generic, Literal, TypeVar

from .money import MAX_DECIMAL_PLACES, decimal_places

T = TypeVar("T")
Status = Literal["ok", "normalized", "missing", "invalid"]


@dataclass(frozen=True)
class Parsed(Generic[T]):
    value: T | None
    status: Status
    note: str | None = None

    @property
    def usable(self) -> bool:
        return self.status in ("ok", "normalized")


_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_PLAIN_INT = re.compile(r"^[+-]?\d+$")
_WHOLE_WITH_ZERO_FRACTION = re.compile(r"^([+-]?\d+)\.0+$")
_PLAIN_DECIMAL = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_CURRENCY_PREFIX = re.compile(r"^(US\$|USD\s*\$?|\$)\s*", re.IGNORECASE)
_CURRENCY_SUFFIX = re.compile(r"\s*USD$", re.IGNORECASE)
_ZERO_PAD_FORMAT = re.compile(r"^0+$")
_INVISIBLE = dict.fromkeys(map(ord, "\u00a0\u200b\u200c\u200d\ufeff"), " ")


def _clean_text(raw: str) -> tuple[str, list[str]]:
    """Strip whitespace and invisible characters; describe what was removed."""
    notes: list[str] = []
    translated = raw.translate(_INVISIBLE)
    if translated != raw:
        notes.append("removed invisible characters")
    stripped = translated.strip()
    if stripped != translated:
        notes.append("trimmed spaces")
    return stripped, notes


def _is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.translate(_INVISIBLE).strip() == "")


def parse_text(raw: Any) -> Parsed[str]:
    if _is_blank(raw):
        return Parsed(None, "missing")
    if isinstance(raw, str):
        text, notes = _clean_text(raw)
        return Parsed(text, "normalized", "; ".join(notes)) if notes else Parsed(text, "ok")
    return Parsed(str(raw), "normalized", "converted to text")


def parse_item_code(raw: Any, number_format: str | None) -> Parsed[str]:
    """Item codes are identifiers, never numbers.

    A code typed as 000101 may be stored as the number 101 with a display
    format of "000000". Excel shows "000101"; a naive reader gets "101" and
    silently merges two different products. We restore the zeros from the
    cell's own format.
    """
    if _is_blank(raw):
        return Parsed(None, "missing")
    if isinstance(raw, bool):
        return Parsed(None, "invalid", f"unexpected value {raw!r}")
    if isinstance(raw, str):
        return parse_text(raw)
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not raw.is_integer():
            return Parsed(repr(raw), "normalized", "numeric code converted to text")
        whole = int(raw)
        fmt = (number_format or "").strip()
        if _ZERO_PAD_FORMAT.fullmatch(fmt) and len(str(abs(whole))) < len(fmt):
            padded = str(whole).zfill(len(fmt))
            return Parsed(padded, "normalized", f"restored leading zeros from cell format ({whole} -> {padded})")
        return Parsed(str(whole), "normalized", "numeric code converted to text")
    return Parsed(None, "invalid", f"unexpected value {raw!r}")


def parse_quantity(raw: Any) -> Parsed[int]:
    """Pieces are whole numbers. Sign is kept; rules.py decides if it's allowed."""
    if _is_blank(raw):
        return Parsed(None, "missing")
    if isinstance(raw, bool) or isinstance(raw, (_dt.date, _dt.datetime, _dt.time)):
        return Parsed(None, "invalid", f"not a quantity: {raw!r}")
    if isinstance(raw, int):
        return Parsed(raw, "ok")
    if isinstance(raw, float):
        if raw.is_integer():
            return Parsed(int(raw), "ok")
        return Parsed(None, "invalid", f"{raw} is not a whole number of pieces")
    if isinstance(raw, Decimal):
        if raw == raw.to_integral_value():
            return Parsed(int(raw), "ok")
        return Parsed(None, "invalid", f"{raw} is not a whole number of pieces")
    if isinstance(raw, str):
        text, notes = _clean_text(raw)
        if _THOUSANDS.fullmatch(text) and "." not in text:
            notes.append("removed thousands separators")
            text = text.replace(",", "")
        match = _WHOLE_WITH_ZERO_FRACTION.fullmatch(text)
        if match:
            text = match.group(1)
        if _PLAIN_INT.fullmatch(text):
            notes.insert(0, f"text {raw!r} read as number")
            return Parsed(int(text), "normalized", "; ".join(notes))
        return Parsed(None, "invalid", f"{raw.strip()!r} is not a quantity")
    return Parsed(None, "invalid", f"not a quantity: {raw!r}")


def parse_money(raw: Any) -> Parsed[Decimal]:
    """Dollar amounts. Accepts "$3.25", "1,200.50", "3.25 USD"; nothing looser."""
    if _is_blank(raw):
        return Parsed(None, "missing")
    if isinstance(raw, bool) or isinstance(raw, (_dt.date, _dt.datetime, _dt.time)):
        return Parsed(None, "invalid", f"not an amount: {raw!r}")

    if isinstance(raw, (int, float, Decimal)):
        try:
            # str() of a float is its shortest round-trip form: 1.8 -> "1.8",
            # not 1.8000000000000000444. That is the value the supplier typed.
            value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
        except InvalidOperation:
            return Parsed(None, "invalid", f"not an amount: {raw!r}")
        if not value.is_finite():
            return Parsed(None, "invalid", f"not an amount: {raw!r}")
        status: Status = "ok"
        note = None
    elif isinstance(raw, str):
        text, notes = _clean_text(raw)
        without_symbol = _CURRENCY_SUFFIX.sub("", _CURRENCY_PREFIX.sub("", text))
        if without_symbol != text:
            notes.append("removed currency symbol")
            text = without_symbol.strip()
        if _THOUSANDS.fullmatch(text):
            notes.append("removed thousands separators")
            text = text.replace(",", "")
        if not _PLAIN_DECIMAL.fullmatch(text):
            return Parsed(None, "invalid", f"{raw.strip()!r} is not an amount")
        value = Decimal(text)
        notes.insert(0, f"text {raw!r} read as number")
        status, note = "normalized", "; ".join(notes)
    else:
        return Parsed(None, "invalid", f"not an amount: {raw!r}")

    if decimal_places(value) > MAX_DECIMAL_PLACES:
        return Parsed(None, "invalid", f"{value} has more than {MAX_DECIMAL_PLACES} decimal places")
    return Parsed(value, status, note)
