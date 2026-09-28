import pytest

from app.config import Settings


def test_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/x")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    s = Settings.from_env()

    assert s.database_url == "postgresql+psycopg://u:p@db/x"
    assert s.redis_url == "redis://redis:6379/0"
    assert s.log_level == "INFO"


def test_missing_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError):
        Settings.from_env()


def test_static_data_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/x")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    for var in ("GTFS_URL", "STATIONS_URL", "GTFS_REFRESH_HOURS"):
        monkeypatch.delenv(var, raising=False)

    s = Settings.from_env()

    assert s.gtfs_url.endswith("gtfs_supplemented.zip")
    assert "39hk-dx4f" in s.stations_url
    assert s.gtfs_refresh_hours == 6


def test_refresh_hours_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/x")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("GTFS_REFRESH_HOURS", "0.5")
    assert Settings.from_env().gtfs_refresh_hours == 0.5
