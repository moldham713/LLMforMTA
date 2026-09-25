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
