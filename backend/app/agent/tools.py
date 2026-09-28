"""Tools the model can call: thin wrappers over the station and departures services.

Results are formatted for the model, not the API: compact JSON, rider-facing line labels
only (never raw ids like 6X or GS), New York local times, and trimmed alert text.
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher

from app.agent.timefmt import alert_window, clock_time
from app.realtime.departures import DeparturesService, UnknownStation, resolve_direction
from app.routes_display import display, resolve_route, sort_route_ids
from app.stations.lookup import ComplexInfo, StationLookup

MAX_CANDIDATES = 3
# A station lookup this far ahead of the runner-up is taken without asking.
CLEAR_WINNER_MARGIN = 0.1
MAX_TRAINS = 6
DESCRIPTION_CHARS = 300
PLANNED_LOOKAHEAD_SECONDS = 2 * 3600
NEAREST_WITH_LINE_METERS = 3000
INTENTS = ["departures", "trip_planning", "bus", "fares", "other"]
SLOTS = ["station", "route", "direction"]

TOOLS = [
    {
        "name": "find_station",
        "description": (
            "Look up subway station complexes by name, street, intersection, landmark or "
            "nickname (misspellings are fine). Pass the line as `route` when the rider named "
            "one: it drops stations that line doesn't serve. Returns up to 3 matches, best "
            "first, each with its lines and, when unambiguous, its two direction labels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What the rider called the station."},
                "route": {"type": "string", "description": "Line, e.g. 6, A, S, SIR."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "stations_near_me",
        "description": (
            "Stations within 1 km of the rider's shared location, nearest first. Only works "
            "when the session has a location. Pass `route` to keep stations on that line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"route": {"type": "string"}},
        },
    },
    {
        "name": "get_departures",
        "description": (
            "Next trains for one line and direction at a station, with that line's alerts. "
            "Identify the station by `complex_id` (from an earlier result) or by `station`, "
            "the rider's own words; with `station` it is looked up on that line, and if "
            "several stations fit you get status 'ambiguous_station' with candidates to ask "
            "about. `direction` may be N or S, the station's direction label (e.g. 'Uptown & "
            "The Bronx', 'uptown', 'Brooklyn'), or a destination (e.g. 'Coney Island', "
            "'Van Cortlandt', 'Queens'); destinations are resolved to N or S for you. "
            "`line_quote` and `direction_quote` must be the rider's own words from this "
            "conversation; if the rider never said which line or which way, pass an empty "
            "string and you'll get status 'need_line' or 'need_direction' with the options "
            "to ask about."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "complex_id": {"type": "integer"},
                "station": {"type": "string", "description": "Station as the rider said it."},
                "route": {"type": "string", "description": "Line label, e.g. 6, A, S."},
                "line_quote": {
                    "type": "string",
                    "description": "The rider's exact words naming the line, e.g. 'the 6', "
                    "'shuttle'; empty if they never named one.",
                },
                "direction": {"type": "string"},
                "direction_quote": {
                    "type": "string",
                    "description": "The rider's exact words giving the direction, e.g. "
                    "'uptown', 'to Canarsie'; empty if they never said which way.",
                },
                "count": {
                    "type": "integer",
                    "description": 'How many trains (default 3, max 6); raise it for "the '
                    'one after that".',
                },
            },
            "required": ["route", "line_quote", "direction", "direction_quote"],
        },
    },
    {
        "name": "get_alerts",
        "description": (
            "Current alerts for a line (optionally only those affecting one station), plus "
            "planned work active now or starting within 2 hours."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "route": {"type": "string"},
                "complex_id": {"type": "integer"},
            },
            "required": ["route"],
        },
    },
    {
        "name": "reply",
        "description": (
            "Send your message to the rider and end the turn. Always finish every turn with "
            "this tool; never answer in plain text."
        ),
        "input_schema": {
            "type": "object",
            # Kept small on purpose: every output token here is rider-visible latency.
            "properties": {
                "message": {"type": "string", "description": "What the rider sees."},
                "intent": {
                    "type": "string",
                    "enum": INTENTS,
                    "description": "Omit for departures (the default).",
                },
                "awaiting": {
                    "type": "string",
                    "enum": SLOTS,
                    "description": "Only when your message asks a question: the slot it asks "
                    "about.",
                },
            },
            "required": ["message"],
        },
    },
]
TOOL_NAMES = {t["name"] for t in TOOLS}


class ToolError(Exception):
    """Shown to the model as an is_error tool result."""


def _json(data) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _ts(iso: str | None) -> float | None:
    return datetime.fromisoformat(iso).timestamp() if iso else None


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


_QUOTE_FILLER = {
    "the", "train", "trains", "line", "bound", "to", "toward", "towards",
    "going", "heading", "headed", "direction", "in", "at", "on", "next", "please",
}  # fmt: skip


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    # Numbers must match exactly ("6" is not "86"); words may be misspelled.
    if a.isdigit() or b.isdigit() or min(len(a), len(b)) < 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.8


def line_label(route_id: str) -> str:
    d = display(route_id)
    return f"{d.label} ({d.shuttle_name})" if d.shuttle_name else d.label


def _lines(route_ids: list[str]) -> list[str]:
    seen: list[str] = []
    for rid in route_ids:
        label = line_label(rid)
        if label not in seen:
            seen.append(label)
    return seen


@dataclass
class Toolbox:
    """Executes tool calls for one chat turn and remembers what they found."""

    lookup: StationLookup
    departures: DeparturesService
    location: dict | None = None
    clock: Callable[[], float] = time.time
    last_departures: dict | None = None  # API-shaped get_departures result, for the card
    found: dict = field(default_factory=dict)  # slots the tools resolved this turn
    rider_text: list[str] | None = None  # everything the rider typed this session

    def execute(self, name: str, args: dict) -> str:
        """Run a tool; raises ToolError for problems the model should see."""
        handler = {
            "find_station": self.find_station,
            "stations_near_me": self.stations_near_me,
            "get_departures": self.get_departures,
            "get_alerts": self.get_alerts,
        }.get(name)
        if handler is None:
            raise ToolError(f"unknown tool {name}")
        try:
            return _json(handler(**args))
        except TypeError as exc:
            raise ToolError(f"bad arguments for {name}: {exc}") from exc
        except (ValueError, UnknownStation) as exc:
            raise ToolError(str(exc)) from exc

    # --- tools ----------------------------------------------------------------------

    def find_station(self, query: str, route: str | None = None) -> dict:
        matches = self.lookup.search_stations(query, route=route, limit=MAX_CANDIDATES)
        route_ids = resolve_route(route) if route else None
        extra = {}
        if not matches and route:
            # The rider may simply be wrong about the line; say so rather than "not found".
            matches = self.lookup.search_stations(query, limit=MAX_CANDIDATES)
            if matches:
                first = self.lookup.complex_info(matches[0].complex_id)
                self._note(first, route)
                extra = {
                    "status": "route_not_typical_here",
                    "note": f"The {route} doesn't normally stop at these stations.",
                    "nearest_with_line": self._nearest_with_line(first, route),
                }
        infos = self.lookup.complexes([m.complex_id for m in matches])
        if not matches:
            extra = {"note": f"no station matches {query!r}"}
        return {
            "matches": [self._station(infos[m.complex_id], route_ids) for m in matches],
            **extra,
        }

    def stations_near_me(self, route: str | None = None) -> dict:
        if not self.location:
            raise ToolError("the rider hasn't shared a location; ask which station they're at")
        route_ids = resolve_route(route) if route else None
        nearby = self.lookup.nearest_stations(self.location["lat"], self.location["lon"], limit=10)
        infos = self.lookup.complexes([n.complex_id for n in nearby])
        out = []
        for n in nearby:
            info = infos[n.complex_id]
            if route_ids and not info.serves(route_ids):
                continue
            out.append({**self._station(info, route_ids), "meters": round(n.distance_m)})
        return (
            {"stations": out[:MAX_CANDIDATES]}
            if out
            else {"stations": [], "note": "none within 1 km"}
        )

    def get_departures(
        self,
        route: str,
        direction: str,
        complex_id: int | None = None,
        station: str | None = None,
        count: int = 3,
        line_quote: str | None = None,
        direction_quote: str | None = None,
    ) -> dict:
        if complex_id is None:
            if not station:
                raise ToolError("give complex_id or station")
            matches = self.lookup.search_stations(station, route=route, limit=MAX_CANDIDATES)
            # A station the line doesn't serve still gets an answer (route_not_typical_here).
            matches = matches or self.lookup.search_stations(station, limit=MAX_CANDIDATES)
            if not matches:
                raise ToolError(f"no station matching {station!r}")
            if len(matches) > 1 and matches[0].score - matches[1].score < CLEAR_WINNER_MARGIN:
                infos = self.lookup.complexes([m.complex_id for m in matches])
                route_ids = resolve_route(route)
                return {
                    "status": "ambiguous_station",
                    "matches": [self._station(infos[m.complex_id], route_ids) for m in matches],
                }
            complex_id = matches[0].complex_id
        info = self.lookup.complex_info(complex_id)
        if info is None:
            raise ToolError(f"unknown complex_id {complex_id}")
        route_ids = resolve_route(route)
        self._note(info)

        # The model must show the rider actually named the line and direction; otherwise it
        # gets the options to ask about instead of trains for a guess.
        lines = _lines(info.typical_route_ids or info.scheduled_route_ids)
        if not self._rider_said(line_quote) and len(lines) > 1:
            return {
                "status": "need_line",
                "station": info.name,
                "lines": lines,
                "note": "The rider hasn't said which line. Ask them; don't call "
                "get_departures again this turn.",
            }
        self._note(info, route)
        if not info.serves(route_ids):
            return self._not_typical(info, route)
        if not self._rider_said(direction_quote):
            stop = self._stop_for(info, route)
            return {
                "status": "need_direction",
                "station": info.name,
                "line": line_label(sort_route_ids(route_ids)[0]),
                "directions": {"N": stop.north_label, "S": stop.south_label},
                "note": "The rider hasn't said which way. Ask them with these two labels; "
                "don't call get_departures again this turn.",
            }

        resolved = None
        try:
            code = self._direction_code(info, route, direction)
        except ValueError:
            resolved = self.departures.direction_toward(complex_id, route, direction)
            if resolved is None:
                raise ToolError(
                    f"couldn't tell which way {direction!r} is on the {route} from "
                    f"{info.name}. {self._direction_options(info, route)}"
                ) from None
            code = resolved["code"]
        count = max(1, min(int(count), MAX_TRAINS))
        result = self.departures.get_departures(complex_id, route, code, n=count)
        self.last_departures = result
        self._note(info, route, result["direction"])

        out = {
            "station": info.name,
            "line": line_label(result["route"]["route_id"]),
            "direction": result["direction"]["label"],
            "direction_code": code,
            "status": result["status"],
            "trains": [self._train(d) for d in result["departures"]],
            "as_of": clock_time(_ts(result["data_as_of"])) if result["data_as_of"] else None,
            "stale": result["stale"],
            **self._alerts_block(route_ids=set(result["route"]["route_ids"])),
        }
        if result["stale"]:
            out["stale_note"] = (
                f"Train times are from {out['as_of'] or 'an earlier update'} and may be out "
                "of date: say they're as of that time."
            )
        if resolved:
            out["direction_from"] = {
                "destination": resolved["destination"],
                "confidence": resolved["confidence"],
            }
        if result["status"] == "route_not_typical_here":
            out["nearest_with_line"] = self._nearest_with_line(info, route)
        return out

    def _not_typical(self, info: ComplexInfo, route: str) -> dict:
        # Checked before direction: a line that doesn't stop here has no direction here.
        result = self.departures.get_departures(info.complex_id, route, "N")
        self.last_departures = result
        return {
            "station": info.name,
            "line": line_label(result["route"]["route_id"]),
            "status": "route_not_typical_here",
            "trains": [],
            "nearest_with_line": self._nearest_with_line(info, route),
        }

    def _note(self, info: ComplexInfo | None = None, route: str | None = None, direction=None):
        if info is not None:
            self.found["station"] = {"complex_id": info.complex_id, "name": info.name}
        if route:
            self.found["route"] = display(sort_route_ids(resolve_route(route))[0]).label
        if direction:
            self.found["direction"] = {"code": direction["code"], "label": direction["label"]}

    def _rider_said(self, quote: str | None) -> bool:
        """Whether the rider really said `quote`: each of its meaningful words appears
        among the rider's words (typos allowed), in any turn. The model often tidies a
        quote ("downtwon 6" -> "the downtown 6"), so exact substrings are too strict.
        Without a transcript (direct tool use) any non-empty quote counts."""
        wanted = [w for w in _normalize(quote or "").split() if w not in _QUOTE_FILLER]
        if not wanted:
            return False
        if self.rider_text is None:
            return True
        said = {w for t in self.rider_text for w in _normalize(t).split()}
        return all(any(_same_word(w, s) for s in said) for w in wanted)

    def get_alerts(self, route: str, complex_id: int | None = None) -> dict:
        route_ids = resolve_route(route)
        self._note(route=route)
        return {
            "line": line_label(sorted(route_ids, key=lambda r: display(r).sort_order)[0]),
            **self._alerts_block(route_ids=route_ids, complex_id=complex_id),
        }

    # --- formatting -------------------------------------------------------------------

    def _station(self, info: ComplexInfo, route_ids: set[str] | None) -> dict:
        out = {
            "complex_id": info.complex_id,
            "name": info.name,
            "borough": info.borough,
            "lines": _lines(info.typical_route_ids or info.scheduled_route_ids),
        }
        # Direction labels only when they can't depend on which line the rider means.
        stops = [s for s in info.stops if not route_ids or route_ids & {*s.typical, *s.scheduled}]
        pairs = {(s.north_label, s.south_label) for s in stops or info.stops}
        if len(pairs) == 1:
            north, south = pairs.pop()
            out["directions"] = {"N": north, "S": south}
        return out

    @staticmethod
    def _train(d: dict) -> dict:
        train = {
            "min": d["minutes_away"],
            "at": clock_time(d["arrival_ts"]),
            "to": d["destination"],
        }
        if d["route"]["is_express"]:
            train["express"] = True
        return train

    def _alerts_block(self, route_ids: set[str], complex_id: int | None = None) -> dict:
        result = self.departures.get_alerts(
            complex_id=complex_id,
            route_ids=route_ids,
            upcoming_within=PLANNED_LOOKAHEAD_SECONDS,
        )
        now = self.clock()
        alerts = []
        for a in result["alerts"]:
            period = next(iter(a["active_periods"]), {"start": None, "end": None})
            description = a["description"]
            if len(description) > DESCRIPTION_CHARS:
                description = description[: DESCRIPTION_CHARS - 1].rstrip() + "…"
            alerts.append(
                {
                    "type": a["alert_type"],
                    "category": a["category"],
                    "lines": _lines(a["route_ids"]),
                    "header": a["header"],
                    "description": description,
                    "when": alert_window(_ts(period["start"]), _ts(period["end"]), now),
                }
            )
        as_of = clock_time(_ts(result["alerts_as_of"])) if result["alerts_as_of"] else None
        block = {
            "alerts": alerts,
            "alerts_as_of": as_of,
            "alerts_stale": result["alerts_stale"],
            "alerts_available": result["alerts_available"],
        }
        # Spelled out because a pair of booleans is easy for the model to skim past.
        if not result["alerts_available"]:
            block["alerts_note"] = (
                "Alerts are unavailable: tell the rider you couldn't check for delays or "
                "service changes. Do not say there are no delays."
            )
        elif result["alerts_stale"]:
            block["alerts_note"] = (
                f"Alerts were last updated at {as_of} and may be out of date: tell the rider "
                "you couldn't check for the latest delays. Do not say there are no delays."
            )
        return block

    def _direction_code(self, info: ComplexInfo, route: str, direction: str) -> str:
        stop = self._stop_for(info, route)
        return resolve_direction(direction, stop)[0]

    def _direction_options(self, info: ComplexInfo, route: str) -> str:
        stop = self._stop_for(info, route)
        return f"Directions here: N = {stop.north_label}, S = {stop.south_label}."

    @staticmethod
    def _stop_for(info: ComplexInfo, route: str):
        ids = resolve_route(route)
        return next((s for s in info.stops if ids & {*s.typical, *s.scheduled}), info.stops[0])

    def _nearest_with_line(self, info: ComplexInfo, route: str) -> dict | None:
        where = self.lookup.complex_location(info.complex_id)
        if where is None:
            return None
        route_ids = resolve_route(route)
        nearby = self.lookup.nearest_stations(
            *where, limit=100, max_meters=NEAREST_WITH_LINE_METERS
        )
        infos = self.lookup.complexes([n.complex_id for n in nearby])
        for n in nearby:
            candidate = infos[n.complex_id]
            if n.complex_id != info.complex_id and route_ids & set(candidate.typical_route_ids):
                return {**self._station(candidate, route_ids), "meters": round(n.distance_m)}
        return None
