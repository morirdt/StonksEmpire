# Stonks Empire — developer entrypoints.
# Every command in CLAUDE.md and the README points here.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Docker Desktop on Windows exposes `docker.exe` to WSL even when the WSL
# integration is switched off. Prefer the native binary, fall back to the
# Windows one, so `make up` works in both setups.
DOCKER := $(shell command -v docker 2>/dev/null || command -v docker.exe 2>/dev/null || echo docker)
COMPOSE := $(DOCKER) compose

BACKEND := backend
FRONTEND := frontend

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ setup ---
.PHONY: setup
setup: ## First-time setup: .env, backend venv, frontend deps
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")
	cd $(BACKEND) && uv sync
	cd $(FRONTEND) && pnpm install
	@echo "Setup complete. Next: make up && make migrate"

# ----------------------------------------------------------------- docker ---
.PHONY: up
up: ## Start the full stack (db + api + web)
	$(COMPOSE) up -d --build

.PHONY: up-db
up-db: ## Start Postgres only (for running the API on the host)
	$(COMPOSE) up -d db

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack AND delete the database volume
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs from all services
	$(COMPOSE) logs -f

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

.PHONY: db-shell
db-shell: ## Open psql against the dev database
	$(COMPOSE) exec db psql -U stonks -d stonks_empire

# --------------------------------------------------------------- database ---
.PHONY: migrate
migrate: ## Apply all pending migrations
	cd $(BACKEND) && uv run alembic upgrade head

.PHONY: revision
revision: ## Autogenerate a migration: make revision m="add trades"
	@test -n "$(m)" || (echo 'usage: make revision m="describe the change"' && exit 1)
	cd $(BACKEND) && uv run alembic revision --autogenerate -m "$(m)"

.PHONY: revision-empty
revision-empty: ## Create an empty migration: make revision-empty m="backfill x"
	@test -n "$(m)" || (echo 'usage: make revision-empty m="describe the change"' && exit 1)
	cd $(BACKEND) && uv run alembic revision -m "$(m)"

.PHONY: downgrade
downgrade: ## Roll back one migration
	cd $(BACKEND) && uv run alembic downgrade -1

.PHONY: migration-check
migration-check: ## Fail if models have drifted from migrations
	cd $(BACKEND) && uv run alembic check

# ------------------------------------------------------------------- run ----
.PHONY: dev-api
dev-api: ## Run the API on the host (needs `make up-db`)
	cd $(BACKEND) && uv run uvicorn app.main:app --reload --port 8000

.PHONY: dev-web
dev-web: ## Run the Vite dev server on the host
	cd $(FRONTEND) && pnpm dev

# ------------------------------------------------------------------ test ----
.PHONY: test
test: test-be test-fe ## Run all tests

.PHONY: test-be
test-be: ## Run backend tests
	cd $(BACKEND) && uv run pytest

.PHONY: test-fe
test-fe: ## Run frontend tests
	cd $(FRONTEND) && pnpm test

.PHONY: cov
cov: ## Backend tests with a coverage report
	cd $(BACKEND) && uv run pytest --cov --cov-report=term-missing

# ------------------------------------------------------------ code quality --
.PHONY: lint
lint: ## Lint backend and frontend
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .
	cd $(FRONTEND) && pnpm lint && pnpm format:check

.PHONY: fmt
fmt: ## Auto-format and auto-fix both stacks
	cd $(BACKEND) && uv run ruff check . --fix && uv run ruff format .
	cd $(FRONTEND) && pnpm lint:fix && pnpm format

.PHONY: typecheck
typecheck: ## Type-check both stacks
	cd $(BACKEND) && uv run mypy
	cd $(FRONTEND) && pnpm typecheck

.PHONY: check
check: lint typecheck test ## Everything CI runs

# ------------------------------------------------------------- api client ---
.PHONY: openapi
openapi: ## Export the OpenAPI schema to backend/openapi.json
	cd $(BACKEND) && uv run python -m scripts.export_openapi

.PHONY: gen-api
gen-api: openapi ## Regenerate the typed TypeScript API client
	cd $(FRONTEND) && pnpm gen:api
