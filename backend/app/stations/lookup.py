"""Station lookup: free text or coordinates -> station complexes.

Plain Python (no Flask) so the agent and worker can reuse it.
"""

import re
from dataclasses import dataclass

from sqlalchemy import Engine, text

from app.routes_display import display, resolve_route, sort_route_ids, typical_route_ids

# Applied to both queries and station names, so either spelling on either side matches.
ABBREVIATIONS = {
    "street": "st",
    "streets": "sts",
    "avenue": "av",
    "ave": "av",
    "avenues": "avs",
    "aves": "avs",
    "square": "sq",
    "place": "pl",
    "road": "rd",
    "boulevard": "blvd",
    "parkway": "pkwy",
    "heights": "hts",
    "junction": "jct",
    "center": "ctr",
    "centre": "ctr",
    "saint": "st",
    "mount": "mt",
    "fort": "ft",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "washington": "wash",
    "lexington": "lex",
}

ORDINAL_WORDS = {
    word: str(i)
    for i, word in enumerate(
        [
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "seventh",
            "eighth",
            "ninth",
            "tenth",
            "eleventh",
            "twelfth",
            "thirteenth",
            "fourteenth",
            "fifteenth",
            "sixteenth",
            "seventeenth",
            "eighteenth",
            "nineteenth",
            "twentieth",
        ],
        start=1,
    )
}

SEPARATOR = "/"
SEPARATOR_WORDS = {"and", "at"}
_NUMERIC_ORDINAL = re.compile(r"^(\d+)(st|nd|rd|th)$")
_SEPARATOR_CHARS = re.compile(r"[&/+@\-–—]")
_APOSTROPHES = re.compile(r"['’]")
_OTHER_PUNCT = re.compile(r"[^a-z0-9/\s]")

MIN_SCORE = 0.3
# Drop candidates scoring below this fraction of the best match.
DEFAULT_RELATIVE_CUTOFF = 0.6


def normalize_query(value: str) -> str:
    """Canonical form for matching, e.g. "14th Street & 8th Ave." -> "14 st / 8 av".

    Parts that name different streets are joined by " / ".
    """
    s = value.lower()
    s = _SEPARATOR_CHARS.sub(f" {SEPARATOR} ", s)
    s = _APOSTROPHES.sub("", s)
    s = _OTHER_PUNCT.sub(" ", s)

    tokens = []
    for token in s.split():
        if token in SEPARATOR_WORDS:
            token = SEPARATOR
        elif m := _NUMERIC_ORDINAL.match(token):
            token = m.group(1)
        else:
            token = ORDINAL_WORDS.get(token) or ABBREVIATIONS.get(token, token)
        tokens.append(token)

    parts = " ".join(tokens).split(SEPARATOR)
    return f" {SEPARATOR} ".join(p.strip() for p in parts if p.strip())


def query_parts(normalized: str) -> list[str]:
    return [p.strip() for p in normalized.split(SEPARATOR) if p.strip()]


def query_numbers(normalized: str) -> list[str]:
    return re.findall(r"\b\d+\b", normalized)


@dataclass(frozen=True)
class RouteAtStation:
    route_id: str
    label: str
    is_express: bool
    color: str
    text_color: str
    shuttle_name: str | None
    stop_id: str  # parent GTFS stop where this route calls within the complex
    north_label: str | None
    south_label: str | None


@dataclass(frozen=True)
class StationRoutes:
    complex_id: int
    # From the stations dataset's Daytime Routes: the normal pattern riders expect.
    typical_routes: list[RouteAtStation]
    # From the current GTFS stop_times: includes this week's reroutes and night service.
    scheduled_routes: list[RouteAtStation]


@dataclass(frozen=True)
class StationStop:
    stop_id: str  # parent GTFS stop
    typical: list[str]
    scheduled: list[str]
    north_label: str | None
    south_label: str | None


@dataclass(frozen=True)
class ComplexInfo:
    complex_id: int
    name: str
    borough: str
    stops: list[StationStop]

    @property
    def typical_route_ids(self) -> list[str]:
        return sort_route_ids(r for s in self.stops for r in s.typical)

    @property
    def scheduled_route_ids(self) -> list[str]:
        return sort_route_ids(r for s in self.stops for r in s.scheduled)

    def serves(self, route_ids: set[str]) -> bool:
        return bool(route_ids & {*self.typical_route_ids, *self.scheduled_route_ids})


@dataclass(frozen=True)
class StationMatch:
    complex_id: int
    name: str
    borough: str
    typical_routes: list[dict]
    scheduled_routes: list[dict]
    score: float


@dataclass(frozen=True)
class NearbyStation:
    complex_id: int
    name: str
    borough: str
    typical_routes: list[dict]
    scheduled_routes: list[dict]
    distance_m: float


# Word similarity finds the query inside longer names ("times sq" in the Times Sq
# complex); plain similarity breaks ties in favour of exact names ("86 st" over "186 st").
# Numbers must match whole words: "8 av" should not score well against "3 av".
_SCORE = """
    (0.6 * word_similarity(:q, {col}) + 0.4 * similarity(:q, {col}))
    * CASE WHEN cardinality(CAST(:numbers AS text[])) = 0 THEN 1.0
           ELSE 0.5 + 0.5 * (
               SELECT CAST(count(*) AS float) FROM unnest(CAST(:numbers AS text[])) AS n
               WHERE {col} ~ ('\\m' || n || '\\M')
           ) / cardinality(CAST(:numbers AS text[]))
      END
"""

_SEARCH_SQL = f"""
WITH scored AS (
    SELECT complex_id,
           GREATEST(
               {_SCORE.format(col="name_norm")},
               -- every part of a cross-street query appears as whole words in the name
               CASE WHEN :multi AND name_norm ~ ALL(CAST(:patterns AS text[]))
                    THEN 1.0 ELSE 0 END
           ) AS score
    FROM gtfs.station_complexes
    UNION ALL
    SELECT complex_id, {_SCORE.format(col="alias_norm")}
    FROM gtfs.station_aliases
)
SELECT complex_id, max(score) AS score
FROM scored
GROUP BY complex_id
HAVING max(score) >= :min_score
ORDER BY score DESC, complex_id
"""

_NEAREST_SQL = """
WITH pt AS (SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography AS g)
SELECT s.complex_id, min(ST_Distance(p.geog, pt.g)) AS distance_m
FROM gtfs.stops p
JOIN gtfs.stations s ON s.gtfs_stop_id = p.stop_id
CROSS JOIN pt
WHERE p.location_type = 1 AND ST_DWithin(p.geog, pt.g, :max_meters)
GROUP BY s.complex_id
ORDER BY distance_m, s.complex_id
LIMIT :limit
"""

_COMPLEXES_SQL = """
SELECT c.complex_id, c.name, c.borough, s.gtfs_stop_id, s.daytime_routes,
       s.north_label, s.south_label,
       ARRAY(SELECT sr.route_id FROM gtfs.stop_routes sr
             WHERE sr.stop_id = s.gtfs_stop_id) AS scheduled
FROM gtfs.station_complexes c
JOIN gtfs.stations s ON s.complex_id = c.complex_id
WHERE c.complex_id = ANY(:ids)
ORDER BY c.complex_id, s.gtfs_stop_id
"""


def _route_dicts(route_ids: list[str]) -> list[dict]:
    return [display(r).to_dict() for r in route_ids]


class StationLookup:
    def __init__(self, engine: Engine, relative_cutoff: float = DEFAULT_RELATIVE_CUTOFF):
        self.engine = engine
        self.relative_cutoff = relative_cutoff

    def search_stations(
        self, value: str, route: str | None = None, limit: int = 5
    ) -> list[StationMatch]:
        """Ranked complexes for free text; `route` (id or label) keeps only complexes
        where it is a typical or scheduled route. Raises ValueError for unknown routes."""
        wanted = resolve_route(route) if route else None
        q = normalize_query(value)
        if not q:
            return []
        parts = query_parts(q)
        params = {
            "q": q,
            "multi": len(parts) > 1,
            "patterns": [rf"\m{p}\M" for p in parts],
            "numbers": query_numbers(q),
            "min_score": MIN_SCORE,
        }
        with self.engine.connect() as conn:
            scored = conn.execute(text(_SEARCH_SQL), params).all()
        infos = self.complexes([r.complex_id for r in scored])

        candidates = [(infos[r.complex_id], float(r.score)) for r in scored]
        if wanted:
            candidates = [(c, s) for c, s in candidates if c.serves(wanted)]
        if candidates:
            # Relative to the best remaining match, so a strong hit hides weak noise.
            floor = candidates[0][1] * self.relative_cutoff
            candidates = [(c, s) for c, s in candidates if s >= floor]
        candidates.sort(key=lambda cs: (-cs[1], cs[0].name, cs[0].complex_id))
        return [
            StationMatch(
                c.complex_id,
                c.name,
                c.borough,
                _route_dicts(c.typical_route_ids),
                _route_dicts(c.scheduled_route_ids),
                round(s, 3),
            )
            for c, s in candidates[:limit]
        ]

    def nearest_stations(
        self, lat: float, lon: float, limit: int = 5, max_meters: float = 1000
    ) -> list[NearbyStation]:
        params = {"lat": lat, "lon": lon, "limit": limit, "max_meters": max_meters}
        with self.engine.connect() as conn:
            rows = conn.execute(text(_NEAREST_SQL), params).all()
        infos = self.complexes([r.complex_id for r in rows])
        return [
            NearbyStation(
                c.complex_id,
                c.name,
                c.borough,
                _route_dicts(c.typical_route_ids),
                _route_dicts(c.scheduled_route_ids),
                round(r.distance_m, 1),
            )
            for r in rows
            for c in [infos[r.complex_id]]
        ]

    def routes_at_station(self, complex_id: int) -> StationRoutes | None:
        info = self.complex_info(complex_id)
        if info is None:
            return None

        def at(route_ids: list[str], attr: str) -> list[RouteAtStation]:
            out = []
            for rid in route_ids:
                stop = next(s for s in info.stops if rid in getattr(s, attr))
                d = display(rid)
                out.append(
                    RouteAtStation(
                        rid,
                        d.label,
                        d.is_express,
                        d.color,
                        d.text_color,
                        d.shuttle_name,
                        stop.stop_id,
                        stop.north_label,
                        stop.south_label,
                    )  # fmt: skip
                )
            return out

        return StationRoutes(
            complex_id,
            typical_routes=at(info.typical_route_ids, "typical"),
            scheduled_routes=at(info.scheduled_route_ids, "scheduled"),
        )

    def complex_info(self, complex_id: int) -> ComplexInfo | None:
        return self.complexes([complex_id]).get(complex_id)

    def complexes(self, complex_ids: list[int]) -> dict[int, ComplexInfo]:
        if not complex_ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(text(_COMPLEXES_SQL), {"ids": list(complex_ids)}).all()
        grouped: dict[int, list] = {}
        for r in rows:
            grouped.setdefault(r.complex_id, []).append(r)
        out = {}
        for cid, rs in grouped.items():
            stops = []
            for r in rs:
                scheduled = sort_route_ids(r.scheduled)
                stops.append(
                    StationStop(
                        r.gtfs_stop_id,
                        typical_route_ids(r.gtfs_stop_id, r.daytime_routes, set(scheduled)),
                        scheduled,
                        r.north_label,
                        r.south_label,
                    )
                )
            out[cid] = ComplexInfo(cid, rs[0].name, rs[0].borough, stops)
        return out

    def stop_names(self, stop_ids: set[str]) -> dict[str, str]:
        """Names for GTFS stop ids; platform ids like "101S" resolve via their parent."""
        if not stop_ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT stop_id, stop_name FROM gtfs.stops WHERE stop_id = ANY(:ids)"),
                {"ids": list(stop_ids)},
            ).all()
        return dict(rows)
