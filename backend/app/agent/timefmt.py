"""Local (New York) time strings for the model. The API itself stays in UTC."""

from datetime import datetime
from zoneinfo import ZoneInfo

NYC = ZoneInfo("America/New_York")


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, NYC)


def clock_time(ts: float) -> str:
    """1790618460 -> "2:01 PM"."""
    t = _local(ts)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def short_time(ts: float, now: float) -> str:
    """Alert boundaries: "5 AM", "5:30 AM", plus the weekday when it isn't today."""
    t = _local(ts)
    hour = t.hour % 12 or 12
    text = f"{hour}{'' if t.minute == 0 else f':{t.minute:02d}'} {'AM' if t.hour < 12 else 'PM'}"
    if t.date() != _local(now).date():
        text += f" {t.strftime('%a')}"
    return text


def weekday_time(ts: float) -> str:
    """Weekday and time, e.g. "Mon 1:59 PM", for telling the model what now is."""
    return f"{_local(ts).strftime('%a')} {clock_time(ts)}"


def alert_window(start: float | None, end: float | None, now: float) -> str:
    """When an alert applies, relative to now: "now until 5 AM Sun", "starts 3:30 PM"."""
    if start and start > now:
        return f"starts {short_time(start, now)}" + (
            f" until {short_time(end, now)}" if end else ""
        )
    return f"now until {short_time(end, now)}" if end else "now, until further notice"
