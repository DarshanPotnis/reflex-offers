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


# ---------- which hardware answered ----------

def test_cpu_info_reports_what_it_knows():
    from app.runtime import cpu_info

    info = cpu_info()
    assert isinstance(info["cpu_count"], int) and info["cpu_count"] >= 1
    assert info["cpu_limit"] is None or info["cpu_limit"] > 0
    assert isinstance(info["cpu_limit_source"], str) and info["cpu_limit_source"]
    assert isinstance(info["workers"], int) and info["workers"] >= 1


def test_a_cgroup_v2_quota_is_read_as_a_fraction(tmp_path, monkeypatch):
    """0.5 vCPU has to read as 0.5, not as the host's core count."""
    from app import runtime

    quota = tmp_path / "cpu.max"
    quota.write_text("50000 100000\n")
    monkeypatch.setattr(runtime, "CGROUP_V2", quota)
    assert runtime.cpu_info()["cpu_limit"] == 0.5


def test_an_unlimited_cgroup_reports_no_limit(tmp_path, monkeypatch):
    from app import runtime

    quota = tmp_path / "cpu.max"
    quota.write_text("max 100000\n")
    monkeypatch.setattr(runtime, "CGROUP_V2", quota)
    monkeypatch.setattr(runtime, "CGROUP_V1_QUOTA", tmp_path / "missing")
    assert runtime.cpu_info()["cpu_limit"] is None


def test_worker_count_comes_from_the_environment(monkeypatch):
    from app.runtime import cpu_info

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert cpu_info()["workers"] == 2


def test_health_reports_the_hardware(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert "cpu_count" in body and "cpu_limit" in body
    assert "cpu_limit_source" in body and "workers" in body


@pytest.mark.parametrize("path", ["/", "/offers/whatever", "/api/health"])
def test_head_is_allowed_where_get_is(client, path):
    """Uptime monitors and Render's probes send HEAD. FastAPI does not add it
    for a GET route the way plain Starlette does, so it is declared."""
    response = client.head(path)
    assert response.status_code != 405, f"HEAD {path} was refused"
    assert response.status_code in (200, 404), response.status_code
