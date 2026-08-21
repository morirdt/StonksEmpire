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
| 2 | Market data foundation + watchlists | ✅ complete — [spec](phases/phase-2-market-data.md) |
| 3 | Analysis & charting | ✅ complete — [spec](phases/phase-3-charting.md) |
| 4 | Screeners | ⬜ next |
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

## Phase 2 — Market data + watchlists ✅

**Spec: [`docs/phases/phase-2-market-data.md`](phases/phase-2-market-data.md)**

`symbols`, `daily_bars`, `daily_indicators`, `latest_quotes` · `MarketDataProvider`
protocol with Finnhub, Tiingo, and Fake implementations · rate limiting, retry,
circuit breaker · universe seed and EOD backfill scripts · server-side indicator
computation · watchlist CRUD and quote grid.

> Three loosely coupled pieces — provider layer, data layer, feature — and only
> the last is user-visible. The `Fake` provider is what lets the other two be
> built and tested without a network or an API key, so it is not a testing
> afterthought: it is the default.

Built with **no provider account**, which turned out to matter less than
expected: `Fake` is the only implementation, and Finnhub and Tiingo remain
specified behind the protocol. Nothing downstream knows the difference. The one
consequence is that the ~530-name universe ships as a CSV in the repo, because
`list_symbols()` had no upstream to ask.

Three things worth carrying forward:

- **`latest_quotes` is written by a Core upsert, which the ORM identity map
  knows nothing about.** With `expire_on_commit=False`, the grid joined in a
  stale quote, refreshed it, and then rendered the pre-refresh price — polling
  looked exactly like a provider that had stopped updating. Both quote reads
  use `populate_existing` now.
- **Indicators recompute over the whole stored series, not the fetched window.**
  Otherwise a 200-day average that only sees new bars is not one, and the
  leading rows of an appended window stay null forever.
- **A provider outage degrades rather than fails.** `/ready` reports it as a
  named check without going red, and stale quotes are served with an honest
  timestamp instead of a blank grid.

## Phase 3 — Analysis & charting ✅

**Spec: [`docs/phases/phase-3-charting.md`](phases/phase-3-charting.md)**

Symbol detail page · candlestick + volume + indicator overlays · bars and
indicators endpoints · `user_chart_preferences`.

> Ordered ahead of screeners deliberately: charting consumes the Phase 2 data
> directly and needs no new backend concepts, while screeners depend on a
> complete, reliably-refreshed universe.

`lightweight-charts` over ECharts, visx, and Recharts: purpose-built for this
one chart, ~45kb, and the alternatives all mean building a candlestick series by
hand or paying for a superset nothing here needs. The costs are real and
accepted — it is imperative, it renders to canvas (so tests cover data shaping
and controls, never pixels), and every colour must be handed to it from the
theme.

The chart's colours were **computed, not chosen**. Running the `dataviz`
validator against this project's own surfaces turned up something worth knowing:
the existing `profit`/`loss` tokens sit at **ΔE 4.4 under deuteranopia**, so
red-green candles alone are unreadable for a substantial minority. The spec
therefore requires fill as a second encoding — hollow up candles, filled down
candles — rather than repainting the palette. The Phase 2 quote grid is
unaffected: it already signs its numbers, which does the same job.

Three things worth carrying forward:

- **Re-running the validator was worth it.** Two of the spec's dark overlay
  values failed the lightness-band check that the spec's own figures had not
  reported, and were snapped to the nearest passing steps before any chart code
  existed. Colour is the one part of a chart that is computable — so compute it,
  every time, rather than trusting a number written down earlier.
- **Fixed per-identity colour caps the number of overlay identities, not just
  the number shown at once.** Three validated slots plus "colour never follows
  position" means there can be exactly three overlays to choose from, which is
  why `PriceOverlay` is the three SMAs and the stored EMAs are not offered.
- **The cross-user harness needed a second shape.** A `/me/...` resource has no
  id to forge and correctly answers 200, so `CROSS_USER_RESOURCES` cannot
  express it. `CALLER_SCOPED_RESOURCES` asserts the failure it can actually
  have: one user's write landing on another user's row.

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
