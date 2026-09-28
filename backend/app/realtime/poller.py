"""Poll every realtime feed concurrently and write each to Redis independently."""

import logging
import threading
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.realtime.feeds import ALERTS_FEED
from app.realtime.parse import parse_alerts, parse_trip_updates
from app.realtime.store import RealtimeStore
from app.routes_display import ROUTES

log = logging.getLogger(__name__)

USER_AGENT = "nyc-transit-assistant/0.2 (realtime poller; subway arrivals)"
MAX_BACKOFF_SECONDS = 300


def http_fetch(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


@dataclass
class _FeedState:
    failures: int = 0
    next_attempt: float = 0.0


class Poller:
    def __init__(
        self,
        store: RealtimeStore,
        urls: dict[str, str],
        *,
        interval: float = 30,
        timeout: float = 10,
        fetch: Callable[[str, float], bytes] = http_fetch,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.urls = urls
        self.interval = interval
        self.timeout = timeout
        self.fetch = fetch
        self.clock = clock
        self.state = {feed: _FeedState() for feed in urls}
        self._unknown_routes: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=len(urls), thread_name_prefix="rt-fetch")

    def poll_once(self) -> dict[str, str]:
        """One cycle. Returns feed -> "ok" | "backoff" | error text; never raises."""
        now = self.clock()
        due = [f for f, s in self.state.items() if s.next_attempt <= now]
        results = {f: "backoff" for f in self.state if f not in due}
        futures = {f: self._pool.submit(self.fetch, self.urls[f], self.timeout) for f in due}
        for feed, future in futures.items():
            try:
                self._ingest(feed, future.result(), now)
            except Exception as exc:
                results[feed] = self._fail(feed, exc, now)
            else:
                self.state[feed] = _FeedState()
                results[feed] = "ok"
        return results

    def run(self, stop: threading.Event, on_cycle: Callable[[], object] = lambda: None) -> None:
        while not stop.is_set():
            started = self.clock()
            results = self.poll_once()
            on_cycle()
            problems = {f: r for f, r in results.items() if r != "ok"}
            took = self.clock() - started
            if problems:
                log.warning("poll cycle %.1fs; problems: %s", took, problems)
            else:
                log.info("poll cycle %.1fs; all %d feeds ok", took, len(results))
            stop.wait(max(0.0, self.interval - took))

    def _ingest(self, feed: str, data: bytes, now: float) -> None:
        if feed == ALERTS_FEED:
            header_ts, alerts = parse_alerts(data, now)
            self.store.write_alerts(header_ts, alerts, now)
            return
        header_ts, arrivals = parse_trip_updates(data, feed, now)
        unknown = {a["route_id"] for a in arrivals} - ROUTES.keys() - self._unknown_routes
        if unknown:
            self._unknown_routes |= unknown
            log.warning("%s: route_ids missing from routes_display: %s", feed, sorted(unknown))
        self.store.write_arrivals(feed, header_ts, arrivals, now)

    def _fail(self, feed: str, exc: Exception, now: float) -> str:
        state = self.state[feed]
        state.failures += 1
        # First failure retries next cycle; repeats back off exponentially. The 1s slack
        # keeps a cycle that starts exactly `delay` later from skipping the feed.
        delay = min(self.interval * 2 ** (state.failures - 1), MAX_BACKOFF_SECONDS)
        state.next_attempt = now + delay - 1
        error = f"{type(exc).__name__}: {exc}"
        try:
            self.store.record_failure(feed, error, now)
        except Exception:
            log.exception("could not record failure for %s", feed)
        return error
