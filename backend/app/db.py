"""Engine and session handling.

One `DATABASE_URL` switches between SQLite (dev, tests) and Postgres (prod).
Nothing above this module knows which one it is talking to, which is why the
same test suite can run against both.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./reflex_offers.db"


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {
            # Wait for the write lock instead of failing with "database is
            # locked" when two saves land at once.
            "timeout": 30,
            # TestClient and uvicorn both hand connections between threads.
            "check_same_thread": False,
        }
    return create_engine(url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_tables(engine: Engine) -> None:
    from . import models  # noqa: F401  (registers the mappings)

    Base.metadata.create_all(engine)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = make_session_factory(get_engine())
    return _session_factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one independent session per request.

    Independent matters — a shared session would serialise the concurrent
    saves the version check exists to handle.
    """
    with get_session_factory()() as session:
        yield session
