"""Parameter validation only; lookups against real data are in tests/integration."""

import pytest


@pytest.mark.parametrize(
    ("path", "params", "error"),
    [
        ("/api/stations/search", {}, "q is required"),
        ("/api/stations/search", {"q": "  "}, "q is required"),
        ("/api/stations/search", {"q": "x", "limit": "0"}, "limit must be between"),
        ("/api/stations/nearest", {"lon": "-73.99"}, "lat is required"),
        ("/api/stations/nearest", {"lat": "abc", "lon": "-73.99"}, "lat must be a number"),
        ("/api/stations/nearest", {"lat": "95", "lon": "-73.99"}, "lat must be between"),
        ("/api/stations/search", {"q": "x", "route": "K"}, "unknown route"),
        ("/api/departures", {"route": "A", "direction": "N"}, "complex_id is required"),
        ("/api/departures", {"complex_id": "618", "direction": "N"}, "route is required"),
        ("/api/departures", {"complex_id": "618", "route": "A"}, "direction is required"),
        (
            "/api/departures",
            {"complex_id": "x", "route": "A", "direction": "N"},
            "must be a number",
        ),
        ("/api/alerts", {"complex_id": "abc"}, "complex_id must be a number"),
    ],
)
def test_bad_params_return_400(client, path, params, error):
    resp = client.get(path, query_string=params)

    assert resp.status_code == 400
    assert error in resp.get_json()["error"]
