# Roadmap

Phase-by-phase build plan. Each phase ends with something demonstrable.

This file is the index: it records *what* each phase covers and *why* the big
calls were made. It is deliberately not enough to implement from. Each phase
gets a full spec in `docs/phases/` — written **just before that phase starts**,
never further ahead, because a spec written four phases early is guessing at
decisions the intervening phases will settle, and a stale spec is worse than no
spec because agents trust it.

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Scaffold, tooling, Docker, Alembic, CI | ✅ complete |
| 1 | Auth & users | ✅ complete — [spec](phases/phase-1-auth.md) |
| 2 | Market data foundation + watchlists | ⬜ |
| 3 | Analysis & charting | ⬜ |
| 4 | Screeners | ⬜ |
| 5 | Alerts, background jobs, real-time | ⬜ |
| 6 | Trading log & performance insights | ⬜ |
| 7 | Polish, hardening, deploy | ⬜ |

## Phase 0 — Scaffold ✅

Monorepo layout · FastAPI app factory with pydantic-settings, structlog, RFC 9457
error handling, health/readiness probes · async SQLAlchemy + Alembic with a
naming convention and a baseline extensions migration · Vite + React + MUI with a
dark-first themed shell, TanStack Query, and a generated typed API client ·
Docker Compose (Postgres + API + web) · ruff/mypy/ESLint/Prettier/pre-commit ·
five-job CI pipeline.

The pipeline was fixed on first execution — it had never actually run, since its
push trigger only matched `main` and no pull request had been opened. Every
component was also pinned to Python 3.14.7, which retires the `uuid-utils`
dependency Phase 1 was going to add: `uuid.uuid7()` is in the standard library
there.

## Phase 1 — Auth & users ✅

**Spec: [`docs/phases/phase-1-auth.md`](phases/phase-1-auth.md)**

`users` + `refresh_tokens` · Argon2 hashing (pwdlib) · JWT access tokens with
rotating refresh tokens and reuse detection · login rate limiting · frontend auth
store, login/register pages, protected routes, 401-refresh-retry interceptor ·
cross-user authorization test harness.

Two things worth carrying forward. The concurrent-401 case needed an explicit
test: because refresh tokens rotate and reuse is treated as theft, a client that
fires one refresh per failed request revokes its own session — and a naive
implementation passes every other assertion. And `email-validator` joined the
dependency list, which the spec's table had missed.

## Phase 2 — Market data + watchlists

`symbols`, `daily_bars`, `daily_indicators`, `latest_quotes` · `MarketDataProvider`
protocol with Finnhub, Tiingo, and Fake implementations · rate limiting, retry,
circuit breaker · universe seed and EOD backfill scripts · server-side indicator
computation · watchlist CRUD and quote grid.

## Phase 3 — Analysis & charting

Symbol detail page · candlestick + volume + indicator overlays · bars and
indicators endpoints.

> Ordered ahead of screeners deliberately: charting consumes the Phase 2 data
> directly and needs no new backend concepts, while screeners depend on a
> complete, reliably-refreshed universe.

## Phase 4 — Screeners

Filter DSL as a Pydantic discriminated union compiled to SQLAlchemy expressions ·
screening runs against local Postgres, never a provider API · saved presets ·
filter-builder UI.

## Phase 5 — Alerts, jobs, real-time

Redis · APScheduler worker container with advisory locks and market-hours gating ·
quote polling, nightly EOD ingest, symbol refresh · alert conditions, trigger
modes, cooldowns, trigger history · email delivery · WebSocket fan-out via Redis
pub/sub · in-app notifications.

## Phase 6 — Trading log & analytics

`trades` + `trade_executions` (position/fills model, so scale-ins and partial
exits work) · per-trade P&L and R-multiple computed at write · analytics computed
on read with SQL over a filtered CTE · equity curve, win rate, profit factor,
expectancy, drawdown, breakdowns by symbol/tag/strategy · journal UI and
analytics dashboard.

## Phase 7 — Polish & deploy

Dashboard · empty/loading/error states · responsive and a11y pass · Playwright
smoke tests · Sentry and metrics · demo seed data · deploy.

## Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Background jobs | APScheduler in a separate container | Cron workload, not a queue. ARQ is the upgrade path; Celery is overkill |
| Journal analytics | On-read SQL, no cache, no materialized views | Data is tiny; MVs cannot be parameterized per user and go stale |
| Trade model | Position + executions | A flat one-row trade breaks on scale-ins and partial exits |
| Screener data | Local Postgres from nightly ingest | No free provider offers usable on-demand screening |
| Live transport | REST + polling now, WebSocket in Phase 5 | Polling is genuinely fine until many symbols are on screen |
| Token storage | Access in memory, refresh in HttpOnly cookie | Immune to XSS token theft |
| Enums | VARCHAR + CHECK + Python StrEnum | Native PG enum values are painful to migrate, and churn is guaranteed |
| API types | Generated from OpenAPI, CI-enforced | Hand-written types drift within weeks |
| Repositories | Only where queries are non-trivial | Repository-per-entity is mostly boilerplate at this scale |
