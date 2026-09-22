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

Notes are worded for a file, not the screen. Issue messages are written for
someone looking at the app ("Enter one to include this line", "Choose which
to keep"); a downloaded file travels without the app, so those instructions
are dropped, a note never claims something the saved decisions have since
made untrue ("counted once here" after both copies were counted), and any
value a reviewer typed says so, with what the sheet held.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any

from .evaluate import EvaluatedLine, EvaluatedOffer
from .money import format_amount
from .reader import FIELD_LABELS

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


# Sentences in issue messages that tell the person on screen what to do next.
# In a file they are noise, so notes drop them. rules.py writes them; a test
# checks no note in any fixture still carries one.
_SCREEN_ONLY = (
    " Enter one to include this line.",
    " Choose which to keep.",
    " Include only if the supplier really has two separate lots.",
    " Check the original.",
)

# Issue field name -> attribute on StoredLine / EvaluatedLine. Money is stored
# in units under a different name, and overridden_fields uses that name too.
_STORED = {
    "item_code": "item_code", "description": "description", "size": "size",
    "category": "category", "quantity": "quantity",
    "unit_cost": "unit_cost_units", "retail": "retail_units",
}
_OVERRIDABLE = ("item_code", "size", "quantity", "unit_cost")


def file_wording(message: str) -> str:
    """An issue message as a note in a file: no instructions, no final period."""
    for sentence in _SCREEN_ONLY:
        message = message.replace(sentence, "")
    return message.strip().rstrip(".")


def _rows(lines: list[EvaluatedLine]) -> str:
    rows = sorted(line.source_row for line in lines)
    if len(rows) == 1:
        return f"row {rows[0]}"
    return f"rows {', '.join(map(str, rows[:-1]))} and {rows[-1]}"


def _sheet_text(line: EvaluatedLine, field: str) -> str:
    """The cell as the supplier wrote it. Text is quoted so stray spaces show."""
    cell = line.cells.get(field)
    raw = None if cell is None else cell.raw
    if raw is None or raw == "":
        return "blank"
    return f"'{raw}'" if isinstance(raw, str) else str(raw)


def _value_text(field: str, value: Any) -> str:
    if field in ("unit_cost", "retail"):
        return format_amount(value)
    return f"'{value}'" if isinstance(value, str) else str(value)


def _siblings(line: EvaluatedLine, by_id: dict[str, EvaluatedLine]) -> list[EvaluatedLine]:
    return [by_id[i] for i in line.related_line_ids if i in by_id]


def _cleaned_up(line: EvaluatedLine, overridden: set[str]) -> list[str]:
    """'Pieces: sheet had ' 45 ', read as 45' — the change, not how we made it."""
    parts = []
    for issue in line.issues:
        if issue.kind != "fixed":
            continue
        field = issue.field
        if field in overridden:
            continue  # the reviewer's value replaced this; that note says so
        if field not in _STORED or field not in line.cells:
            parts.append(file_wording(issue.message))
            continue
        read_as = getattr(line.original, _STORED[field]).value
        parts.append(
            f"{FIELD_LABELS[field]}: sheet had {_sheet_text(line, field)}, "
            f"read as {_value_text(field, read_as)}"
        )
    return parts


def _duplicate_kept(line: EvaluatedLine, message: str, by_id: dict[str, EvaluatedLine]) -> str | None:
    """True only while the copies are still out. Once one is counted too,
    the DUPLICATE_AFTER_EDIT warning says so and this note would contradict it."""
    siblings = _siblings(line, by_id)
    if not siblings or any(s.included for s in siblings):
        return None
    verb = "is" if len(siblings) == 1 else "are"
    note = f"Identical to {_rows(siblings)}, which {verb} left out so the stock is counted once"
    if "Only the text differs: " in message:
        note = (
            f"Same pieces, cost and retail as {_rows(siblings)}, which {verb} left out so "
            f"the stock is counted once; only the text differs: "
            f"{message.split('Only the text differs: ', 1)[1].rstrip('.')}"
        )
    return note


def _included_by_reviewer(line: EvaluatedLine, by_id: dict[str, EvaluatedLine]) -> str:
    """Why a line the tool held back is in the file after all."""
    codes = {i.code for i in line.original.issues}
    siblings = _siblings(line, by_id)
    also = [s for s in siblings if s.included]
    left = [s for s in siblings if not s.included]
    if "CONFLICTING_ROWS" in codes and siblings:
        if not also:
            return f"Chosen by reviewer over {_rows(left)}, which had different values"
        return f"Included by reviewer alongside {_rows(also)}" + (
            f"; {_rows(left)} left out" if left else ""
        )
    if "DUPLICATE_ROW" in codes and siblings:
        if also:
            return f"Included by reviewer as a separate lot from {_rows(also)}"
        return f"Included by reviewer in place of {_rows(left)}"
    return "Included by reviewer"


def line_notes(line: EvaluatedLine, by_id: dict[str, EvaluatedLine]) -> str:
    """What was cleaned up, what to look at, what the reviewer did and wrote."""
    decision = line.decision
    overridden = {f for f in _OVERRIDABLE if _STORED[f] in line.overridden_fields}

    parts = _cleaned_up(line, overridden)
    for issue in line.issues:
        if issue.kind != "warning":
            continue
        if issue.code == "DUPLICATE_KEPT":
            note = _duplicate_kept(line, issue.message, by_id)
            if note:
                parts.append(note)
        else:
            parts.append(file_wording(issue.message))

    if decision is not None and decision.action == "include":
        if not line.original.default_included:
            parts.append(_included_by_reviewer(line, by_id))
        for field in _OVERRIDABLE:
            if field in overridden:
                value = getattr(line, _STORED[field])
                parts.append(
                    f"{FIELD_LABELS[field]} set by reviewer to {_value_text(field, value)} "
                    f"(sheet: {_sheet_text(line, field)})"
                )
    if decision is not None and decision.note:
        parts.append(f"Reviewer's note: {decision.note}")
    return "; ".join(parts)


@dataclass(frozen=True)
class LeftOutRecord:
    """A line that is not in the file, and the reason, for the Summary sheet."""

    source_row: int
    item_code: str
    size: str
    description: str
    quantity: int | None
    unit_cost_units: int | None
    reason: str


def left_out_reason(line: EvaluatedLine) -> str:
    decision = line.decision
    if decision is not None and decision.action == "exclude":
        return f"Excluded by reviewer: {decision.note}" if decision.note else "Excluded by reviewer"
    auto = [file_wording(i.message) for i in line.issues if i.kind == "auto_excluded"]
    if line.needs_decision_open:
        blocking = [file_wording(i.message) for i in line.issues if i.kind == "needs_decision"]
        return "Awaiting a decision: " + "; ".join(blocking + auto)
    if auto:
        return "; ".join(auto)
    return file_wording(line.status_reason)


def left_out_records(offer: EvaluatedOffer) -> list[LeftOutRecord]:
    """Every line out of the totals, in sheet order, each with its reason."""
    return [
        LeftOutRecord(
            source_row=line.source_row,
            item_code=line.item_code or "",
            size=line.size or "",
            description=line.description or "",
            quantity=line.quantity,
            unit_cost_units=line.unit_cost_units,
            reason=left_out_reason(line),
        )
        for line in offer.lines
        if not line.included
    ]


def export_records(
    offer: EvaluatedOffer, *, offer_id: str, supplier: str | None
) -> list[ExportRecord]:
    by_id = offer.by_id()
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
            notes=line_notes(line, by_id),
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
