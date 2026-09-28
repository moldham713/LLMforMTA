import redis
from sqlalchemy import create_engine

from app.realtime.feeds import ALERTS_FEED, TRIP_FEEDS
from tests.conftest import NOW, StubRedis, fresh_meta


def test_healthy(client):
    resp = client.get("/api/health")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["checks"] == {"postgres": {"ok": True}, "redis": {"ok": True}}
    assert set(body["feeds"]) == {*TRIP_FEEDS, ALERTS_FEED}
    assert body["feeds"]["ace"]["age_seconds"] == 5
    assert body["feeds"]["ace"]["stale"] is False


def test_stale_feed_degrades_but_stays_200(app, client):
    meta = fresh_meta()
    meta["ace"] = {"header_ts": NOW - 91, "last_success": NOW - 91, "failures": 0}
    app.extensions["redis"] = StubRedis(meta=meta)

    resp = client.get("/api/health")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["feeds"]["ace"]["stale"] is True
    assert body["feeds"]["bdfm"]["stale"] is False


def test_never_polled_feed_is_stale(app, client):
    meta = fresh_meta()
    del meta["si"]
    app.extensions["redis"] = StubRedis(meta=meta)

    body = client.get("/api/health").get_json()

    assert body["status"] == "degraded"
    assert body["feeds"]["si"]["stale"] is True
    assert body["feeds"]["si"]["age_seconds"] is None


def test_alerts_staleness_uses_last_poll_not_header(app, client):
    # The alerts header only changes when alerts do, so an old header is normal.
    meta = fresh_meta()
    meta["alerts"] = {"header_ts": NOW - 3600, "last_success": NOW - 10, "failures": 0}
    app.extensions["redis"] = StubRedis(meta=meta)

    body = client.get("/api/health").get_json()

    assert body["feeds"]["alerts"]["stale"] is False
    assert body["status"] == "ok"


def test_feed_errors_reported_without_text(app, client):
    meta = fresh_meta()
    meta["g"].update(failures=3, last_error="URLError: internal-host:8443", last_error_at=NOW)
    app.extensions["redis"] = StubRedis(meta=meta)

    body = client.get("/api/health").get_json()

    assert body["feeds"]["g"]["failures"] == 3
    assert "last_error" not in body["feeds"]["g"]
    assert b"internal-host" not in client.get("/api/health").data


def test_redis_down(app, client):
    app.extensions["redis"] = StubRedis(redis.ConnectionError("connection refused"))

    resp = client.get("/api/health")

    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"] == {"ok": True}
    assert body["checks"]["redis"] == {"ok": False, "error": "ConnectionError"}
    assert body["feeds"] == {}


def test_postgres_down(app, client):
    app.extensions["db_engine"] = create_engine("sqlite:///no/such/dir/db.sqlite")

    resp = client.get("/api/health")

    assert resp.status_code == 503
    assert resp.get_json()["checks"]["postgres"] == {"ok": False, "error": "OperationalError"}


def test_error_details_not_leaked(app, client):
    app.extensions["redis"] = StubRedis(redis.ConnectionError("secret-host:6379"))

    resp = client.get("/api/health")

    assert b"secret-host" not in resp.data
