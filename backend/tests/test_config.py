"""DATABASE_URL handling.

A globally exported DATABASE_URL from an unrelated project is an easy way to
lose an afternoon, so a wrong one has to explain itself rather than fail
somewhere inside SQLAlchemy.
"""

import pytest

from app.db import (
    DEFAULT_DATABASE_URL,
    ConfigurationError,
    database_url,
    make_engine,
    validate_database_url,
)


def test_a_jdbc_url_says_what_to_use_instead():
    with pytest.raises(ConfigurationError) as caught:
        validate_database_url("jdbc:postgresql://localhost/yardsdb")

    message = str(caught.value)
    assert "JDBC" in message
    assert "postgresql+psycopg://localhost/yardsdb" in message  # the translation
    assert "unset DATABASE_URL" in message  # the likely real fix


def test_the_old_postgres_scheme_is_named_and_corrected():
    with pytest.raises(ConfigurationError, match="postgresql\\+psycopg"):
        validate_database_url("postgres://user:pw@host/db")


@pytest.mark.parametrize("url", ["", "   ", "yardsdb", "not a url at all"])
def test_anything_unparseable_is_refused_with_advice(url):
    with pytest.raises(ConfigurationError) as caught:
        validate_database_url(url)
    assert "DATABASE_URL" in str(caught.value)


@pytest.mark.parametrize("url, driver", [
    ("sqlite:///./reflex_offers.db", "sqlite"),
    ("sqlite:///:memory:", "sqlite"),
    ("postgresql+psycopg://user:pw@host/reflex", "postgresql+psycopg"),
])
def test_usable_urls_are_accepted(url, driver):
    assert validate_database_url(url).drivername == driver


def test_no_database_url_means_the_local_sqlite_file(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url() == DEFAULT_DATABASE_URL
    assert validate_database_url(database_url()).drivername == "sqlite"


def test_a_stray_database_url_cannot_reach_create_engine(monkeypatch):
    """The guard sits in make_engine, so no caller can skip it."""
    monkeypatch.setenv("DATABASE_URL", "jdbc:postgresql://localhost/yardsdb")
    with pytest.raises(ConfigurationError):
        make_engine()


def test_sqlite_engines_wait_for_the_write_lock(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test_x.db'}")
    try:
        # Without this, two saves landing together fail with "database is
        # locked" instead of queueing.
        assert engine.dialect.name == "sqlite"
        with engine.connect() as connection:
            assert connection.exec_driver_sql("select 1").scalar() == 1
    finally:
        engine.dispose()
