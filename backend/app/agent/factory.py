"""Build an Agent from settings; shared by the API, the CLI and the eval harness."""

import time
from collections.abc import Callable

import redis
from sqlalchemy import Engine

from app.agent.core import Agent
from app.agent.llm import LLM, AnthropicLLM
from app.agent.session import SessionStore
from app.config import Settings
from app.extensions import make_store
from app.realtime.departures import DeparturesService
from app.stations.lookup import StationLookup


def build_agent(
    settings: Settings,
    engine: Engine,
    client: redis.Redis,
    *,
    llm: LLM | None = None,
    clock: Callable[[], float] = time.time,
    session_client: redis.Redis | None = None,
) -> Agent:
    lookup = StationLookup(engine, settings.search_relative_cutoff)
    departures = DeparturesService(
        lookup,
        make_store(client, settings),
        clock=clock,
        stale_after=settings.rt_stale_seconds,
        alerts_stale_after=settings.rt_alerts_stale_seconds,
    )
    return Agent(
        llm or AnthropicLLM(),
        lookup,
        departures,
        SessionStore(session_client or client, settings.chat_session_ttl_seconds),
        model=settings.llm_model,
        clock=clock,
        timeout_seconds=settings.chat_timeout_seconds,
        max_iterations=settings.chat_max_tool_iterations,
        max_input_chars=settings.chat_max_input_chars,
    )
