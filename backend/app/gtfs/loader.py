"""Static GTFS ingestion: download, stage, validate, swap.

Every load builds complete copies of the feed tables in the `gtfs_staging` schema,
validates them, then swaps them into `gtfs` inside the same transaction. Any failure
rolls back the whole thing, so readers only ever see the previous or the new feed.
"""

import csv
import hashlib
import io
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Connection, Engine, MetaData, Table, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.fetch import fetched
from app.gtfs import schema
from app.gtfs.pgcopy import copy_csv_raw
from app.models import NAMING_CONVENTION

log = logging.getLogger(__name__)

STAGING = "gtfs_staging"
# Arbitrary constant identifying the GTFS load in pg_advisory_xact_lock.
ADVISORY_LOCK_KEY = 7_411_001
# A new feed with fewer rows than this fraction of the current one is assumed truncated.
MIN_ROW_RATIO = 0.5
SWAP_LOCK_TIMEOUT = "30s"

FILES = {
    "stops.txt": schema.stops,
    "routes.txt": schema.routes,
    "trips.txt": schema.trips,
    "stop_times.txt": schema.stop_times,
    "calendar.txt": schema.calendar,
    "calendar_dates.txt": schema.calendar_dates,
    "transfers.txt": schema.transfers,
}
REQUIRED_FILES = {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}

# Referential checks run against staging; each query counts violating rows.
ORPHAN_CHECKS = {
    "stop_times.stop_id not in stops": """
        SELECT count(*) FROM {s}.stop_times st
        WHERE NOT EXISTS (SELECT 1 FROM {s}.stops s WHERE s.stop_id = st.stop_id)""",
    "stop_times.trip_id not in trips": """
        SELECT count(*) FROM {s}.stop_times st
        WHERE NOT EXISTS (SELECT 1 FROM {s}.trips t WHERE t.trip_id = st.trip_id)""",
    "trips.route_id not in routes": """
        SELECT count(*) FROM {s}.trips t
        WHERE NOT EXISTS (SELECT 1 FROM {s}.routes r WHERE r.route_id = t.route_id)""",
    "stops.parent_station not in stops": """
        SELECT count(*) FROM {s}.stops c
        WHERE c.parent_station IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM {s}.stops p WHERE p.stop_id = c.parent_station)""",
}


class LoadError(Exception):
    pass


@dataclass
class LoadResult:
    status: str  # success | skipped
    source: str
    file_sha256: str
    feed_version: str | None = None
    row_counts: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    download_seconds: float = 0.0


def load_gtfs(engine: Engine, source: str, *, force: bool = False) -> LoadResult:
    """Load a GTFS zip from a URL or local path.

    Skips when the file hash matches the last successful load, unless `force`.
    `force` also bypasses the row-count shrink check. Raises LoadError on failure,
    after recording it in load_log.
    """
    started = time.monotonic()
    sha = None
    try:
        with fetched(source, "gtfs.zip") as path:
            download_seconds = time.monotonic() - started
            sha = _sha256(path)
            result = _load(engine, source, path, sha, force)
    except Exception as exc:
        duration = time.monotonic() - started
        _record(engine, source, sha, "failed", duration, error=str(exc))
        if isinstance(exc, LoadError):
            raise
        raise LoadError(f"GTFS load failed: {exc}") from exc

    result.download_seconds = round(download_seconds, 2)
    result.duration_seconds = round(time.monotonic() - started, 2)
    _record(
        engine,
        source,
        sha,
        result.status,
        result.duration_seconds,
        feed_version=result.feed_version,
        row_counts=result.row_counts or None,
    )
    return result


def _load(engine: Engine, source: str, path: Path, sha: str, force: bool) -> LoadResult:
    with engine.begin() as conn:
        locked = conn.execute(
            text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}
        ).scalar()
        if not locked:
            raise LoadError("another GTFS load is in progress")

        if not force and sha == _last_success_sha(conn):
            log.info("GTFS unchanged (sha256 %s); skipping", sha[:12])
            return LoadResult(status="skipped", source=source, file_sha256=sha)

        with zipfile.ZipFile(path) as zf:
            members = {Path(n).name: n for n in zf.namelist()}
            missing = REQUIRED_FILES - members.keys()
            if missing:
                raise LoadError(f"feed is missing {sorted(missing)}")

            staged = _create_staging(conn)
            counts = {}
            for filename, table in FILES.items():
                if filename in members:
                    with zf.open(members[filename]) as stream:
                        counts[table.name] = _stage_file(conn, stream, staged[table.name])
                else:
                    counts[table.name] = 0
            feed_version = _feed_version(zf, members)

        counts["stop_routes"] = _build_stop_routes(conn)
        _validate(conn, counts, force)
        for table in staged.values():
            for index in table.indexes:
                conn.execute(CreateIndex(index))
            conn.exec_driver_sql(f"ANALYZE {STAGING}.{table.name}")
        _swap(conn)

    log.info("GTFS loaded: %s", counts)
    return LoadResult(
        status="success",
        source=source,
        file_sha256=sha,
        feed_version=feed_version,
        row_counts=counts,
    )


def _create_staging(conn: Connection) -> dict[str, Table]:
    conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {STAGING} CASCADE")
    conn.exec_driver_sql(f"CREATE SCHEMA {STAGING}")
    # Same naming convention so constraint names match Alembic's after the swap.
    md = MetaData(naming_convention=NAMING_CONVENTION)
    staged = {}
    for table in schema.FEED_TABLES:
        copy = table.to_metadata(md, schema=STAGING)
        # Table only; indexes are built after the bulk load, which is much faster.
        conn.execute(CreateTable(copy))
        staged[table.name] = copy
    return staged


def _stage_file(conn: Connection, stream, table: Table) -> int:
    raw = f"{STAGING}.raw_{table.name}"
    header = set(copy_csv_raw(conn, stream, raw))
    quote = conn.dialect.identifier_preparer.quote_identifier

    targets, exprs = [], []
    for col in table.columns:
        if col.name == "geog" or col.identity is not None:
            continue
        if col.name not in header:
            if not col.nullable:
                raise LoadError(f"{table.name}: required column {col.name!r} missing")
            continue
        sql_type = col.type.compile(dialect=postgresql.dialect())
        targets.append(quote(col.name))
        exprs.append(f"NULLIF(trim({quote(col.name)}), '')::{sql_type}")
    if table.name == "stops":
        targets.append("geog")
        exprs.append(
            "ST_SetSRID(ST_MakePoint(stop_lon::double precision,"
            " stop_lat::double precision), 4326)::geography"
        )

    result = conn.exec_driver_sql(
        f"INSERT INTO {STAGING}.{table.name} ({', '.join(targets)}) "
        f"SELECT {', '.join(exprs)} FROM {raw}"
    )
    conn.exec_driver_sql(f"DROP TABLE {raw}")
    return result.rowcount


def _build_stop_routes(conn: Connection) -> int:
    result = conn.exec_driver_sql(f"""
        INSERT INTO {STAGING}.stop_routes (stop_id, route_id)
        SELECT DISTINCT coalesce(s.parent_station, s.stop_id), t.route_id
        FROM {STAGING}.stop_times st
        JOIN {STAGING}.trips t ON t.trip_id = st.trip_id
        JOIN {STAGING}.stops s ON s.stop_id = st.stop_id
    """)
    return result.rowcount


def _validate(conn: Connection, counts: dict[str, int], force: bool) -> None:
    empty = [t for t in ("stops", "routes", "trips", "stop_times") if counts[t] == 0]
    if counts["calendar"] == 0 and counts["calendar_dates"] == 0:
        empty.append("calendar/calendar_dates")
    if empty:
        raise LoadError(f"feed has no rows in {empty}")

    for name, sql in ORPHAN_CHECKS.items():
        n = conn.exec_driver_sql(sql.format(s=STAGING)).scalar()
        if n:
            raise LoadError(f"{n} rows fail check: {name}")

    if force:
        return
    for table in schema.FEED_TABLES:
        current = conn.exec_driver_sql(f"SELECT count(*) FROM gtfs.{table.name}").scalar()
        if current and counts[table.name] < current * MIN_ROW_RATIO:
            raise LoadError(
                f"{table.name} would shrink from {current} to {counts[table.name]} rows; "
                "use --force if this is expected"
            )


def _swap(conn: Connection) -> None:
    # Fail rather than queue behind a long-running reader; the load is retried next cycle.
    conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'")
    for table in schema.FEED_TABLES:
        conn.exec_driver_sql(f"DROP TABLE gtfs.{table.name}")
        conn.exec_driver_sql(f"ALTER TABLE {STAGING}.{table.name} SET SCHEMA gtfs")
    conn.exec_driver_sql(f"DROP SCHEMA {STAGING} CASCADE")


def _last_success_sha(conn: Connection) -> str | None:
    ll = schema.load_log
    return conn.execute(
        select(ll.c.file_sha256).where(ll.c.status == "success").order_by(ll.c.id.desc()).limit(1)
    ).scalar()


def _record(
    engine: Engine,
    source: str,
    sha: str | None,
    status: str,
    duration: float,
    *,
    feed_version: str | None = None,
    row_counts: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(schema.load_log).values(
                source_url=source,
                file_sha256=sha,
                feed_version=feed_version,
                row_counts=row_counts,
                duration_seconds=round(duration, 2),
                status=status,
                error=error,
            )
        )


def _feed_version(zf: zipfile.ZipFile, members: dict[str, str]) -> str | None:
    if "feed_info.txt" not in members:
        return None
    with zf.open(members["feed_info.txt"]) as f:
        rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    return rows[0].get("feed_version") if rows else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()
