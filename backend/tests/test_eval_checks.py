import json

import pytest

from evals.checks import alerts_unchecked, claims_no_delays, fabricated_numbers, slot_mismatches

RESULT = json.dumps(
    {
        "trains": [
            {"min": 2, "at": "2:01 PM"},
            {"min": 7, "at": "2:06 PM"},
            {"min": 12, "at": "2:11 PM"},
        ],
        "alerts": [{"header": "[6] runs every 8 minutes", "when": "now until 5 AM Sun"}],
    }  # fmt: skip
)


@pytest.mark.parametrize(
    "reply",
    [
        "Next downtown 6 trains at 86 St: 2, 7, and 12 min (the 7-min train is an express).",
        "The next one is at 2:06 PM, then 2:11 PM.",
        "Trains run every 8 minutes until 5 AM Sun.",
        "Next train at 86 St is arriving; after that 12 minutes.",
    ],
)
def test_numbers_from_tool_results_pass(reply):
    assert fabricated_numbers(reply, [RESULT]) == []


@pytest.mark.parametrize(
    ("reply", "bad"),
    [
        ("Next trains: 3 and 7 min.", ["3 min"]),
        ("Next train in about 5 minutes.", ["5 min"]),
        ("It leaves at 2:05 PM.", ["2:05"]),
        ("Service resumes at 6 AM.", ["6 AM"]),
    ],
)
def test_invented_numbers_are_caught(reply, bad):
    assert fabricated_numbers(reply, [RESULT]) == bad


def test_station_numbers_are_not_times():
    assert fabricated_numbers("Next uptown 1 at 96 St and 125 St.", []) == []


def test_no_tool_results_means_any_minute_count_is_fabricated():
    assert fabricated_numbers("The 6 comes in 4 min.", []) == ["4 min"]


@pytest.mark.parametrize(
    "reply",
    [
        "No delays on the 6.",
        "There are no current alerts for the L.",
        "The 7 is running normally.",
        "Good service on the A.",
        "no service changes reported",
    ],
)
def test_no_delay_claims(reply):
    assert claims_no_delays(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "No trains are showing right now, and I couldn't check for delays.",
        "I couldn't check for delays or service changes.",
        "Next trains: 2 and 7 min.",
        "I couldn't check for delays, so I can't confirm there are no issues on the line.",
        "I wasn't able to check whether there are no delays.",
    ],
)
def test_not_no_delay_claims(reply):
    assert not claims_no_delays(reply)


def test_alerts_unchecked():
    assert alerts_unchecked([json.dumps({"alerts_stale": True})])
    assert alerts_unchecked(["not json", json.dumps({"alerts_available": False})])
    assert not alerts_unchecked([json.dumps({"alerts_stale": False, "alerts_available": True})])


def test_slot_mismatches():
    slots = {
        "station": {"complex_id": 397, "name": "86 St"},
        "route": "6",
        "direction": {"code": "S", "label": "Downtown"},
    }
    assert slot_mismatches({"complex_id": 397, "route": "6", "direction": "S"}, slots) == []
    assert slot_mismatches({"direction": "N"}, slots) == ["direction: expected 'N', got 'S'"]
