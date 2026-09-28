"""Tables in the `gtfs` schema.

Feed tables are replaced wholesale on every load (see loader.py). Station tables come
from the MTA Subway Stations dataset and survive feed reloads.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Double,
    Float,
    Identity,
    Index,
    Integer,
    Interval,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import base as postgresql_base
from sqlalchemy.types import UserDefinedType

from app.models import Base

SCHEMA = "gtfs"
metadata = Base.metadata


class Geography(UserDefinedType):
    """PostGIS geography(Point, 4326); defined inline to avoid a GeoAlchemy dependency."""

    cache_ok = True

    def __init__(self, *args):
        # Reflection passes the declared ("Point", "4326"); only that form is used here.
        pass

    def get_col_spec(self, **kw):
        return "geography(Point,4326)"


# Lets reflection (and so `alembic check`) recognise the column instead of warning.
postgresql_base.ischema_names["geography"] = Geography


# --- Feed tables -----------------------------------------------------------------

stops = Table(
    "stops",
    metadata,
    Column("stop_id", Text, primary_key=True),
    Column("stop_name", Text, nullable=False),
    Column("stop_lat", Double, nullable=False),
    Column("stop_lon", Double, nullable=False),
    # 1 = parent station; NULL/0 = platform (e.g. 127N/127S under parent 127)
    Column("location_type", SmallInteger),
    Column("parent_station", Text),
    Column("geog", Geography, nullable=False),
    Index("ix_stops_geog", "geog", postgresql_using="gist"),
    Index("ix_stops_parent_station", "parent_station"),
    schema=SCHEMA,
)

routes = Table(
    "routes",
    metadata,
    Column("route_id", Text, primary_key=True),
    Column("agency_id", Text),
    Column("route_short_name", Text),
    Column("route_long_name", Text),
    Column("route_type", SmallInteger, nullable=False),
    Column("route_desc", Text),
    Column("route_url", Text),
    Column("route_color", Text),
    Column("route_text_color", Text),
    Column("route_sort_order", Integer),
    schema=SCHEMA,
)

trips = Table(
    "trips",
    metadata,
    Column("trip_id", Text, primary_key=True),
    Column("route_id", Text, nullable=False),
    Column("service_id", Text, nullable=False),
    Column("trip_headsign", Text),
    Column("direction_id", SmallInteger),
    Column("shape_id", Text),
    Index("ix_trips_route_id", "route_id"),
    schema=SCHEMA,
)

stop_times = Table(
    "stop_times",
    metadata,
    Column("trip_id", Text, nullable=False),
    Column("stop_id", Text, nullable=False),
    # interval, not time: GTFS allows values past 24:00:00 for after-midnight service.
    Column("arrival_time", Interval),
    Column("departure_time", Interval),
    Column("stop_sequence", Integer, nullable=False),
    PrimaryKeyConstraint("trip_id", "stop_sequence"),
    Index("ix_stop_times_stop_id_departure_time", "stop_id", "departure_time"),
    Index("ix_stop_times_trip_id", "trip_id"),
    schema=SCHEMA,
)

calendar = Table(
    "calendar",
    metadata,
    Column("service_id", Text, primary_key=True),
    Column("monday", Boolean, nullable=False),
    Column("tuesday", Boolean, nullable=False),
    Column("wednesday", Boolean, nullable=False),
    Column("thursday", Boolean, nullable=False),
    Column("friday", Boolean, nullable=False),
    Column("saturday", Boolean, nullable=False),
    Column("sunday", Boolean, nullable=False),
    Column("start_date", Date, nullable=False),
    Column("end_date", Date, nullable=False),
    schema=SCHEMA,
)

calendar_dates = Table(
    "calendar_dates",
    metadata,
    Column("service_id", Text, nullable=False),
    Column("date", Date, nullable=False),
    Column("exception_type", SmallInteger, nullable=False),
    PrimaryKeyConstraint("service_id", "date"),
    schema=SCHEMA,
)

transfers = Table(
    "transfers",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("from_stop_id", Text, nullable=False),
    Column("to_stop_id", Text, nullable=False),
    Column("transfer_type", SmallInteger, nullable=False),
    Column("min_transfer_time", Integer),
    Index("ix_transfers_from_stop_id", "from_stop_id"),
    schema=SCHEMA,
)

# Derived from stop_times at load time so lookups never scan stop_times.
stop_routes = Table(
    "stop_routes",
    metadata,
    Column("stop_id", Text, nullable=False),  # parent station
    Column("route_id", Text, nullable=False),
    PrimaryKeyConstraint("stop_id", "route_id"),
    schema=SCHEMA,
)

FEED_TABLES = [stops, routes, trips, stop_times, calendar, calendar_dates, transfers, stop_routes]

load_log = Table(
    "load_log",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("source_url", Text, nullable=False),
    Column("file_sha256", Text),
    Column("feed_version", Text),
    Column("row_counts", JSONB),
    Column("duration_seconds", Float),
    Column("status", Text, nullable=False),  # success | failed | skipped
    Column("error", Text),
    Index("ix_load_log_status_id", "status", "id"),
    schema=SCHEMA,
)

# --- Station tables (MTA Subway Stations dataset) -----------------------------------

stations = Table(
    "stations",
    metadata,
    # Station ID is not unique: e.g. W 4 St has one ID but separate A C E and B D F M stops.
    Column("gtfs_stop_id", Text, primary_key=True),
    Column("station_id", Integer, nullable=False),
    Column("complex_id", Integer, nullable=False),
    Column("name", Text, nullable=False),
    Column("borough", Text, nullable=False),
    Column("daytime_routes", Text),
    Column("north_label", Text),
    Column("south_label", Text),
    Column("lat", Double, nullable=False),
    Column("lon", Double, nullable=False),
    Index("ix_stations_complex_id", "complex_id"),
    schema=SCHEMA,
)

station_complexes = Table(
    "station_complexes",
    metadata,
    Column("complex_id", Integer, primary_key=True, autoincrement=False),
    Column("name", Text, nullable=False),
    Column("name_norm", Text, nullable=False),
    Column("borough", Text, nullable=False),
    schema=SCHEMA,
)

station_aliases = Table(
    "station_aliases",
    metadata,
    Column("alias", Text, nullable=False),
    Column("alias_norm", Text, nullable=False),
    Column("complex_id", Integer, nullable=False),
    PrimaryKeyConstraint("alias_norm", "complex_id"),
    schema=SCHEMA,
)
