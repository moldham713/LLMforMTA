import redis
from sqlalchemy import create_engine

from tests.conftest import StubRedis


def test_healthy(client):
    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "status": "ok",
        "checks": {"postgres": {"ok": True}, "redis": {"ok": True}},
    }


def test_redis_down(app, client):
    app.extensions["redis"] = StubRedis(redis.ConnectionError("connection refused"))

    resp = client.get("/api/health")

    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"] == {"ok": True}
    assert body["checks"]["redis"] == {"ok": False, "error": "ConnectionError"}


def test_postgres_down(app, client):
    app.extensions["db_engine"] = create_engine("sqlite:///no/such/dir/db.sqlite")

    resp = client.get("/api/health")

    assert resp.status_code == 503
    assert resp.get_json()["checks"]["postgres"] == {"ok": False, "error": "OperationalError"}


def test_error_details_not_leaked(app, client):
    app.extensions["redis"] = StubRedis(redis.ConnectionError("secret-host:6379"))

    resp = client.get("/api/health")

    assert b"secret-host" not in resp.data
