# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Stonks Empire: stock-trading utilities (watchlists, screeners, alerts, charting,
trading journal). React + MUI frontend, FastAPI + PostgreSQL backend.

The build is phased. `docs/ROADMAP.md` is the index of phases and the decisions
behind them; the phase being built has a full spec in `docs/phases/`. **Read the
current phase's spec before implementing it** — the roadmap alone is a summary
and will leave you inventing details that are already decided.

## Commands

`make help` lists everything. Most-used:

```bash
make setup           # .env + backend venv + frontend deps
make up              # full stack in Docker; make up-db for Postgres only
make migrate         # alembic upgrade head
make revision m="…"  # autogenerate a migration
make check           # everything CI runs: lint + typecheck + test
make gen-api         # regenerate the typed TS client after any API change
```

Single tests:

```bash
cd backend  && uv run pytest tests/unit/test_config.py::test_docs_are_disabled_in_production
cd frontend && pnpm vitest run src/lib/api/client.test.ts
cd frontend && pnpm vitest run -t "shows the API identity"
```

Backend commands need `uv` on PATH (`~/.local/bin`). `make` auto-detects
`docker` vs `docker.exe`, so targets work whether or not Docker Desktop's WSL
integration is enabled.

## Environment specifics

- **Postgres is published on host port 5433**, not 5432 — the host already runs
  a Postgres on 5432. Inside Compose the API still reaches it at `db:5432`.
- **`.env` lives only at the repo root.** `app/core/config.py` resolves it by
  path relative to the source file, not the working directory, so `make` targets
  work from anywhere. A `backend/.env` overrides it if present.
- The working tree is on a Windows drive mounted into WSL, where small-file I/O
  is ~150× slower than the Linux filesystem. `pnpm install` takes minutes; this
  is expected. `frontend/.npmrc` sets `node-linker=hoisted` — do **not** add
  `package-import-method=copy`, which makes it far slower (the pnpm store is on
  the same drive, so hardlinks work).

## Backend architecture

Layering is `route → service → repository → SQLAlchemy`:

- Routes are thin — parse, call one service method, return a response model.
- **Services raise domain errors from `app/core/exceptions.py`, never
  `HTTPException`.** `app/api/errors.py` translates them into RFC 9457
  `application/problem+json`; every error response in the app shares that one
  shape, including validation and unhandled errors.
- Repositories exist only where querying is non-trivial. Simple CRUD uses the
  session directly — do not add a repository per entity.
- `get_db` does **not** commit. Services own their transaction boundary.

Cross-cutting:

- Settings come from `get_settings()` (cached) and nothing else. Nested groups
  use a double underscore: `DB__HOST` → `settings.db.host`. Production
  hardening is enforced by a validator that fails startup.
- `CorrelationIdMiddleware` binds a correlation id to every log line and echoes
  it as `X-Request-ID`; it appears in every error body. structlog renders
  console locally, JSON in prod, and redacts a fixed set of sensitive keys.
- `/health` (liveness, no dependencies) and `/ready` (checks dependencies) are
  unversioned and mounted outside `/api`.

### Data conventions

These bind every table; follow them without being asked.

- **Primary keys are UUIDv7**, via `uuid_utils.compat.uuid7` (returns a real
  `uuid.UUID`, so SQLAlchemy's `Uuid` type handles it natively). Time-ordered,
  so they index well and do not leak row counts. `uuid.uuid7()` arrives in
  Python 3.14 — drop the dependency then.
- **All timestamps are `TIMESTAMPTZ` stored in UTC.** Use `TimestampMixin` from
  `app/db/base.py` for `created_at`/`updated_at`; the database maintains them.
- **Money and prices are `NUMERIC`, never float** — `NUMERIC(18, 6)` for prices
  and quantities, `NUMERIC(18, 2)` for money. They map to Python `Decimal`;
  never round-trip them through `float`.
- **Every user-scoped table carries a `user_id` FK** with `ON DELETE CASCADE`
  and a composite index leading with `user_id`.
- Email addresses use `citext` (the extension is enabled in the baseline
  migration) so uniqueness and lookup are case-insensitive.

### Dependency policy

Some popular packages are deliberately banned here:

- **Never `passlib`** — unmaintained since 2020 and warns on Python 3.13+.
  Use `pwdlib[argon2]` (Argon2id).
- **Never `python-jose`** — stale, with a CVE history. Use `PyJWT`.
- **Never `moment`, `create-react-app`, or `react-router-dom`** (v7+ renamed the
  package to `react-router`).

### Database and migrations

- **Every model module must be imported in `app/models/__init__.py`.** Alembic's
  `env.py` imports that package to populate metadata; a model missing from it is
  invisible to autogenerate, and the next migration will drop its table.
- The `MetaData` naming convention in `app/db/base.py` must not change — it is
  baked into existing migrations and keeps autogenerate diffs stable.
- Migration URLs come from `app.core.config`, never `alembic.ini`.
- Always review autogenerated migrations, give them a real `downgrade()`, and
  keep schema and data changes in separate revisions. CI runs `alembic check`
  for drift plus a full `downgrade base` → `upgrade head` round-trip.
- Enums are `VARCHAR` + CHECK constraint + a Python `StrEnum`, not native
  Postgres enums — enum values change often here and `ALTER TYPE` is painful.

## Frontend architecture

- Feature-first under `src/features/<feature>/{api,components,hooks,routes}`.
  Features may import from `components/`, `lib/`, `theme/` — **never from
  another feature.** Shared code moves up instead.
- `@/` aliases `src/`. Use it for cross-directory imports; keep sibling and
  child imports relative.
- Server state is TanStack Query. All query keys are built in
  `src/lib/queryKeys.ts` so invalidation stays precise.
- `src/lib/api/client.ts` normalises every non-2xx response into an `ApiError`
  carrying `code` and `correlationId`, so components never branch on
  `data`/`error` shapes.
- Theme is dark-first with `profit`/`loss` as real palette tokens — use those
  rather than green/red literals. Numerals render `tabular-nums` so price and
  P&L columns line up.

### Generated API client

`src/lib/api/schema.ts` is generated from the backend's OpenAPI schema — never
edit it. After changing any route, schema, or response model, run `make gen-api`
and commit the result; CI fails if it is stale. Generation reads
`backend/openapi.json` (exported from the app object), so it needs no running
server.

## Version constraints worth knowing

- **TypeScript is pinned to 6.x on purpose.** `openapi-typescript` 7.13 crashes
  on TypeScript 7 (`ts.factory` is undefined under the new compiler). Do not
  bump it until that is fixed upstream.
- MUI is v9: layout system props (`alignItems`, `flexWrap`, …) are no longer
  accepted directly on `Stack` — put them in `sx`.
- Config tests build `Settings` with `_env_file=None` so they test declared
  defaults rather than the developer's `.env`. Note `os.environ` outranks both,
  so avoid pinning config values in `conftest.py` unless every test wants them.
