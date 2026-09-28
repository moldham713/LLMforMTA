# LLMforMTA

Ask about the MTA schedule in plain English. See [CLAUDE.md](claude.md) for scope and conventions.

## Quickstart

Requires Docker (with Compose v2.24+) and `make`.

```sh
make up        # builds and starts db, redis, api, worker, web; waits until all are healthy
make migrate   # applies Alembic migrations (extensions, analytics + gtfs schemas)
make load      # loads static GTFS and the MTA stations dataset (~45 s)
```

Open http://localhost:8080. The page shows live `/api/health` status for Postgres and Redis.

On first run `make up` copies `.env.example` to `.env`. Edit `.env` to change settings.

## Make targets

| Target         | What it does                                                   |
| -------------- | -------------------------------------------------------------- |
| `make up`      | Dev stack: Vite hot reload + Flask debug on bind-mounted source |
| `make up-prod` | Production-shaped stack: nginx static build + gunicorn         |
| `make down`    | Stop and remove containers (data volume is kept)               |
| `make logs`    | Follow logs for all services                                   |
| `make test`    | Unit tier: SQLite + stubs, no services needed                  |
| `make test-integration` | Integration tier: real PostGIS in `transit_test`      |
| `make test-live` | Opt-in: loads the real MTA feed; prints row counts and timing |
| `make lint`    | `ruff check` + `ruff format --check`                           |
| `make migrate` | `alembic upgrade head`                                         |
| `make load`    | `flask gtfs load` + `flask stations load`                      |
| `make shell`   | Bash shell in the running api container                        |

## Layout

```
backend/    Flask app factory (app/), gunicorn entrypoint (wsgi.py), worker (app/worker.py),
            Alembic migrations (migrations/), tests (tests/)
frontend/   Vite + React + TypeScript; nginx.conf serves the build and proxies /api
docker-compose.yml           production-shaped services
docker-compose.override.yml  dev overrides, applied automatically by `docker compose`
```

## Dev notes

- Ports: web on `8080`, api directly on `8000` (dev only).
- New migration: `make shell`, then `alembic revision --autogenerate -m "..."`.
- No local Python or Node is needed; everything runs in containers.
- Windows: install make with `winget install ezwinports.make`, or run the
  `docker compose` commands from the Makefile directly.

## Static data

- `flask gtfs load [--source URL|PATH] [--force]` loads the subway GTFS feed (`GTFS_URL`,
  default: MTA supplemented feed) into the `gtfs` schema. It stages everything in
  `gtfs_staging`, validates, then swaps in one transaction, so a failed load leaves the
  current data untouched. Unchanged files (same SHA-256) are skipped; every attempt is
  recorded in `gtfs.load_log`.
- `flask stations load [--source URL|PATH]` loads the MTA Subway Stations dataset
  (`STATIONS_URL`), rebuilds `gtfs.station_complexes`, and reseeds aliases from
  `backend/app/stations/aliases.csv`.
- The worker reruns the GTFS load every `GTFS_REFRESH_HOURS` (default 6; `0` disables).
- Debug endpoints: `GET /api/stations/search?q=times sq` and
  `GET /api/stations/nearest?lat=40.7359&lon=-73.9906`.

The integration fixture in `backend/tests/fixtures/` is a slice of the real feed; rebuild it
with `backend/scripts/build_gtfs_fixture.py`.

## Realtime

The worker polls the 8 subway GTFS-rt feeds and the subway alerts feed (JSON variant, which
carries Mercury's `alert_type`) every `RT_POLL_SECONDS`, concurrently, and writes each feed
to its own Redis keys (`rt:{feed}:{stop_id}`), merged on read. Data expires after
`RT_TTL_SECONDS`, so a dead feed ages out without touching the others. The worker's
healthcheck fails only if poll cycles stop completing; feed staleness shows up in
`/api/health` (`"status": "degraded"`, still HTTP 200).

Debug endpoints:

- `GET /api/departures?complex_id=611&route=S&direction=Grand Central`
- `GET /api/alerts?route=A&complex_id=618`
- `GET /api/stations/618/routes` (typical vs. scheduled routes)
- `GET /api/stations/search?q=8th ave&route=L`

To take one feed offline for testing, set `RT_FEED_URL_<FEED>` (e.g. `RT_FEED_URL_ACE`)
to a bad URL for the worker. `backend/app/realtime/nyct_subway_pb2.py` is generated from
MTA's `nyct-subway.proto`; regenerate with `sh scripts/gen_nyct_proto.sh` in the api
container. Realtime test fixtures in `backend/tests/fixtures/realtime/` are raw snapshots
recorded on 2026-09-28 (see `RECORDED`).
