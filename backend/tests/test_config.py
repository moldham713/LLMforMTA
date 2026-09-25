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
