"""Tables, and the one mechanical mapping between rows and core objects.

`offer_lines` stores each parsed field flat (`size`, `size_status`,
`size_note`) because that is what a database is good at; `StoredLine` groups
them into `Parsed` objects because that is what the rules are written
against. This module is the only place that knows both shapes.

Money columns are `BigInteger`: a unit cost of $1,000,000 is 10^10 units,
past the range of a 32-bit integer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .core.evaluate import Decision, StoredCell, StoredLine
from .core.normalize import Parsed
from .core.rules import Issue
from .db import Base

TEXT_FIELDS = ("item_code", "description", "size", "category")
NUMBER_FIELDS = ("quantity", "unit_cost_units", "retail_units")


def now() -> datetime:
    return datetime.now(UTC)


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supplier_name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(512))
    layout: Mapped[str] = mapped_column(String(32))
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    source_filename: Mapped[str | None] = mapped_column(String(512))
    source_sha256: Mapped[str] = mapped_column(String(64))
    notices: Mapped[list] = mapped_column(JSON, default=list)
    upload_key: Mapped[str | None] = mapped_column(String(255), unique=True)

    __table_args__ = (Index("ix_offers_created_at", "created_at"),)


class OfferSource(Base):
    """The original upload, kept so a reviewer can check against the sheet."""

    __tablename__ = "offer_sources"

    offer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("offers.id"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column("bytes", LargeBinary)


class OfferLine(Base):
    """Written once at upload and never edited. Edits live in `decisions`,
    so the original parse stays available to compare against."""

    __tablename__ = "offer_lines"

    offer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("offers.id"), primary_key=True
    )
    line_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    source_row: Mapped[int] = mapped_column(Integer)

    item_code: Mapped[str | None] = mapped_column(String(255))
    item_code_status: Mapped[str] = mapped_column(String(16))
    item_code_note: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    description_status: Mapped[str] = mapped_column(String(16))
    description_note: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(String(255))
    size_status: Mapped[str] = mapped_column(String(16))
    size_note: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(255))
    category_status: Mapped[str] = mapped_column(String(16))
    category_note: Mapped[str | None] = mapped_column(Text)

    quantity: Mapped[int | None] = mapped_column(Integer)
    quantity_status: Mapped[str] = mapped_column(String(16))
    quantity_note: Mapped[str | None] = mapped_column(Text)
    unit_cost_units: Mapped[int | None] = mapped_column(BigInteger)
    unit_cost_units_status: Mapped[str] = mapped_column(String(16))
    unit_cost_units_note: Mapped[str | None] = mapped_column(Text)
    retail_units: Mapped[int | None] = mapped_column(BigInteger)
    retail_units_status: Mapped[str] = mapped_column(String(16))
    retail_units_note: Mapped[str | None] = mapped_column(Text)

    cells: Mapped[dict] = mapped_column(JSON, default=dict)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    related_line_ids: Mapped[list] = mapped_column(JSON, default=list)
    default_included: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (Index("ix_offer_lines_position", "offer_id", "position"),)


class DecisionRow(Base):
    """Upserted, never appended. Saving the same decision twice cannot double
    anything, which is what makes a retry safe."""

    __tablename__ = "decisions"

    offer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("offers.id"), primary_key=True
    )
    line_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(16))
    item_code: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[int | None] = mapped_column(Integer)
    unit_cost_units: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class SaveRequest(Base):
    """One row per accepted save. Turns a retry into a lookup."""

    __tablename__ = "save_requests"

    offer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("offers.id"), primary_key=True
    )
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    receipt: Mapped[dict] = mapped_column(JSON)
    body_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)


# --------------------------------------------------------------------------
# Rows <-> core objects
# --------------------------------------------------------------------------

def issue_to_json(issue: Issue) -> dict:
    return {
        "code": issue.code,
        "kind": issue.kind,
        "message": issue.message,
        "field": issue.field,
    }


def issue_from_json(raw: dict) -> Issue:
    return Issue(raw["code"], raw["kind"], raw["message"], raw.get("field"))


def line_values(offer_id: str, line: StoredLine) -> dict[str, Any]:
    """Column values for one `offer_lines` row, ready for an executemany."""
    values: dict[str, Any] = {
        "offer_id": offer_id,
        "line_id": line.line_id,
        "position": line.position,
        "source_row": line.source_row,
        "cells": {
            name: {"coordinate": cell.coordinate, "raw": cell.raw}
            for name, cell in line.cells.items()
        },
        "issues": [issue_to_json(i) for i in line.issues],
        "related_line_ids": list(line.related_line_ids),
        "default_included": line.default_included,
    }
    for name in TEXT_FIELDS + NUMBER_FIELDS:
        parsed: Parsed[Any] = getattr(line, name)
        values[name] = parsed.value
        values[f"{name}_status"] = parsed.status
        values[f"{name}_note"] = parsed.note
    return values


def to_stored_line(row: OfferLine) -> StoredLine:
    fields = {
        name: Parsed(
            getattr(row, name),
            getattr(row, f"{name}_status"),
            getattr(row, f"{name}_note"),
        )
        for name in TEXT_FIELDS + NUMBER_FIELDS
    }
    return StoredLine(
        line_id=row.line_id,
        position=row.position,
        source_row=row.source_row,
        cells={
            name: StoredCell(cell["coordinate"], cell["raw"])
            for name, cell in (row.cells or {}).items()
        },
        issues=tuple(issue_from_json(i) for i in (row.issues or [])),
        related_line_ids=tuple(row.related_line_ids or []),
        default_included=row.default_included,
        **fields,
    )


def decision_values(offer_id: str, decision: Decision) -> dict[str, Any]:
    return {
        "offer_id": offer_id,
        "line_id": decision.line_id,
        "action": decision.action,
        "item_code": decision.item_code,
        "size": decision.size,
        "quantity": decision.quantity,
        "unit_cost_units": decision.unit_cost_units,
        "note": decision.note,
        "updated_at": now(),
    }


def to_decision(row: DecisionRow) -> Decision:
    return Decision(
        line_id=row.line_id,
        action=row.action,  # type: ignore[arg-type]
        item_code=row.item_code,
        size=row.size,
        quantity=row.quantity,
        unit_cost_units=row.unit_cost_units,
        note=row.note,
    )
