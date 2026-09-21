"""CSV export, built from the same evaluated lines the screen shows.

Included lines only, in sheet order. Every number comes from ``evaluate()``
rather than being recomputed here, which is what makes the file agree with
the page it was downloaded from.

Two details that matter for a file someone opens in Excel:

* **Formula injection.** A cell beginning ``=``, ``+``, ``-``, ``@``, tab or
  carriage return is executed as a formula on open. A supplier can put
  ``=HYPERLINK(...)`` in a product name, so text cells are prefixed with an
  apostrophe. Tab and CR are DDE vectors Excel honours just like ``=``.
* **UTF-8 BOM.** Without it Excel reads UTF-8 as the local code page and
  mangles anything non-ASCII.
"""

from __future__ import annotations

import csv
import io

from .evaluate import EvaluatedLine, EvaluatedOffer
from .money import format_amount

COLUMNS = (
    "offer_id", "supplier", "item_code", "description", "size", "category",
    "quantity", "unit_cost_usd", "line_value_usd", "retail_reference_usd",
    "source_row", "notes",
)

# Columns holding supplier-controlled text, so the ones an attacker reaches.
_TEXT_COLUMNS = frozenset(
    {"offer_id", "supplier", "item_code", "description", "size", "category", "notes"}
)

_RISKY_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_text(value: str) -> str:
    """Neutralise a cell a spreadsheet would otherwise execute."""
    return "'" + value if value[:1] in _RISKY_PREFIXES else value


def line_notes(line: EvaluatedLine) -> str:
    """What was fixed, what to look at, and anything the user wrote."""
    parts = [i.message for i in line.issues if i.kind == "fixed"]
    parts += [i.message for i in line.issues if i.kind == "warning"]
    if line.decision is not None and line.decision.note:
        parts.append(line.decision.note)
    return "; ".join(parts)


def export_rows(offer: EvaluatedOffer, *, offer_id: str, supplier: str | None) -> list[list[str]]:
    """Header row followed by one row per included line."""
    rows = [list(COLUMNS)]
    for line in offer.lines:
        if not line.included:
            continue
        retail = (
            format_amount(line.quantity * line.retail_units)
            if line.retail_units is not None and line.retail_units > 0 and line.quantity
            else ""
        )
        values = {
            "offer_id": offer_id,
            "supplier": supplier or "",
            "item_code": line.item_code or "",
            "description": line.description or "",
            "size": line.size or "",
            "category": line.category or "",
            "quantity": str(line.quantity),
            "unit_cost_usd": format_amount(line.unit_cost_units or 0),
            "line_value_usd": format_amount(line.line_value_units or 0),
            "retail_reference_usd": retail,
            "source_row": str(line.source_row),
            "notes": line_notes(line),
        }
        rows.append([
            escape_text(values[c]) if c in _TEXT_COLUMNS else values[c]
            for c in COLUMNS
        ])
    return rows


def to_csv(offer: EvaluatedOffer, *, offer_id: str, supplier: str | None) -> bytes:
    """The downloadable file: UTF-8 with BOM, CRLF line endings, no totals row."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerows(export_rows(offer, offer_id=offer_id, supplier=supplier))
    return buffer.getvalue().encode("utf-8-sig")
