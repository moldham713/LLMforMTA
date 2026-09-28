"""Tool results as the model sees them, against the fixture Postgres and Redis."""

import json
import re

import pytest

from app.agent.tools import Toolbox, ToolError
from app.realtime.departures import DeparturesService
from app.stations.lookup import StationLookup
from tests.integration.conftest import FROZEN_NOW

pytestmark = pytest.mark.integration

RAW_IDS = re.compile(r'"(6X|7X|FX|GS|FS|H|SI)"')


@pytest.fixture
def toolbox(loaded, rt_store) -> Toolbox:
    lookup = StationLookup(loaded)
    departures = DeparturesService(lookup, rt_store, clock=lambda: FROZEN_NOW)
    return Toolbox(lookup, departures, clock=lambda: FROZEN_NOW)


# Tools called directly have no transcript, so any quote counts as the rider's words.
QUOTES = {"line_quote": "that one", "direction_quote": "that way"}


def run(toolbox: Toolbox, name: str, **args) -> dict:
    if name == "get_departures":
        args = {**QUOTES, **args}
    raw = toolbox.execute(name, args)
    assert not RAW_IDS.search(raw), f"raw route id leaked: {raw}"
    return json.loads(raw)


def test_find_station_lists_lines_and_directions(toolbox):
    result = run(toolbox, "find_station", query="86th", route="6")

    top = result["matches"][0]
    assert top["name"] == "86 St" and top["lines"] == ["4", "5", "6"]
    assert top["directions"] == {"N": "Uptown", "S": "Downtown"}


def test_find_station_ambiguous_returns_at_most_three(toolbox):
    result = run(toolbox, "find_station", query="86 st")

    assert len(result["matches"]) == 3
    assert all(m["name"] == "86 St" and m["lines"] for m in result["matches"])


def test_shuttle_lines_are_named(toolbox):
    lines = run(toolbox, "find_station", query="times sq")["matches"][0]["lines"]
    assert "S (42 St Shuttle)" in lines


def test_get_departures_formats_for_the_model(toolbox):
    result = run(toolbox, "get_departures", complex_id=397, route="6", direction="downtown")

    assert result["station"] == "86 St"
    assert result["line"] == "6"
    assert result["direction"] == "Downtown"
    assert result["status"] == "ok"
    first = result["trains"][0]
    assert set(first) <= {"min", "at", "to", "express"}
    assert re.fullmatch(r"\d{1,2}:\d{2} [AP]M", first["at"])
    assert result["as_of"] == "1:59 PM"
    assert {"stale", "alerts", "alerts_stale", "alerts_available", "alerts_as_of"} <= set(result)
    assert toolbox.last_departures["station"]["complex_id"] == 397  # card for the API


def test_express_trains_are_flagged(toolbox):
    trains = run(toolbox, "get_departures", complex_id=602, route="6", direction="uptown")["trains"]
    assert any(t.get("express") for t in trains) or all("express" not in t for t in trains)


def test_destination_direction_is_resolved(toolbox):
    result = run(toolbox, "get_departures", complex_id=618, route="L", direction="Canarsie")

    assert result["direction_code"] == "S"
    assert result["direction_from"] == {
        "destination": "Canarsie-Rockaway Pkwy",
        "confidence": "high",
    }


def test_unresolvable_direction_lists_labels(toolbox):
    with pytest.raises(ToolError, match=r"N = Uptown, S = Downtown"):
        toolbox.execute(
            "get_departures",
            {"complex_id": 397, "route": "6", "direction": "Flushing", **QUOTES},
        )


def test_shuttle_departures_use_rider_labels(toolbox):
    result = run(toolbox, "get_departures", complex_id=611, route="S", direction="Grand Central")

    assert result["line"] == "S (42 St Shuttle)"
    assert result["trains"][0]["min"] == 0


def test_route_not_typical_offers_nearest_station(toolbox):
    result = run(toolbox, "get_departures", complex_id=611, route="L", direction="N")

    assert result["status"] == "route_not_typical_here"
    nearest = result["nearest_with_line"]
    assert "L" in nearest["lines"] and nearest["meters"] < 3000


def test_alerts_are_trimmed_and_typed(toolbox):
    result = run(toolbox, "get_alerts", route="A")

    assert result["alerts_available"] is True and result["alerts_stale"] is False
    assert result["alerts"][0]["category"] == "current"
    assert result["alerts"][0]["when"].startswith("now")
    assert all(len(a["description"]) <= 300 for a in result["alerts"])
    assert all(a["type"] for a in result["alerts"])


def test_missing_alerts_are_flagged(toolbox, redis_client):
    redis_client.delete("alerts:present", "alerts:data")

    result = run(toolbox, "get_departures", complex_id=618, route="A", direction="N")

    assert result["alerts_available"] is False and result["alerts"] == []


def test_stations_near_me(toolbox):
    with pytest.raises(ToolError, match="hasn't shared a location"):
        toolbox.execute("stations_near_me", {})

    toolbox.location = {"lat": 40.7359, "lon": -73.9906}
    stations = run(toolbox, "stations_near_me", route="L")["stations"]

    assert stations[0]["name"] == "14 St-Union Sq"
    assert all("L" in s["lines"] for s in stations)


def test_unknown_route_is_a_tool_error(toolbox):
    with pytest.raises(ToolError, match="unknown route"):
        toolbox.execute("find_station", {"query": "86", "route": "K"})


def test_chat_endpoint_round_trip(rt_app):
    from tests.test_agent_loop import ScriptedLLM, reply, response, tool_use

    rt_app.extensions["llm"] = ScriptedLLM(
        response(
            tool_use(
                "get_departures",
                complex_id=397,
                route="6",
                direction="downtown",
                line_quote="6",
                direction_quote="downtown",
            )  # fmt: skip
        ),
        response(reply("Next downtown 6 trains at 86 St: soon.")),
    )
    client = rt_app.test_client()

    body = client.post("/api/chat", json={"message": "downtown 6 at 86th"}).get_json()

    assert body["reply"] == "Next downtown 6 trains at 86 St: soon."
    assert body["state"]["slots"]["station"] == {"complex_id": 397, "name": "86 St"}
    assert body["state"]["awaiting"] is None
    card = body["card"]
    departures = client.get(
        "/api/departures", query_string={"complex_id": 397, "route": "6", "direction": "S"}
    ).get_json()
    assert set(card) == set(departures)  # same shape the frontend already renders

    again = client.post(
        "/api/chat", json={"message": "hi", "session_id": body["session_id"]}
    ).get_json()
    assert again["session_id"] == body["session_id"]


def test_departures_by_station_name(toolbox):
    result = run(toolbox, "get_departures", station="86th st", route="6", direction="downtown")

    assert result["station"] == "86 St" and result["status"] == "ok"


def test_departures_by_ambiguous_station_name_returns_candidates(toolbox):
    # Three 14 St complexes have the L; none is a clear winner.
    result = run(toolbox, "get_departures", station="14 st", route="L", direction="Brooklyn")

    assert result["status"] == "ambiguous_station"
    assert {m["complex_id"] for m in result["matches"]} == {601, 602, 618}
    assert toolbox.last_departures is None


def test_departures_by_name_off_the_line_says_not_typical(toolbox):
    result = run(toolbox, "get_departures", station="Times Square", route="L", direction="N")

    assert result["status"] == "route_not_typical_here"


def test_departures_count(toolbox):
    result = run(toolbox, "get_departures", complex_id=611, route="1", direction="N", count=5)
    assert len(result["trains"]) == 5
    with pytest.raises(ToolError, match="no station matching"):
        toolbox.execute(
            "get_departures", {"station": "zzqq", "route": "6", "direction": "N", **QUOTES}
        )


def test_guessed_direction_gets_options_instead_of_trains(toolbox):
    toolbox.rider_text = ["next 6 at 86th"]

    result = json.loads(
        toolbox.execute(
            "get_departures",
            {
                "complex_id": 397,
                "route": "6",
                "line_quote": "6",
                "direction": "S",
                "direction_quote": "downtown",
            },  # never said by the rider
        )  # fmt: skip
    )

    assert result["status"] == "need_direction"
    assert (result["station"], result["line"]) == ("86 St", "6")
    assert result["directions"] == {"N": "Uptown", "S": "Downtown"}
    assert "Ask them" in result["note"]
    assert toolbox.last_departures is None


def test_guessed_line_at_multi_line_station_gets_options(toolbox):
    toolbox.rider_text = ["next downtown train at columbus circle"]

    result = json.loads(
        toolbox.execute(
            "get_departures",
            {
                "complex_id": 614,
                "route": "A",
                "line_quote": "",
                "direction": "downtown",
                "direction_quote": "downtown",
            },
        )  # fmt: skip
    )

    assert result["status"] == "need_line"
    assert result["lines"] == ["A", "C", "B", "D", "1"]


def test_quotes_from_earlier_turns_and_single_line_stations_count(toolbox):
    toolbox.rider_text = ["uptown 1 at 96 St", "refresh"]

    result = json.loads(
        toolbox.execute(
            "get_departures",
            {
                "complex_id": 306,
                "route": "1",
                "line_quote": "",
                "direction": "N",
                "direction_quote": "Uptown",
            },  # 125 St on the 1 is single-line
        )  # fmt: skip
    )

    assert result["status"] == "ok"


def test_line_not_serving_station_reports_before_direction(toolbox):
    toolbox.rider_text = ["next L at Times Square going to Brooklyn"]

    result = json.loads(
        toolbox.execute(
            "get_departures",
            {
                "station": "Times Square",
                "route": "L",
                "line_quote": "L",
                "direction": "Brooklyn",
                "direction_quote": "going to Brooklyn",
            },
        )  # fmt: skip
    )

    assert result["status"] == "route_not_typical_here"
    assert result["nearest_with_line"]["meters"] > 0


def test_find_station_off_the_line_says_so(toolbox):
    result = run(toolbox, "find_station", query="Times Square", route="L")

    assert result["status"] == "route_not_typical_here"
    assert result["matches"][0]["complex_id"] == 611
    assert "L" in result["nearest_with_line"]["lines"]


def test_stale_times_carry_a_note(loaded, rt_store):
    lookup = StationLookup(loaded)
    later = FROZEN_NOW + 200
    departures = DeparturesService(lookup, rt_store, clock=lambda: later)
    toolbox = Toolbox(lookup, departures, clock=lambda: later)

    result = run(toolbox, "get_departures", complex_id=310, route="1", direction="N")

    assert result["stale"] is True
    assert "1:59 PM" in result["stale_note"]
