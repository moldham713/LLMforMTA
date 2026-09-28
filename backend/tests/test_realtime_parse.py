from collections import Counter
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2

from app.realtime import nyct_subway_pb2
from app.realtime.parse import FeedParseError, parse_trip_updates

REALTIME = Path(__file__).parent / "fixtures" / "realtime"
# Header timestamp of the recorded 1234567 snapshot.
SNAPSHOT_NOW = 1790618390


def snapshot(feed: str) -> bytes:
    return (REALTIME / f"{feed}.pb").read_bytes()


def message(*trips, header_ts=1_000_000) -> bytes:
    """Build a GTFS-rt message; each trip is (route, direction|None, [(stop, arr, dep)])."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "1.0"
    msg.header.timestamp = header_ts
    for i, (route, direction, stops) in enumerate(trips):
        entity = msg.entity.add(id=str(i))
        tu = entity.trip_update
        tu.trip.trip_id = f"trip{i}"
        tu.trip.route_id = route
        nyct = tu.trip.Extensions[nyct_subway_pb2.nyct_trip_descriptor]
        nyct.is_assigned = i % 2 == 0
        if direction:
            nyct.direction = direction
        for stop_id, arr, dep in stops:
            u = tu.stop_time_update.add(stop_id=stop_id)
            if arr:
                u.arrival.time = arr
            if dep:
                u.departure.time = dep
    return msg.SerializeToString()


@pytest.mark.parametrize(("feed", "routes"), [
    ("1234567", {"1", "2", "3", "4", "5", "6", "6X", "7", "GS"}),
    ("bdfm", {"B", "D", "F", "M", "FS"}),
    ("ace", {"A", "C", "E", "H"}),
    ("nqrw", {"N", "Q", "R", "W"}),
])  # fmt: skip
def test_snapshot_normalizes(feed, routes):
    header_ts, arrivals = parse_trip_updates(snapshot(feed), feed, SNAPSHOT_NOW)

    assert abs(header_ts - SNAPSHOT_NOW) < 60
    assert len(arrivals) > 500
    assert {a["route_id"] for a in arrivals} == routes
    assert all(a["feed"] == feed for a in arrivals)
    assert all(a["arrival_ts"] >= SNAPSHOT_NOW for a in arrivals)
    assert {a["stop_id"][-1] for a in arrivals} == {"N", "S"}
    assert {a["is_assigned"] for a in arrivals} == {True, False}
    assert set(arrivals[0]) == {
        "route_id", "stop_id", "direction", "arrival_ts", "destination_stop_id",
        "is_assigned", "feed",
    }  # fmt: skip


def test_nyct_direction_agrees_with_platform_suffix():
    _, arrivals = parse_trip_updates(snapshot("1234567"), "1234567", SNAPSHOT_NOW)
    agree = Counter(a["direction"] == a["stop_id"][-1] for a in arrivals)
    assert agree[True] / sum(agree.values()) > 0.99


def test_destination_is_last_stop_of_the_trip():
    _, arrivals = parse_trip_updates(snapshot("1234567"), "1234567", SNAPSHOT_NOW)
    # Southbound 1 trains in the snapshot all run to South Ferry.
    dests = {
        a["destination_stop_id"]
        for a in arrivals
        if a["route_id"] == "1" and a["stop_id"] == "127S"
    }
    assert dests == {"142S"}


def test_past_times_are_dropped_by_parser():
    everything = parse_trip_updates(snapshot("l"), "l", 0)[1]
    later = parse_trip_updates(snapshot("l"), "l", SNAPSHOT_NOW + 600)[1]
    assert len(later) < len(everything)
    assert min(a["arrival_ts"] for a in later) >= SNAPSHOT_NOW + 600


def test_departure_used_when_arrival_missing():
    data = message(("A", 1, [("A02N", None, 1_000_100), ("A03N", 1_000_200, 1_000_230)]))

    _, arrivals = parse_trip_updates(data, "ace", 1_000_000)

    assert [(a["stop_id"], a["arrival_ts"]) for a in arrivals] == [
        ("A02N", 1_000_100),
        ("A03N", 1_000_200),
    ]
    assert {a["destination_stop_id"] for a in arrivals} == {"A03N"}


def test_direction_from_extension_else_platform_suffix():
    data = message(
        ("A", 3, [("A02S", 1_000_100, None)]),
        ("C", None, [("A03N", 1_000_100, None)]),
    )

    _, arrivals = parse_trip_updates(data, "ace", 1_000_000)

    assert [(a["route_id"], a["direction"], a["is_assigned"]) for a in arrivals] == [
        ("A", "S", True),
        ("C", "N", False),
    ]


def test_trip_without_updates_is_skipped():
    _, arrivals = parse_trip_updates(message(("A", 1, [])), "ace", 0)
    assert arrivals == []


@pytest.mark.parametrize("data", [b"\xff\xff\xff not protobuf", b"<html>503</html>", b""])
def test_garbage_is_a_parse_error(data):
    with pytest.raises(FeedParseError):
        parse_trip_updates(data, "ace", 0)
