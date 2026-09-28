"""The eval cases' expectations hold in the fixture data (no model involved), so an eval
failure means the agent went wrong, not the case."""

from collections import Counter

import pytest

from app.realtime.departures import DeparturesService
from app.stations.lookup import StationLookup
from evals.harness import load_cases
from tests.integration.conftest import FROZEN_NOW

pytestmark = pytest.mark.integration

CASES = load_cases()
CATEGORIES = {
    "clear_request", "ambiguous_station", "missing_direction", "destination_direction",
    "slang_shorthand", "misspellings", "express_shuttle", "follow_up_refresh",
    "no_trains_showing", "alerts_unavailable", "out_of_scope",
}  # fmt: skip


def test_case_inventory():
    counts = Counter(c.category for c in CASES)
    assert set(counts) == CATEGORIES
    assert min(counts.values()) >= 5
    assert len(CASES) >= 50
    assert len({c.id for c in CASES}) == len(CASES)


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if {"complex_id", "route"} <= set(c.expect.get("slots", {}))],
    ids=lambda c: c.id,
)
def test_expected_slots_are_reachable(case, loaded, rt_store, redis_client):
    slots = case.expect["slots"]
    variant = case.setup.get("variant", "normal")
    if variant == "no_ace":
        rt_store.write_arrivals("ace", FROZEN_NOW, [], FROZEN_NOW)
    svc = DeparturesService(StationLookup(loaded), rt_store, clock=lambda: FROZEN_NOW)

    result = svc.get_departures(slots["complex_id"], slots["route"], slots.get("direction", "N"))

    assert result["route"]["label"] == slots["route"]
    expected = case.expect.get("status")
    if expected is None and case.category != "no_trains_showing":
        expected = "ok"
    assert expected is None or result["status"] == expected
