import os
from dataclasses import dataclass, field

from app.realtime.feeds import feed_urls

DEFAULT_GTFS_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip"
DEFAULT_STATIONS_URL = "https://data.ny.gov/api/views/39hk-dx4f/rows.csv?accessType=DOWNLOAD"
DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    log_level: str = "INFO"
    gtfs_url: str = DEFAULT_GTFS_URL
    stations_url: str = DEFAULT_STATIONS_URL
    gtfs_refresh_hours: float = 6.0
    search_relative_cutoff: float = 0.6
    rt_poll_seconds: float = 30.0
    rt_timeout_seconds: float = 10.0
    rt_stale_seconds: float = 90.0
    rt_ttl_seconds: int = 180
    rt_alerts_ttl_seconds: int = 900
    rt_alerts_stale_seconds: float = 180.0
    rt_feed_urls: dict[str, str] = field(default_factory=lambda: feed_urls({}))
    llm_model: str = DEFAULT_LLM_MODEL
    chat_session_ttl_seconds: int = 1800
    chat_timeout_seconds: float = 20.0
    chat_max_tool_iterations: int = 6
    chat_max_input_chars: int = 500

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            database_url=env["DATABASE_URL"],
            redis_url=env["REDIS_URL"],
            log_level=env.get("LOG_LEVEL", "INFO"),
            gtfs_url=env.get("GTFS_URL") or DEFAULT_GTFS_URL,
            stations_url=env.get("STATIONS_URL") or DEFAULT_STATIONS_URL,
            gtfs_refresh_hours=float(env.get("GTFS_REFRESH_HOURS", "6")),
            search_relative_cutoff=float(env.get("SEARCH_RELATIVE_CUTOFF", "0.6")),
            rt_poll_seconds=float(env.get("RT_POLL_SECONDS", "30")),
            rt_timeout_seconds=float(env.get("RT_TIMEOUT_SECONDS", "10")),
            rt_stale_seconds=float(env.get("RT_STALE_SECONDS", "90")),
            rt_ttl_seconds=int(env.get("RT_TTL_SECONDS", "180")),
            rt_alerts_ttl_seconds=int(env.get("RT_ALERTS_TTL_SECONDS", "900")),
            rt_alerts_stale_seconds=float(env.get("RT_ALERTS_STALE_SECONDS", "180")),
            rt_feed_urls=feed_urls(env),
            llm_model=env.get("LLM_MODEL") or DEFAULT_LLM_MODEL,
            chat_session_ttl_seconds=int(env.get("CHAT_SESSION_TTL_SECONDS", "1800")),
            chat_timeout_seconds=float(env.get("CHAT_TIMEOUT_SECONDS", "20")),
            chat_max_tool_iterations=int(env.get("CHAT_MAX_TOOL_ITERATIONS", "6")),
            chat_max_input_chars=int(env.get("CHAT_MAX_INPUT_CHARS", "500")),
        )
