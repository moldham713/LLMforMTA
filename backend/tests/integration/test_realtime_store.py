import json
import time

import pytest

from app.realtime.store import RealtimeStore
from tests.integration.conftest import FROZEN_NOW, load_realtime_fixtures

pytestmark = pytest.mark.integration


def test_each_feed_writes_its_own_keys_and_reads_merge(rt_store, redis_client):
    # 86 St (A20) platforms carry B trains (bdfm feed) and C trains (ace feed).
    assert redis_client.exists("rt:bdfm:A20N", "rt:ace:A20N") == 2

    merged = rt_store.arrivals({"A20N"})

    assert {(a["route_id"], a["feed"]) for a in merged} == {("B", "bdfm"), ("C", "ace")}


def test_refreshing_one_feed_leaves_other_feeds_alone(rt_store, redis_client):
    ace_before = redis_client.get("rt:ace:A20N")

    rt_store.write_arrivals("bdfm", FROZEN_NOW + 30, [], FROZEN_NOW + 30)

    assert redis_client.get("rt:ace:A20N") == ace_before
    # Platforms the feed no longer reports are cleared rather than left to expire.
    assert not redis_client.exists("rt:bdfm:A20N")
    assert {a["route_id"] for a in rt_store.arrivals({"A20N"})} == {"C"}


def test_failure_keeps_data_and_records_error(rt_store, redis_client):
    before = redis_client.get("rt:ace:A31N")

    rt_store.record_failure("ace", "TimeoutError: timed out", FROZEN_NOW + 30)
    rt_store.record_failure("ace", "TimeoutError: timed out", FROZEN_NOW + 60)

    assert redis_client.get("rt:ace:A31N") == before
    meta = rt_store.feed_meta("ace")
    assert meta["failures"] == "2"
    assert meta["last_error"] == "TimeoutError: timed out"
    assert meta["last_success"] == str(FROZEN_NOW)


def test_data_keys_expire_but_metadata_does_not(rt_store, redis_client):
    assert 170 <= redis_client.ttl("rt:ace:A31N") <= 180
    assert 890 <= redis_client.ttl("alerts:data") <= 900
    assert 890 <= redis_client.ttl("alerts:present") <= 900
    assert redis_client.ttl("rt:meta:ace") == -1


def test_dead_feed_ages_out(redis_client):
    store = RealtimeStore(redis_client, ttl_seconds=1)
    load_realtime_fixtures(store)
    assert store.arrivals({"A31N"})

    time.sleep(1.5)

    assert store.arrivals({"A31N"}) == []
    status = store.feed_status(FROZEN_NOW + 120, stale_after=90)
    assert status["ace"]["stale"] is True
    assert status["ace"]["header_ts"] is not None


def test_alerts_indexed_by_route_and_stop(rt_store, redis_client):
    by_route = json.loads(redis_client.get("alerts:route:7"))
    seven = rt_store.alerts(route_ids={"7"})

    assert {a["id"] for a in seven} == set(by_route)
    stop_specific = next(a for a in seven if a["stop_ids"])
    assert stop_specific["id"] in {
        a["id"] for a in rt_store.alerts(stop_ids={stop_specific["stop_ids"][0]})
    }


def test_alert_rewrite_clears_vanished_keys(rt_store, redis_client):
    rt_store.write_alerts(FROZEN_NOW, [], FROZEN_NOW)

    assert redis_client.keys("alerts:route:*") == []
    assert rt_store.alerts() == []


def test_feed_status_covers_every_feed(rt_store):
    status = rt_store.feed_status(FROZEN_NOW, stale_after=90)

    assert not any(s["stale"] for s in status.values())
    assert status["ace"]["header_ts"] == 1790618389
    assert status["alerts"]["age_seconds"] == 0  # judged on last poll, not header
