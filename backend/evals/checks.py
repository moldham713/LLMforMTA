"""Automated checks on one eval conversation. Pure functions, unit-tested."""

import json
import re

# "2, 7, and 12 min", "the 7-min train", "in 1 minute"
_MINUTES = re.compile(
    r"((?:\b\d{1,3}\b(?:\s*(?:,|and|or|&)\s*|\s+)?)+)(?:-|\s)?(?:min|mins|minutes)\b", re.I
)
# "2:04 PM", "14:04"
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
# "5 AM", "5pm"
_HOUR = re.compile(r"(?<!:)\b(\d{1,2})\s*([ap])\.?m\.?\b", re.I)

_NO_DELAYS = re.compile(
    r"\bno\s+(?:known\s+|current\s+|reported\s+|active\s+)?"
    r"(?:delays?|alerts?|service\s+(?:changes?|alerts?|disruptions?)|problems?|issues?|"
    r"disruptions?)\b"
    r"|\b(?:running|operating)\s+(?:normally|on\s+schedule|as\s+scheduled|smoothly)\b"
    r"|\bgood\s+service\b|\bnothing\s+(?:unusual|to\s+report)\b|\ball\s+clear\b",
    re.I,
)


def fabricated_numbers(reply: str, tool_results: list[str]) -> list[str]:
    """Times and minute counts in `reply` that no tool result in the same turn contains."""
    corpus = "\n".join(tool_results)
    missing = []
    for m in _MINUTES.finditer(reply):
        for n in re.findall(r"\d+", m.group(1)):
            if not (
                re.search(rf'"min":\s*{n}\b', corpus)
                or re.search(rf"\b{n}[\s-]*(?:min|mins|minutes)\b", corpus, re.I)
            ):
                missing.append(f"{n} min")
    for m in _CLOCK.finditer(reply):
        if f"{int(m.group(1))}:{m.group(2)}" not in corpus:
            missing.append(m.group(0))
    for m in _HOUR.finditer(reply):
        hour, half = int(m.group(1)), m.group(2).upper()
        if not re.search(rf"\b{hour}(?::\d\d)? {half}M\b", corpus):
            missing.append(m.group(0))
    return missing


def alerts_unchecked(tool_results: list[str]) -> bool:
    """True when some tool result this turn said alerts were stale or unavailable."""
    for raw in tool_results:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and (
            data.get("alerts_stale") is True or data.get("alerts_available") is False
        ):
            return True
    return False


# "I can't confirm there are no delays" hedges rather than claims.
_HEDGE = re.compile(
    r"(can't|cannot|can not|couldn't|could not|unable to|not able to|wasn't able to)\s+"
    r"(confirm|say|tell|verify|check)|\bwhether\b|\bif there (are|is)\b",
    re.I,
)


def claims_no_delays(reply: str) -> bool:
    for m in _NO_DELAYS.finditer(reply):
        if not _HEDGE.search(reply[max(0, m.start() - 60) : m.start()]):
            return True
    return False


def slot_mismatches(expected: dict, slots: dict) -> list[str]:
    """Differences between expected {complex_id, route, direction} and final slots."""
    actual = {
        "complex_id": (slots.get("station") or {}).get("complex_id"),
        "route": slots.get("route"),
        "direction": (slots.get("direction") or {}).get("code"),
    }
    return [
        f"{key}: expected {want!r}, got {actual[key]!r}"
        for key, want in expected.items()
        if actual.get(key) != want
    ]
