# Phase 2 — Market Data & Watchlists

Implementable spec. Read `CLAUDE.md` first for data conventions, the dependency
policy, and the service/error layering rules — this document does not repeat
them. Phase 1 settled the auth conventions this phase builds on: every route
here is user-scoped through `CurrentUser`.

**Goal:** a signed-in user can search for a symbol, put it on a watchlist, and
see a live-ish quote grid. Behind that, the database becomes the system of
record for market data — which is what makes Phase 3 charting and Phase 4
screening possible without hammering a provider API.

## The shape of this phase

Phase 1 was one cohesive feature. This one is three, and they are worth keeping
mentally separate because only the last is user-visible:

1. **The provider layer** — an interface over an unreliable third party.
2. **The data layer** — symbols, bars, indicators, quotes, and the scripts that
   fill them.
3. **The feature** — watchlists and the quote grid.

Build them in that order. The provider layer has a `Fake` implementation, so
everything downstream can be developed and tested without a network or an API
key.

## Dependencies to add

Backend (`cd backend && uv add …`):

| Package | Purpose |
|---|---|
| `httpx` | Provider HTTP client. **Currently a dev-only dependency** — it moves into the main list |
| `tenacity` | Retry with exponential backoff and jitter |

Frontend (`cd frontend && pnpm add …`):

| Package | Purpose |
|---|---|
| `@mui/x-data-grid` | The quote grid — sorting, column sizing, virtualisation |

**No pandas, no numpy.** Prices are `NUMERIC` and `CLAUDE.md` forbids round-tripping
them through `float`; both libraries would force exactly that. Indicators are
plain arithmetic over a few hundred rows, so compute them in `Decimal`. The
dependency cost and the precision cost both point the same way.

**Circuit breaking is hand-rolled**, not a library. It needs per-provider state
and has to be inspectable from `/ready`, which is a poor fit for the decorator
shape the libraries offer. `tenacity` earns its place because backoff jitter is
easy to get subtly wrong; a breaker is a small state machine.

## Configuration

New nested settings group, `market_data` (`MARKET_DATA__*`):

| Setting | Default | Notes |
|---|---|---|
| `provider` | `fake` | `finnhub` \| `tiingo` \| `fake` |
| `finnhub_api_key` | `None` | `SecretStr` |
| `tiingo_api_key` | `None` | `SecretStr` |
| `quote_ttl_seconds` | `60` | How stale a cached quote may be before refetch |
| `request_timeout_seconds` | `10` | |
| `max_retries` | `3` | |
| `breaker_failure_threshold` | `5` | Consecutive failures before opening |
| `breaker_reset_seconds` | `60` | How long the breaker stays open |
| `universe_max_symbols` | `1000` | Ceiling on the seeded universe |

`fake` is the default deliberately: a fresh clone must come up working without
anyone holding an API key. Add a validator so that selecting a real provider
without its key fails at startup, the same way production hardening does today.

Add the keys to `.env.example`, commented out, with a line saying where to get
them.

## The provider layer

### `MarketDataProvider` protocol

A `typing.Protocol` in `app/integrations/market_data/base.py`, not an ABC —
implementations are independent and there is no shared behaviour worth
inheriting.

| Method | Returns |
|---|---|
| `list_symbols()` | The tradable universe |
| `get_daily_bars(ticker, start, end)` | OHLCV history |
| `get_quote(ticker)` | One current quote |
| `get_quotes(tickers)` | Batch quotes — a provider without a batch endpoint fans out internally |

Returns are **provider-agnostic dataclasses** defined next to the protocol, never
raw provider JSON and never ORM models. This is the seam that stops Finnhub's
field names leaking into the service layer, and it is what makes the `Fake`
implementation honest rather than a mock that happens to agree with itself.

Every provider failure surfaces as `ExternalServiceError` from
`app/core/exceptions.py`, which already maps to a `502`.

### Implementations

> **Not built in Phase 2** — see `## Decisions`. No provider account exists, so
> Finnhub and Tiingo remain specified-but-unimplemented behind the protocol, and
> `Fake` is the only concrete provider. The rest of this section is the contract
> whichever one gets built first must satisfy.

- **Finnhub** — quotes and the symbol universe.
- **Tiingo** — daily bar history.
- **Fake** — deterministic pseudo-random walks seeded by ticker, so the same
  symbol always produces the same series. Used by every test and by the default
  local setup.

Splitting the two real providers by capability rather than picking one is
deliberate: their free tiers are generous in different places. **Confirm the
current rate limits against each provider's docs when you implement** — do not
trust numbers written here or memorised from training data. Whatever they are,
they belong in settings, not in a constant.

**Use adjusted prices for history.** Unadjusted bars make every chart lie the
moment a split happens, and Phase 6's P&L would silently disagree with reality.
Record on `daily_bars` whether the row is adjusted, so a later backfill can tell.

### Resilience

Three separate concerns, applied in this order:

1. **Rate limiting** — a token bucket per provider. Phase 1 already has one in
   `app/core/rate_limit.py`; reuse it rather than writing a second. It is
   in-process, which is fine until the Phase 5 worker container exists.
2. **Retry** — `tenacity`, exponential backoff with jitter, only on timeouts,
   connection errors, `429`, and `5xx`. **Never retry a `4xx` other than `429`**:
   a bad ticker is not going to become good.
3. **Circuit breaker** — per provider. Opens after N consecutive failures, fails
   fast while open, and allows a single trial request after the reset window.

The breaker's state must be readable by `/ready`, but a provider being down must
**not** make the app unready — it is a degraded dependency, not a fatal one.
Report it as a named check alongside `database` and keep the overall status
green. Getting this backwards takes the whole app out when a third party
hiccups.

## Data model

### `symbols`

The universe. One row per tradable instrument.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | UUIDv7 |
| `ticker` | `VARCHAR(20)` | unique, uppercase |
| `name` | `VARCHAR(200)` | |
| `exchange` | `VARCHAR(20)` | |
| `asset_type` | `VARCHAR(20)` | `StrEnum` + CHECK — `common_stock`, `etf`, `adr`, … |
| `currency` | `VARCHAR(3)` | |
| `is_active` | `BOOLEAN` | delisted symbols stay, so old journal entries still resolve |
| `last_refreshed_at` | `TIMESTAMPTZ` | nullable |

Search uses a **trigram index on `ticker` and `name`** — `pg_trgm` is already
enabled by the Phase 0 baseline migration, which is why it is there.

### `daily_bars`

| Column | Type | Notes |
|---|---|---|
| `symbol_id` | `UUID` FK → `symbols.id` | `ON DELETE CASCADE` |
| `trade_date` | `DATE` | |
| `open` / `high` / `low` / `close` | `NUMERIC(18, 6)` | |
| `volume` | `BIGINT` | |
| `is_adjusted` | `BOOLEAN` | |

**Composite primary key `(symbol_id, trade_date)`.** This table is the one place
in the project where a natural key beats a surrogate: it makes the idempotent
upsert that every backfill re-run depends on trivial, and it is the index the
range queries want anyway.

### `daily_indicators`

Same `(symbol_id, trade_date)` primary key. One **column per indicator**, all
`NUMERIC(18, 6)`, nullable — the leading rows of any window have no value.

Start with: `sma_20`, `sma_50`, `sma_200`, `ema_12`, `ema_26`, `rsi_14`,
`macd`, `macd_signal`, `macd_histogram`, `atr_14`, `volume_sma_20`.

A wide table rather than a tall `(name, value)` one, because Phase 4's screener
filters on several indicators at once and compiles to SQL. Tall storage would
turn every screen into a pile of self-joins and make indexing hopeless. The cost
is a migration per new indicator, which is the right trade at this scale.

**Indicators are computed here, never fetched.** Providers disagree with each
other on RSI smoothing and on what "MACD" means; computing locally means the
chart in Phase 3 and the screen in Phase 4 cannot contradict each other.

### `latest_quotes`

One row per symbol, upserted. `symbol_id` as the primary key — this is a cache
of current state, not a history, and history is what `daily_bars` is for.

| Column | Type |
|---|---|
| `symbol_id` | `UUID` PK, FK → `symbols.id` |
| `price`, `change`, `change_percent`, `day_open`, `day_high`, `day_low`, `previous_close` | `NUMERIC(18, 6)` |
| `volume` | `BIGINT` |
| `quoted_at` | `TIMESTAMPTZ` — the provider's timestamp |
| `fetched_at` | `TIMESTAMPTZ` — ours, and what the TTL is measured against |

Two timestamps because they answer different questions: "how old is this price"
versus "should I refetch". Conflating them makes a stale-market weekend look
like a broken ingest.

### `watchlists` and `watchlist_items`

| `watchlists` | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK → `users.id` | `ON DELETE CASCADE`, composite index leading with `user_id` |
| `name` | `VARCHAR(80)` | unique per user |
| `is_default` | `BOOLEAN` | |

| `watchlist_items` | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `watchlist_id` | `UUID` FK | `ON DELETE CASCADE` |
| `symbol_id` | `UUID` FK → `symbols.id` | unique together with `watchlist_id` |
| `position` | `INTEGER` | manual ordering |
| `notes` | `VARCHAR(500)` | nullable |

## Endpoints

All under `/api/v1`, all requiring `CurrentUser`.

| Method | Path | Notes |
|---|---|---|
| GET | `/symbols?search=&limit=` | Trigram search; exact ticker matches rank first |
| GET | `/symbols/{ticker}` | `404` when unknown |
| GET | `/quotes?tickers=AAPL,MSFT` | Cache-first, refetch past TTL |
| GET | `/watchlists` | The caller's own, always |
| POST | `/watchlists` | `409` on duplicate name |
| GET/PATCH/DELETE | `/watchlists/{id}` | **`404`, not `403`, for another user's row** |
| POST | `/watchlists/{id}/items` | By ticker; `409` if already present |
| PATCH | `/watchlists/{id}/items` | Bulk reorder |
| DELETE | `/watchlists/{id}/items/{item_id}` | |
| GET | `/watchlists/{id}/quotes` | The grid payload: items joined to `latest_quotes` in one query |

`/watchlists/{id}/quotes` exists so the grid is one request rather than N+1. It
is the endpoint the frontend actually polls.

## Repositories and services

Per `CLAUDE.md`, only non-trivial querying earns a repository:

- `SymbolRepository` — trigram search with ranking, bulk upsert from the seed.
- `BarRepository` — range reads and the idempotent bulk upsert.
- `QuoteRepository` — staleness-aware read, bulk upsert.
- **Watchlists use the session directly.** It is ordinary user-scoped CRUD.

Services: `MarketDataService` (cache-first quote reads, ingest orchestration),
`IndicatorService` (pure computation over a bar series — no I/O, so it unit-tests
without a database), `WatchlistService`.

## Scripts

Both in `backend/scripts/`, both idempotent and safely re-runnable:

- `seed_universe.py` — fetch the symbol list, upsert, cap at `universe_max_symbols`.
- `backfill_bars.py --days N [--tickers …]` — fetch history, upsert bars, then
  recompute indicators for the affected range.

Add `make seed` and `make backfill` targets. **Neither runs automatically.**
Scheduling arrives with the Phase 5 worker; running an unbounded backfill from a
`make up` would burn a free-tier quota before anyone noticed.

## Frontend work

- `src/features/watchlists/` with `api/`, `components/`, `routes/`.
- `src/features/symbols/` for the search box — separate feature because Phase 3's
  symbol detail page will use it too, and features may not import each other.
- Quote grid with `@mui/x-data-grid`: `tabular-nums`, `profit`/`loss` palette
  tokens for change columns — never green/red literals.
- Polling via TanStack Query `refetchInterval`, roughly the quote TTL, and
  **paused when the tab is hidden**. WebSockets replace this in Phase 5.
- Real empty, loading, and error states. An empty watchlist is the first thing a
  new user sees, so it needs to say what to do next.
- Add every new key to `src/lib/queryKeys.ts`.
- `make gen-api` and commit `schema.ts`.

## Testing

- **One contract test suite, parameterized over every provider**, including
  `Fake`. It is the only thing keeping the implementations honestly
  interchangeable.
- Provider HTTP mocked at the transport layer with recorded fixtures. No live
  network in tests, ever.
- Retry: gives up after N, does **not** retry a `404`, and does retry a `429`.
- Breaker: opens after the threshold, fails fast while open, closes after a
  successful trial. Injectable clock — no `sleep`.
- Indicators: known input series with hand-checked expected values, including
  the leading-`NULL` window and a short series that cannot produce `sma_200`.
- Ingest idempotency: running a backfill twice changes no row counts and no
  values.
- Watchlist CRUD, duplicate names, duplicate items, reordering.
- **Append every watchlist and item endpoint to `CROSS_USER_RESOURCES`** in
  `tests/integration/test_cross_user_authorization.py`. This is the phase that
  harness was built for.

## Out of scope

Intraday and tick data · WebSocket streaming (Phase 5) · scheduled ingest
(Phase 5) · charting (Phase 3) · screening (Phase 4) · fundamentals, earnings,
and news · options, crypto, and forex · corporate-action reconstruction beyond
the adjusted prices the provider supplies.

## Definition of done

- [ ] `make check` green; `make migration-check` reports no drift.
- [ ] Migrations round-trip: `alembic downgrade base` → `upgrade head`.
- [ ] The app starts and the full test suite passes with **no API keys set**.
- [ ] Contract tests pass identically against every implemented provider
      (`Fake` alone in this phase — the suite is parameterized so a real one
      joins without edits).
- [ ] `make seed` then `make backfill` populates bars and indicators; running
      both a second time changes nothing.
- [ ] A provider outage degrades `/ready` to a named failing check without
      taking the app unready.
- [ ] In a browser: search a symbol, add it to a watchlist, see a quote, reorder,
      remove it.
- [ ] `CROSS_USER_RESOURCES` covers every new endpoint, and the parameterized
      test no longer skips.
- [ ] `schema.ts` regenerated and committed.
- [ ] `docs/ROADMAP.md` Phase 2 marked complete; `CLAUDE.md` updated with any new
      convention settled here.

## Decisions

The spec's open questions were answered before implementation started. Recorded
here because each one shapes code that is expensive to change later.

1. **No provider accounts exist — `Fake` is the only implementation built.**
   Finnhub and Tiingo stay behind the `MarketDataProvider` protocol, unbuilt.
   This is exactly the case the protocol was designed for, and it costs nothing
   downstream: the data layer, the scripts, the endpoints, and the whole
   watchlist feature are provider-agnostic by construction. The contract test
   suite is still parameterized, so adding a real provider later means writing
   one class and one fixture, not editing anything that consumes it.

   The consequence worth naming: `list_symbols()` has no upstream to call, so
   the universe ships as a curated list in-tree (see below) that `Fake` serves.

2. **The universe is ~500 curated liquid names** — large-cap US equities plus
   liquid ETFs, checked into the repo as a data file rather than fetched. It
   backfills in minutes, exercises every code path, and is broad enough for
   Phase 3 charting and Phase 4 screening to be interesting. The
   `universe_max_symbols` ceiling of 1000 stays as declared and is not reached.

3. **Two years of daily history.** Covers `sma_200` with roughly 250 bars of
   headroom, which is what makes the leading-`NULL` window testable rather than
   theoretical. Deeper history is a script re-run, not a migration, so this is
   the cheap end of a reversible decision.

4. **The indicator column list stands as specced** — `sma_20`, `sma_50`,
   `sma_200`, `ema_12`, `ema_26`, `rsi_14`, `macd`, `macd_signal`,
   `macd_histogram`, `atr_14`, `volume_sma_20`. Phase 4 screens against exactly
   these; adding one later is a migration, and that was accepted knowingly.
