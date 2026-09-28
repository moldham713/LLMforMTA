"""Parse MTA GTFS-rt trip updates and Mercury alerts into plain dicts.

Realtime trip_ids don't reliably match static GTFS, so nothing here keeps or joins on
them: arrivals are keyed by route and platform stop only.
"""

import json
from typing import TypedDict

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from app.realtime import nyct_subway_pb2
from app.routes_display import sort_route_ids

# NyctTripDescriptor.Direction; the subway only uses NORTH and SOUTH.
_DIRECTIONS = {1: "N", 3: "S"}


class FeedParseError(ValueError):
    pass


class Arrival(TypedDict):
    route_id: str
    stop_id: str  # platform, with N/S suffix
    direction: str | None
    arrival_ts: int
    destination_stop_id: str
    is_assigned: bool
    feed: str


class Alert(TypedDict):
    id: str
    route_ids: list[str]
    stop_ids: list[str]
    alert_type: str | None
    header: str
    description: str
    active_periods: list[dict]
    category: str
    updated_at: int | None


def parse_trip_updates(data: bytes, feed: str, now: float) -> tuple[int, list[Arrival]]:
    """(header timestamp, future arrivals) from one GTFS-rt protobuf snapshot."""
    msg = gtfs_realtime_pb2.FeedMessage()
    try:
        msg.ParseFromString(data)
    except DecodeError as exc:
        raise FeedParseError(f"{feed}: not a GTFS-rt message ({exc})") from exc
    # Arbitrary bytes (an HTML error page, say) can decode as an empty message.
    if not msg.header.gtfs_realtime_version or not msg.header.timestamp:
        raise FeedParseError(f"{feed}: missing GTFS-rt header")

    arrivals: list[Arrival] = []
    for entity in msg.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        updates = tu.stop_time_update
        if not updates:
            continue
        nyct = tu.trip.Extensions[nyct_subway_pb2.nyct_trip_descriptor]
        trip_direction = _DIRECTIONS.get(nyct.direction) if nyct.HasField("direction") else None
        destination = updates[-1].stop_id
        for u in updates:
            # A trip's first stop often has only a departure time.
            ts = u.arrival.time if u.HasField("arrival") and u.arrival.time else u.departure.time
            if not ts or ts < now:
                continue
            suffix = u.stop_id[-1:]
            arrivals.append(
                Arrival(
                    route_id=tu.trip.route_id,
                    stop_id=u.stop_id,
                    direction=trip_direction or (suffix if suffix in ("N", "S") else None),
                    arrival_ts=int(ts),
                    destination_stop_id=destination,
                    is_assigned=bool(nyct.is_assigned),
                    feed=feed,
                )
            )
    return int(msg.header.timestamp), arrivals


# Mercury alert_type -> category. "delay" means an unplanned problem happening now,
# which includes unplanned suspensions and reroutes, not just slow trains.
_PLANNED_TYPES = {"reduced service", "special schedule"}
_DISRUPTION_WORDS = (
    "delay",
    "suspend",
    "reroute",
    "skipped",
    "express to local",
    "local to express",
)


def categorize(alert_type: str | None) -> str:
    if not alert_type:
        return "other"
    t = alert_type.strip().lower()
    if t.startswith("planned") or t in _PLANNED_TYPES:
        return "planned_work"
    if any(word in t for word in _DISRUPTION_WORDS):
        return "delay"
    return "other"


def _text(translated: dict | None) -> str:
    translations = (translated or {}).get("translation", [])
    for lang in ("en", None):
        for t in translations:
            if lang is None or t.get("language") == lang:
                return (t.get("text") or "").strip()
    return ""


def parse_alerts(data: bytes, now: float) -> tuple[int, list[Alert]]:
    """(header timestamp, alerts not yet over) from the camsys subway-alerts JSON feed."""
    try:
        doc = json.loads(data)
        header_ts = int(doc["header"]["timestamp"])
    except (ValueError, KeyError, TypeError) as exc:
        raise FeedParseError(f"alerts: not a GTFS-rt JSON document ({exc})") from exc

    alerts: list[Alert] = []
    for entity in doc.get("entity", []):
        a = entity.get("alert")
        if not a:
            continue
        periods = [
            {"start": p.get("start"), "end": p.get("end")} for p in a.get("active_period", [])
        ]
        if periods and all(p["end"] and p["end"] < now for p in periods):
            continue
        entities = a.get("informed_entity", [])
        mercury = a.get("transit_realtime.mercury_alert", {})
        alert_type = mercury.get("alert_type")
        alerts.append(
            Alert(
                id=entity["id"],
                route_ids=sort_route_ids(e["route_id"] for e in entities if e.get("route_id")),
                stop_ids=sorted({e["stop_id"] for e in entities if e.get("stop_id")}),
                alert_type=alert_type,
                header=_text(a.get("header_text")),
                description=_text(a.get("description_text")),
                active_periods=periods,
                category=categorize(alert_type),
                updated_at=mercury.get("updated_at"),
            )
        )
    return header_ts, alerts
