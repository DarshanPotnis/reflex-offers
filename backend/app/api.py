"""HTTP surface. Translates requests into service calls and results into JSON.

It computes nothing. Every number in a response came from `evaluate()`, and
every amount is a decimal **string** — a float would undo the exactness the
rest of the system is built on.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, File, Header, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt
from sqlalchemy.orm import Session

from . import service
from .core.evaluate import Change, EvaluatedLine, EvaluatedOffer, Summary
from .core.export import to_csv
from .core.export_xlsx import WorkbookContext, to_xlsx
from .core.money import format_amount
from .db import get_session
from .models import Offer, TEXT_FIELDS, NUMBER_FIELDS

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]


def fault_injection_enabled() -> bool:
    return os.environ.get("FAULT_INJECTION") == "1"


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class ChangeIn(BaseModel):
    """`quantity` is StrictInt so `"12"`, `true` and `12.0` are refused here
    as well as in `validate_change`. Two layers on purpose: the rule has to
    hold even if a future endpoint forgets the annotation."""

    model_config = ConfigDict(extra="forbid")

    line_id: str
    action: Literal["include", "exclude", "reset"]
    item_code: str | None = None
    size: str | None = None
    quantity: StrictInt | None = None
    unit_cost: str | None = None
    note: str | None = None

    def to_core(self) -> Change:
        return Change(
            line_id=self.line_id,
            action=self.action,
            item_code=self.item_code,
            size=self.size,
            quantity=self.quantity,
            unit_cost=self.unit_cost,
            note=self.note,
        )


class DecisionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    base_version: int
    changes: list[ChangeIn]


def canonical_body_hash(payload: DecisionsIn) -> str:
    """Hash the meaning of the request, not its bytes.

    A client retrying an identical save may re-serialise it with different
    key order or whitespace; that is the same request and must not be
    accused of having changed.
    """
    blob = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def _amount(units: int | None) -> str | None:
    return None if units is None else format_amount(units)


def offer_meta(offer: Offer) -> dict[str, Any]:
    return {
        "id": offer.id,
        "version": offer.version,
        "supplier_name": offer.supplier_name,
        "title": offer.title,
        "layout": offer.layout,
        "sheet_name": offer.sheet_name,
        "source_filename": offer.source_filename,
        "source_sha256": offer.source_sha256,
        "created_at": offer.created_at.isoformat(),
        "updated_at": offer.updated_at.isoformat(),
    }


def summary_json(summary: Summary) -> dict[str, Any]:
    return {
        "total_lines": summary.total_lines,
        "included_lines": summary.included_lines,
        "pieces": summary.pieces,
        "supplier_cost": format_amount(summary.supplier_cost_units),
        "retail_reference": format_amount(summary.retail_reference_units),
        "needs_decision_open": summary.needs_decision_open,
        "warnings": summary.warnings,
        "fixed": summary.fixed,
        "auto_excluded": summary.auto_excluded,
        "user_excluded": summary.user_excluded,
    }


def line_json(line: EvaluatedLine) -> dict[str, Any]:
    original: dict[str, Any] = {}
    for name in TEXT_FIELDS + NUMBER_FIELDS:
        parsed = getattr(line.original, name)
        value = parsed.value
        if name in NUMBER_FIELDS and name != "quantity" and value is not None:
            value = format_amount(value)
        original[name] = {"value": value, "status": parsed.status, "note": parsed.note}

    decision = line.decision
    return {
        "line_id": line.line_id,
        "position": line.position,
        "source_row": line.source_row,
        "item_code": line.item_code,
        "description": line.description,
        "size": line.size,
        "category": line.category,
        "quantity": line.quantity,
        "unit_cost": _amount(line.unit_cost_units),
        "retail": _amount(line.retail_units),
        "line_value": _amount(line.line_value_units),
        # Pieces x cost even while excluded, so a card comparing rows never
        # has to multiply money in the browser. Not part of any total.
        "would_be_line_value": _amount(line.would_be_value_units),
        "original": original,
        "cells": {
            name: {"coordinate": cell.coordinate, "raw": cell.raw}
            for name, cell in line.cells.items()
        },
        "issues": [
            {"code": i.code, "kind": i.kind, "message": i.message, "field": i.field}
            for i in line.issues
        ],
        "related_line_ids": list(line.related_line_ids),
        "status": line.status,
        "status_reason": line.status_reason,
        "needs_decision_open": line.needs_decision_open,
        "overridden_fields": list(line.overridden_fields),
        "decision": None if decision is None else {
            "action": decision.action,
            "item_code": decision.item_code,
            "size": decision.size,
            "quantity": decision.quantity,
            "unit_cost": _amount(decision.unit_cost_units),
            "note": decision.note,
        },
    }


def offer_json(offer: Offer, evaluated: EvaluatedOffer) -> dict[str, Any]:
    return {
        **offer_meta(offer),
        "notices": list(offer.notices or []),
        "summary": summary_json(evaluated.summary),
        "lines": [line_json(line) for line in evaluated.lines],
    }


def errors_json(errors: list[service.LineErrors]) -> dict[str, Any]:
    return {"errors": [{"line_id": e.line_id, "messages": e.messages} for e in errors]}


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

async def _read_limited(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1 << 20):
        total += len(chunk)
        if total > service.MAX_UPLOAD_BYTES:
            raise _http(413, "That file is larger than the 10 MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


class _HttpError(Exception):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body


def _http(status: int, detail: str, **extra: Any) -> _HttpError:
    return _HttpError(status, {"detail": detail, **extra})


@router.post("/offers")
async def upload_offer(
    response: Response,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    data = await _read_limited(file)
    if not data:
        raise _http(422, "That file is empty.")

    timings = service.Timings()
    try:
        offer, replayed = service.create_offer(
            session,
            data=data,
            filename=file.filename,
            upload_key=idempotency_key,
            timings=timings,
        )
    except service.UnsupportedUpload as exc:
        raise _http(422, str(exc)) from exc
    except service.UploadKeyReused as exc:
        raise _http(
            409,
            "That Idempotency-Key was already used for a different file.",
            offer_id=exc.existing_id,
        ) from exc

    # A receipt, not the offer. The client navigates to the offer page and
    # fetches it there, so building a 5.7 MB response here only to have it
    # replaced a moment later was pure waste — and it cost a full read-back
    # of all 5,000 stored lines to produce.
    response.status_code = 200 if replayed else 201
    response.headers["Server-Timing"] = timings.header()
    return {"offer_id": offer.id, "version": offer.version}


@router.get("/config")
def read_config() -> dict[str, Any]:
    """What the UI needs to know about this deployment.

    The test-mode toggle is rendered only when this says so, which is what
    keeps it out of production.
    """
    return {"fault_injection": fault_injection_enabled()}


@router.get("/offers")
def list_offers(session: SessionDep) -> dict[str, Any]:
    return {
        "offers": [
            {
                "id": offer.id,
                "supplier_name": offer.supplier_name,
                "source_filename": offer.source_filename,
                "created_at": offer.created_at.isoformat(),
                "version": offer.version,
            }
            for offer in service.recent_offers(session)
        ]
    }


@router.get("/offers/{offer_id}")
def read_offer(offer_id: str, response: Response, session: SessionDep) -> dict[str, Any]:
    timings = service.Timings()
    try:
        offer, evaluated = service.load_evaluated(session, offer_id, timings)
    except service.OfferNotFound as exc:
        raise _http(404, "No offer with that id.") from exc
    with timings.stage("serialize"):
        body = offer_json(offer, evaluated)
    response.headers["Server-Timing"] = timings.header()
    return body


@router.post("/offers/{offer_id}/decisions")
def save_decisions(
    offer_id: str,
    payload: DecisionsIn,
    response: Response,
    session: SessionDep,
    x_fault: Annotated[str | None, Header(alias="X-Fault")] = None,
) -> dict[str, Any]:
    timings = service.Timings()
    fault = x_fault if fault_injection_enabled() else None
    try:
        receipt, replayed = service.save_decisions(
            session,
            offer_id=offer_id,
            request_id=str(payload.request_id),
            base_version=payload.base_version,
            changes=[c.to_core() for c in payload.changes],
            body_sha256=canonical_body_hash(payload),
            fault=fault,
            timings=timings,
        )
    except service.OfferNotFound as exc:
        raise _http(404, "No offer with that id.") from exc
    except service.VersionConflict as exc:
        raise _HttpError(409, {
            "detail": "This offer changed in another window. Reload the latest version.",
            "current_version": exc.current_version,
        }) from exc
    except service.RequestReused as exc:
        raise _HttpError(422, errors_json([service.LineErrors(
            None, ["request_id reused with different changes."]
        )])) from exc
    except service.ChangesRejected as exc:
        raise _HttpError(422, errors_json(exc.errors)) from exc
    except service.FaultInjected as exc:
        raise _http(503, f"Injected failure ({exc.when}).") from exc

    response.headers["Server-Timing"] = timings.header()
    response.headers["X-Replayed"] = "true" if replayed else "false"
    return receipt


XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _public_origin(request: Request) -> str:
    """The origin the user's browser reached, for a link written into a file.

    Render terminates TLS at its proxy, so the app itself sees plain http; the
    proxy's X-Forwarded-Proto says what the user actually used. This only
    shapes a link in the requester's own download.
    """
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host") or request.url.netloc
    return f"{proto.split(',')[0].strip()}://{host}"


def _export(session: Session, request: Request, offer_id: str, suffix: str) -> Response:
    timings = service.Timings()
    try:
        offer, evaluated = service.load_evaluated(session, offer_id, timings)
    except service.OfferNotFound as exc:
        raise _http(404, "No offer with that id.") from exc
    with timings.stage("serialize"):
        if suffix == "xlsx":
            data = to_xlsx(
                evaluated,
                offer_id=offer.id,
                supplier=offer.supplier_name,
                context=WorkbookContext(
                    offer_url=f"{_public_origin(request)}/offers/{offer.id}",
                    source_filename=offer.source_filename,
                    sheet_name=offer.sheet_name,
                    version=offer.version,
                    exported_at=datetime.now(timezone.utc),
                ),
            )
            media_type = XLSX_MEDIA_TYPE
        else:
            data = to_csv(evaluated, offer_id=offer.id, supplier=offer.supplier_name)
            media_type = "text/csv; charset=utf-8"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="offer-{offer.id}.{suffix}"',
            "Server-Timing": timings.header(),
        },
    )


@router.get("/offers/{offer_id}/export.xlsx")
def export_offer_xlsx(offer_id: str, request: Request, session: SessionDep) -> Response:
    """The one the UI links to: Excel keeps 000101 a code, not the number 101."""
    return _export(session, request, offer_id, "xlsx")


@router.get("/offers/{offer_id}/export.csv")
def export_offer_csv(offer_id: str, request: Request, session: SessionDep) -> Response:
    """Kept for imports and for byte-exact decimal amounts."""
    return _export(session, request, offer_id, "csv")


@router.get("/offers/{offer_id}/source")
def download_source(offer_id: str, session: SessionDep) -> Response:
    try:
        offer, data = service.get_source(session, offer_id)
    except service.OfferNotFound as exc:
        raise _http(404, "No offer with that id.") from exc
    name = offer.source_filename or f"offer-{offer.id}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --------------------------------------------------------------------------
# Error handlers
# --------------------------------------------------------------------------

def _humanise(error: dict[str, Any]) -> str:
    field = error["loc"][-1] if error["loc"] else None
    if field == "quantity":
        return 'Pieces must be a whole number. Send 45, not "45" or 45.0.'
    if field == "unit_cost":
        return 'Supplier cost must be a decimal string like "4.50".'
    if error["type"] == "extra_forbidden":
        return f"Unknown field {field!r}."
    return f"{field}: {error['msg']}" if field else error["msg"]


async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
    """Give Pydantic's rejections the same shape as our own.

    A StrictInt violation is refused before any route runs, so without this
    the caller would get two different 422 bodies depending on which layer
    caught the problem.
    """
    assert isinstance(exc, RequestValidationError)
    try:
        body = json.loads(await request.body())
    except Exception:
        body = None
    changes = body.get("changes") if isinstance(body, dict) else None

    grouped: dict[str, list[str]] = defaultdict(list)
    general: list[str] = []
    for error in exc.errors():
        loc = error["loc"]
        line_id = None
        if (
            len(loc) >= 3 and loc[0] == "body" and loc[1] == "changes"
            and isinstance(loc[2], int) and isinstance(changes, list)
            and loc[2] < len(changes) and isinstance(changes[loc[2]], dict)
        ):
            line_id = changes[loc[2]].get("line_id")
        message = _humanise(error)
        if isinstance(line_id, str):
            grouped[line_id].append(message)
        else:
            general.append(message)

    errors = [{"line_id": line_id, "messages": msgs} for line_id, msgs in grouped.items()]
    if general:
        errors.append({"line_id": None, "messages": general})
    return JSONResponse(status_code=422, content={"errors": errors})


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, _HttpError)
    return JSONResponse(status_code=exc.status, content=exc.body)


def register_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(_HttpError, http_error_handler)
