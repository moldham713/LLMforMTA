import pytest

from app import create_app
from app.config import Settings


class StubRedis:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def ping(self) -> bool:
        if self.error:
            raise self.error
        return True


@pytest.fixture
def app():
    # In-memory SQLite stands in for Postgres; the health probe is plain `SELECT 1`.
    app = create_app(Settings(database_url="sqlite://", redis_url="redis://unused:6379/0"))
    app.extensions["redis"] = StubRedis()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()
