"""Engine and session handling.

One `DATABASE_URL` switches between SQLite (dev, tests) and Postgres (prod).
Nothing above this module knows which one it is talking to, which is why the
same test suite can run against both.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./reflex_offers.db"

_UNSET_HINT = (
    "If it belongs to another project, unset it before starting this one:\n"
    "    unset DATABASE_URL"
)


class ConfigurationError(RuntimeError):
    """DATABASE_URL is set to something we cannot use."""


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


def validate_database_url(url: str) -> URL:
    """Turn a bad DATABASE_URL into advice rather than a traceback.

    A JDBC url is the usual culprit: the same database spelled for a
    different ecosystem, often exported globally by some other project. The
    failure otherwise surfaces deep inside SQLAlchemy, where it looks like
    our bug rather than a stray environment variable.
    """
    if not url.strip():
        raise ConfigurationError(
            "DATABASE_URL is set but empty.\n"
            f"Unset it to use the default ({DEFAULT_DATABASE_URL}), or give a "
            "SQLAlchemy url."
        )
    if url.startswith("jdbc:"):
        equivalent = url[len("jdbc:"):]
        if equivalent.startswith("postgresql:"):
            equivalent = "postgresql+psycopg:" + equivalent[len("postgresql:"):]
        raise ConfigurationError(
            f"DATABASE_URL is a JDBC url, which SQLAlchemy can't read:\n"
            f"    {url}\n"
            f"For the same database, this app needs:\n"
            f"    {equivalent}\n"
            f"{_UNSET_HINT}"
        )
    try:
        parsed = make_url(url)
    except Exception as exc:
        raise ConfigurationError(
            f"DATABASE_URL isn't a SQLAlchemy url:\n    {url}\n"
            "Expected something like sqlite:///./reflex_offers.db or "
            "postgresql+psycopg://user:password@host/dbname.\n"
            f"{_UNSET_HINT}"
        ) from exc
    if parsed.drivername == "postgres":
        raise ConfigurationError(
            f"DATABASE_URL uses the old 'postgres://' scheme that SQLAlchemy "
            f"dropped:\n    {url}\n"
            "Use postgresql+psycopg:// instead."
        )
    return parsed


def make_engine(url: str | None = None) -> Engine:
    parsed = validate_database_url(url or database_url())
    kwargs: dict = {"pool_pre_ping": True}
    if parsed.drivername.startswith("sqlite"):
        kwargs["connect_args"] = {
            # Wait for the write lock instead of failing with "database is
            # locked" when two saves land at once.
            "timeout": 30,
            # TestClient and uvicorn both hand connections between threads.
            "check_same_thread": False,
        }
    return create_engine(parsed, **kwargs)


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
