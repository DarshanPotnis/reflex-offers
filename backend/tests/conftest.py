"""Test database fixtures.

Runs on SQLite by default and on Postgres with

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host/reflex_test pytest -q

which is the only place the version race actually exists — SQLite serialises
writers, so the race tests mean little until they run against Postgres.
"""

import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app import models  # noqa: F401  (registers the mappings)
from app.db import Base, get_session, make_engine, make_session_factory
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"


def _guard(url: str) -> None:
    """Refuse anything that isn't obviously a test database.

    These fixtures drop every table. Pointing them at a real database would
    be unrecoverable, so the name has to say "test".
    """
    name = make_url(url).database or ""
    if "test" not in Path(name).name.lower():
        raise RuntimeError(
            f"Refusing to run tests against {name!r}: the fixtures drop every "
            "table, so the database name must contain 'test'."
        )


@pytest.fixture(scope="session")
def engine(tmp_path_factory):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        url = f"sqlite:///{tmp_path_factory.mktemp('db') / 'test_offers.db'}"
    _guard(url)
    built = make_engine(url)
    yield built
    built.dispose()


@pytest.fixture
def db(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def sessions(db):
    """A factory, not a session — the concurrency tests need independent ones."""
    return make_session_factory(db)


@pytest.fixture
def client(db, sessions):
    app = create_app(engine=db)

    def _session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def is_postgres(db):
    return db.dialect.name == "postgresql"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def upload(client, name: str, *, key: str | None = None, filename: str | None = None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(
        "/api/offers",
        files={"file": (filename or name, fixture_bytes(name), "application/vnd.ms-excel")},
        headers=headers,
    )


def run_together(sessions, jobs):
    """Run each job in its own thread and session, released at the same moment.

    Returns [("ok", value) | ("error", exception)] in the order given.
    """
    barrier = threading.Barrier(len(jobs))
    results: list = [None] * len(jobs)

    def run(index, job):
        with sessions() as session:
            barrier.wait(timeout=30)
            try:
                results[index] = ("ok", job(session))
            except Exception as exc:  # noqa: BLE001 - the test inspects it
                results[index] = ("error", exc)

    threads = [threading.Thread(target=run, args=(i, j)) for i, j in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results
