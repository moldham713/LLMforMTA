import pytest

from app import create_app
from app.config import Settings
from app.realtime.feeds import ALERTS_FEED, TRIP_FEEDS

NOW = 1_800_000_000


class StubRedis:
    def __init__(self, error: Exception | None = None, meta: dict[str, dict] | None = None):
        self.error = error
        self.meta = meta if meta is not None else fresh_meta()

    def ping(self) -> bool:
        if self.error:
            raise self.error
        return True

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        feed = key.removeprefix("rt:meta:")
        return {k.encode(): str(v).encode() for k, v in self.meta.get(feed, {}).items()}


def fresh_meta(age: int = 5) -> dict[str, dict]:
    return {
        f: {"header_ts": NOW - age, "last_success": NOW - age, "failures": 0}
        for f in [*TRIP_FEEDS, ALERTS_FEED]
    }


@pytest.fixture
def app():
    # In-memory SQLite stands in for Postgres; the health probe is plain `SELECT 1`.
    app = create_app(Settings(database_url="sqlite://", redis_url="redis://unused:6379/0"))
    app.extensions["redis"] = StubRedis()
    app.extensions["clock"] = lambda: NOW
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()
