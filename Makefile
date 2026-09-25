COMPOSE ?= docker compose
PROD_COMPOSE = $(COMPOSE) -f docker-compose.yml

.PHONY: up up-prod down logs test lint migrate shell

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

test: .env
	$(COMPOSE) run --rm --no-deps --build api pytest

lint: .env
	$(COMPOSE) run --rm --no-deps api sh -c "ruff check . && ruff format --check ."

migrate: .env
	$(COMPOSE) run --rm api alembic upgrade head

shell: .env
	$(COMPOSE) exec api bash
