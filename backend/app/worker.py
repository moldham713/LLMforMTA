"""Background worker: realtime feed polling plus scheduled static GTFS refresh.

The heartbeat file is touched after every completed poll cycle; the container
healthcheck fails only when cycles stop completing, never because a feed is down.
"""

import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine

log = logging.getLogger("worker")

HEARTBEAT_FILE = Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/worker-heartbeat"))


def beat(path: Path = HEARTBEAT_FILE) -> None:
    path.touch()


def run(interval: float, stop: threading.Event, path: Path = HEARTBEAT_FILE) -> None:
    while not stop.is_set():
        beat(path)
        stop.wait(interval)


def is_alive(max_age: float, path: Path = HEARTBEAT_FILE) -> bool:
    try:
        return time.time() - path.stat().st_mtime < max_age
    except FileNotFoundError:
        return False


def refresh_loop(refresh: Callable[[], object], interval: float, stop: threading.Event) -> None:
    """Run `refresh` now and then every `interval` seconds until `stop` is set.

    Failures are logged and swallowed: the loader leaves existing data in place, and
    the next cycle retries.
    """
    while not stop.is_set():
        try:
            refresh()
        except Exception:
            log.exception("GTFS refresh failed; existing data left in place")
        stop.wait(interval)


def refresh_static_data(engine: "Engine", gtfs_url: str, stations_url: str) -> None:
    # App imports are deferred so the frequent `--check` healthcheck stays cheap.
    from app.gtfs.loader import load_gtfs
    from app.stations.loader import load_stations, stations_loaded

    result = load_gtfs(engine, gtfs_url)
    log.info("GTFS refresh %s in %ss", result.status, result.duration_seconds)
    # The stations dataset changes rarely; load it only to bootstrap an empty database.
    if not stations_loaded(engine):
        load_stations(engine, stations_url)


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if "--check" in argv:
        max_age = float(os.environ.get("WORKER_MAX_CYCLE_AGE_SECONDS", "120"))
        return 0 if is_alive(max_age) else 1

    from app.config import Settings
    from app.extensions import make_engine, make_redis
    from app.realtime.poller import Poller
    from app.realtime.store import RealtimeStore

    settings = Settings.from_env()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    if settings.gtfs_refresh_hours > 0:
        engine = make_engine(settings.database_url)
        threading.Thread(
            target=refresh_loop,
            args=(
                lambda: refresh_static_data(engine, settings.gtfs_url, settings.stations_url),
                settings.gtfs_refresh_hours * 3600,
                stop,
            ),
            name="gtfs-refresh",
            daemon=True,
        ).start()
        log.info("GTFS refresh every %sh", settings.gtfs_refresh_hours)
    else:
        log.info("GTFS refresh disabled (GTFS_REFRESH_HOURS=0)")

    beat()
    if settings.rt_poll_seconds > 0:
        store = RealtimeStore(make_redis(settings.redis_url), settings.rt_ttl_seconds)
        poller = Poller(
            store,
            settings.rt_feed_urls,
            interval=settings.rt_poll_seconds,
            timeout=settings.rt_timeout_seconds,
        )
        log.info("polling %d feeds every %ss", len(settings.rt_feed_urls), settings.rt_poll_seconds)
        poller.run(stop, on_cycle=beat)
    else:
        log.info("realtime polling disabled (RT_POLL_SECONDS=0)")
        run(15, stop)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
