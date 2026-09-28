COMPOSE ?= docker compose
PROD_COMPOSE = $(COMPOSE) -f docker-compose.yml
# Idempotent; also covers db volumes created before the init script existed.
ENSURE_TEST_DB = $(COMPOSE) exec -T db sh -c \
	'psql -q -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d postgres -f /docker-entrypoint-initdb.d/20-test-db.sql'

.PHONY: up up-prod down logs test test-integration test-live lint migrate load shell eval eval-compare

.env:
	cp .env.example .env

up: .env  ## Start the dev stack and wait until every service is healthy
	$(COMPOSE) up -d --build --wait

up-prod: .env  ## Start the production-shaped stack (no dev overrides)
	$(PROD_COMPOSE) up -d --build --wait

down: .env
	$(COMPOSE) down

logs: .env
	$(COMPOSE) logs -f --tail=100

test: .env  ## Fast unit tier: SQLite and stubs, no services needed
	$(COMPOSE) run --rm --no-deps --build api pytest

test-integration: .env  ## Real PostGIS (transit_test) and Redis (db 15)
	$(COMPOSE) up -d --wait db redis
	$(ENSURE_TEST_DB)
	$(COMPOSE) run --rm --no-deps --build api pytest -m integration

test-live: .env  ## Opt-in: loads the real MTA feed into transit_test
	$(COMPOSE) up -d --wait db
	$(ENSURE_TEST_DB)
	$(COMPOSE) run --rm --no-deps --build api pytest -m live -s

eval: .env  ## Agent eval against fixtures with the real model (needs ANTHROPIC_API_KEY). CATEGORY=name filters
	$(COMPOSE) up -d --wait db redis
	$(ENSURE_TEST_DB)
	$(COMPOSE) run --rm --no-deps --build api python -m evals.run $(if $(CATEGORY),--category $(CATEGORY)) $(EVAL_ARGS)

eval-compare: .env  ## Diff the two most recent eval runs
	$(COMPOSE) run --rm --no-deps api python -m evals.compare

lint: .env
	$(COMPOSE) run --rm --no-deps api sh -c "ruff check . && ruff format --check ."

migrate: .env
	$(COMPOSE) run --rm api alembic upgrade head

load: .env  ## Load static GTFS and the stations dataset into the dev database
	$(COMPOSE) run --rm api sh -c "flask gtfs load && flask stations load"

shell: .env
	$(COMPOSE) exec api bash
