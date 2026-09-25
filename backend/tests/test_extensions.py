from app.extensions import CONNECT_TIMEOUT_SECONDS, make_engine, make_redis


def test_redis_client_has_timeouts():
    client = make_redis("redis://example:6379/0")
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == CONNECT_TIMEOUT_SECONDS
    assert kwargs["socket_timeout"] == CONNECT_TIMEOUT_SECONDS


def test_sqlite_engine_skips_pg_connect_timeout():
    # SQLite would reject psycopg's connect_timeout argument.
    engine = make_engine("sqlite://")
    with engine.connect():
        pass
