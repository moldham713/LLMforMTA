import redis
from sqlalchemy import Engine, create_engine, make_url

from app.realtime.store import RealtimeStore

# Short timeouts keep /api/health responsive when a dependency is down.
CONNECT_TIMEOUT_SECONDS = 2


def make_engine(database_url: str) -> Engine:
    connect_args = {}
    if make_url(database_url).get_backend_name() == "postgresql":
        connect_args["connect_timeout"] = CONNECT_TIMEOUT_SECONDS
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def make_redis(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        socket_timeout=CONNECT_TIMEOUT_SECONDS,
    )


def make_store(client: redis.Redis, settings) -> RealtimeStore:
    return RealtimeStore(client, settings.rt_ttl_seconds, settings.rt_alerts_ttl_seconds)
