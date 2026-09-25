import logging
from collections.abc import Callable

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

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


@bp.get("/health")
def health():
    checks = {
        "postgres": _check("postgres", _ping_postgres),
        "redis": _check("redis", _ping_redis),
    }
    healthy = all(c["ok"] for c in checks.values())
    body = {"status": "ok" if healthy else "degraded", "checks": checks}
    return jsonify(body), 200 if healthy else 503
