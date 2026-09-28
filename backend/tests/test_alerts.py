import json
from pathlib import Path

import pytest

from app.realtime.parse import FeedParseError, categorize, parse_alerts
from app.routes_display import sort_route_ids

ALERTS = Path(__file__).parent / "fixtures" / "realtime" / "alerts.json"
SNAPSHOT_NOW = 1790618392


@pytest.mark.parametrize(
    ("alert_type", "category"),
    [
        ("Delays", "delay"),
        ("Some Delays", "delay"),
        ("Severe Delays", "delay"),
        ("Expect Delays", "delay"),
        ("Part Suspended", "delay"),
        ("Suspended", "delay"),
        ("Trains Rerouted", "delay"),
        ("Stops Skipped", "delay"),
        ("Express to Local", "delay"),
        ("Planned - Part Suspended", "planned_work"),
        ("Planned - Stops Skipped", "planned_work"),
        ("Planned - Express to Local", "planned_work"),
        ("Planned - Reroute", "planned_work"),
        ("Reduced Service", "planned_work"),
        ("Special Schedule", "planned_work"),
        ("Boarding Change", "other"),
        ("Station Notice", "other"),
        ("Extra Service", "other"),
        (None, "other"),
        ("", "other"),
    ],
)
def test_categorize(alert_type, category):
    assert categorize(alert_type) == category


def test_snapshot_normalizes():
    header_ts, alerts = parse_alerts(ALERTS.read_bytes(), SNAPSHOT_NOW)

    assert header_ts == 1790618008
    assert len(alerts) > 100
    assert {a["category"] for a in alerts} == {"delay", "planned_work", "other"}
    a = alerts[0]
    assert set(a) == {
        "id", "route_ids", "stop_ids", "alert_type", "header", "description",
        "active_periods", "category", "updated_at",
    }  # fmt: skip
    # Plain-text translations, never the en-html ones.
    assert not any("<p>" in x["header"] or "<p>" in x["description"] for x in alerts)
    # Mercury stop ids are 3-character parent stations, never N/S platforms.
    assert all(len(s) == 3 for x in alerts for s in x["stop_ids"])


def test_route_ids_use_display_order():
    _, alerts = parse_alerts(ALERTS.read_bytes(), SNAPSHOT_NOW)
    multi = next(a for a in alerts if len(a["route_ids"]) > 1)
    assert multi["route_ids"] == sort_route_ids(multi["route_ids"])


def doc(*alerts, ts=1_000):
    return json.dumps({"header": {"timestamp": ts}, "entity": list(alerts)}).encode()


def entity(id_, periods, alert_type="Delays", routes=("A",)):
    return {
        "id": id_,
        "alert": {
            "active_period": periods,
            "informed_entity": [{"agency_id": "MTASBWY", "route_id": r} for r in routes],
            "header_text": {
                "translation": [
                    {"text": "<p>html</p>", "language": "en-html"},
                    {"text": " plain ", "language": "en"},
                ]
            },  # fmt: skip
            "transit_realtime.mercury_alert": {"alert_type": alert_type, "updated_at": 5},
        },
    }


def test_finished_alerts_are_dropped():
    data = doc(
        entity("over", [{"start": 100, "end": 200}]),
        entity("open", [{"start": 100}]),
        entity("later", [{"start": 100, "end": 200}, {"start": 900, "end": 1100}]),
    )

    _, alerts = parse_alerts(data, now=500)

    assert [a["id"] for a in alerts] == ["open", "later"]
    assert alerts[0]["header"] == "plain"
    assert alerts[0]["description"] == ""
    assert alerts[0]["active_periods"] == [{"start": 100, "end": None}]


@pytest.mark.parametrize("data", [b"not json", b"{}", b'{"header": {}}'])
def test_bad_document_is_a_parse_error(data):
    with pytest.raises(FeedParseError):
        parse_alerts(data, 0)
