#!/usr/bin/env python3
"""Count SQL statements and data volume per endpoint.

    python scripts/db_profile.py
    DATABASE_URL='postgresql+psycopg://…' python scripts/db_profile.py

Answers one question for each endpoint: is its `db` stage slow because of
**round trips** (many statements, each paying the network latency) or
because of **volume** (few statements moving a lot of bytes)? The two have
completely different fixes, and a stopwatch alone cannot tell them apart.

Runs in-process against the real app, so the statement counts are exactly
what a deployed instance would issue. Point `DATABASE_URL` at a remote
database and the per-statement timings become real round-trip latency.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, select  # noqa: E402

from app.db import Base, make_engine, make_session_factory, get_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import DecisionRow, Offer, OfferLine  # noqa: E402

FIXTURE = ROOT / "backend/tests/fixtures/03-northstar-5000-rows.xlsx"


class Recorder:
    """Every statement the endpoint issued, and how long each one took."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, float, int]] = []
        self._started: float = 0.0

    def before(self, conn, cursor, statement, parameters, context, executemany):
        self._started = time.perf_counter()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        elapsed = (time.perf_counter() - self._started) * 1000
        verb = statement.strip().split(None, 1)[0].upper()
        rows = len(parameters) if executemany and parameters else 1
        self.entries.append((verb, elapsed, rows))

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def total_ms(self) -> float:
        return sum(ms for _, ms, _ in self.entries)

    @property
    def median_ms(self) -> float:
        return statistics.median([ms for _, ms, _ in self.entries]) if self.entries else 0.0

    def verbs(self) -> str:
        counts: dict[str, int] = {}
        for verb, _, _ in self.entries:
            counts[verb] = counts.get(verb, 0) + 1
        return " ".join(f"{v}×{n}" for v, n in sorted(counts.items()))


@contextmanager
def watching(engine):
    recorder = Recorder()
    event.listen(engine, "before_cursor_execute", recorder.before)
    event.listen(engine, "after_cursor_execute", recorder.after)
    try:
        yield recorder
    finally:
        event.remove(engine, "before_cursor_execute", recorder.before)
        event.remove(engine, "after_cursor_execute", recorder.after)


def row_bytes(row: object) -> int:
    """Roughly what the database had to send for one row."""
    total = 0
    for column in row.__table__.columns:  # type: ignore[attr-defined]
        value = getattr(row, column.name)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            total += len(json.dumps(value, separators=(",", ":")))
        elif isinstance(value, bytes):
            total += len(value)
        else:
            total += len(str(value))
    return total


def human(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f} MB"
    if count >= 1_000:
        return f"{count / 1_000:.0f} kB"
    return f"{count} B"


def verdict(statements: int, db_bytes: int) -> str:
    if statements <= 6 and db_bytes > 500_000:
        return "**volume** — few statements, lots of bytes"
    if statements > 20:
        return "**round trips** — many statements"
    if db_bytes < 50_000:
        return "neither — small and quick"
    return "mixed"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    temp_dir = None
    if not url:
        temp_dir = tempfile.mkdtemp(prefix="reflex-profile-")
        url = f"sqlite:///{Path(temp_dir) / 'profile_test.db'}"

    engine = make_engine(url)
    sessions = make_session_factory(engine)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    app = create_app(engine=engine)

    def session_dep():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_dep

    rows: list[tuple[str, Recorder, int]] = []
    data = FIXTURE.read_bytes()

    with TestClient(app) as client:
        with watching(engine) as rec:
            created = client.post(
                "/api/offers",
                files={"file": (FIXTURE.name, data, "application/vnd.ms-excel")},
            )
        created.raise_for_status()
        offer_id = created.json()["id"]

        # What the database actually had to send for a full offer read.
        with sessions() as session:
            lines = list(session.execute(
                select(OfferLine).where(OfferLine.offer_id == offer_id)
            ).scalars())
            offer_row = session.get(Offer, offer_id)
            line_bytes = sum(row_bytes(line) for line in lines)
            offer_bytes = row_bytes(offer_row) if offer_row else 0
        full_read = line_bytes + offer_bytes
        rows.append(("POST /api/offers (upload)", rec, full_read + len(data)))

        with watching(engine) as rec:
            client.get(f"/api/offers/{offer_id}").raise_for_status()
        rows.append(("GET /api/offers/{id}", rec, full_read))

        with watching(engine) as rec:
            saved = client.post(
                f"/api/offers/{offer_id}/decisions",
                json={
                    "request_id": str(uuid.uuid4()),
                    "base_version": 1,
                    "changes": [{"line_id": lines[0].line_id, "action": "exclude"}],
                },
            )
        saved.raise_for_status()
        with sessions() as session:
            one = session.execute(
                select(OfferLine).where(
                    OfferLine.offer_id == offer_id,
                    OfferLine.line_id == lines[0].line_id,
                )
            ).scalar_one()
            decisions = list(session.execute(
                select(DecisionRow).where(DecisionRow.offer_id == offer_id)
            ).scalars())
        rows.append((
            "POST /{id}/decisions (1 line)",
            rec,
            row_bytes(one) + sum(row_bytes(d) for d in decisions),
        ))

        with watching(engine) as rec:
            client.get(f"/api/offers/{offer_id}/export.xlsx").raise_for_status()
        rows.append(("GET /{id}/export.xlsx", rec, full_read))

        with watching(engine) as rec:
            client.get(f"/api/offers/{offer_id}/export.csv").raise_for_status()
        rows.append(("GET /{id}/export.csv", rec, full_read))

        with watching(engine) as rec:
            client.get("/api/offers").raise_for_status()
        rows.append(("GET /api/offers (list)", rec, 0))

    engine.dispose()

    dialect = url.split(":", 1)[0]
    print(f"### SQL per endpoint — {dialect}, 5,000-row offer\n")
    print(f"`{url.split('@')[-1] if '@' in url else dialect}`\n")
    print(
        "| Endpoint | SQL statements | Statement mix | Total SQL time | "
        "Median statement | Approx bytes from DB | Bound by |"
    )
    print("| --- | ---: | --- | ---: | ---: | ---: | --- |")
    for name, rec, db_bytes in rows:
        print(
            f"| `{name}` | {rec.count} | {rec.verbs()} | {rec.total_ms:.0f} ms | "
            f"{rec.median_ms:.1f} ms | {human(db_bytes)} | "
            f"{verdict(rec.count, db_bytes)} |"
        )
    print()
    if temp_dir:
        print("_Local SQLite: statement counts are real, timings are not network-bound._")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
