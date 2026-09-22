"""The downloadable workbook — the format an operations person actually opens.

Why this exists as well as the CSV: Excel reads a CSV column of digits as a
number, so item code ``000101`` opens as ``101``. That is precisely the trap
`parse_item_code` works to survive, undone at the last step. Here the code is
written as a **text** cell and stays ``000101``.

Three things this file gets deliberately right:

**Text cells stay text.** openpyxl turns a string starting with ``=`` into a
formula, so a supplier description of ``=HYPERLINK(...)`` would become live on
open. Every text cell is forced back to ``data_type = "s"`` after assignment,
which writes it as an inline string — no apostrophe needed, unlike the CSV.
Item codes and sizes also get the ``@`` (text) number format, so ``000101``
keeps its zeros and a size of ``1/2`` is not read as a date.

**Money is numeric, so the columns can be summed.** A spreadsheet's numeric
cell is an IEEE double — that is the file format, not a choice we get to
make. Our code never builds a float: it hands openpyxl the exact ``Decimal``
from `money.to_decimal` and lets the library serialise it. For everything in
our range this round-trips exactly, and a test reads the workbook back and
checks every amount against the summary. The CSV remains the exact-decimal
artifact for anything that needs to be byte-exact.

**Retail is labelled.** Two columns, both saying "reference only", so a retail
figure can never be mistaken for what we pay.

A **Summary** sheet comes first, so a file forwarded on its own still says
what it is: the offer link, supplier, source file, the saved version it was
built from and when, the totals, and every line left out with its reason.
The offer id lives there rather than repeated down 5,000 rows.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from .evaluate import EvaluatedOffer
from .export import ExportRecord, export_records, left_out_records
from .money import to_decimal

SHEET_NAME = "Offer"
SUMMARY_SHEET = "Summary"

# (field, header, kind, width)
COLUMN_SPEC: tuple[tuple[str, str, str, int], ...] = (
    ("supplier", "Supplier", "text", 20),
    ("item_code", "Item code", "code", 14),
    ("description", "Description", "text", 30),
    ("size", "Size", "code", 10),
    ("category", "Category", "text", 14),
    ("quantity", "Pieces", "count", 10),
    ("unit_cost_usd", "Supplier cost / piece", "money", 18),
    ("line_value_usd", "Line value", "money", 14),
    ("retail_unit_usd", "Retail / piece (reference only)", "money", 26),
    ("retail_line_usd", "Retail line (reference only)", "money", 26),
    ("source_row", "Source row", "int", 11),
    ("notes", "Notes", "text", 60),
)

# At least two decimals, up to four, so a $0.0125 cost is not shown as $0.01.
MONEY_FORMAT = '"$"#,##0.00##'
COUNT_FORMAT = "#,##0"

# Summary sheet: labels in A; the left-out table spans A-G beneath them.
SUMMARY_WIDTHS = (40, 16, 14, 10, 30, 10, 20)
LEFT_OUT_HEADERS = (
    "Why it was left out", "Source row", "Item code", "Size", "Description",
    "Pieces", "Supplier cost / piece",
)


@dataclass(frozen=True)
class WorkbookContext:
    """Where the file came from, for the Summary sheet. Supplied by the API:
    the core never reads the clock or the request."""

    offer_url: str | None = None
    source_filename: str | None = None
    sheet_name: str | None = None
    version: int | None = None
    exported_at: datetime | None = None


def _values(record: ExportRecord) -> dict[str, object]:
    return {
        "supplier": record.supplier,
        "item_code": record.item_code,
        "description": record.description,
        "size": record.size,
        "category": record.category,
        "quantity": record.quantity,
        "unit_cost_usd": record.unit_cost_units,
        "line_value_usd": record.line_value_units,
        "retail_unit_usd": record.retail_unit_units,
        "retail_line_usd": record.retail_line_units,
        "source_row": record.source_row,
        "notes": record.notes,
    }


def _text_cell(sheet: Any, value: str, *, as_code: bool) -> Any:
    """A string that stays a string.

    openpyxl infers a leading '=' as a formula, so `data_type` is forced back
    after construction. `as_code` additionally pins the display format to text,
    which is what keeps 000101 from opening as 101 and a size of 1/2 from
    being read as a date.
    """
    cell = WriteOnlyCell(sheet, value=value)
    cell.data_type = "s"
    if as_code:
        cell.number_format = "@"
    return cell


def _money_cell(sheet: Any, units: int) -> Any:
    cell = WriteOnlyCell(sheet, value=to_decimal(units))
    cell.number_format = MONEY_FORMAT
    return cell


def _count_cell(sheet: Any, value: int) -> Any:
    cell = WriteOnlyCell(sheet, value=value)
    cell.number_format = COUNT_FORMAT
    return cell


def _bold(sheet: Any, text: str, size: int | None = None) -> Any:
    cell = WriteOnlyCell(sheet, value=text)
    cell.data_type = "s"
    cell.font = Font(bold=True, size=size) if size else Font(bold=True)
    return cell


def _write_summary(
    sheet: Any,
    offer: EvaluatedOffer,
    *,
    offer_id: str,
    supplier: str | None,
    context: WorkbookContext,
) -> None:
    for index, width in enumerate(SUMMARY_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    def text(value: str | None, *, as_code: bool = False) -> Any:
        return _text_cell(sheet, value, as_code=as_code) if value else None

    link = text(context.offer_url)
    if link is not None:
        link.hyperlink = context.offer_url

    exported = (
        context.exported_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if context.exported_at else None
    )
    summary = offer.summary
    left_out = left_out_records(offer)

    sheet.append([_bold(sheet, "Supplier offer", size=14)])
    sheet.append([])
    sheet.append(["Offer link", link])
    sheet.append(["Offer ID", text(offer_id)])
    sheet.append(["Supplier", text(supplier)])
    sheet.append(["Source file", text(context.source_filename)])
    sheet.append(["Sheet read", text(context.sheet_name)])
    sheet.append(["Saved version", context.version])
    sheet.append(["Exported at", text(exported)])
    sheet.append([])
    sheet.append([_bold(sheet, "Totals (included lines only)")])
    sheet.append(["Lines included", _count_cell(sheet, summary.included_lines)])
    sheet.append(["Pieces", _count_cell(sheet, summary.pieces)])
    sheet.append(["Supplier cost total", _money_cell(sheet, summary.supplier_cost_units)])
    sheet.append([
        "Retail reference total (not what we pay)",
        _money_cell(sheet, summary.retail_reference_units),
    ])
    sheet.append(["Lines left out", _count_cell(sheet, len(left_out))])
    sheet.append([])
    sheet.append([_bold(sheet, "Lines left out, and why")])
    if not left_out:
        sheet.append(["None: every line in the sheet is included."])
        return
    sheet.append([_bold(sheet, label) for label in LEFT_OUT_HEADERS])
    wrapped = Alignment(vertical="top", wrap_text=True)
    for record in left_out:
        reason = _text_cell(sheet, record.reason, as_code=False)
        reason.alignment = wrapped
        sheet.append([
            reason,
            record.source_row,
            text(record.item_code, as_code=True),
            text(record.size, as_code=True),
            text(record.description),
            None if record.quantity is None else _count_cell(sheet, record.quantity),
            None if record.unit_cost_units is None else _money_cell(sheet, record.unit_cost_units),
        ])


def to_xlsx(
    offer: EvaluatedOffer,
    *,
    offer_id: str,
    supplier: str | None,
    context: WorkbookContext | None = None,
) -> bytes:
    """A Summary sheet, then the included lines from saved state. No totals row
    on the lines sheet; the totals are on the Summary.

    Written in openpyxl's `write_only` mode: rows are streamed straight to the
    sheet instead of being held as 5,000 rows of live Cell objects. The normal
    API spent ~3 s of a deployed request building that object graph.
    """
    workbook = Workbook(write_only=True)
    _write_summary(
        workbook.create_sheet(SUMMARY_SHEET),
        offer,
        offer_id=offer_id,
        supplier=supplier,
        context=context or WorkbookContext(),
    )
    sheet = workbook.create_sheet(SHEET_NAME)

    # Both have to be set before any row is appended.
    for index, (_, _, _, width) in enumerate(COLUMN_SPEC, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"

    bold = Font(bold=True)
    wrapped = Alignment(vertical="top", wrap_text=True)
    header_row = []
    for _, label, _, _ in COLUMN_SPEC:
        cell = WriteOnlyCell(sheet, value=label)
        cell.data_type = "s"
        cell.font = bold
        cell.alignment = wrapped
        header_row.append(cell)
    sheet.append(header_row)

    rows = 1
    for record in export_records(offer, offer_id=offer_id, supplier=supplier):
        values = _values(record)
        row: list[Any] = []
        for field, _, kind, _width in COLUMN_SPEC:
            value = values[field]
            if kind == "money":
                row.append(None if value is None else _money_cell(sheet, value))  # type: ignore[arg-type]
            elif kind == "count":
                row.append(_count_cell(sheet, value))  # type: ignore[arg-type]
            elif kind == "int":
                row.append(value)
            else:
                text = str(value)
                row.append(
                    _text_cell(sheet, text, as_code=(kind == "code")) if text else None
                )
        sheet.append(row)
        rows += 1

    sheet.auto_filter.ref = f"A1:{get_column_letter(len(COLUMN_SPEC))}{rows}"

    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()  # write_only keeps a temp file open until closed
    return buffer.getvalue()


def read_amount(value: object) -> Decimal | None:
    """Read a money cell back as an exact Decimal.

    openpyxl hands back a float, because that is what the cell holds. The
    shortest round-trip representation of that double is the decimal we
    wrote; going via ``repr`` recovers it without inventing digits.
    """
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(repr(value))
