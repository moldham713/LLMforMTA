import pytest
from alembic import command
from sqlalchemy import text

from app.gtfs.loader import LoadError, load_gtfs
from tests.integration.conftest import GTFS_DIR, alembic_config, make_gtfs_zip

pytestmark = pytest.mark.integration

FEED_FILES = {
    "stops": "stops.txt",
    "routes": "routes.txt",
    "trips": "trips.txt",
    "stop_times": "stop_times.txt",
    "calendar": "calendar.txt",
    "calendar_dates": "calendar_dates.txt",
    "transfers": "transfers.txt",
}


def fixture_rows(filename: str) -> int:
    with (GTFS_DIR / filename).open(encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


def table_counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM gtfs.{t}")).scalar()
            for t in [*FEED_FILES, "stop_routes"]
        }


def last_log(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT * FROM gtfs.load_log ORDER BY id DESC LIMIT 1")).one()


def test_row_counts_match_fixture(loaded):
    counts = table_counts(loaded)
    for table, filename in FEED_FILES.items():
        assert counts[table] == fixture_rows(filename), table
    assert counts["stop_routes"] > 0


def test_platforms_link_to_parent_station(loaded):
    with loaded.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT stop_id, location_type, parent_station FROM gtfs.stops "
                "WHERE stop_id IN ('127', '127N', '127S') ORDER BY stop_id"
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("127", 1, None),
        ("127N", None, "127"),
        ("127S", None, "127"),
    ]


def test_geography_matches_lat_lon(loaded):
    with loaded.connect() as conn:
        row = conn.execute(
            text(
                "SELECT stop_lat, stop_lon, ST_Y(geog::geometry) AS y, ST_X(geog::geometry) AS x "
                "FROM gtfs.stops WHERE stop_id = '127'"
            )
        ).one()
    assert (row.y, row.x) == pytest.approx((row.stop_lat, row.stop_lon))


def test_times_past_midnight_are_kept(loaded, tmp_path, gtfs_zip):
    stop_times = (GTFS_DIR / "stop_times.txt").read_text()
    trip_id = stop_times.splitlines()[1].split(",")[0]
    late = make_gtfs_zip(
        tmp_path / "late.zip",
        {"stop_times.txt": stop_times + f"{trip_id},127N,25:10:00,25:10:30,999\n"},
    )

    load_gtfs(loaded, str(late))
    with loaded.connect() as conn:
        dep = conn.execute(
            text("SELECT departure_time FROM gtfs.stop_times WHERE stop_sequence = 999")
        ).scalar()
    load_gtfs(loaded, str(gtfs_zip), force=True)

    assert dep.total_seconds() == 25 * 3600 + 10 * 60 + 30


def test_indexes_survive_swap(loaded):
    with loaded.connect() as conn:
        indexes = dict(
            conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'gtfs'")
            ).all()
        )
    assert "(stop_id, departure_time)" in indexes["ix_stop_times_stop_id_departure_time"]
    assert "ix_stop_times_trip_id" in indexes
    assert "USING gist (geog)" in indexes["ix_stops_geog"]
    assert "pk_stop_times" in indexes


def test_schema_matches_models_after_swap(loaded, db_url):
    # Fails if a swap renamed anything or migrations drifted from app/gtfs/schema.py.
    command.check(alembic_config(db_url))


def test_unchanged_feed_is_skipped(loaded, gtfs_zip):
    before = table_counts(loaded)

    result = load_gtfs(loaded, str(gtfs_zip))

    assert result.status == "skipped"
    assert table_counts(loaded) == before
    assert last_log(loaded).status == "skipped"


def test_force_reload_gives_identical_counts(loaded, gtfs_zip):
    before = table_counts(loaded)

    result = load_gtfs(loaded, str(gtfs_zip), force=True)

    assert result.status == "success"
    assert result.row_counts == before
    assert table_counts(loaded) == before
    log = last_log(loaded)
    assert (log.status, log.file_sha256, log.row_counts) == ("success", result.file_sha256, before)
    assert log.feed_version == result.feed_version is not None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"trips.txt": None}, "missing"),
        (
            {
                "stop_times.txt": (GTFS_DIR / "stop_times.txt").read_text()
                + "no-such-trip,127N,08:00:00,08:00:00,1\n"
            },
            "stop_times.trip_id not in trips",
        ),
        ({"stops.txt": "stop_id,stop_name\n127,Times Sq\n"}, "required column 'stop_lat'"),
        ({"calendar_dates.txt": "service_id,date,exception_type\nX,notadate,1\n"}, "date"),
    ],
    ids=["missing-file", "orphan-row", "missing-column", "bad-value"],
)
def test_failed_load_leaves_data_untouched(loaded, tmp_path, overrides, message):
    before = table_counts(loaded)
    bad = make_gtfs_zip(tmp_path / "bad.zip", overrides)

    with pytest.raises(LoadError, match=message):
        load_gtfs(loaded, str(bad))

    assert table_counts(loaded) == before
    log = last_log(loaded)
    assert log.status == "failed"
    assert message in log.error
    with loaded.connect() as conn:
        staging = conn.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = 'gtfs_staging'")
        ).first()
    assert staging is None


def test_truncated_feed_is_rejected_unless_forced(loaded, tmp_path, gtfs_zip):
    lines = (GTFS_DIR / "stop_times.txt").read_text().splitlines(keepends=True)
    small = make_gtfs_zip(tmp_path / "small.zip", {"stop_times.txt": "".join(lines[:11])})
    before = table_counts(loaded)

    with pytest.raises(LoadError, match="would shrink"):
        load_gtfs(loaded, str(small))
    assert table_counts(loaded) == before

    assert load_gtfs(loaded, str(small), force=True).row_counts["stop_times"] == 10
    # Restore the full fixture for the rest of the session.
    assert load_gtfs(loaded, str(gtfs_zip), force=True).row_counts == before


def test_cli_load_twice_skips_second(app, gtfs_zip):
    runner = app.test_cli_runner()

    first = runner.invoke(args=["gtfs", "load", "--source", str(gtfs_zip), "--force"])
    second = runner.invoke(args=["gtfs", "load", "--source", str(gtfs_zip)])

    assert first.exit_code == 0, first.output
    assert "status: success" in first.output
    assert second.exit_code == 0, second.output
    assert "status: skipped" in second.output


def test_cli_reports_failure(app, tmp_path):
    bad = make_gtfs_zip(tmp_path / "bad.zip", {"routes.txt": None})

    result = app.test_cli_runner().invoke(args=["gtfs", "load", "--source", str(bad)])

    assert result.exit_code != 0
    assert "missing" in result.output
