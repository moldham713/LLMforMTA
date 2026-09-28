"""POST /api/chat: the conversational agent over HTTP."""

from flask import Blueprint, current_app, jsonify, request

from app.agent.core import Agent
from app.agent.factory import build_agent
from app.agent.llm import AnthropicLLM

bp = Blueprint("chat", __name__, url_prefix="/api")


class ChatUnavailable(RuntimeError):
    pass


def chat_agent() -> Agent:
    ext = current_app.extensions
    if "llm" not in ext:
        llm = AnthropicLLM()
        if not llm.has_credentials:
            raise ChatUnavailable("chat is unavailable: ANTHROPIC_API_KEY is not set")
        ext["llm"] = llm
    return build_agent(
        ext["settings"], ext["db_engine"], ext["redis"], llm=ext["llm"], clock=ext["clock"]
    )


def _location(value) -> dict | None:
    if value is None:
        return None
    try:
        lat, lon = float(value["lat"]), float(value["lon"])
    except (TypeError, KeyError, ValueError):
        raise ValueError("location must be {lat, lon}") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("location out of range")
    return {"lat": lat, "lon": lon}


@bp.post("/chat")
def chat():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="expected a JSON object"), 400
    message, session_id = body.get("message"), body.get("session_id")
    if not isinstance(message, str):
        return jsonify(error="message is required"), 400
    if session_id is not None and not isinstance(session_id, str):
        return jsonify(error="session_id must be a string"), 400
    try:
        location = _location(body.get("location"))
        result = chat_agent().chat(message, session_id=session_id, location=location)
    except ChatUnavailable as exc:
        return jsonify(error=str(exc)), 503
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    out = {"session_id": result.session_id, "reply": result.reply, "state": result.state}
    if result.card:
        out["card"] = result.card  # same shape as /api/departures
    return jsonify(out)
