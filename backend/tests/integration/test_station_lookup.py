import pytest
from sqlalchemy import text

from app.stations.lookup import StationLookup

pytestmark = pytest.mark.integration

TIMES_SQ = 611
UNION_SQ = 602
FOURTEENTH_AND_8TH = 618
FOURTEENTH_AND_6TH = 601
PENN_123, PENN_ACE = 318, 164
UNION_SQ_POINT = (40.7359, -73.9906)  # Union Square Park, south end


@pytest.fixture
def lookup(loaded) -> StationLookup:
    return StationLookup(loaded)


def ids(routes: list[dict]) -> set[str]:
    return {r["route_id"] for r in routes}


# --- data built by the stations loader --------------------------------------------


def test_complexes_group_parent_stations(loaded):
    with loaded.connect() as conn:
        stop_ids = conn.execute(
            text("SELECT gtfs_stop_id FROM gtfs.stations WHERE complex_id = :c ORDER BY 1"),
            {"c": TIMES_SQ},
        ).scalars()
        names = dict(
            conn.execute(text("SELECT complex_id, name FROM gtfs.station_complexes")).all()
        )
    assert list(stop_ids) == ["127", "725", "902", "A27", "R16"]
    assert names[TIMES_SQ] == "Times Sq-42 St/42 St-Port Authority Bus Terminal"
    assert names[FOURTEENTH_AND_8TH] == "14 St/8 Av"


def test_stops_sharing_a_station_id_are_both_kept(lookup):
    # MTA gives W 4 St one Station ID (167) but two GTFS stops (A32, D20).
    routes = {r.route_id: r.stop_id for r in lookup.routes_at_station(167).typical_routes}
    assert routes["A"] == "A32"
    assert routes["F"] == "D20"


def test_only_aliases_for_known_complexes_are_seeded(loaded):
    with loaded.connect() as conn:
        aliases = conn.execute(
            text("SELECT alias_norm, complex_id FROM gtfs.station_aliases")
        ).all()
        known = set(conn.execute(text("SELECT complex_id FROM gtfs.station_complexes")).scalars())
    assert ("port authority", TIMES_SQ) in aliases
    assert {cid for _, cid in aliases} <= known


# --- search_stations --------------------------------------------------------------


def test_times_sq_resolves_to_complex_with_routes(lookup):
    top = lookup.search_stations("times sq")[0]

    assert top.complex_id == TIMES_SQ
    assert top.name.startswith("Times Sq-42 St")
    assert top.borough == "Manhattan"
    assert {"1", "2", "3", "7", "A", "C", "E", "N", "Q", "R", "W", "GS"} <= ids(
        top.scheduled_routes
    )
    assert ids(top.typical_routes) == {"1", "2", "3", "7", "A", "C", "E", "N", "Q", "R", "W", "GS"}


def test_86_st_returns_candidates_on_different_lines(lookup):
    results = lookup.search_stations("86 st")

    exact = [r for r in results if r.name == "86 St"]
    assert len(exact) >= 4
    assert len({frozenset(ids(r.typical_routes)) for r in exact}) == len(exact)
    assert {r.borough for r in exact} == {"Manhattan", "Brooklyn"}


@pytest.mark.parametrize(
    "query",
    ["14th street and 8th ave", "14th and 8th", "8th Avenue & W 14th St.", "14 St / 8 Av"],
)
def test_cross_streets_resolve_to_14_st_8_av(lookup, query):
    top = lookup.search_stations(query)[0]

    assert top.complex_id == FOURTEENTH_AND_8TH
    assert top.name == "14 St/8 Av"
    assert ids(top.typical_routes) == ids(top.scheduled_routes) == {"A", "C", "E", "L"}


def test_cross_streets_distinguish_neighbouring_complex(lookup):
    assert lookup.search_stations("14th and 6th")[0].complex_id == FOURTEENTH_AND_6TH


@pytest.mark.parametrize(
    ("query", "complex_id"),
    [("port authority", TIMES_SQ), ("Times Square", TIMES_SQ), ("union square", UNION_SQ)],
)
def test_aliases(lookup, query, complex_id):
    assert lookup.search_stations(query)[0].complex_id == complex_id


def test_alias_can_name_several_complexes(lookup):
    top_two = {r.complex_id for r in lookup.search_stations("penn station")[:2]}
    assert top_two == {PENN_123, PENN_ACE}


def test_results_are_ranked_and_limited(lookup):
    results = lookup.search_stations("st", limit=3)
    assert len(results) <= 3
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


@pytest.mark.parametrize("query", ["", "   ", "&&&", "zzzzqqqq"])
def test_nonsense_returns_nothing(lookup, query):
    assert lookup.search_stations(query) == []


# --- nearest_stations -------------------------------------------------------------


def test_nearest_to_union_square(lookup):
    results = lookup.nearest_stations(*UNION_SQ_POINT)

    assert results[0].complex_id == UNION_SQ
    assert results[0].distance_m < 200
    assert {"L", "N", "Q", "R", "W", "4", "5", "6"} <= ids(results[0].typical_routes)
    distances = [r.distance_m for r in results]
    assert distances == sorted(distances)
    assert all(d <= 1000 for d in distances)


def test_nearest_respects_max_meters_and_limit(lookup):
    assert len(lookup.nearest_stations(*UNION_SQ_POINT, limit=2)) == 2
    assert [r.complex_id for r in lookup.nearest_stations(*UNION_SQ_POINT, max_meters=200)] == [
        UNION_SQ
    ]
    # Middle of the Hudson River.
    assert lookup.nearest_stations(40.75, -74.02, max_meters=500) == []


# --- routes_at_station ------------------------------------------------------------


def test_routes_at_station_include_colors_and_direction_labels(lookup):
    routes = {r.route_id: r for r in lookup.routes_at_station(FOURTEENTH_AND_8TH).typical_routes}

    assert set(routes) == {"A", "C", "E", "L"}
    assert routes["A"].stop_id == "A31"
    assert (routes["A"].north_label, routes["A"].south_label) == ("Uptown", "Downtown")
    assert routes["L"].stop_id == "L01"
    assert (routes["L"].north_label, routes["L"].south_label) == ("Last Stop", "Brooklyn")
    assert routes["A"].color.startswith("#") and len(routes["A"].color) == 7


def test_routes_at_unknown_station(lookup):
    assert lookup.routes_at_station(999_999) is None


# --- debug endpoints --------------------------------------------------------------


def test_search_endpoint(app):
    resp = app.test_client().get("/api/stations/search", query_string={"q": "times sq"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["results"][0]["complex_id"] == TIMES_SQ
    assert set(body["results"][0]) == {
        "complex_id", "name", "borough", "typical_routes", "scheduled_routes", "score",
    }  # fmt: skip
    assert set(body["results"][0]["typical_routes"][0]) == {
        "route_id", "label", "is_express", "color", "text_color", "shuttle_name",
    }  # fmt: skip


def test_nearest_endpoint(app):
    lat, lon = UNION_SQ_POINT
    resp = app.test_client().get("/api/stations/nearest", query_string={"lat": lat, "lon": lon})

    assert resp.status_code == 200
    assert resp.get_json()["results"][0]["complex_id"] == UNION_SQ


# --- Phase 2 follow-ups: typical vs scheduled, cutoff, route filter ---------------------


def test_typical_and_scheduled_routes_are_separate(lookup):
    # This week's feed reroutes A/C/E via 6 Av, so they're scheduled at 14 St (D19)
    # without being its typical service.
    routes = lookup.routes_at_station(FOURTEENTH_AND_6TH)

    typical = {r.route_id for r in routes.typical_routes}
    scheduled = {r.route_id for r in routes.scheduled_routes}
    assert typical == {"1", "2", "3", "F", "M", "L"}
    assert {"A", "C", "E", "FX"} <= scheduled - typical


def test_shuttle_label_resolves_by_station(lookup):
    times_sq = {r.route_id: r for r in lookup.routes_at_station(TIMES_SQ).typical_routes}

    assert "GS" in times_sq and "S" not in times_sq
    assert (times_sq["GS"].label, times_sq["GS"].shuttle_name) == ("S", "42 St Shuttle")


def test_express_variants_carry_display_fields(lookup):
    union_sq = {r.route_id: r for r in lookup.routes_at_station(UNION_SQ).scheduled_routes}

    assert (union_sq["6X"].label, union_sq["6X"].is_express) == ("6", True)
    assert (union_sq["6"].label, union_sq["6"].is_express) == ("6", False)


def test_route_filter_keeps_only_complexes_on_that_route(lookup):
    results = lookup.search_stations("8th ave", route="L")

    assert [r.complex_id for r in results] == [FOURTEENTH_AND_8TH]


def test_route_filter_accepts_labels_and_rejects_unknown_routes(lookup):
    assert lookup.search_stations("times sq", route="S")[0].complex_id == TIMES_SQ
    assert lookup.search_stations("86 st", route="6")[0].typical_routes[0]["label"] == "4"
    with pytest.raises(ValueError, match="unknown route"):
        lookup.search_stations("times sq", route="K")


def test_relative_cutoff_drops_weak_matches(loaded):
    strict = StationLookup(loaded, relative_cutoff=0.6).search_stations("14th street and 8th ave")
    loose = StationLookup(loaded, relative_cutoff=0.0).search_stations("14th street and 8th ave")

    top = strict[0].score
    assert all(r.score >= 0.6 * top for r in strict)
    assert len(loose) > len(strict)


def test_numbers_must_match(lookup):
    # "8 av" shares trigrams with "3 av" and "6 av"; the number mismatch keeps them out.
    names = [r.name for r in lookup.search_stations("8th ave")]
    assert "3 Av" not in names
    assert "14 St/6 Av" not in names
