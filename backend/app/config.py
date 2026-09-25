import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            redis_url=os.environ["REDIS_URL"],
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
