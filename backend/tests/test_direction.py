import pytest

from app.realtime.departures import resolve_direction
from app.stations.lookup import StationStop


def stop(north, south):
    return StationStop("X01", [], [], north, south)


@pytest.mark.parametrize(
    ("value", "labels", "expected"),
    [
        ("N", ("Uptown & The Bronx", "Downtown & Brooklyn"), "N"),
        ("southbound", ("Uptown & The Bronx", "Downtown & Brooklyn"), "S"),
        ("uptown", ("Uptown & The Bronx", "Downtown & Brooklyn"), "N"),
        ("the Bronx", ("Uptown & The Bronx", "Downtown & Brooklyn"), "N"),
        ("Brooklyn", ("Uptown & The Bronx", "Downtown & Brooklyn"), "S"),
        ("brooklyn-bound train", ("Last Stop", "Brooklyn"), "S"),
        ("towards Queens", ("Queens", "Hudson Yards"), "N"),
        ("Grand Central", ("Last Stop", "Grand Central"), "S"),
    ],
)
def test_resolve_direction(value, labels, expected):
    code, label = resolve_direction(value, stop(*labels))
    assert code == expected
    assert label == labels[0 if expected == "N" else 1]


@pytest.mark.parametrize("value", ["queens", "", "the", "downtown uptown"])
def test_unmatched_direction_lists_options(value):
    with pytest.raises(ValueError, match=r"use N \(Uptown\) or S \(Downtown\)"):
        resolve_direction(value, stop("Uptown", "Downtown"))


def test_missing_labels_fall_back_to_compass():
    assert resolve_direction("n", stop(None, None)) == ("N", "Northbound")
