# Stonks Empire

Personal stock-trading utilities: watchlists, screeners, alerts, charting, and a
trading journal with performance analytics.

**Stack:** React + MUI (Vite) · FastAPI · PostgreSQL · Alembic · Docker Compose

> Status: **Phase 0 — scaffolding complete.** Features land phase by phase; see
> [docs/ROADMAP.md](docs/ROADMAP.md).

## Quick start

```bash
make setup      # .env, backend venv, frontend deps
make up         # Postgres + API + web, via Docker
make migrate    # apply database migrations
```

- Web: <http://localhost:5173>
- API: <http://localhost:8000>
- API docs: <http://localhost:8000/docs>
- Postgres: `localhost:5433` (not 5432 — see below)

Run `make help` for every available command.

### Running without Docker

```bash
make up-db      # Postgres only
make migrate
make dev-api    # http://localhost:8000
make dev-web    # http://localhost:5173
```

## Repository layout

```
backend/    FastAPI app, SQLAlchemy models, Alembic migrations, pytest suite
frontend/   Vite + React + MUI, TanStack Query, typed API client
infra/      Container-support files (Postgres init)
docs/       Roadmap and design notes
```

## Things worth knowing

- **Postgres is published on host port 5433**, not 5432, so it cannot collide
  with a Postgres already running on the host. Inside the Compose network the
  API still reaches it as `db:5432`.
- **`.env` lives at the repo root only.** The backend resolves it by path, not
  by working directory, so `make` targets work from anywhere.
- **The TypeScript API client is generated** from the backend's OpenAPI schema.
  After changing any route or schema, run `make gen-api` and commit the result —
  CI fails if it is stale.
- **Never edit `frontend/src/lib/api/schema.ts`** by hand.

## Checks

```bash
make lint       # ruff + eslint + prettier
make typecheck  # mypy (strict) + tsc
make test       # pytest + vitest
make check      # everything CI runs
```

## License

Private, personal project.
