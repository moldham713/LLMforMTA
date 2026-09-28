import threading
import time
from pathlib import Path

from app.realtime.poller import USER_AGENT, Poller

REALTIME = Path(__file__).parent / "fixtures" / "realtime"
NOW = 1790618392


class FakeStore:
    def __init__(self):
        self.arrivals, self.alerts, self.failures = {}, None, []

    def write_arrivals(self, feed, header_ts, arrivals, now):
        self.arrivals[feed] = arrivals

    def write_alerts(self, header_ts, alerts, now):
        self.alerts = alerts

    def record_failure(self, feed, error, now):
        self.failures.append((feed, error))


URLS = {"ace": "http://x/ace", "bdfm": "http://x/bdfm", "alerts": "http://x/alerts"}
BODIES = {
    "http://x/ace": (REALTIME / "ace.pb").read_bytes(),
    "http://x/bdfm": (REALTIME / "bdfm.pb").read_bytes(),
    "http://x/alerts": (REALTIME / "alerts.json").read_bytes(),
}


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def make(fetch, clock=None, store=None):
    return Poller(
        store or FakeStore(), URLS, interval=30, timeout=10, fetch=fetch, clock=clock or Clock()
    )


def test_all_feeds_ingested():
    p = make(lambda url, timeout: BODIES[url])

    assert p.poll_once() == {"ace": "ok", "bdfm": "ok", "alerts": "ok"}
    assert {"A", "C", "E"} <= {a["route_id"] for a in p.store.arrivals["ace"]}
    assert {"B", "D"} <= {a["route_id"] for a in p.store.arrivals["bdfm"]}
    assert p.store.alerts


def test_feeds_are_fetched_concurrently_with_timeout():
    started, seen = [], []

    def fetch(url, timeout):
        seen.append(timeout)
        started.append(time.monotonic())
        time.sleep(0.2)
        return BODIES[url]

    make(fetch).poll_once()

    assert seen == [10, 10, 10]
    assert max(started) - min(started) < 0.15


def test_one_failing_feed_does_not_affect_others():
    def fetch(url, timeout):
        if url.endswith("ace"):
            raise TimeoutError("timed out")
        return BODIES[url]

    p = make(fetch)
    results = p.poll_once()

    assert results["bdfm"] == results["alerts"] == "ok"
    assert results["ace"] == "TimeoutError: timed out"
    assert "ace" not in p.store.arrivals and "bdfm" in p.store.arrivals
    assert p.store.failures == [("ace", "TimeoutError: timed out")]


def test_unparseable_body_counts_as_failure():
    p = make(lambda url, timeout: b"<html>busy</html>" if url.endswith("bdfm") else BODIES[url])
    assert p.poll_once()["bdfm"].startswith("FeedParseError")


def test_backoff_grows_on_repeated_failures_and_resets_on_success():
    clock = Clock()
    broken = {"flag": True}

    def fetch(url, timeout):
        if url.endswith("ace") and broken["flag"]:
            raise OSError("down")
        return BODIES[url]

    p = make(fetch, clock)
    attempted = []
    for _ in range(8):  # 8 cycles, 30s apart
        attempted.append(p.poll_once()["ace"] != "backoff")
        clock.t += 30
    # Fails at t=0, retries next cycle, then waits 2, then 4 cycles.
    assert attempted == [True, True, False, True, False, False, False, True]

    broken["flag"] = False
    while p.poll_once()["ace"] == "backoff":
        clock.t += 30
    clock.t += 30
    assert p.poll_once()["ace"] == "ok"
    assert p.state["ace"].failures == 0


def test_poll_never_raises_even_if_recording_fails():
    class BrokenStore(FakeStore):
        def record_failure(self, feed, error, now):
            raise ConnectionError("redis down")

    p = make(lambda url, timeout: (_ for _ in ()).throw(OSError("x")), store=BrokenStore())
    assert set(p.poll_once().values()) == {"OSError: x"}


def test_run_calls_on_cycle_each_cycle():
    stop = threading.Event()
    cycles = []

    def on_cycle():
        cycles.append(1)
        if len(cycles) == 2:
            stop.set()

    p = Poller(FakeStore(), URLS, interval=0.01, fetch=lambda u, t: BODIES[u])
    p.run(stop, on_cycle)
    assert len(cycles) == 2


def test_user_agent_is_descriptive():
    assert "nyc-transit-assistant" in USER_AGENT
