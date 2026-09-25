# LLMforMTA

Ask about the MTA schedule in plain English. See [CLAUDE.md](claude.md) for scope and conventions.

## Quickstart

Requires Docker (with Compose v2.24+) and `make`.

```sh
make up        # builds and starts db, redis, api, worker, web; waits until all are healthy
make migrate   # applies Alembic migrations (PostGIS, pg_trgm, analytics schema)
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
| `make test`    | Run backend pytest suite in the api container                  |
| `make lint`    | `ruff check` + `ruff format --check`                           |
| `make migrate` | `alembic upgrade head`                                         |
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
