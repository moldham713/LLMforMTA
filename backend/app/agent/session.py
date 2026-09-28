"""Chat sessions in Redis: history, filled slots, location and intent, 30-minute TTL."""

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

MAX_TURNS = 10
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def empty_slots() -> dict:
    return {"station": None, "route": None, "direction": None}


@dataclass
class Session:
    id: str
    # One entry per rider turn: that turn's API messages, ending in the assistant's reply.
    turns: list[list[dict]] = field(default_factory=list)
    slots: dict = field(default_factory=empty_slots)
    location: dict | None = None
    intent: str | None = None
    awaiting: str | None = None

    def history(self) -> list[dict]:
        return [m for turn in self.turns for m in turn]

    def add_turn(self, messages: list[dict]) -> None:
        # Whole turns only, so every tool_use keeps its tool_result.
        self.turns = [*self.turns, messages][-MAX_TURNS:]


def valid_session_id(value: str) -> bool:
    return bool(_ID.match(value))


class SessionStore:
    def __init__(self, client, ttl_seconds: int = 1800):
        self.redis = client
        self.ttl = ttl_seconds

    @staticmethod
    def _key(session_id: str) -> str:
        return f"chat:session:{session_id}"

    def load_or_create(self, session_id: str | None) -> Session:
        if session_id:
            if not valid_session_id(session_id):
                raise ValueError("session_id must be 1-64 letters, digits, - or _")
            raw = self.redis.get(self._key(session_id))
            if raw:
                return Session(**json.loads(raw))
            return Session(id=session_id)
        return Session(id=uuid.uuid4().hex)

    def save(self, session: Session) -> None:
        self.redis.set(
            self._key(session.id),
            json.dumps(asdict(session), separators=(",", ":")),
            ex=self.ttl,
        )
