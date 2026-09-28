"""get_departures / get_alerts against real PostGIS and Redis loaded from recorded feeds.

The clock is frozen at the snapshot time, so "minutes away" is deterministic.
"""

import pytest

from app.realtime.departures import DeparturesService, UnknownStation
from app.realtime.poller import Poller
from app.realtime.store import RealtimeStore
from app.stations.lookup import StationLookup
from tests.integration.conftest import FROZEN_NOW, REALTIME_DIR

pytestmark = pytest.mark.integration

TIMES_SQ, FOURTEENTH_AND_8TH, EIGHTY_SIXTH_CPW, UNION_SQ = 611, 618, 158, 602


def service(loaded, store: RealtimeStore, now: float = FROZEN_NOW) -> DeparturesService:
    return DeparturesService(StationLookup(loaded), store, clock=lambda: now)


@pytest.fixture
def svc(loaded, rt_store) -> DeparturesService:
    return service(loaded, rt_store)


# --- get_departures -----------------------------------------------------------------


def test_normal_case(svc):
    result = svc.get_departures(FOURTEENTH_AND_8TH, "A", "uptown")

    assert result["status"] == "ok"
    assert result["station"] == {"complex_id": FOURTEENTH_AND_8TH, "name": "14 St/8 Av"}
    assert result["direction"] == {"code": "N", "label": "Uptown"}
    assert result["route"]["route_id"] == "A" and result["route"]["color"] == "#0062CF"
    first = result["departures"][0]
    assert first["arrival_ts"] == FROZEN_NOW + 196
    assert (first["minutes_away"], first["display"]) == (3, "3 min")
    assert first["destination"] == "Inwood-207 St"
    assert first["is_assigned"] is True
    assert first["route"]["route_id"] == "A"
    assert len(result["departures"]) == 3
    times = [d["arrival_ts"] for d in result["departures"]]
    assert times == sorted(times)
    assert result["stale"] is False
    assert result["data_as_of"] == "2026-09-28T17:59:49+00:00"  # ace feed header


def test_shared_platform_merges_two_feeds(svc, rt_store):
    # 86 St (CPW): B trains come from the bdfm feed, C trains from the ace feed.
    assert {a["feed"] for a in rt_store.arrivals({"A20N"})} == {"ace", "bdfm"}

    b = svc.get_departures(EIGHTY_SIXTH_CPW, "B", "N")
    c = svc.get_departures(EIGHTY_SIXTH_CPW, "C", "N")

    assert (b["departures"][0]["minutes_away"], b["departures"][0]["route"]["route_id"]) == (3, "B")
    assert (c["departures"][0]["minutes_away"], c["departures"][0]["route"]["route_id"]) == (9, "C")
    assert b["status"] == c["status"] == "ok"


def test_stale_feed_is_flagged(loaded, rt_store):
    result = service(loaded, rt_store, now=FROZEN_NOW + 100).get_departures(
        FOURTEENTH_AND_8TH, "L", "Brooklyn"
    )

    assert result["stale"] is True
    assert result["data_as_of"] == "2026-09-28T17:59:52+00:00"
    assert all(d["arrival_ts"] >= FROZEN_NOW + 100 for d in result["departures"])


def test_one_feed_failing_while_others_stay_intact(loaded, rt_store):
    later = FROZEN_NOW + 30

    def fetch(url, timeout):
        if url == "ace":
            raise TimeoutError("timed out")
        return (REALTIME_DIR / ("alerts.json" if url == "alerts" else f"{url}.pb")).read_bytes()

    feeds = ["1234567", "ace", "bdfm", "g", "jz", "nqrw", "l", "si", "alerts"]
    results = Poller(rt_store, {f: f for f in feeds}, fetch=fetch, clock=lambda: later).poll_once()

    assert results["ace"] == "TimeoutError: timed out"
    assert all(r == "ok" for f, r in results.items() if f != "ace")
    status = rt_store.feed_status(later, stale_after=90)
    assert status["ace"]["failures"] == 1 and status["ace"]["last_error"]
    assert status["bdfm"]["last_success"] == later

    svc = service(loaded, rt_store, now=later)
    # Both feeds still serve the shared platform: bdfm fresh, ace from its last good poll.
    assert svc.get_departures(EIGHTY_SIXTH_CPW, "B", "N")["departures"]
    assert svc.get_departures(EIGHTY_SIXTH_CPW, "C", "N")["departures"]


def test_no_trains_showing_attaches_alerts(loaded, rt_store):
    rt_store.write_arrivals("ace", FROZEN_NOW, [], FROZEN_NOW)

    result = service(loaded, rt_store).get_departures(FOURTEENTH_AND_8TH, "A", "N")

    assert result["status"] == "no_trains_showing"
    assert result["departures"] == []
    assert result["alerts"], "a typical route with no trains must say why if it can"
    assert result["alerts"][0]["category"] == "current"
    assert "A" in result["alerts"][0]["route_ids"]


def test_shuttle_resolved_by_station(svc):
    result = svc.get_departures(TIMES_SQ, "S", "Grand Central")

    assert result["route"]["route_id"] == "GS"
    assert result["route"]["route_ids"] == ["GS"]
    assert (result["route"]["label"], result["route"]["shuttle_name"]) == ("S", "42 St Shuttle")
    assert result["direction"] == {"code": "S", "label": "Grand Central"}
    first = result["departures"][0]
    assert (first["minutes_away"], first["display"]) == (0, "arriving")
    assert first["destination"] == "Grand Central-42 St"


def test_route_not_typical_here(svc):
    result = svc.get_departures(TIMES_SQ, "L", "N")

    assert result["status"] == "route_not_typical_here"
    assert result["departures"] == []


def test_label_covers_express_variants(svc):
    result = svc.get_departures(UNION_SQ, "6", "uptown")

    assert result["route"]["route_ids"] == ["6", "6X"]
    assert {d["route"]["label"] for d in result["departures"]} == {"6"}


def test_bad_inputs(svc):
    with pytest.raises(UnknownStation):
        svc.get_departures(999_999, "A", "N")
    with pytest.raises(ValueError, match="unknown route"):
        svc.get_departures(FOURTEENTH_AND_8TH, "K", "N")
    with pytest.raises(ValueError, match=r"use N \(Uptown\) or S \(Downtown\)"):
        svc.get_departures(FOURTEENTH_AND_8TH, "A", "queens")


# --- get_alerts ---------------------------------------------------------------------


def _active(alert) -> bool:
    return any(
        p["end"] is None or p["end"] > "2026-09-28T17:59:52" for p in alert["active_periods"]
    )


def test_alerts_active_only_delays_first(svc, rt_store):
    everything = rt_store.alerts()
    alerts = svc.get_alerts()["alerts"]

    assert 0 < len(alerts) < len(everything)
    ranks = [{"current": 0, "other": 1, "planned": 2}[a["category"]] for a in alerts]
    assert ranks == sorted(ranks)
    assert all(_active(a) for a in alerts)
    assert all(len(a["active_periods"]) <= 3 for a in alerts)
    assert alerts[0]["routes"][0]["label"]


def test_alerts_by_route_and_station(svc):
    seven = svc.get_alerts(route="7")["alerts"]
    at_times_sq = svc.get_alerts(route="7", complex_id=TIMES_SQ)["alerts"]

    stop_specific = [a for a in seven if a["stop_ids"]]
    assert stop_specific, "fixture has a 7 alert for Queens stops"
    # Line-wide 7 alerts remain; ones pinned to Queens stations don't apply at Times Sq.
    assert {a["id"] for a in at_times_sq} == {a["id"] for a in seven if not a["stop_ids"]}


def test_alerts_by_station_only(svc):
    # Station-only filtering returns just alerts that name one of its stops.
    assert svc.get_alerts(complex_id=TIMES_SQ)["alerts"] == [
        a
        for a in svc.get_alerts()["alerts"]
        if set(a["stop_ids"]) & {"127", "725", "902", "A27", "R16"}
    ]


# --- API ------------------------------------------------------------------------------


def test_departures_endpoint(rt_app):
    resp = rt_app.test_client().get(
        "/api/departures", query_string={"complex_id": TIMES_SQ, "route": "S", "direction": "S"}
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["route"]["route_id"] == "GS"
    assert body["departures"][0]["display"] == "arriving"


def test_departures_endpoint_errors(rt_app):
    client = rt_app.test_client()
    missing = client.get("/api/departures?complex_id=999999&route=A&direction=N")
    bad_direction = client.get("/api/departures?complex_id=618&route=A&direction=queens")

    assert missing.status_code == 404
    assert bad_direction.status_code == 400
    assert "Uptown" in bad_direction.get_json()["error"]


def test_alerts_endpoint(rt_app):
    resp = rt_app.test_client().get("/api/alerts", query_string={"route": "A"})

    assert resp.status_code == 200
    alerts = resp.get_json()["alerts"]
    assert alerts[0]["category"] == "current"


def test_health_reports_feed_freshness(rt_app):
    client = rt_app.test_client()
    fresh = client.get("/api/health").get_json()
    rt_app.extensions["clock"] = lambda: FROZEN_NOW + 100
    stale = client.get("/api/health")

    assert fresh["status"] == "ok"
    assert stale.status_code == 200
    assert stale.get_json()["status"] == "degraded"
    assert all(f["stale"] for name, f in stale.get_json()["feeds"].items() if name != "alerts")


def test_stations_routes_endpoint(rt_app):
    body = rt_app.test_client().get(f"/api/stations/{TIMES_SQ}/routes").get_json()

    gs = next(r for r in body["typical_routes"] if r["route_id"] == "GS")
    assert (gs["label"], gs["stop_id"], gs["south_label"]) == ("S", "902", "Grand Central")


# --- alerts freshness ---------------------------------------------------------------


def test_alerts_carry_freshness(svc):
    result = svc.get_alerts(route="A")

    assert result["alerts_available"] is True
    assert result["alerts_stale"] is False
    assert result["alerts_as_of"] == "2026-09-28T17:59:52+00:00"  # last good poll
    assert all(a["alert_type"] for a in result["alerts"])


def test_alerts_go_stale_after_three_minutes(loaded, rt_store):
    result = service(loaded, rt_store, now=FROZEN_NOW + 181).get_alerts(route="A")

    assert result["alerts_stale"] is True
    assert result["alerts_available"] is True


def test_missing_alerts_are_unavailable_not_empty(loaded, rt_store, redis_client):
    redis_client.delete("alerts:present", "alerts:data")

    departures = service(loaded, rt_store).get_departures(FOURTEENTH_AND_8TH, "A", "N")

    assert departures["alerts"] == []
    assert departures["alerts_available"] is False
    assert departures["alerts_stale"] is True
    assert departures["alerts_as_of"] is None


def test_upcoming_alerts_are_flagged_not_active(svc):
    now_only = svc.get_alerts()["alerts"]
    soon = svc.get_alerts(upcoming_within=2 * 3600)["alerts"]

    assert all(a["active_now"] for a in now_only)
    assert len(soon) >= len(now_only)
    assert {a["id"] for a in soon if a["active_now"]} == {a["id"] for a in now_only}


# --- direction_toward ---------------------------------------------------------------

COLUMBUS_CIRCLE, EIGHTH_AV_L = 614, FOURTEENTH_AND_8TH


@pytest.mark.parametrize(
    ("complex_id", "route", "destination", "code"),
    [
        (COLUMBUS_CIRCLE, "A", "Brooklyn", "S"),
        (TIMES_SQ, "1", "Van Cortlandt", "N"),
        # 8 Av is the L's Manhattan terminal; Canarsie-bound (east) trains are "S".
        (EIGHTH_AV_L, "L", "Canarsie", "S"),
        (TIMES_SQ, "1", "the Bronx", "N"),
        (TIMES_SQ, "1", "South Ferry", "S"),
        (TIMES_SQ, "7", "Flushing", "N"),
        (COLUMBUS_CIRCLE, "A", "uptown", "N"),
    ],
)
def test_direction_toward(svc, complex_id, route, destination, code):
    result = svc.direction_toward(complex_id, route, destination)

    assert result is not None
    assert result["code"] == code
    assert result["confidence"] in ("high", "medium")


def test_direction_toward_labels(svc):
    result = svc.direction_toward(EIGHTH_AV_L, "L", "Canarsie")

    assert result["label"] == "Brooklyn"
    assert result["destination"] == "Canarsie-Rockaway Pkwy"


def test_direction_toward_off_route_is_none(svc):
    assert svc.direction_toward(TIMES_SQ, "1", "Flushing") is None
    assert svc.direction_toward(TIMES_SQ, "1", "Staten Island") is None
    assert svc.direction_toward(COLUMBUS_CIRCLE, "A", "zzzz qqqq") is None
