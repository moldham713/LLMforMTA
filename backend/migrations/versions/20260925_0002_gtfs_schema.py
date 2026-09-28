"""gtfs schema: feed tables, load log, stations, complexes, aliases

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "gtfs"
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class Geography(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "geography(Point,4326)"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")

    op.create_table(
        "stops",
        sa.Column("stop_id", sa.Text(), nullable=False),
        sa.Column("stop_name", sa.Text(), nullable=False),
        sa.Column("stop_lat", sa.Double(), nullable=False),
        sa.Column("stop_lon", sa.Double(), nullable=False),
        sa.Column("location_type", sa.SmallInteger(), nullable=True),
        sa.Column("parent_station", sa.Text(), nullable=True),
        sa.Column("geog", Geography(), nullable=False),
        sa.PrimaryKeyConstraint("stop_id", name="pk_stops"),
        schema=S,
    )
    op.create_index("ix_stops_geog", "stops", ["geog"], schema=S, postgresql_using="gist")
    op.create_index("ix_stops_parent_station", "stops", ["parent_station"], schema=S)

    op.create_table(
        "routes",
        sa.Column("route_id", sa.Text(), nullable=False),
        sa.Column("agency_id", sa.Text(), nullable=True),
        sa.Column("route_short_name", sa.Text(), nullable=True),
        sa.Column("route_long_name", sa.Text(), nullable=True),
        sa.Column("route_type", sa.SmallInteger(), nullable=False),
        sa.Column("route_desc", sa.Text(), nullable=True),
        sa.Column("route_url", sa.Text(), nullable=True),
        sa.Column("route_color", sa.Text(), nullable=True),
        sa.Column("route_text_color", sa.Text(), nullable=True),
        sa.Column("route_sort_order", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("route_id", name="pk_routes"),
        schema=S,
    )

    op.create_table(
        "trips",
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("route_id", sa.Text(), nullable=False),
        sa.Column("service_id", sa.Text(), nullable=False),
        sa.Column("trip_headsign", sa.Text(), nullable=True),
        sa.Column("direction_id", sa.SmallInteger(), nullable=True),
        sa.Column("shape_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("trip_id", name="pk_trips"),
        schema=S,
    )
    op.create_index("ix_trips_route_id", "trips", ["route_id"], schema=S)

    op.create_table(
        "stop_times",
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("stop_id", sa.Text(), nullable=False),
        sa.Column("arrival_time", sa.Interval(), nullable=True),
        sa.Column("departure_time", sa.Interval(), nullable=True),
        sa.Column("stop_sequence", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("trip_id", "stop_sequence", name="pk_stop_times"),
        schema=S,
    )
    op.create_index(
        "ix_stop_times_stop_id_departure_time",
        "stop_times",
        ["stop_id", "departure_time"],
        schema=S,
    )
    op.create_index("ix_stop_times_trip_id", "stop_times", ["trip_id"], schema=S)

    op.create_table(
        "calendar",
        sa.Column("service_id", sa.Text(), nullable=False),
        *[sa.Column(day, sa.Boolean(), nullable=False) for day in DAYS],
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("service_id", name="pk_calendar"),
        schema=S,
    )

    op.create_table(
        "calendar_dates",
        sa.Column("service_id", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("exception_type", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("service_id", "date", name="pk_calendar_dates"),
        schema=S,
    )

    op.create_table(
        "transfers",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("from_stop_id", sa.Text(), nullable=False),
        sa.Column("to_stop_id", sa.Text(), nullable=False),
        sa.Column("transfer_type", sa.SmallInteger(), nullable=False),
        sa.Column("min_transfer_time", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_transfers"),
        schema=S,
    )
    op.create_index("ix_transfers_from_stop_id", "transfers", ["from_stop_id"], schema=S)

    op.create_table(
        "stop_routes",
        sa.Column("stop_id", sa.Text(), nullable=False),
        sa.Column("route_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("stop_id", "route_id", name="pk_stop_routes"),
        schema=S,
    )

    op.create_table(
        "load_log",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.Text(), nullable=True),
        sa.Column("feed_version", sa.Text(), nullable=True),
        sa.Column("row_counts", postgresql.JSONB(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_load_log"),
        schema=S,
    )
    op.create_index("ix_load_log_status_id", "load_log", ["status", "id"], schema=S)

    op.create_table(
        "stations",
        sa.Column("gtfs_stop_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("complex_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("borough", sa.Text(), nullable=False),
        sa.Column("daytime_routes", sa.Text(), nullable=True),
        sa.Column("north_label", sa.Text(), nullable=True),
        sa.Column("south_label", sa.Text(), nullable=True),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("gtfs_stop_id", name="pk_stations"),
        schema=S,
    )
    op.create_index("ix_stations_complex_id", "stations", ["complex_id"], schema=S)

    op.create_table(
        "station_complexes",
        sa.Column("complex_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.Column("borough", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("complex_id", name="pk_station_complexes"),
        schema=S,
    )

    op.create_table(
        "station_aliases",
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("alias_norm", sa.Text(), nullable=False),
        sa.Column("complex_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("alias_norm", "complex_id", name="pk_station_aliases"),
        schema=S,
    )


def downgrade() -> None:
    op.execute(f"DROP SCHEMA IF EXISTS {S} CASCADE")
