"""Every transaction boundary in the application lives here.

`api.py` translates HTTP; `app/core` decides what numbers mean; this module
is the only place that opens, commits and rolls back.

The two hard problems it solves:

**Losing work.** Two people saving the same offer must not overwrite each
other. The version is bumped with a conditional `UPDATE ... WHERE version =
:base`, which both detects a stale base and takes the row lock that
serialises the rest of the transaction. A plain read-then-write loses updates
under Postgres READ COMMITTED.

**Doubling work.** A save that commits but fails to reach the client will be
retried. `request_id` makes the retry a lookup instead of a second write.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .core.evaluate import Change, Decision, EvaluatedOffer, evaluate, prepare_change, store
from .core.reader import UnsupportedWorkbook, read_workbook
from .core.rules import analyze
from .models import (
    DecisionRow,
    Offer,
    OfferLine,
    OfferSource,
    SaveRequest,
    decision_values,
    line_values,
    now,
    to_decision,
    to_stored_line,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
RECENT_OFFER_LIMIT = 50


# --------------------------------------------------------------------------
# Outcomes the API turns into status codes
# --------------------------------------------------------------------------

class OfferNotFound(Exception):
    pass


class UnsupportedUpload(Exception):
    """The reader could not read this file. Carries its plain-English message."""


class UploadKeyReused(Exception):
    """Same Idempotency-Key, different file. Returning the first offer would
    hide the second upload entirely."""

    def __init__(self, existing_id: str) -> None:
        super().__init__(existing_id)
        self.existing_id = existing_id


class VersionConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__(current_version)
        self.current_version = current_version


class RequestReused(Exception):
    """Same request_id, different changes."""


@dataclass(frozen=True)
class LineErrors:
    line_id: str | None
    messages: list[str]


class ChangesRejected(Exception):
    def __init__(self, errors: list[LineErrors]) -> None:
        super().__init__(errors)
        self.errors = errors


class FaultInjected(Exception):
    """The deliberate failure behind the recovery demo."""

    def __init__(self, when: str) -> None:
        super().__init__(when)
        self.when = when


# --------------------------------------------------------------------------
# Server-Timing
# --------------------------------------------------------------------------

@dataclass
class Timings:
    """Milliseconds per stage, so the slowest one can be pointed at."""

    stages: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            self.stages[name] = self.stages.get(name, 0.0) + elapsed

    def header(self) -> str:
        return ", ".join(f"{name};dur={ms:.1f}" for name, ms in self.stages.items())


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------

def create_offer(
    session: Session,
    *,
    data: bytes,
    filename: str | None,
    upload_key: str | None = None,
    timings: Timings | None = None,
) -> tuple[Offer, bool]:
    """Parse a workbook and save it. Returns (offer, was_already_there)."""
    timings = timings or Timings()
    digest = hashlib.sha256(data).hexdigest()

    if upload_key:
        existing = _offer_by_upload_key(session, upload_key)
        if existing is not None:
            return _same_upload_or_conflict(existing, digest), True

    with timings.stage("parse"):
        try:
            result = read_workbook(data)
        except UnsupportedWorkbook as exc:
            raise UnsupportedUpload(str(exc)) from exc

    with timings.stage("analyze"):
        lines = store(analyze(result.lines))

    offer_id = str(uuid.uuid4())
    with timings.stage("db"):
        session.add(Offer(
            id=offer_id,
            created_at=now(),
            updated_at=now(),
            version=1,
            supplier_name=result.supplier_name,
            title=result.title,
            layout=result.layout,
            sheet_name=result.sheet_name,
            source_filename=filename,
            source_sha256=digest,
            notices=list(result.notices),
            upload_key=upload_key,
        ))
        session.add(OfferSource(offer_id=offer_id, data=data))
        if lines:
            # One executemany, not 5,000 ORM adds.
            session.execute(insert(OfferLine), [line_values(offer_id, ln) for ln in lines])
        try:
            session.commit()
        except IntegrityError:
            # Two uploads raced on the same Idempotency-Key.
            session.rollback()
            if upload_key:
                existing = _offer_by_upload_key(session, upload_key)
                if existing is not None:
                    return _same_upload_or_conflict(existing, digest), True
            raise

    offer = session.get(Offer, offer_id)
    assert offer is not None
    return offer, False


def _offer_by_upload_key(session: Session, upload_key: str) -> Offer | None:
    return session.execute(
        select(Offer).where(Offer.upload_key == upload_key)
    ).scalar_one_or_none()


def _same_upload_or_conflict(existing: Offer, digest: str) -> Offer:
    if existing.source_sha256 != digest:
        raise UploadKeyReused(existing.id)
    return existing


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def get_offer(session: Session, offer_id: str) -> Offer:
    offer = session.get(Offer, offer_id)
    if offer is None:
        raise OfferNotFound(offer_id)
    return offer


def recent_offers(session: Session) -> list[Offer]:
    return list(session.execute(
        select(Offer).order_by(Offer.created_at.desc(), Offer.id).limit(RECENT_OFFER_LIMIT)
    ).scalars())


def load_evaluated(
    session: Session, offer_id: str, timings: Timings | None = None
) -> tuple[Offer, EvaluatedOffer]:
    """The saved state, evaluated. The screen and the CSV both come from here,
    which is what makes the download agree with the screen."""
    timings = timings or Timings()
    offer = get_offer(session, offer_id)
    with timings.stage("db"):
        rows = list(session.execute(
            select(OfferLine)
            .where(OfferLine.offer_id == offer_id)
            .order_by(OfferLine.position)
        ).scalars())
        decisions = list(session.execute(
            select(DecisionRow).where(DecisionRow.offer_id == offer_id)
        ).scalars())
    with timings.stage("evaluate"):
        result = evaluate(
            [to_stored_line(r) for r in rows],
            [to_decision(d) for d in decisions],
        )
    return offer, result


def get_source(session: Session, offer_id: str) -> tuple[Offer, bytes]:
    offer = get_offer(session, offer_id)
    source = session.get(OfferSource, offer_id)
    if source is None:
        raise OfferNotFound(offer_id)
    return offer, source.data


# --------------------------------------------------------------------------
# Saving decisions
# --------------------------------------------------------------------------

def save_decisions(
    session: Session,
    *,
    offer_id: str,
    request_id: str,
    base_version: int,
    changes: list[Change],
    body_sha256: str,
    fault: str | None = None,
    timings: Timings | None = None,
) -> tuple[dict, bool]:
    """Apply a batch of changes. Returns (receipt, was_replayed).

    Order matters and is documented in CLAUDE.md; the comments below say why
    at each step.
    """
    timings = timings or Timings()

    if not changes:
        raise ChangesRejected([LineErrors(
            None, ["This save contains no changes."]
        )])

    counts = Counter(c.line_id for c in changes)
    duplicates = sorted(line_id for line_id, n in counts.items() if n > 1)
    if duplicates:
        raise ChangesRejected([
            LineErrors(line_id, ["This line appears twice in one save; send it once."])
            for line_id in duplicates
        ])

    with timings.stage("db"):
        # 1. Already done? A retry is a lookup, never a second write. This is
        #    checked before the version, because a retry's own earlier commit
        #    is what made its base_version stale.
        replayed = _replay(session, offer_id, request_id, body_sha256)
        if replayed is not None:
            return replayed, True

        offer = session.get(Offer, offer_id)
        if offer is None:
            raise OfferNotFound(offer_id)

        # 2. The conditional bump is both the staleness check and the lock
        #    that serialises everything below it.
        bumped = session.execute(
            update(Offer)
            .where(Offer.id == offer_id, Offer.version == base_version)
            .values(version=Offer.version + 1, updated_at=now())
            .execution_options(synchronize_session=False)
        )
        if bumped.rowcount == 0:
            # 3. Two copies of one retry can both miss step 1. Only one wins
            #    the bump; the loser must not be told 409 for work its own
            #    twin just committed. Roll back first so the read is fresh
            #    whatever the isolation level.
            session.rollback()
            replayed = _replay(session, offer_id, request_id, body_sha256)
            if replayed is not None:
                return replayed, True
            current = session.execute(
                select(Offer.version).where(Offer.id == offer_id)
            ).scalar_one_or_none()
            if current is None:
                raise OfferNotFound(offer_id)
            raise VersionConflict(current)

        # 4. Validate against the effective values. Only the referenced lines
        #    are loaded — a save on the 5,000-row offer touches one row.
        line_ids = [c.line_id for c in changes]
        rows = session.execute(
            select(OfferLine).where(
                OfferLine.offer_id == offer_id, OfferLine.line_id.in_(line_ids)
            )
        ).scalars()
        lines = {row.line_id: to_stored_line(row) for row in rows}

        errors: list[LineErrors] = []
        decisions: list[Decision] = []
        for change in changes:
            decision, problems = prepare_change(lines.get(change.line_id), change)
            if problems:
                errors.append(LineErrors(change.line_id, problems))
            elif decision is not None:
                decisions.append(decision)
        if errors:
            session.rollback()  # undoes the version bump too
            raise ChangesRejected(errors)

        # 5. Replace the decisions for the touched lines. Delete-then-insert
        #    is atomic inside the transaction and spells the same on SQLite
        #    and Postgres; a reset is the delete on its own.
        session.execute(
            delete(DecisionRow)
            .where(DecisionRow.offer_id == offer_id, DecisionRow.line_id.in_(line_ids))
            .execution_options(synchronize_session=False)
        )
        if decisions:
            session.execute(
                insert(DecisionRow), [decision_values(offer_id, d) for d in decisions]
            )

        receipt = {
            "offer_id": offer_id,
            "request_id": request_id,
            "version": base_version + 1,
            "applied": len(changes),
        }
        session.execute(insert(SaveRequest), [{
            "offer_id": offer_id,
            "request_id": request_id,
            "receipt": receipt,
            "body_sha256": body_sha256,
            "created_at": now(),
        }])

        if fault == "before-commit":
            session.rollback()
            raise FaultInjected("before-commit")

        try:
            session.commit()
        except IntegrityError:
            # Two retries still collided on the save_requests primary key.
            session.rollback()
            replayed = _replay(session, offer_id, request_id, body_sha256)
            if replayed is not None:
                return replayed, True
            raise

    if fault == "after-commit":
        # Saved, but the client is about to be told it failed. The retry has
        # to find the receipt above and apply nothing.
        raise FaultInjected("after-commit")

    return receipt, False


def _replay(
    session: Session, offer_id: str, request_id: str, body_sha256: str
) -> dict | None:
    """The stored receipt for this request_id, if we have already done it."""
    stored = session.get(SaveRequest, (offer_id, request_id))
    if stored is None:
        return None
    if stored.body_sha256 != body_sha256:
        raise RequestReused(request_id)
    return dict(stored.receipt)
