"""Next departures and active alerts for a station, route and direction.

Plain Python (no Flask) so the agent reuses it. Every time and alert text comes from the
feeds; nothing here invents one.
"""

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.realtime.feeds import feeds_for_routes
from app.realtime.store import RealtimeStore
from app.routes_display import display, resolve_route, sort_route_ids
from app.stations.lookup import ComplexInfo, StationLookup, StationStop

DEFAULT_STALE_SECONDS = 90
CATEGORY_ORDER = {"delay": 0, "other": 1, "planned_work": 2}
MAX_PERIODS_SHOWN = 3

_NORTH_WORDS = {"n", "north", "northbound"}
_SOUTH_WORDS = {"s", "south", "southbound"}
# Filler riders add around a direction ("the uptown train", "towards brooklyn").
_FILLER = {
    "the",
    "and",
    "to",
    "toward",
    "towards",
    "bound",
    "train",
    "trains",
    "going",
    "direction",
}


class UnknownStation(LookupError):
    pass


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts else None


def _words(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def resolve_direction(value: str, stop: StationStop) -> tuple[str, str]:
    """Rider direction -> (N|S, this station's label for it). Raises ValueError."""
    labels = {"N": stop.north_label or "Northbound", "S": stop.south_label or "Southbound"}
    v = value.strip().lower()
    if v in _NORTH_WORDS:
        return "N", labels["N"]
    if v in _SOUTH_WORDS:
        return "S", labels["S"]
    wanted = _words(v) - _FILLER
    matches = [code for code, label in labels.items() if wanted and wanted <= _words(label)]
    if len(matches) != 1:
        raise ValueError(
            f"direction {value!r} doesn't match this station; use N ({labels['N']}) "
            f"or S ({labels['S']})"
        )
    return matches[0], labels[matches[0]]


class DeparturesService:
    def __init__(
        self,
        lookup: StationLookup,
        store: RealtimeStore,
        *,
        clock: Callable[[], float] = time.time,
        stale_after: float = DEFAULT_STALE_SECONDS,
    ):
        self.lookup = lookup
        self.store = store
        self.clock = clock
        self.stale_after = stale_after

    def get_departures(self, complex_id: int, route: str, direction: str, n: int = 3) -> dict:
        """Raises UnknownStation, or ValueError for an unknown route or direction."""
        info = self.lookup.complex_info(complex_id)
        if info is None:
            raise UnknownStation(f"unknown complex_id {complex_id}")
        route_ids = self._route_here(route, info)
        serving = [s for s in info.stops if route_ids & {*s.typical, *s.scheduled}]
        code, label = resolve_direction(direction, (serving or info.stops)[0])
        platforms = {s.stop_id + code for s in (serving or info.stops)}

        now = self.clock()
        upcoming = sorted(
            (
                a
                for a in self.store.arrivals(platforms)
                if a["route_id"] in route_ids and a["arrival_ts"] >= now
            ),
            key=lambda a: a["arrival_ts"],
        )[:n]
        dests = {a["destination_stop_id"] for a in upcoming}
        names = self.lookup.stop_names(dests | {d[:-1] for d in dests})

        feeds = self.store.feed_status(now, self.stale_after)
        relevant = [feeds[f] for f in sorted(feeds_for_routes(route_ids))]
        header_times = [f["header_ts"] for f in relevant]
        data_as_of = min(header_times) if relevant and all(header_times) else None

        if upcoming:
            status = "ok"
        elif serving:
            status = "no_trains_showing"
        else:
            status = "route_not_typical_here"

        return {
            "station": {"complex_id": info.complex_id, "name": info.name},
            "route": {
                **display(sort_route_ids(route_ids)[0]).to_dict(),
                "route_ids": sort_route_ids(route_ids),
            },
            "direction": {"code": code, "label": label},
            "departures": [self._departure(a, now, names) for a in upcoming],
            "status": status,
            "data_as_of": _iso(data_as_of),
            "stale": not relevant or any(f["stale"] for f in relevant),
            "alerts": self.get_alerts(route_ids=route_ids),
        }

    def get_alerts(
        self,
        route: str | None = None,
        complex_id: int | None = None,
        *,
        route_ids: set[str] | None = None,
    ) -> list[dict]:
        """Active alerts, delays first then other notices then planned work.

        With both a route and a station: that route's alerts that are line-wide or
        mention the station.
        """
        if route:
            route_ids = resolve_route(route)
        stop_ids = None
        if complex_id is not None:
            info = self.lookup.complex_info(complex_id)
            if info is None:
                raise UnknownStation(f"unknown complex_id {complex_id}")
            stop_ids = {s.stop_id for s in info.stops}

        if route_ids is not None:
            alerts = self.store.alerts(route_ids=route_ids)
            if stop_ids is not None:
                alerts = [a for a in alerts if not a["stop_ids"] or stop_ids & set(a["stop_ids"])]
        else:
            alerts = self.store.alerts(stop_ids=stop_ids)

        now = self.clock()
        active = [a for a in alerts if self._active(a, now)]
        active.sort(key=lambda a: (CATEGORY_ORDER.get(a["category"], 1), -(a["updated_at"] or 0)))
        return [self._present(a, now) for a in active]

    def feed_status(self) -> dict[str, dict]:
        return self.store.feed_status(self.clock(), self.stale_after)

    def _route_here(self, route: str, info: ComplexInfo) -> set[str]:
        """Resolve a route id or label; labels shared by several routes ("S", "6") narrow
        to the ones at this station, e.g. "S" at Times Sq is the 42 St Shuttle."""
        ids = resolve_route(route)
        here = ids & {*info.typical_route_ids, *info.scheduled_route_ids}
        return here or ids

    @staticmethod
    def _departure(a: dict, now: float, names: dict[str, str]) -> dict:
        minutes = int((a["arrival_ts"] - now) // 60)
        dest = a["destination_stop_id"]
        return {
            "route": display(a["route_id"]).to_dict(),
            "arrival_ts": a["arrival_ts"],
            "arrival_time": _iso(a["arrival_ts"]),
            "minutes_away": minutes,
            "display": "arriving" if minutes == 0 else f"{minutes} min",
            "destination": names.get(dest) or names.get(dest[:-1]),
            "destination_stop_id": dest,
            "is_assigned": a["is_assigned"],
        }

    @staticmethod
    def _active(alert: dict, now: float) -> bool:
        periods = alert["active_periods"]
        return not periods or any(
            (p["start"] or 0) <= now and (not p["end"] or now < p["end"]) for p in periods
        )

    @staticmethod
    def _present(alert: dict, now: float) -> dict:
        # Planned work can list hundreds of windows; the current and next few are enough.
        periods = [p for p in alert["active_periods"] if not p["end"] or p["end"] >= now]
        return {
            **{k: v for k, v in alert.items() if k != "active_periods"},
            "routes": [display(r).to_dict() for r in alert["route_ids"]],
            "active_periods": [
                {"start": _iso(p["start"]), "end": _iso(p["end"])}
                for p in periods[:MAX_PERIODS_SHOWN]
            ],
        }
