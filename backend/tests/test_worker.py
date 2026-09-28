import os
import threading
import time

from app import worker


def test_beat_touches_file(tmp_path):
    path = tmp_path / "hb"
    worker.beat(path)
    assert path.exists()


def test_run_stops_when_event_set(tmp_path):
    path = tmp_path / "hb"
    stop = threading.Event()
    t = threading.Thread(target=worker.run, args=(60, stop, path))
    t.start()
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()
    assert path.exists()


def test_is_alive(tmp_path):
    path = tmp_path / "hb"
    assert not worker.is_alive(10, path)

    path.touch()
    assert worker.is_alive(10, path)

    stale = time.time() - 60
    os.utime(path, (stale, stale))
    assert not worker.is_alive(10, path)


def test_refresh_runs_immediately_then_on_interval():
    stop = threading.Event()
    calls = []

    def refresh():
        calls.append(time.monotonic())
        if len(calls) == 3:
            stop.set()

    worker.refresh_loop(refresh, interval=0.01, stop=stop)

    assert len(calls) == 3


def test_refresh_failure_is_logged_and_retried(caplog):
    stop = threading.Event()
    calls = []

    def refresh():
        calls.append(1)
        if len(calls) == 2:
            stop.set()
        raise RuntimeError("feed down")

    worker.refresh_loop(refresh, interval=0.01, stop=stop)

    assert len(calls) == 2
    assert "existing data left in place" in caplog.text
    assert "feed down" in caplog.text
