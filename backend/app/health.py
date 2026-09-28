import logging
from collections.abc import Callable

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from app.realtime.store import RealtimeStore

log = logging.getLogger(__name__)

bp = Blueprint("health", __name__, url_prefix="/api")


def _check(name: str, probe: Callable[[], object]) -> dict:
    try:
        probe()
    except Exception as exc:
        log.warning("health check %s failed: %s", name, exc)
        # Only the exception type is returned; messages can include hostnames or credentials.
        return {"ok": False, "error": type(exc).__name__}
    return {"ok": True}


def _ping_postgres() -> None:
    with current_app.extensions["db_engine"].connect() as conn:
        conn.execute(text("SELECT 1"))


def _ping_redis() -> None:
    current_app.extensions["redis"].ping()


def _feeds() -> dict:
    settings = current_app.extensions["settings"]
    store = RealtimeStore(current_app.extensions["redis"], settings.rt_ttl_seconds)
    status = store.feed_status(current_app.extensions["clock"](), settings.rt_stale_seconds)
    # Poll errors can quote upstream hosts; the worker logs have the full text.
    return {feed: {k: v for k, v in s.items() if k != "last_error"} for feed, s in status.items()}


@bp.get("/health")
def health():
    checks = {
        "postgres": _check("postgres", _ping_postgres),
        "redis": _check("redis", _ping_redis),
    }
    deps_ok = all(c["ok"] for c in checks.values())
    feeds = _feeds() if checks["redis"]["ok"] else {}
    # Stale feeds degrade the status but not the HTTP code: the api itself still works.
    fresh = bool(feeds) and not any(f["stale"] for f in feeds.values())
    body = {"status": "ok" if deps_ok and fresh else "degraded", "checks": checks, "feeds": feeds}
    return jsonify(body), 200 if deps_ok else 503
