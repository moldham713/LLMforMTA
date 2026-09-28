"""Integration tier: real Postgres/PostGIS in the `transit_test` database.

Each session drops our schemas and re-runs every Alembic migration, so runs start clean.
"""

import os
import zipfile
from pathlib import Path

import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, make_url, text

from app import create_app
from app.config import Settings
from app.extensions import make_engine
from app.gtfs.loader import load_gtfs
from app.realtime.feeds import TRIP_FEEDS
from app.realtime.parse import parse_alerts, parse_trip_updates
from app.realtime.store import RealtimeStore
from app.stations.loader import load_stations

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "tests" / "fixtures"
GTFS_DIR = FIXTURES / "gtfs"
STATIONS_CSV = FIXTURES / "stations.csv"
REALTIME_DIR = FIXTURES / "realtime"
# Newest header in the recorded realtime snapshots; tests freeze the clock here.
FROZEN_NOW = 1790618392


def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or (
        make_url(os.environ["DATABASE_URL"])
        .set(database="transit_test")
        .render_as_string(hide_password=False)
    )
    # The session fixture drops schemas; never let it point at a real database.
    if not make_url(url).database.endswith("_test"):
        raise RuntimeError(f"refusing to run integration tests against {url!r}")
    return url


test_database_url.__test__ = False  # not a test, despite the name


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["skip_logging"] = True
    return cfg


def make_gtfs_zip(dest: Path, overrides: dict[str, str | None] | None = None) -> Path:
    """Zip the fixture feed; `overrides` replaces (str) or drops (None) member files."""
    overrides = overrides or {}
    with zipfile.ZipFile(dest, "w") as zf:
        for path in sorted(GTFS_DIR.glob("*.txt")):
            if path.name in overrides:
                continue
            zf.write(path, path.name)
        for name, content in overrides.items():
            if content is not None:
                zf.writestr(name, content)
    return dest


@pytest.fixture(scope="session")
def db_url() -> str:
    return test_database_url()


@pytest.fixture(scope="session")
def engine(db_url) -> Engine:
    eng = make_engine(db_url)
    with eng.begin() as conn:
        for schema in ("gtfs", "gtfs_staging", "analytics"):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    command.upgrade(alembic_config(db_url), "head")
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def gtfs_zip(tmp_path_factory) -> Path:
    return make_gtfs_zip(tmp_path_factory.mktemp("gtfs") / "fixture.zip")


@pytest.fixture(scope="session")
def loaded(engine, gtfs_zip) -> Engine:
    """Fixture feed and stations loaded once per session."""
    load_gtfs(engine, str(gtfs_zip))
    load_stations(engine, str(STATIONS_CSV))
    return engine


@pytest.fixture
def app(loaded, db_url):
    app = create_app(Settings(database_url=db_url, redis_url="redis://unused:6379/0"))
    app.config["TESTING"] = True
    return app


def test_redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL") or "redis://redis:6379/15"
    # Tests flush this database; keep them off the app's db 0.
    if url.rstrip("/").endswith("/0"):
        raise RuntimeError(f"refusing to run integration tests against {url!r}")
    return url


test_redis_url.__test__ = False


@pytest.fixture
def redis_client() -> redis.Redis:
    client = redis.Redis.from_url(test_redis_url())
    client.flushdb()
    yield client
    client.flushdb()


@pytest.fixture
def store(redis_client) -> RealtimeStore:
    return RealtimeStore(redis_client, ttl_seconds=180)


def load_realtime_fixtures(store: RealtimeStore, now: float = FROZEN_NOW) -> None:
    """Parse every recorded snapshot and write it as the worker would at `now`."""
    for feed in TRIP_FEEDS:
        header_ts, arrivals = parse_trip_updates(
            (REALTIME_DIR / f"{feed}.pb").read_bytes(), feed, now
        )
        store.write_arrivals(feed, header_ts, arrivals, now)
    header_ts, alerts = parse_alerts((REALTIME_DIR / "alerts.json").read_bytes(), now)
    store.write_alerts(header_ts, alerts, now)


@pytest.fixture
def rt_store(loaded, store) -> RealtimeStore:
    load_realtime_fixtures(store)
    return store


@pytest.fixture
def rt_app(app, redis_client, rt_store):
    """App wired to the fixture Postgres and Redis, with the clock frozen."""
    app.extensions["redis"] = redis_client
    app.extensions["clock"] = lambda: FROZEN_NOW
    return app
