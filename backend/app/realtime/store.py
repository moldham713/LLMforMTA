"""Realtime data in Redis.

Layout:
  rt:{feed}:{stop_id}   JSON list of arrivals at one platform, from one feed only
  rt:index:{feed}       set of stop_ids the feed wrote last time (to clear vanished ones)
  rt:meta:{feed}        hash: header_ts, last_success, last_error, last_error_at, failures
  alerts:data           hash: alert id -> alert JSON
  alerts:route:{id}     JSON list of alert ids
  alerts:stop:{id}      JSON list of alert ids
  alerts:index          set of the alerts:route/stop keys written last time

Each feed only ever writes its own keys, so a shared platform (e.g. B and C at 86 St)
merges on read, and one feed failing or refreshing can't clobber another's arrivals.
Data keys expire, so a dead feed's arrivals age out on their own.
"""

import json
from collections import defaultdict

import redis

from app.realtime.feeds import ALERTS_FEED, TRIP_FEEDS
from app.realtime.parse import Alert, Arrival

_JSON = {"separators": (",", ":")}


def _meta_key(feed: str) -> str:
    return f"rt:meta:{feed}"


class RealtimeStore:
    def __init__(self, client: redis.Redis, ttl_seconds: int = 180):
        self.redis = client
        self.ttl = ttl_seconds

    # --- writes (worker) ------------------------------------------------------------

    def write_arrivals(self, feed: str, header_ts: int, arrivals: list[Arrival], now: float):
        by_stop: dict[str, list[Arrival]] = defaultdict(list)
        for a in arrivals:
            by_stop[a["stop_id"]].append(a)
        index = f"rt:index:{feed}"
        previous = {s.decode() for s in self.redis.smembers(index)}

        pipe = self.redis.pipeline(transaction=True)
        for stop_id, items in by_stop.items():
            items.sort(key=lambda a: a["arrival_ts"])
            pipe.set(f"rt:{feed}:{stop_id}", json.dumps(items, **_JSON), ex=self.ttl)
        vanished = previous - by_stop.keys()
        if vanished:
            pipe.delete(*(f"rt:{feed}:{s}" for s in vanished))
        pipe.delete(index)
        if by_stop:
            pipe.sadd(index, *by_stop)
            pipe.expire(index, self.ttl)
        self._mark_success(pipe, feed, header_ts, now, count=len(arrivals))
        pipe.execute()

    def write_alerts(self, header_ts: int, alerts: list[Alert], now: float):
        by_key: dict[str, list[str]] = defaultdict(list)
        for a in alerts:
            for rid in a["route_ids"]:
                by_key[f"alerts:route:{rid}"].append(a["id"])
            for sid in a["stop_ids"]:
                by_key[f"alerts:stop:{sid}"].append(a["id"])
        previous = {k.decode() for k in self.redis.smembers("alerts:index")}

        pipe = self.redis.pipeline(transaction=True)
        pipe.delete("alerts:data")
        if alerts:
            pipe.hset("alerts:data", mapping={a["id"]: json.dumps(a, **_JSON) for a in alerts})
            pipe.expire("alerts:data", self.ttl)
        for key, ids in by_key.items():
            pipe.set(key, json.dumps(ids, **_JSON), ex=self.ttl)
        vanished = previous - by_key.keys()
        if vanished:
            pipe.delete(*vanished)
        pipe.delete("alerts:index")
        if by_key:
            pipe.sadd("alerts:index", *by_key)
            pipe.expire("alerts:index", self.ttl)
        self._mark_success(pipe, ALERTS_FEED, header_ts, now, count=len(alerts))
        pipe.execute()

    def record_failure(self, feed: str, error: str, now: float) -> None:
        pipe = self.redis.pipeline(transaction=True)
        pipe.hset(_meta_key(feed), mapping={"last_error": error[:500], "last_error_at": int(now)})
        pipe.hincrby(_meta_key(feed), "failures", 1)
        pipe.execute()

    def _mark_success(self, pipe, feed: str, header_ts: int, now: float, count: int) -> None:
        pipe.hset(
            _meta_key(feed),
            mapping={
                "header_ts": header_ts,
                "last_success": int(now),
                "failures": 0,
                "count": count,
            },
        )

    # --- reads (api) ----------------------------------------------------------------

    def arrivals(self, stop_ids: set[str]) -> list[Arrival]:
        """Arrivals at the given platforms, merged across every feed."""
        keys = [f"rt:{feed}:{stop}" for stop in sorted(stop_ids) for feed in TRIP_FEEDS]
        if not keys:
            return []
        out: list[Arrival] = []
        for raw in self.redis.mget(keys):
            if raw:
                out.extend(json.loads(raw))
        return out

    def alerts(self, route_ids: set[str] | None = None, stop_ids: set[str] | None = None):
        """Alerts on any of `route_ids` or `stop_ids`; everything when both are None."""
        if route_ids is None and stop_ids is None:
            raws = self.redis.hvals("alerts:data")
        else:
            keys = [f"alerts:route:{r}" for r in sorted(route_ids or ())]
            keys += [f"alerts:stop:{s}" for s in sorted(stop_ids or ())]
            ids = sorted({i for raw in self.redis.mget(keys) if raw for i in json.loads(raw)})
            raws = self.redis.hmget("alerts:data", ids) if ids else []
        return [json.loads(r) for r in raws if r]

    def feed_meta(self, feed: str) -> dict[str, str]:
        return {k.decode(): v.decode() for k, v in self.redis.hgetall(_meta_key(feed)).items()}

    def feed_status(self, now: float, stale_after: float) -> dict[str, dict]:
        """Freshness per feed. Trip feeds go stale on header age; the alerts header only
        moves when alerts change, so alerts go stale on time since the last good poll."""
        status = {}
        for feed in [*TRIP_FEEDS, ALERTS_FEED]:
            meta = self.feed_meta(feed)
            header_ts = int(meta["header_ts"]) if meta.get("header_ts") else None
            last_success = int(meta["last_success"]) if meta.get("last_success") else None
            basis = last_success if feed == ALERTS_FEED else header_ts
            age = round(now - basis, 1) if basis else None
            status[feed] = {
                "header_ts": header_ts,
                "last_success": last_success,
                "age_seconds": age,
                "stale": age is None or age > stale_after,
                "failures": int(meta.get("failures", 0)),
                "last_error": meta.get("last_error") or None,
                "last_error_at": int(meta["last_error_at"]) if meta.get("last_error_at") else None,
            }
        return status
