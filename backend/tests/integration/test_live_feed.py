"""Opt-in: loads the real MTA static and realtime feeds (make test-live)."""

import pytest
from sqlalchemy import text

from app.config import DEFAULT_GTFS_URL, DEFAULT_STATIONS_URL
from app.gtfs.loader import load_gtfs
from app.realtime.departures import DeparturesService
from app.realtime.feeds import feed_urls
from app.realtime.poller import Poller
from app.routes_display import SHUTTLE_AT_STOP
from app.stations.loader import load_stations
from app.stations.lookup import StationLookup

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def real_data(engine):
    """The real static feed and stations dataset, loaded once for this module."""
    result = load_gtfs(engine, DEFAULT_GTFS_URL, force=True)
    stations = load_stations(engine, DEFAULT_STATIONS_URL)
    return engine, result, stations


def ids(routes: list[dict]) -> set[str]:
    return {r["route_id"] for r in routes}


def test_real_feed_loads_and_resolves_stations(real_data):
    engine, result, stations = real_data

    print(f"\nfeed_version: {result.feed_version}")
    print(f"sha256: {result.file_sha256}")
    for table, n in result.row_counts.items():
        print(f"  {table:<15} {n:>10,}")
    print(f"download: {result.download_seconds}s  total: {result.duration_seconds}s")
    print(f"stations: {stations.stations}  complexes: {stations.complexes}  "
          f"aliases: {stations.aliases}  skipped aliases: {stations.skipped_aliases}")  # fmt: skip

    assert result.status == "success"
    assert result.row_counts["stops"] > 1000
    assert result.row_counts["stop_times"] > 1_000_000
    assert stations.skipped_aliases == []

    lookup = StationLookup(engine)
    assert lookup.search_stations("times sq")[0].complex_id == 611
    assert len([r for r in lookup.search_stations("86 st") if r.name == "86 St"]) >= 4
    top = lookup.search_stations("14th street and 8th ave")[0]
    assert top.name == "14 St/8 Av"
    assert ids(top.typical_routes) == {"A", "C", "E", "L"}
    assert [r.complex_id for r in lookup.search_stations("8th ave", route="L")] == [618]
    assert lookup.nearest_stations(40.7359, -73.9906)[0].complex_id == 602

    for query in ("times sq", "86 st", "14th street and 8th ave"):
        print(f"{query!r}:")
        for r in lookup.search_stations(query):
            labels = " ".join(x["label"] for x in r.typical_routes)
            print(f"  {r.score:.2f} {r.name} ({r.borough}) {labels}")


def test_shuttle_map_agrees_with_scheduled_routes(real_data):
    engine, _, _ = real_data
    lookup = StationLookup(engine)
    with engine.connect() as conn:
        complex_of = dict(
            conn.execute(text("SELECT gtfs_stop_id, complex_id FROM gtfs.stations")).all()
        )
    for stop_id, shuttle in SHUTTLE_AT_STOP.items():
        info = lookup.complex_info(complex_of[stop_id])
        stop = next(s for s in info.stops if s.stop_id == stop_id)
        assert shuttle in stop.scheduled, stop_id


def test_real_realtime_feeds_poll_and_serve(real_data, store):
    engine, _, _ = real_data

    results = Poller(store, feed_urls({})).poll_once()
    print(f"\npoll results: {results}")
    assert set(results.values()) == {"ok"}

    svc = DeparturesService(StationLookup(engine), store)
    for complex_id, route, direction in [(611, "1", "uptown"), (618, "L", "Brooklyn")]:
        result = svc.get_departures(complex_id, route, direction)
        print(route, result["status"], [d["display"] for d in result["departures"]])
        assert result["stale"] is False
        assert result["status"] in ("ok", "no_trains_showing")
