"""Run eval cases against the real model, fixture data and a frozen clock.

Static data: the checked-in fixture GTFS and stations, loaded into the test database.
Realtime: the recorded snapshots, written to one Redis database per scenario ("variant")
so cases can run in parallel without mutating shared state.
"""

import copy
import json
import re
import statistics
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import redis
import yaml
from alembic import command
from sqlalchemy import Engine, text

from app.agent.factory import build_agent
from app.agent.llm import LLM
from app.config import Settings
from app.extensions import make_engine, make_store
from app.gtfs.loader import load_gtfs
from app.realtime.parse import parse_alerts
from app.stations.loader import load_stations
from evals.checks import alerts_unchecked, claims_no_delays, fabricated_numbers, slot_mismatches
from tests.integration.conftest import (
    FROZEN_NOW,
    REALTIME_DIR,
    STATIONS_CSV,
    alembic_config,
    load_realtime_fixtures,
    make_gtfs_zip,
    test_database_url,
    test_redis_url,
)

CASES_DIR = Path(__file__).parent / "cases"
SESSION_DB = 9
VARIANT_DBS = {"normal": 10, "no_ace": 11, "alerts_missing": 12, "alerts_stale": 13}
HARD_TURN_CAP = 8


def redis_db(n: int) -> redis.Redis:
    # from_url ignores a db= argument when the URL names one, so rewrite the URL instead.
    return redis.Redis.from_url(re.sub(r"/\d*$", f"/{n}", test_redis_url()))


# $ per million tokens (input, output); cache writes cost 1.25x input, reads 0.1x.
PRICES = [
    ("claude-haiku-4-5", 1.0, 5.0),
    ("claude-sonnet-5", 2.0, 10.0),
    ("claude-sonnet-4-6", 3.0, 15.0),
    ("claude-opus-5", 5.0, 25.0),
    ("claude-opus-4", 5.0, 25.0),
]


@dataclass
class Case:
    id: str
    category: str
    turns: list[str]
    expect: dict
    answers: dict = field(default_factory=dict)
    setup: dict = field(default_factory=dict)


def load_cases(category: str | None = None, only: list[str] | None = None) -> list[Case]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text(encoding="utf-8")):
            case = Case(**raw)
            if category and case.category != category:
                continue
            if only and case.id not in only:
                continue
            cases.append(case)
    return cases


# --- environment ------------------------------------------------------------------------


def prepare_database(tmp: Path) -> Engine:
    url = test_database_url()
    engine = make_engine(url)
    with engine.begin() as conn:
        for schema in ("gtfs", "gtfs_staging", "analytics"):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    command.upgrade(alembic_config(url), "head")
    load_gtfs(engine, str(make_gtfs_zip(tmp / "fixture.zip")))
    load_stations(engine, str(STATIONS_CSV))
    return engine


def prepare_variants(settings: Settings) -> dict[str, redis.Redis]:
    clients = {}
    for name, db in VARIANT_DBS.items():
        client = redis_db(db)
        client.flushdb()
        store = make_store(client, settings)
        load_realtime_fixtures(store)
        if name == "alerts_stale":
            # Alerts last polled 10 minutes before "now": still present, but stale.
            header, alerts = parse_alerts((REALTIME_DIR / "alerts.json").read_bytes(), FROZEN_NOW)
            store.write_alerts(header, alerts, FROZEN_NOW - 600)
        if name == "no_ace":
            store.write_arrivals("ace", FROZEN_NOW, [], FROZEN_NOW)
        if name == "alerts_missing":
            client.delete("alerts:present", "alerts:data", *client.keys("alerts:*"))
        clients[name] = client
    return clients


# --- running ------------------------------------------------------------------------------


def run_case(
    case: Case,
    settings: Settings,
    engine: Engine,
    variants: dict[str, redis.Redis],
    sessions: redis.Redis,
    llm: LLM,
) -> dict:
    variant = case.setup.get("variant", "normal")
    now = FROZEN_NOW + case.setup.get("clock_offset", 0)
    agent = build_agent(
        settings, engine, variants[variant], llm=llm, clock=lambda: now, session_client=sessions
    )
    location = case.setup.get("location")

    queue = deque(case.turns)
    answered: set[str] = set()
    turns, session_id, asked, result = [], None, False, None
    while queue and len(turns) < HARD_TURN_CAP:
        message = queue.popleft()
        result = agent.chat(message, session_id=session_id, location=location)
        session_id, location = result.session_id, None
        t = result.trace
        tool_results = [c["result"] for c in t["tool_calls"]]
        turns.append(
            {
                "user": message,
                "reply": result.reply,
                "awaiting": result.state["awaiting"],
                "intent": result.state["intent"],
                "latency_ms": t["latency_ms"],
                "input_tokens": t["input_tokens"],
                "output_tokens": t["output_tokens"],
                "cache_read_input_tokens": t["cache_read_input_tokens"],
                "cache_creation_input_tokens": t["cache_creation_input_tokens"],
                "llm_calls": len(t["llm_calls"]),
                "tool_calls": [
                    {k: c[k] for k in ("name", "args", "ok", "error", "latency_ms")}
                    for c in t["tool_calls"]
                ],
                "status": _last_status(t),
                "fabricated": fabricated_numbers(result.reply, tool_results),
                "no_delays_violation": alerts_unchecked(tool_results)
                and claims_no_delays(result.reply),
                "error": t["error"],
            }
        )
        slot = result.state["awaiting"]
        if slot:
            asked = True
            if slot in case.answers and slot not in answered:
                answered.add(slot)
                queue.appendleft(case.answers[slot])
            elif not queue:
                break

    final_state = copy.deepcopy(result.state) if result else {}
    return {
        "id": case.id,
        "category": case.category,
        "setup": case.setup,
        "turns": turns,
        "final_state": final_state,
        **grade(case, turns, final_state, asked),
    }


def _last_status(trace: dict) -> str | None:
    for call in reversed(trace["tool_calls"]):
        if call["name"] == "get_departures" and call["ok"]:
            return json.loads(call["result"]).get("status")
    return None


def grade(case: Case, turns: list[dict], state: dict, asked: bool) -> dict:
    expect = case.expect
    failures = []
    if "slots" in expect:
        failures += [f"slots {m}" for m in slot_mismatches(expect["slots"], state.get("slots", {}))]
    if "intent" in expect:
        allowed = expect["intent"] if isinstance(expect["intent"], list) else [expect["intent"]]
        if state.get("intent") not in allowed:
            failures.append(f"intent: expected {allowed}, got {state.get('intent')!r}")
    if "clarify" in expect and asked != expect["clarify"]:
        failures.append(f"clarify: expected {expect['clarify']}, asked={asked}")
    if len(turns) > expect.get("max_turns", HARD_TURN_CAP):
        failures.append(f"turns: {len(turns)} > max {expect['max_turns']}")
    if "status" in expect and turns and turns[-1]["status"] != expect["status"]:
        failures.append(f"status: expected {expect['status']}, got {turns[-1]['status']}")
    if (
        "reply_matches" in expect
        and turns
        and not re.search(expect["reply_matches"], turns[-1]["reply"], re.I)
    ):
        failures.append(f"reply doesn't match {expect['reply_matches']!r}")
    if expect.get("followups_call_departures"):
        for t in turns[1:]:
            if not any(c["name"] == "get_departures" and c["ok"] for c in t["tool_calls"]):
                failures.append(f"follow-up {t['user']!r} didn't call get_departures")
            if t["awaiting"]:
                failures.append(f"follow-up {t['user']!r} asked a question")
    fabrication = [f for t in turns for f in t["fabricated"]]
    no_delays = [t["reply"] for t in turns if t["no_delays_violation"]]
    errors = [t["error"] for t in turns if t["error"]]
    if fabrication:
        failures.append(f"fabricated: {fabrication}")
    if no_delays:
        failures.append("claimed no delays without a fresh alerts check")
    if errors:
        failures.append(f"errors: {errors}")
    return {
        "passed": not failures,
        "failures": failures,
        "fabrication_failures": len(fabrication),
        "no_delays_failures": len(no_delays),
    }


# --- summary --------------------------------------------------------------------------------


def price(model: str) -> tuple[float, float]:
    return next(((i, o) for prefix, i, o in PRICES if model.startswith(prefix)), (1.0, 5.0))


def summarize(results: list[dict], model: str) -> dict:
    by_category: dict[str, list[bool]] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["passed"])
    latencies = sorted(t["latency_ms"] for r in results for t in r["turns"])
    p_in, p_out = price(model)

    def cost(r):
        total = 0.0
        for t in r["turns"]:
            total += t["input_tokens"] * p_in + t["output_tokens"] * p_out
            total += t["cache_creation_input_tokens"] * p_in * 1.25
            total += t["cache_read_input_tokens"] * p_in * 0.1
        return total / 1e6

    token_keys = (
        "input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
    )

    def tokens(r):
        return sum(t[k] for t in r["turns"] for k in token_keys)

    n = len(results) or 1
    return {
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "overall_accuracy": round(sum(r["passed"] for r in results) / n, 3),
        "per_category": {
            c: {"passed": sum(v), "total": len(v), "accuracy": round(sum(v) / len(v), 3)}
            for c, v in sorted(by_category.items())
        },
        "fabrication_failures": sum(r["fabrication_failures"] for r in results),
        "no_delays_failures": sum(r["no_delays_failures"] for r in results),
        "turns": len(latencies),
        "latency_ms_p50": _pct(latencies, 50),
        "latency_ms_p95": _pct(latencies, 95),
        "avg_tokens_per_conversation": round(statistics.mean(tokens(r) for r in results))
        if results
        else 0,
        "avg_cost_usd_per_conversation": round(statistics.mean(cost(r) for r in results), 5)
        if results
        else 0,
        "avg_llm_calls_per_turn": round(
            statistics.mean(t["llm_calls"] for r in results for t in r["turns"]), 2
        )
        if latencies
        else 0,
    }


def _pct(sorted_values: list[int], pct: int) -> int | None:
    if not sorted_values:
        return None
    k = max(0, min(len(sorted_values) - 1, round(pct / 100 * len(sorted_values) + 0.5) - 1))
    return sorted_values[k]
