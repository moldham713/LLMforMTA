import csv
from pathlib import Path

import pytest

from app.realtime.feeds import FEED_ROUTES, TRIP_FEEDS
from app.realtime.parse import parse_trip_updates
from app.routes_display import ROUTES, display, resolve_route, sort_route_ids, typical_route_ids

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("route_id", "label", "is_express", "shuttle_name"),
    [
        ("6X", "6", True, None),
        ("7X", "7", True, None),
        ("FX", "F", True, None),
        ("GS", "S", False, "42 St Shuttle"),
        ("FS", "S", False, "Franklin Av Shuttle"),
        ("H", "S", False, "Rockaway Park Shuttle"),
        ("SI", "SIR", False, None),
        ("A", "A", False, None),
    ],
)
def test_display_fields(route_id, label, is_express, shuttle_name):
    d = display(route_id)
    assert (d.label, d.is_express, d.shuttle_name) == (label, is_express, shuttle_name)
    assert set(d.to_dict()) == {
        "route_id", "label", "is_express", "color", "text_color", "shuttle_name",
    }  # fmt: skip


def test_every_static_route_is_mapped_with_its_colors():
    with (FIXTURES / "gtfs" / "routes.txt").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = ROUTES[row["route_id"]]
            assert d.color == f"#{row['route_color']}"
            assert d.text_color == f"#{row['route_text_color']}"


def test_every_realtime_route_is_mapped():
    seen = set()
    for feed in TRIP_FEEDS:
        _, arrivals = parse_trip_updates(
            (FIXTURES / "realtime" / f"{feed}.pb").read_bytes(), feed, 0
        )
        seen |= {a["route_id"] for a in arrivals}
        assert {a["route_id"] for a in arrivals} <= FEED_ROUTES[feed], feed
    assert seen <= ROUTES.keys()


def test_unknown_route_renders_as_itself():
    d = display("K")
    assert (d.route_id, d.label, d.is_express) == ("K", "K", False)
    assert sort_route_ids(["K", "A", "1"]) == ["A", "1", "K"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("6", {"6", "6X"}),
        ("6x", {"6X"}),
        ("S", {"GS", "FS", "H"}),
        ("gs", {"GS"}),
        ("SIR", {"SI"}),
        ("SI", {"SI"}),
        (" l ", {"L"}),
    ],
)
def test_resolve_route(value, expected):
    assert resolve_route(value) == expected


def test_resolve_route_rejects_unknown():
    with pytest.raises(ValueError, match="unknown route"):
        resolve_route("K")


@pytest.mark.parametrize(
    ("stop_id", "daytime", "scheduled", "expected"),
    [
        ("902", "S", set(), ["GS"]),
        ("D26", "B Q S", {"B", "Q", "FS"}, ["B", "Q", "FS"]),
        ("H12", "A S", {"A", "H"}, ["A", "H"]),
        ("X99", "S", {"FS"}, ["FS"]),  # unknown stop: fall back to the scheduled shuttle
        ("S31", "SIR", {"SI"}, ["SI"]),
        ("A31", "A C E", set(), ["A", "C", "E"]),
        ("A31", None, set(), []),
    ],
)
def test_typical_route_ids(stop_id, daytime, scheduled, expected):
    assert typical_route_ids(stop_id, daytime, scheduled) == expected
