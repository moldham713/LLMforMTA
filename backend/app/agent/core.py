"""The conversational agent: one rider message in, one reply (plus card and trace) out.

Plain Python (no Flask) so the SMS channel can reuse it. The loop is written out by hand
rather than using the SDK tool runner because each turn needs a hard iteration cap, an
overall time budget, and a per-call trace.
"""

import copy
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import anthropic

from app.agent.llm import LLM, cache_minimum_tokens
from app.agent.prompt import SYSTEM_PROMPT, turn_context
from app.agent.session import Session, SessionStore
from app.agent.tools import INTENTS, SLOTS, TOOLS, Toolbox, ToolError
from app.realtime.departures import DeparturesService
from app.routes_display import display, resolve_route, sort_route_ids
from app.stations.lookup import StationLookup

log = logging.getLogger(__name__)

TOO_LONG_REPLY = "That message is too long. Please keep it under {limit} characters."
EMPTY_REPLY = "Which subway station and train are you waiting for?"
TIMEOUT_REPLY = "Sorry, that took too long. Please try again in a moment."
LIMIT_REPLY = "Sorry, I couldn't finish looking that up. Please try asking again."
ERROR_REPLY = "Sorry, something went wrong on my end. Please try again."
MIN_CALL_SECONDS = 1.0

# Whether the stable prefix is big enough to cache, per model; measured once per process.
_cacheable: dict[str, tuple[bool, int | None]] = {}


@dataclass
class TurnResult:
    session_id: str
    reply: str
    state: dict
    card: dict | None
    trace: dict


def _block_param(block) -> dict | None:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return None


class Agent:
    def __init__(
        self,
        llm: LLM,
        lookup: StationLookup,
        departures: DeparturesService,
        sessions: SessionStore,
        *,
        model: str,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        timeout_seconds: float = 20.0,
        max_iterations: int = 6,
        max_input_chars: int = 500,
        max_tokens: int = 1024,
        toolbox_factory: Callable[[dict | None, list[str]], Toolbox] | None = None,
    ):
        self.llm = llm
        self.lookup = lookup
        self.departures = departures
        self.sessions = sessions
        self.model = model
        self.clock = clock
        self.monotonic = monotonic
        self.timeout = timeout_seconds
        self.max_iterations = max_iterations
        self.max_input_chars = max_input_chars
        self.max_tokens = max_tokens
        self.toolbox_factory = toolbox_factory or (
            lambda location, rider_text: Toolbox(
                lookup, departures, location=location, clock=clock, rider_text=rider_text
            )
        )

    # --- public -----------------------------------------------------------------------

    def chat(
        self, message: str, session_id: str | None = None, location: dict | None = None
    ) -> TurnResult:
        started = self.monotonic()
        session = self.sessions.load_or_create(session_id)
        if location:
            session.location = {"lat": float(location["lat"]), "lon": float(location["lon"])}
        trace = self._new_trace(session)

        if len(message) > self.max_input_chars:
            return self._finish(
                session, trace, started, TOO_LONG_REPLY.format(limit=self.max_input_chars),
                error="input_too_long", store=False,
            )  # fmt: skip
        if not message.strip():
            return self._finish(session, trace, started, EMPTY_REPLY, store=False)

        rider_text = [
            m["content"]
            for m in session.history()
            if m["role"] == "user" and isinstance(m["content"], str)
        ] + [message]
        toolbox = self.toolbox_factory(session.location, rider_text)
        turn = [{"role": "user", "content": message}]
        reply, error = self._loop(session, turn, toolbox, trace, started)
        if reply is None:
            text = {"timeout": TIMEOUT_REPLY, "max_iterations": LIMIT_REPLY}.get(error, ERROR_REPLY)
            return self._finish(session, trace, started, text, turn=turn, error=error)
        return self._finish(session, trace, started, reply["message"], turn=turn, reply=reply,
                            toolbox=toolbox)  # fmt: skip

    # --- loop -------------------------------------------------------------------------

    def _loop(self, session: Session, turn: list[dict], toolbox: Toolbox, trace: dict, started):
        """Run model/tool iterations. Returns (reply input, None) or (None, error)."""
        cache, prompt_tokens = self._cache_decision()
        trace["prompt_cached"], trace["prompt_tokens"] = cache, prompt_tokens
        for _ in range(self.max_iterations):
            remaining = self.timeout - (self.monotonic() - started)
            if remaining < MIN_CALL_SECONDS:
                return None, "timeout"
            params = self._params(session, session.history() + turn, cache)
            t0 = self.monotonic()
            try:
                response = self.llm.create(timeout=remaining, **params)
            except (anthropic.APITimeoutError, TimeoutError):
                return None, "timeout"
            except Exception as exc:
                log.exception("LLM call failed")
                trace["error_detail"] = f"{type(exc).__name__}: {exc}"
                return None, "llm_error"
            self._record_call(trace, response, t0)

            blocks = list(response.content)
            reply = next((b for b in blocks if b.type == "tool_use" and b.name == "reply"), None)
            if reply is not None:
                return dict(reply.input), None
            tool_uses = [b for b in blocks if b.type == "tool_use"]
            if not tool_uses:
                # tool_choice=any should prevent this; keep any text rather than failing.
                text = " ".join(b.text for b in blocks if b.type == "text").strip()
                if text:
                    return {"message": text, "intent": "departures", "awaiting": None}, None
                return None, "empty_response"

            results = [self._run_tool(toolbox, b, trace) for b in tool_uses]
            turn.append(
                {"role": "assistant", "content": [p for b in blocks if (p := _block_param(b))]}
            )
            turn.append({"role": "user", "content": results})
        return None, "max_iterations"

    def _run_tool(self, toolbox: Toolbox, block, trace: dict) -> dict:
        t0 = self.monotonic()
        entry = {"name": block.name, "args": block.input, "ok": True, "error": None}
        try:
            content = toolbox.execute(block.name, dict(block.input))
        except ToolError as exc:
            content, entry["ok"], entry["error"] = f"Error: {exc}", False, str(exc)
        except Exception as exc:
            log.exception("tool %s failed", block.name)
            content, entry["ok"], entry["error"] = (
                "Error: internal error",
                False,
                type(exc).__name__,
            )
        entry["latency_ms"] = round((self.monotonic() - t0) * 1000)
        entry["result"] = content
        trace["tool_calls"].append(entry)
        result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
        if not entry["ok"]:
            result["is_error"] = True
        return result

    def _params(self, session: Session, messages: list[dict], cache: bool) -> dict:
        stable = {"type": "text", "text": SYSTEM_PROMPT}
        if cache:
            stable["cache_control"] = {"type": "ephemeral"}
        context = turn_context(
            self.clock(), session.slots, bool(session.location), session.awaiting
        )
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [stable, {"type": "text", "text": context}],
            "tools": TOOLS,
            "tool_choice": {"type": "any"},
            "messages": messages,
        }

    def _cache_decision(self) -> tuple[bool, int | None]:
        """Cache the tools + system prompt only if they clear the model's minimum."""
        if self.model not in _cacheable:
            try:
                tokens = self.llm.count_tokens(
                    model=self.model,
                    system=[{"type": "text", "text": SYSTEM_PROMPT}],
                    tools=TOOLS,
                    messages=[{"role": "user", "content": "hi"}],
                )
                _cacheable[self.model] = (tokens >= cache_minimum_tokens(self.model), tokens)
            except Exception:
                log.warning("couldn't count prompt tokens; caching disabled", exc_info=True)
                return False, None
        return _cacheable[self.model]

    # --- finishing --------------------------------------------------------------------

    def _finish(
        self,
        session: Session,
        trace: dict,
        started: float,
        text: str,
        *,
        turn: list[dict] | None = None,
        reply: dict | None = None,
        toolbox: Toolbox | None = None,
        error: str | None = None,
        store: bool = True,
    ) -> TurnResult:
        intent = session.intent
        awaiting = None
        if reply:
            intent = reply.get("intent") if reply.get("intent") in INTENTS else "departures"
            awaiting = reply.get("awaiting") if reply.get("awaiting") in SLOTS else None
            if intent == "departures":
                session.slots = self._merge_slots(session.slots, toolbox)
        session.intent, session.awaiting = intent, awaiting
        if store:
            if turn is not None:
                session.add_turn([*turn, {"role": "assistant", "content": text}])
            self.sessions.save(session)

        trace.update(
            latency_ms=round((self.monotonic() - started) * 1000),
            slots_after=copy.deepcopy(session.slots),
            intent=intent,
            awaiting=awaiting,
            clarifying=awaiting is not None,
            error=error,
            reply=text,
        )
        log.debug("chat turn %s", json.dumps(trace, default=str))
        card = toolbox.last_departures if toolbox else None
        state = {"slots": session.slots, "intent": intent, "awaiting": awaiting}
        return TurnResult(session.id, text, state, card, trace)

    @staticmethod
    def _merge_slots(current: dict, toolbox: Toolbox | None) -> dict:
        """Slots come from what the tools resolved, not from the model's say-so."""
        slots = copy.deepcopy(current)
        found = getattr(toolbox, "found", None) or {}
        station = found.get("station")
        if station and (slots["station"] or {}).get("complex_id") != station["complex_id"]:
            slots["direction"] = None  # direction labels belong to the old station
        for key in ("station", "route", "direction"):
            if found.get(key):
                slots[key] = copy.deepcopy(found[key])
        return slots

    # --- trace ------------------------------------------------------------------------

    def _new_trace(self, session: Session) -> dict:
        return {
            "session_id": session.id,
            "model": self.model,
            "at": self.clock(),
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "llm_calls": [],
            "tool_calls": [],
            "slots_before": copy.deepcopy(session.slots),
        }

    def _record_call(self, trace: dict, response, t0: float) -> None:
        usage = response.usage
        call = {
            "latency_ms": round((self.monotonic() - t0) * 1000),
            "stop_reason": response.stop_reason,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        trace["llm_calls"].append(call)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            trace[key] += call[key]
