"""Background worker. For now it only emits a heartbeat; realtime feed polling lands here later."""

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("worker")

HEARTBEAT_FILE = Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/worker-heartbeat"))


def beat(path: Path = HEARTBEAT_FILE) -> None:
    log.info("heartbeat")
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


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    interval = float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "15"))

    # Container healthcheck: alive if we beat within the last few intervals.
    if "--check" in argv:
        return 0 if is_alive(max_age=interval * 3) else 1

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker started, heartbeat every %ss", interval)
    run(interval, stop)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
