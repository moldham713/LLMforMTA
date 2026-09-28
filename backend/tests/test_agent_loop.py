"""The agent loop against a scripted fake LLM: no API calls, no database."""

import json
from types import SimpleNamespace

import pytest

from app.agent import core
from app.agent.core import LIMIT_REPLY, TIMEOUT_REPLY, Agent
from app.agent.session import MAX_TURNS, SessionStore
from app.agent.tools import ToolError

NOW = 1790618392
DEPARTURES_626_S = {
    "station": {"complex_id": 626, "name": "86 St"},
    "route": {"route_id": "6", "label": "6", "route_ids": ["6", "6X"]},
    "direction": {"code": "S", "label": "Downtown & Brooklyn"},
    "departures": [],
    "status": "ok",
}


# --- fakes ----------------------------------------------------------------------------


def tool_use(name, id_="t1", **args):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


def text(value):
    return SimpleNamespace(type="text", text=value)


def reply(message, intent="departures", awaiting=None, id_="r1", **slots):
    args = {"message": message, "intent": intent, "awaiting": awaiting}
    if slots:
        args["slots"] = slots
    return tool_use("reply", id_=id_, **args)


def response(*blocks):
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)  # fmt: skip
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


class ScriptedLLM:
    """Returns scripted responses in order; each step may be a response or a callable
    taking the request params (to assert on them) and returning one."""

    def __init__(self, *steps, prompt_tokens=1000, on_call=None):
        self.steps = list(steps)
        self.calls: list[dict] = []
        self.prompt_tokens = prompt_tokens
        self.on_call = on_call

    def create(self, *, timeout, **params):
        self.calls.append({"timeout": timeout, **params})
        if self.on_call:
            self.on_call()
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        return step(params) if callable(step) else step

    def count_tokens(self, **params):
        return self.prompt_tokens


class FakeToolbox:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []
        self.last_departures = None
        self.found = {}

    def execute(self, name, args):
        self.calls.append((name, args))
        result = self.results.get(name, {"ok": True})
        if isinstance(result, Exception):
            raise result
        if name == "get_departures":
            self.last_departures = {**DEPARTURES_626_S, "direction": {
                "code": "N" if "up" in args["direction"].lower() else "S",
                "label": "Uptown & The Bronx" if "up" in args["direction"].lower()
                else "Downtown & Brooklyn",
            }}  # fmt: skip
            self.found = {
                "station": self.last_departures["station"],
                "route": "6",
                "direction": self.last_departures["direction"],
            }
        return json.dumps(result)


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def fresh_cache_decision():
    core._cacheable.clear()
    yield
    core._cacheable.clear()


def make_agent(llm, toolbox=None, clock=None, **kwargs):
    toolbox = toolbox or FakeToolbox()
    lookup = SimpleNamespace(
        complex_info=lambda cid: (
            SimpleNamespace(name={626: "86 St", 611: "Times Sq"}.get(cid))
            if cid in (626, 611)
            else None
        )
    )
    agent = Agent(
        llm, lookup, None, SessionStore(FakeRedis()), model="claude-haiku-4-5-20251001",
        clock=lambda: NOW, monotonic=clock or Clock(), toolbox_factory=lambda loc, text: toolbox,
        **kwargs,
    )  # fmt: skip
    return agent, toolbox


# --- tests ------------------------------------------------------------------------------


def test_tools_dispatch_and_results_flow_back():
    def check_results(params):
        last = params["messages"][-1]
        assert last["role"] == "user"
        assert [r["tool_use_id"] for r in last["content"]] == ["a", "b"]
        return response(tool_use("get_departures", "c", complex_id=626, route="6", direction="S"))

    llm = ScriptedLLM(
        response(text("Looking up."), tool_use("find_station", "a", query="86 st", route="6"),
                 tool_use("get_alerts", "b", route="6")),
        check_results,
        response(reply("Next downtown 6 trains at 86 St: 2 min.")),
    )  # fmt: skip
    agent, toolbox = make_agent(llm)

    result = agent.chat("downtown 6 at 86th")

    assert [c[0] for c in toolbox.calls] == ["find_station", "get_alerts", "get_departures"]
    assert toolbox.calls[0][1] == {"query": "86 st", "route": "6"}
    assert result.reply == "Next downtown 6 trains at 86 St: 2 min."
    assert result.card == toolbox.last_departures
    assert result.state["slots"]["station"] == {"complex_id": 626, "name": "86 St"}
    assert result.state["slots"]["direction"] == {"code": "S", "label": "Downtown & Brooklyn"}
    trace = result.trace
    assert [c["name"] for c in trace["tool_calls"]] == [
        "find_station",
        "get_alerts",
        "get_departures",
    ]
    assert all(c["ok"] for c in trace["tool_calls"])
    assert len(trace["llm_calls"]) == 3
    assert (trace["input_tokens"], trace["output_tokens"]) == (300, 60)
    assert trace["intent"] == "departures" and trace["clarifying"] is False
    assert trace["slots_before"]["station"] is None
    # Every request forces a tool call and carries the stable prompt first.
    assert all(c["tool_choice"] == {"type": "any"} for c in llm.calls)
    assert llm.calls[0]["system"][0]["text"] == core.SYSTEM_PROMPT


def test_tool_errors_are_returned_to_the_model():
    def check(params):
        result = params["messages"][-1]["content"][0]
        assert result["is_error"] is True
        assert "unknown route" in result["content"]
        return response(reply("Which line?", awaiting="route"))

    llm = ScriptedLLM(response(tool_use("find_station", query="x", route="K")), check)
    agent, _ = make_agent(llm, FakeToolbox({"find_station": ToolError("unknown route 'K'")}))

    result = agent.chat("K train at x")

    assert result.trace["tool_calls"][0]["ok"] is False
    assert result.state["awaiting"] == "route"
    assert result.trace["clarifying"] is True


def test_iteration_cap():
    llm = ScriptedLLM(response(tool_use("find_station", query="86")))
    agent, toolbox = make_agent(llm, max_iterations=6)

    result = agent.chat("86")

    assert len(llm.calls) == 6
    assert len(toolbox.calls) == 6
    assert result.reply == LIMIT_REPLY
    assert result.trace["error"] == "max_iterations"


def test_overall_timeout():
    clock = Clock()
    llm = ScriptedLLM(
        response(tool_use("find_station", query="86")),
        on_call=lambda: setattr(clock, "t", clock.t + 8),  # each model call takes 8s
    )
    agent, _ = make_agent(llm, clock=clock, timeout_seconds=20)

    result = agent.chat("86")

    assert result.reply == TIMEOUT_REPLY
    assert result.trace["error"] == "timeout"
    assert len(llm.calls) == 3  # at 0s, 8s and 16s; at 24s the budget is gone
    assert [round(c["timeout"]) for c in llm.calls] == [20, 12, 4]


def test_llm_timeout_exception_is_graceful():
    def boom(params):
        raise TimeoutError("read timed out")

    agent, _ = make_agent(ScriptedLLM(boom))

    result = agent.chat("next 1 at 96")

    assert result.reply == TIMEOUT_REPLY


def test_slots_carry_over_to_follow_up():
    first = ScriptedLLM(
        response(tool_use("get_departures", complex_id=626, route="6", direction="downtown")),
        response(reply("Next downtown 6 trains at 86 St: 2 min.")),
    )
    agent, toolbox = make_agent(first)
    session_id = agent.chat("downtown 6 at 86 st").session_id

    def follow_up(params):
        context = params["system"][1]["text"]
        assert "station=86 St (complex_id 626)" in context
        assert "line=6" in context
        assert "direction=S (Downtown & Brooklyn)" in context
        # The previous turn is in the history, ending with the reply the rider saw.
        assert params["messages"][0] == {"role": "user", "content": "downtown 6 at 86 st"}
        assert params["messages"][-2]["content"] == "Next downtown 6 trains at 86 St: 2 min."
        return response(tool_use("get_departures", complex_id=626, route="6", direction="uptown"))

    agent.llm = ScriptedLLM(follow_up, response(reply("Next uptown 6 trains at 86 St: 4 min.")))

    result = agent.chat("what about uptown?", session_id=session_id)

    assert toolbox.calls[-1] == (
        "get_departures",
        {"complex_id": 626, "route": "6", "direction": "uptown"},
    )
    assert result.state["slots"]["station"]["complex_id"] == 626
    assert result.state["slots"]["route"] == "6"
    assert result.state["slots"]["direction"]["code"] == "N"


def test_slots_come_from_tools_not_model_claims():
    # A question with no tool calls leaves slots alone, whatever the reply says.
    llm = ScriptedLLM(response(reply("Which direction?", awaiting="direction")))
    agent, toolbox = make_agent(llm)
    toolbox.found = {"station": {"complex_id": 397, "name": "86 St"}, "route": "6"}

    result = agent.chat("6 at 86")

    assert result.state["slots"]["station"] == {"complex_id": 397, "name": "86 St"}
    assert result.state["slots"]["route"] == "6"
    assert result.state["slots"]["direction"] is None
    assert result.state["awaiting"] == "direction"


def test_out_of_scope_keeps_slots_and_records_intent():
    llm = ScriptedLLM(response(reply("I handle subway departures.", intent="fares")))
    agent, toolbox = make_agent(llm)

    result = agent.chat("how much is a fare?")

    assert result.state["intent"] == "fares"
    assert toolbox.calls == []
    assert result.card is None


def test_input_cap_skips_the_model():
    llm = ScriptedLLM(response(reply("unused")))
    agent, _ = make_agent(llm, max_input_chars=500)

    result = agent.chat("x" * 501)

    assert "500 characters" in result.reply
    assert llm.calls == []
    assert result.trace["error"] == "input_too_long"


def test_plain_text_answer_is_still_delivered():
    agent, _ = make_agent(ScriptedLLM(response(text("Which station?"))))
    assert agent.chat("hi").reply == "Which station?"


def test_history_keeps_last_ten_turns():
    agent, _ = make_agent(ScriptedLLM(response(reply("ok"))))
    session_id = None
    for i in range(MAX_TURNS + 3):
        session_id = agent.chat(f"message {i}", session_id=session_id).session_id

    session = agent.sessions.load_or_create(session_id)
    assert len(session.turns) == MAX_TURNS
    assert session.turns[0][0]["content"] == "message 3"


def test_session_persists_location():
    llm = ScriptedLLM(response(reply("ok")))
    agent, _ = make_agent(llm)
    sid = agent.chat("near me", location={"lat": 40.7, "lon": -73.9}).session_id

    agent.chat("again", session_id=sid)

    assert "Rider location shared: yes" in llm.calls[-1]["system"][1]["text"]


@pytest.mark.parametrize(("tokens", "cached"), [(5000, True), (2000, False)])
def test_prompt_cached_only_above_model_minimum(tokens, cached):
    llm = ScriptedLLM(response(reply("ok")), prompt_tokens=tokens)
    agent, _ = make_agent(llm)

    trace = agent.chat("hi").trace

    assert ("cache_control" in llm.calls[0]["system"][0]) is cached
    assert "cache_control" not in llm.calls[0]["system"][1]
    assert trace["prompt_cached"] is cached and trace["prompt_tokens"] == tokens
