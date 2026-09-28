import pytest


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")  # no profile on disk either


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (None, "expected a JSON object"),
        ({}, "message is required"),
        ({"message": 5}, "message is required"),
        ({"message": "hi", "session_id": 7}, "session_id must be a string"),
    ],
)
def test_bad_requests(client, body, error):
    resp = client.post("/api/chat", json=body)
    assert resp.status_code == 400
    assert error in resp.get_json()["error"]


def test_bad_location(client, app):
    app.extensions["llm"] = object()  # never reached
    resp = client.post("/api/chat", json={"message": "hi", "location": {"lat": 95, "lon": 0}})
    assert resp.status_code == 400


def test_missing_credentials_is_503(client):
    resp = client.post("/api/chat", json={"message": "next 6 at 86"})
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.get_json()["error"]
