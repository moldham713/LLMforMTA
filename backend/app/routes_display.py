"""How each subway route_id is shown to riders.

Kept in code rather than the database so realtime-only route_ids (absent from static GTFS)
still render, and so display never depends on a load having run.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RouteDisplay:
    route_id: str
    label: str  # what riders see on the bullet: "6" for 6X, "S" for GS
    is_express: bool
    color: str
    text_color: str
    shuttle_name: str | None
    sort_order: int

    def to_dict(self) -> dict:
        d = asdict(self)
        del d["sort_order"]
        return d


RED, GREEN, PURPLE = "#D82233", "#009952", "#9A38A1"
BLUE, ORANGE, LIME, BROWN, GRAY, YELLOW, SIR_BLUE = (
    "#0062CF", "#EB6800", "#799534", "#8E5C33", "#7C858C", "#F6BC26", "#08179C",
)  # fmt: skip
WHITE, BLACK = "#FFFFFF", "#000000"

# route_id: (label, is_express, color, text_color, shuttle_name); order is MTA's.
_ROUTES = {
    "A": ("A", False, BLUE, WHITE, None),
    "C": ("C", False, BLUE, WHITE, None),
    "E": ("E", False, BLUE, WHITE, None),
    "B": ("B", False, ORANGE, WHITE, None),
    "D": ("D", False, ORANGE, WHITE, None),
    "F": ("F", False, ORANGE, WHITE, None),
    "FX": ("F", True, ORANGE, WHITE, None),
    "M": ("M", False, ORANGE, WHITE, None),
    "G": ("G", False, LIME, WHITE, None),
    "J": ("J", False, BROWN, WHITE, None),
    "Z": ("Z", False, BROWN, WHITE, None),
    "L": ("L", False, GRAY, WHITE, None),
    "N": ("N", False, YELLOW, BLACK, None),
    "Q": ("Q", False, YELLOW, BLACK, None),
    "R": ("R", False, YELLOW, BLACK, None),
    "W": ("W", False, YELLOW, BLACK, None),
    "GS": ("S", False, GRAY, WHITE, "42 St Shuttle"),
    "FS": ("S", False, GRAY, WHITE, "Franklin Av Shuttle"),
    "H": ("S", False, GRAY, WHITE, "Rockaway Park Shuttle"),
    "1": ("1", False, RED, WHITE, None),
    "2": ("2", False, RED, WHITE, None),
    "3": ("3", False, RED, WHITE, None),
    "4": ("4", False, GREEN, WHITE, None),
    "5": ("5", False, GREEN, WHITE, None),
    # Not in static GTFS; has appeared in the realtime feed for Dyre Av express runs.
    "5X": ("5", True, GREEN, WHITE, None),
    "6": ("6", False, GREEN, WHITE, None),
    "6X": ("6", True, GREEN, WHITE, None),
    "7": ("7", False, PURPLE, WHITE, None),
    "7X": ("7", True, PURPLE, WHITE, None),
    "SI": ("SIR", False, SIR_BLUE, WHITE, None),
}

ROUTES: dict[str, RouteDisplay] = {
    rid: RouteDisplay(rid, *spec, sort_order=i) for i, (rid, spec) in enumerate(_ROUTES.items())
}

# The stations dataset lists every shuttle as "S"; these stops say which one.
SHUTTLE_AT_STOP = {
    "901": "GS", "902": "GS",
    "S01": "FS", "S03": "FS", "S04": "FS", "D26": "FS",
    "H04": "H", "H12": "H", "H13": "H", "H14": "H", "H15": "H",
}  # fmt: skip
SHUTTLES = {"GS", "FS", "H"}


def display(route_id: str) -> RouteDisplay:
    """Display fields for a route_id; unknown ids render as themselves, sorted last."""
    known = ROUTES.get(route_id)
    if known:
        return known
    return RouteDisplay(route_id, route_id, False, GRAY, WHITE, None, sort_order=len(ROUTES))


def sort_route_ids(route_ids) -> list[str]:
    return sorted(set(route_ids), key=lambda r: (display(r).sort_order, r))


def resolve_route(value: str) -> set[str]:
    """Route input -> route_ids it can mean.

    An exact route_id ("6X", "GS") means just that route. A rider-facing label means
    every route wearing it: "6" -> {6, 6X}, "S" -> all three shuttles, "SIR" -> {SI}.
    Raises ValueError for unknown input.
    """
    v = value.strip().upper()
    if v in ROUTES and ROUTES[v].label != v:
        return {v}
    ids = {rid for rid, r in ROUTES.items() if r.label == v}
    if not ids:
        raise ValueError(f"unknown route {value!r}")
    return ids


def typical_route_ids(stop_id: str, daytime_routes: str | None, scheduled: set[str]) -> list[str]:
    """Route_ids for the dataset's Daytime Routes labels ("A C E", "S", "SIR") at a stop."""
    ids = []
    for label in (daytime_routes or "").split():
        if label == "S":
            shuttle = SHUTTLE_AT_STOP.get(stop_id) or next(iter(sorted(scheduled & SHUTTLES)), None)
            if shuttle:
                ids.append(shuttle)
        elif label == "SIR":
            ids.append("SI")
        else:
            ids.append(label)
    return ids
