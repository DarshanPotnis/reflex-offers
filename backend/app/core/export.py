"""Export rows, built from the same evaluated lines the screen shows.

Included lines only, in sheet order. Every number comes from ``evaluate()``
rather than being recomputed here, which is what makes the file agree with
the page it was downloaded from.

``export_records`` is the single source both file formats render, so the CSV
and the workbook cannot drift apart. ``export_xlsx`` writes the workbook.

Retail is reported as two explicit columns. A single "retail reference"
sitting beside the unit cost reads like a unit price when it is in fact the
line total — the one thing this whole app exists to stop.

Two details that matter for a file someone opens in Excel:

* **Formula injection.** A cell beginning ``=``, ``+``, ``-``, ``@``, tab or
  carriage return is executed as a formula on open. A supplier can put
  ``=HYPERLINK(...)`` in a product name, so text cells in the CSV are
  prefixed with an apostrophe. (The workbook handles this differently; see
  ``export_xlsx``.)
* **UTF-8 BOM.** Without it Excel reads UTF-8 as the local code page and
  mangles anything non-ASCII.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .evaluate import EvaluatedLine, EvaluatedOffer
from .money import format_amount

COLUMNS = (
    "offer_id", "supplier", "item_code", "description", "size", "category",
    "quantity", "unit_cost_usd", "line_value_usd", "retail_unit_usd",
    "retail_line_usd", "source_row", "notes",
)

# Columns holding supplier-controlled text, so the ones an attacker reaches.
_TEXT_COLUMNS = frozenset(
    {"offer_id", "supplier", "item_code", "description", "size", "category", "notes"}
)

_RISKY_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ExportRecord:
    """One included line, money still in integer units."""

    offer_id: str
    supplier: str
    item_code: str
    description: str
    size: str
    category: str
    quantity: int
    unit_cost_units: int
    line_value_units: int
    retail_unit_units: int | None
    retail_line_units: int | None
    source_row: int
    notes: str


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


def export_records(
    offer: EvaluatedOffer, *, offer_id: str, supplier: str | None
) -> list[ExportRecord]:
    records: list[ExportRecord] = []
    for line in offer.lines:
        if not line.included:
            continue
        quantity = line.quantity or 0
        retail_unit = (
            line.retail_units
            if line.retail_units is not None and line.retail_units > 0
            else None
        )
        records.append(ExportRecord(
            offer_id=offer_id,
            supplier=supplier or "",
            item_code=line.item_code or "",
            description=line.description or "",
            size=line.size or "",
            category=line.category or "",
            quantity=quantity,
            unit_cost_units=line.unit_cost_units or 0,
            line_value_units=line.line_value_units or 0,
            retail_unit_units=retail_unit,
            retail_line_units=None if retail_unit is None else quantity * retail_unit,
            source_row=line.source_row,
            notes=line_notes(line),
        ))
    return records


def _as_strings(record: ExportRecord) -> dict[str, str]:
    money = lambda units: "" if units is None else format_amount(units)  # noqa: E731
    return {
        "offer_id": record.offer_id,
        "supplier": record.supplier,
        "item_code": record.item_code,
        "description": record.description,
        "size": record.size,
        "category": record.category,
        "quantity": str(record.quantity),
        "unit_cost_usd": format_amount(record.unit_cost_units),
        "line_value_usd": format_amount(record.line_value_units),
        "retail_unit_usd": money(record.retail_unit_units),
        "retail_line_usd": money(record.retail_line_units),
        "source_row": str(record.source_row),
        "notes": record.notes,
    }


def export_rows(offer: EvaluatedOffer, *, offer_id: str, supplier: str | None) -> list[list[str]]:
    """Header row followed by one row per included line."""
    rows = [list(COLUMNS)]
    for record in export_records(offer, offer_id=offer_id, supplier=supplier):
        values = _as_strings(record)
        rows.append([
            escape_text(values[c]) if c in _TEXT_COLUMNS else values[c]
            for c in COLUMNS
        ])
    return rows


def to_csv(offer: EvaluatedOffer, *, offer_id: str, supplier: str | None) -> bytes:
    """UTF-8 with BOM, CRLF line endings, no totals row.

    Every amount is written as its exact decimal string, so this file — not
    the workbook — is the exact-value artifact. A spreadsheet's numeric cell
    is a float by definition; see ``export_xlsx``.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerows(export_rows(offer, offer_id=offer_id, supplier=supplier))
    return buffer.getvalue().encode("utf-8-sig")
