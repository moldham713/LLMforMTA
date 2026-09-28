"""Load the MTA Subway Stations dataset and derive station complexes and aliases."""

import csv
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, Engine, select, text

from app.fetch import fetched
from app.gtfs import schema
from app.gtfs.pgcopy import copy_csv_raw, copy_rows
from app.stations.lookup import normalize_query

log = logging.getLogger(__name__)

DEFAULT_ALIASES = Path(__file__).with_name("aliases.csv")
MIN_STATIONS = 1

BOROUGHS = {"M": "Manhattan", "Bk": "Brooklyn", "Bx": "Bronx", "Q": "Queens", "SI": "Staten Island"}

# Dataset column -> stations column. Dataset headers are human-readable.
COLUMNS = {
    "Station ID": ("station_id", "integer"),
    "Complex ID": ("complex_id", "integer"),
    "GTFS Stop ID": ("gtfs_stop_id", "text"),
    "Stop Name": ("name", "text"),
    "Daytime Routes": ("daytime_routes", "text"),
    "North Direction Label": ("north_label", "text"),
    "South Direction Label": ("south_label", "text"),
    "GTFS Latitude": ("lat", "double precision"),
    "GTFS Longitude": ("lon", "double precision"),
}


class StationsLoadError(Exception):
    pass


@dataclass
class StationsResult:
    stations: int
    complexes: int
    aliases: int
    skipped_aliases: list[str]


def load_stations(
    engine: Engine, source: str, aliases_path: Path = DEFAULT_ALIASES
) -> StationsResult:
    """Replace stations, complexes and aliases in one transaction."""
    with fetched(source, "stations.csv") as path, engine.begin() as conn:
        n_stations = _load_station_rows(conn, path)
        n_complexes = _build_complexes(conn)
        n_aliases, skipped = _load_aliases(conn, aliases_path)
    if skipped:
        log.warning("aliases skipped (unknown complex_id): %s", skipped)
    log.info("stations loaded: %s stations, %s complexes", n_stations, n_complexes)
    return StationsResult(n_stations, n_complexes, n_aliases, skipped)


def stations_loaded(engine: Engine) -> bool:
    with engine.connect() as conn:
        return conn.execute(select(schema.stations.c.station_id).limit(1)).first() is not None


def _load_station_rows(conn: Connection, path: Path) -> int:
    raw = "raw_stations"
    with path.open("rb") as f:
        header = set(copy_csv_raw(conn, f, raw, temporary=True))
    missing = (COLUMNS.keys() | {"Borough"}) - header
    if missing:
        raise StationsLoadError(f"stations dataset missing columns {sorted(missing)}")

    quote = conn.dialect.identifier_preparer.quote_identifier
    targets = [dst for dst, _ in COLUMNS.values()] + ["borough"]
    exprs = [f"NULLIF(trim({quote(src)}), '')::{typ}" for src, (_, typ) in COLUMNS.items()]
    borough_case = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in BOROUGHS.items())
    exprs.append(f'CASE trim("Borough") {borough_case} ELSE trim("Borough") END')

    conn.exec_driver_sql("DELETE FROM gtfs.stations")
    n = conn.exec_driver_sql(
        f"INSERT INTO gtfs.stations ({', '.join(targets)}) SELECT {', '.join(exprs)} FROM {raw}"
    ).rowcount
    if n < MIN_STATIONS:
        raise StationsLoadError("stations dataset is empty")
    return n


def complex_name(station_names: list[str]) -> str:
    """ "14 St" + "8 Av" -> "14 St/8 Av"; the most common station name comes first."""
    counts = Counter(station_names)
    ordered = sorted(counts, key=lambda name: (-counts[name], name))
    return "/".join(ordered)


def _build_complexes(conn: Connection) -> int:
    rows = conn.execute(text("SELECT complex_id, name, borough FROM gtfs.stations")).all()
    names, boroughs = defaultdict(list), defaultdict(list)
    for r in rows:
        names[r.complex_id].append(r.name)
        boroughs[r.complex_id].append(r.borough)

    complexes = []
    for cid in sorted(names):
        name = complex_name(names[cid])
        borough = Counter(boroughs[cid]).most_common(1)[0][0]
        complexes.append((cid, name, normalize_query(name), borough))

    conn.exec_driver_sql("DELETE FROM gtfs.station_complexes")
    copy_rows(
        conn,
        "gtfs.station_complexes",
        ["complex_id", "name", "name_norm", "borough"],
        complexes,
    )
    return len(complexes)


def _load_aliases(conn: Connection, path: Path) -> tuple[int, list[str]]:
    known = set(conn.execute(select(schema.station_complexes.c.complex_id)).scalars())
    rows, skipped, seen = [], [], set()
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            alias, cid = rec["alias"].strip(), int(rec["complex_id"])
            key = (normalize_query(alias), cid)
            if cid not in known:
                skipped.append(f"{alias}->{cid}")
            elif key not in seen:
                seen.add(key)
                rows.append((alias, key[0], cid))

    conn.exec_driver_sql("DELETE FROM gtfs.station_aliases")
    copy_rows(conn, "gtfs.station_aliases", ["alias", "alias_norm", "complex_id"], rows)
    return len(rows), skipped
