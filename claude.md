# NYC Transit Assistant

A rider says in plain English where they are and what train they're waiting for.
The app returns the next 3 departures and any active service alerts for that line.
Subway first; buses and SMS come in later phases.

## Stack
- Backend: Python 3.12, Flask (app factory), SQLAlchemy 2.x + Alembic, gunicorn
- Frontend: React + TypeScript (Vite)
- Postgres 16 + PostGIS + pg_trgm: static GTFS, `analytics` schema
- Redis: realtime cache, chat sessions, log queue
- LLM: Anthropic API with tool use; model name comes from an env var
- Docker Compose services: db, redis, api, worker, web

## Data sources
- Subway static GTFS from MTA developer resources (use the supplemented feed)
- Subway realtime GTFS-rt protobuf, no API key needed:
  api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs[ ,-ace,-bdfm,-g,-jz,-nqrw,-l,-si]
- Subway alerts GTFS-rt feed (camsys/subway-alerts)
- MTA Subway Stations dataset (data.ny.gov): station complexes, direction labels

## Conventions
- All config comes from env vars; .env.example is the source of truth
- Every module gets pytest tests; network calls use recorded fixtures
- The LLM never generates arrival times or alert text; it only relays tool output
- Stay lightweight: ask before adding new services or major dependencies
- Comments should be short and sweet; never comment about what changed, only about why things are they way they are