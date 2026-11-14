# Phase 5 — Alerts, jobs, real-time

Implementable spec. Read `CLAUDE.md` first for the data, auth, market data,
screener, layering, and testing conventions — this document does not repeat
them.

**Goal:** the data refreshes itself, and the app tells a signed-in user when
something they care about happens. A user can set an alert on a symbol or on a
saved screen. A background worker keeps quotes and bars current and evaluates
alerts against them. When one fires, it lands in an in-app notification list,
and an open tab hears about it within seconds without reloading.

## The shape of this phase

Every phase so far ran only when someone made a request. This one adds **a
second process that acts on a timer**, and all of the phase's hard parts come
from that:

- **Nobody is watching when it fails.** A request that breaks returns a 500
  someone can see. A worker that stops leaves alerts silently never firing, and
  that looks exactly like "the condition was never met". Phase 4's lesson was
  that sparse data made screens silently *wrong* rather than visibly empty; a
  dead worker would do the same to alerts. So the phase makes worker liveness
  and per-alert freshness **visible** (see *Liveness*), rather than assuming the
  worker is running.
- **Two processes now share things that used to be per-process**: the
  provider's rate limit, the circuit breaker, and the login rate limiter. Phase 1
  and Phase 2 each wrote "fine until the Phase 5 worker exists" next to one of
  these. That point has now arrived, and Redis is the answer to it.
- **Time is now an input.** Market hours, holidays, DST, cooldowns, and "did
  this become true *since last time*" all depend on the clock. Every job takes
  the clock as a parameter, and nothing in job logic calls `datetime.now()`.

The rule that answers most of it:

> **Postgres is the source of truth and Redis never is.** Every alert, trigger,
> and notification lives in Postgres and is written in the same
> transaction as the change that caused it. Redis carries rate-limit counts,
> breaker state, a heartbeat, and pub/sub *hints*. If Redis is flushed, nothing
> a user can see is lost. If a push message is dropped, the client refetches and
> loses nothing.

Build in this order:

1. **Redis, and the shared limiter and breaker moved behind it**, with an
   in-process fallback. Nothing user-visible yet, but every later step spends
   provider quota from two processes.
2. **The market calendar and the `Fake` intraday path.** Without the calendar
   the worker cannot be gated. Without the intraday path a quote never changes
   during the day, so no intraday alert could ever fire in development.
3. **The worker** — container, scheduler, advisory locks, heartbeat — and its
   three ingest jobs, with alert evaluation still stubbed out.
4. **Alerts**: model, CRUD, evaluation inside the jobs, trigger history.
5. **Notifications** — the in-app list.
6. **The WebSocket** and the frontend wiring.
7. **Screen alerts**, last, because they are the one piece that can be cut
   without disturbing the rest.

## Dependencies to add

| Side | Package | Why |
|---|---|---|
| backend | `apscheduler>=3.11,<4` | The scheduler. **Pinned below 4:** 4.0 is still alpha (`4.0.0a6` at the time of writing) and has an entirely different API. Do not "upgrade" across that line in passing |
| backend | `redis>=8.1` | `redis.asyncio`. Not `aioredis`, which merged into `redis-py` and is dead. Add it to the dependency policy in `CLAUDE.md` |
| backend | `holidays` | Its NYSE financial calendar, used for market-hours gating. Pure Python. **Not `exchange_calendars`** — it pulls in pandas, and the project computes in `Decimal` without pandas on purpose |
| frontend | none | The browser's native `WebSocket` is enough. A socket library would add a protocol on top of one that this phase deliberately keeps small |

Compose gains two services: `redis` (`redis:8-alpine`) and `worker` (same
image as `api`, different command). CI gains a `redis` service next to `postgres` in both jobs that
run integration tests.

After adding dependencies, recreate both containers that install them:
`docker compose up -d -V --build api worker`. `CLAUDE.md` explains why `-V` is
needed and `down -v` must never be used for this.

## Redis

### What it holds

| Key / channel | Purpose | If lost |
|---|---|---|
| `ratelimit:provider:{name}` | Provider quota, shared by `api` and `worker` | Bucket refills from full — at worst one extra burst of requests |
| `ratelimit:login:{ip}`, `ratelimit:register:{ip}` | Phase 1's limiter, moved | Same |
| `breaker:{provider}` | Circuit breaker state | Breaker closes; the next failures re-open it |
| `worker:heartbeat` | Last tick, with a TTL | `/ready` reports the worker degraded until the next tick |
| channel `events` | Pub/sub fan-out to WebSocket connections | Clients miss a hint and catch up on their next fetch |

**The provider limit has to be shared: it is a quota on the provider account,
not a limit per process.** With `api` and `worker` each holding a full
in-process bucket, the app spends twice its budget, and the first sign is a
real provider's `429`. Implement the bucket as a single Lua script (read,
refill, take, write in one round trip) so two processes cannot both read "one
token left". Keep `TokenBucketLimiter`'s interface, `allow` and
`time_until_token`, so `RateLimitGate` and the auth routes do not change.

**The breaker is shared for a reason other than correctness.** `/ready` runs in
`api`, but from now on the worker makes almost all the provider calls. A breaker
held per process would report the API's view of a provider the API hardly
touches.

### When Redis is down

**Fall back to the in-process implementation and log a warning once per
outage, not once per call.** That is exactly the Phase 1/2 behaviour, which was
acceptable then:

- The login limiter fails *to* in-process limits, not *open*. Turning off
  credential-stuffing protection whenever a cache is unavailable is not
  degrading, it is disarming.
- The provider limiter falls back to a per-process bucket. With a real
  provider, the worst case is a `429`, which the existing retry already handles.
- Pub/sub stops, so pushes stop. Clients detect the dropped socket and go back
  to polling (see *Frontend*).

`/ready` reports `redis` and `worker` as named checks. **Neither goes into
`_REQUIRED_CHECKS`**: losing either one degrades the app without stopping it,
which is the same reasoning that kept `market_data` out.

## The market calendar

One module, `app/core/market_calendar.py`, with pure functions over an injected
`datetime`:

```python
def is_trading_day(day: date) -> bool
def session_bounds(day: date) -> tuple[datetime, datetime] | None   # UTC
def is_market_open(at: datetime) -> bool
def last_completed_session(at: datetime) -> date
```

- Times are computed in `America/New_York` via `zoneinfo` and **returned in
  UTC**, matching the timestamp convention. Never hard-code a UTC offset: the
  session opens at 13:30 UTC for half the year and 14:30 for the other half.
- Holidays come from `holidays`' NYSE calendar. **Early closes (~3 a year,
  13:00 ET) are treated as full sessions.** On those days the worker polls for
  three extra hours and gets the same price each time. Modelling half-days
  correctly would cost a hand-kept table, and three hours of wasted polling is
  cheaper than that.
- **Scheduling is not gated in cron expressions.** Cron can say "weekdays at
  17:30" but has no way to express "unless it is Good Friday". Jobs fire on a
  plain schedule, and the first thing each one does is ask the calendar. That
  way the calendar is a single tested function instead of a rule spread across
  several triggers.

`Fake`'s daily series stays weekdays-only, as Phase 2 documented. The calendar
decides *when the worker runs*, not which bars exist. On a holiday `Fake`
invents a bar, and the next nightly ingest stores it, which is harmless: Phase 2
already established that nothing downstream reasons about which days should
exist.

## `Fake` grows an intraday path

Today `Fake.get_quote` returns the latest daily bar's close. That price is
fixed for the whole day, and that is a problem:

- An intraday alert can never fire in development, because the price it watches
  does not move.
- Today's bar is returned **before today's session has closed**. Until now that
  was harmless. But once the nightly ingest stores bars and alerts evaluate
  against them, it means the "end-of-day" values are known at 10 a.m.

Two changes, both deterministic:

1. **The daily series ends at the last *completed* session.** That is
   `last_completed_session(now)`, with the clock injected into the provider
   (default: the real clock). Before 16:00 ET, today has no bar.
2. **During a session, `get_quote` follows a path through the day's bar.** The
   bar for today is generated exactly as before. Then the minute-by-minute path
   is a piecewise bridge through four fixed points: `open` at 09:30, the day's
   `high` and `low` at seeded minutes, and `close` at 16:00. It is clamped to
   `[low, high]`, seeded by `crc32(f"{ticker}:{date}")`, and quantised to cents.
   `day_high` and `day_low` are the running extremes up to the current minute,
   and `volume` is the cumulative fraction of the day's volume.

**At 16:00 the quote equals the bar exactly**: price is the close, running
extremes are the bar's `high` and `low`, and volume is the day's volume. Test
that directly. Otherwise the last quote of the day and the bar the nightly ingest
stores would disagree about the same session.

The contract suite in `tests/unit/test_market_data_contract.py` gains a frozen
clock fixture. Any provider must satisfy it, and `Fake` uses it for these
assertions.

## The worker

### Process

A separate Compose service running `python -m app.jobs.worker` from the API's
image. Jobs are plain async functions in `app/jobs/tasks/`. **Each one takes a
session factory and a clock and knows nothing about APScheduler**, so tests call
them directly. The scheduler module is wiring, and wiring is the only thing it
contains.

APScheduler 3.11 `AsyncIOScheduler`, with `timezone="America/New_York"`, the
in-memory job store, and on every job `coalesce=True`, `max_instances=1`, and a
`misfire_grace_time` shorter than the job's interval. **No persistent job
store**: the schedule is defined in code, so storing it in a database adds
nothing and creates something that can go stale.

### Advisory locks

Every job run first takes `pg_try_advisory_lock(key)` on **a connection
dedicated to that run**. If it does not get the lock, it logs `job_skipped` and
returns. That makes a second worker replica, or a run that overlaps the next
one, harmless.

- **Session-level lock, not `pg_try_advisory_xact_lock`.** A job commits many
  times (once per symbol during ingest), and a transaction-scoped lock would be
  released at the first commit.
- **Release it in `finally`.** If the process dies, Postgres releases the lock
  when the connection drops. That property is the reason to use an advisory lock
  rather than a lock row.
- **Keys are fixed integers in one table in `app/jobs/locks.py`, never
  `hash(name)`.** `hash()` is salted per process, so two workers would compute
  different keys for the same job and both would run it. This is the same trap
  `Fake` avoided by using `crc32`.

### Jobs

| Job | Schedule (ET) | Gate | Does |
|---|---|---|---|
| `poll_quotes` | every 60 s | `is_market_open`, plus one final run at 16:05 | Refresh quotes for the hot set; evaluate intraday alerts; publish `quotes_updated` |
| `ingest_eod` | 17:30 daily | `is_trading_day` | Fetch bars since each active symbol's last stored date; recompute indicators; evaluate end-of-day and screen alerts |
| `refresh_symbols` | Sun 06:00 | — | Upsert the provider's universe; **deactivate** symbols no longer listed |
| `prune_notifications` | 03:00 daily | — | Delete read notifications older than 90 days |
| `heartbeat` | every 15 s | — | `SET worker:heartbeat <now> EX 60` |

**The hot set is the union of symbols on any watchlist and symbols with an
active intraday alert**, not the whole universe. Polling all 533 symbols every
minute would need 533 calls a minute against a `requests_per_minute` default of
60, so a real provider would spend the entire session rate-limited. Log the
hot-set size and how long each run took. If a run takes longer than the
interval, `coalesce` stops a backlog from building, and the log line shows the
budget has run out.

**`ingest_eod` is incremental, and its window is bounded.** For each symbol it
fetches from the day after its last stored bar through `last_completed_session`,
and it never reaches back further than 30 days. A symbol with no bars at all, or
one more than 30 days behind, is logged and skipped. That needs a manual `make
backfill`. **`make seed` and `make backfill` stay manual**, as `CLAUDE.md`
requires. The worker schedules the incremental top-up, never the two-year
backfill.

Indicators are still recomputed over each symbol's **whole stored series**,
because the convention does not change just because the new window is one bar.
So the nightly run costs much more than "one bar per symbol". Phase 4 measured a
full 730-day backfill at **8m19s**, and the recompute is a large part of that.
**Measure the first real nightly run and record it in the PR.** If it is long
enough to matter, the fix is to bound the recompute to a warm-up window per
indicator, and that is its own change with its own byte-identical test. It is
not something to slip into this one.

**`refresh_symbols` deactivates and never deletes.** Today's seed upserts with
`is_active=True` and never turns a symbol off. A delisted ticker therefore stays
in the screener universe forever, and watchlists and alerts reference it by FK.
Deactivating it also **disables every alert on it**, with a notification saying
why. Deleting the alerts would remove them without telling the user.

### Liveness

A dead worker has to be visible in three places:

- **`/ready`** reports `worker: false` when the heartbeat is missing or older
  than 60 s. It is degraded, not fatal.
- **Compose** gives `worker` a healthcheck that reads the same key.
- **The UI**: every alert shows `last_evaluated_at`. "Last checked 3 days ago"
  is what tells a user something is wrong. "Not triggered" on its own tells them
  nothing.

## Alerts

### An alert is a one-symbol screen with a schedule

Phase 4 built a filter language, a compiler, caps, null semantics, and a field
registry. An alert condition reuses **all of it**, and the alternative is a
second way of saying "price above its 200-day average" that would drift from
the first. Three kinds:

| `kind` | Target | Condition | Evaluated |
|---|---|---|---|
| `intraday` | one symbol | filter tree over the **intraday** registry | each `poll_quotes`, against quotes fetched *in that run* |
| `end_of_day` | one symbol | filter tree over the **screener** registry | each `ingest_eod`, at the new `as_of` |
| `screen` | a screener preset | the preset's own tree | each `ingest_eod`; fires on **new entrants** |

**The intraday registry is the screener registry with the bar fields swapped
for live ones.** `close`, `open`, `high`, `low`, `volume` become `price`,
`day_open`, `day_high`, `day_low`, `volume` from `latest_quotes`, plus
`change_percent`. Every indicator field remains, joined at the shared `as_of`.
So "price crosses above its 200-day SMA" is a `compare` between a live price and
last night's average. Both have the unit `price`, so the existing unit check
accepts it unchanged. Give `fields.py` a registry per source rather than a flag
on each entry. `GET /screener/fields` keeps serving the screener registry, and
`GET /alerts/fields?kind=intraday` serves the other.

**Evaluation runs in SQL through the existing compiler.** It returns `TRUE`,
`FALSE`, or `NULL` per alert. Do not write a second evaluator in Python: two
evaluators means two sets of null semantics. One statement per alert is fine at
this scale. Measure the poll with 200 active alerts and record the timing. Batch
the alerts with `UNION ALL` only if that measurement says it is needed.

**Intraday alerts are evaluated only against quotes this run actually
fetched.** The filter is `fetched_at >= run_started_at`, and the `latest_quotes`
table as a whole is never used for this. During a provider outage the grid
serves stale quotes with an honest timestamp (which is correct for a grid), and
alerts are *not evaluated*. Evaluating a stale price would fire an alert on a
price the provider has not confirmed.

### Firing is edge-triggered

An alert fires when its condition goes **from not-true to true**, not
whenever it is true. A level check that fires every minute for as long as AAPL
is above 200 is a notification storm, not an alert.

- `last_state` (nullable boolean) is stored on the alert. It fires when the new
  state is `TRUE` and `last_state` is not `TRUE`.
- **A new alert does not fire on its first evaluation.** That evaluation only
  records the state. If the condition is already met when the alert is created,
  the create response says so (`condition_met_now: true`) and the UI tells the
  user. An alert that fires the second it is saved tells the user nothing they
  did not already know.
- **`NULL` does not create an edge.** If `sma_200` disappears for one
  evaluation, `last_state` is left unchanged, so the next `TRUE` is not mistaken
  for a new crossing. This is Phase 4's "nulls do not match" rule applied over
  time.

| `trigger_mode` | Behaviour |
|---|---|
| `once` | Fires, then sets `status = triggered`. Re-arming is an explicit user action |
| `rearm` | Fires on every new edge, but **not within `cooldown_minutes` of the last firing** (default 60, min 5, max 1440) |

The cooldown is there for flapping. A price oscillating around a level makes a
new edge every few minutes, and edge-triggering alone does not stop that. During
the cooldown `last_state` still updates; only the firing is suppressed.

A `screen` alert is always `rearm` and has no cooldown, since it runs once a
day. Its "edge" is set difference: symbols in today's matches that were not in
`last_matched_symbol_ids`. The first run only records the set. A run that
produces no new entrants records the set and does not fire.

### One bad alert stops nothing

Each alert is evaluated in its own savepoint. If an alert's tree no longer
validates (the preset names a field that was removed — Phase 4 called this
case out) or its symbol has been deactivated, then that alert gets
`status = disabled` and a `disabled_reason`, and the user gets a notification.
**The run continues.** A worker that crashes on one stale preset stops every
user's alerts with nothing visible to show for it.

### Recording a firing

Within one transaction:

1. Update the alert's state (`last_state`, `last_evaluated_at`,
   `last_triggered_at`, and `status` if `once`).
2. Insert an `alert_triggers` row with a **snapshot** of the values that made it
   fire. This is the Phase 4 rule that a result shows *why*. "AAPL crossed
   above its 200-day" carries the price and the average at that minute.
3. Insert a `notifications` row.

**After** the commit, publish a `notification_created` hint to Redis. Nothing outside Postgres happens inside the
transaction: a crash after commit loses at most one push hint, and a crash
before commit loses nothing, because the next run finds the same edge.

### Limits

| Limit | Value | Why |
|---|---|---|
| Active alerts per user | 50 | Every intraday alert can add a symbol to the hot set, which is spent against provider quota |

It is a setting. Test it at the limit and one past it, as Phase 4 did.

## Notifications

One table, one row per thing the user should hear about: `alert_triggered` and
`alert_disabled`. In this phase it is the only channel.

**This table is also where email will attach in Phase 7**, as outbox columns on
the same row, which is why a firing writes its notification inside the firing's
transaction. The ordering in *Recording a firing* already gives a later email
channel what it needs (a durable row to deliver from, written exactly once), so
adding it should be columns and a delivery job, not a redesign. Do not add
those columns now: an unused `email_status` is a schema promise nothing keeps.

**A push is a hint, not the data.** `notification_created` carries the id. The
client invalidates its notification queries and refetches over REST. That way
there is one path the data takes, and a missed push costs a few seconds, not a
lost notification.

The list is **cursor-paginated** on `(created_at, id)`, unlike the screener.
Screen results are bounded by the universe. Notifications grow without bound,
and an offset that shifts every time a new one arrives skips or repeats rows.

## Real-time

### One socket, authenticated after it opens

`GET /api/v1/ws` upgrades to a WebSocket. A browser cannot set an
`Authorization` header on a WebSocket, and a token in the query string ends up
in every access log between the browser and the app. So:

1. **Check the `Origin`** against `cors_origins` and reject anything else. CORS
   does not apply to WebSockets. The bearer handshake already blocks
   cross-site hijacking, but the check is one line.
2. The client's first message must be `{"type": "auth", "token": "<access>"}`,
   sent within 5 s. Otherwise the server closes with code **4401**.
3. The token is validated **with the same checks as `CurrentUser`**, including
   whether the account is still active.
4. **The connection lives only as long as the token.** The server closes with
   4401 when it expires, unless the client has sent a newer token on the same
   socket before then. The client sends one after every refresh. That keeps
   `CLAUDE.md`'s rule that deactivation takes effect within one access-token
   lifetime. A socket that outlives its token would quietly break that rule.

**On 4401 the client goes through the single in-flight refresh in
`lib/api/client.ts`. It never starts its own.** A socket that refreshes on its
own spends a rotated token that a concurrent request also spends, and the
session family gets revoked as theft. Phase 1 needed an explicit test for this
case, and this phase needs one too.

### Fan-out

Each API process holds **one** Redis subscription to `events`, not one per
socket, and routes messages to its local connections. Two message types:

| Type | Payload | Delivered to |
|---|---|---|
| `quotes_updated` | `fetched_at`, `tickers: [...]` | every connection |
| `notification_created` | `user_id`, `notification_id` | that user's connections **only** |

**Both are invalidation hints.** `quotes_updated` carries no prices. The client
checks whether any ticker it is showing is in the list, and if so invalidates
that query, which refetches through the same REST path as today. The grid
therefore cannot disagree with the endpoint, prices stay strings that are parsed
only in `lib/format.ts`, and there are no per-connection subscriptions to
validate, cap, or leak. A list of 533 tickers once a minute is a few kilobytes.

**What the socket buys**: an update arrives seconds after the poll instead of
up to 60 s later. There is no polling at all while the market is closed and
nothing is changing. And a notification shows up without a reload.

Routing `notification_created` to the wrong user is a cross-user leak like any
other, and it is tested as one (see *Testing*).

## Endpoints

All under `/api/v1`, all requiring `CurrentUser` except where noted.

| Method | Path | Notes |
|---|---|---|
| GET | `/alerts/fields?kind=` | Registry for `intraday` or `end_of_day` |
| GET | `/alerts` | The caller's alerts, with `last_evaluated_at` and `last_triggered_at` |
| POST | `/alerts` | Create. `422` past the active cap or on an invalid tree. Response includes `condition_met_now` |
| GET | `/alerts/{id}` | One alert |
| PATCH | `/alerts/{id}` | Edit name, condition, mode, cooldown, or `status` between `active`/`paused`. Editing the condition resets `last_state` |
| DELETE | `/alerts/{id}` | `204` |
| GET | `/alerts/{id}/triggers` | Firing history with snapshots, newest first, cursor-paginated |
| GET | `/me/notifications` | Cursor-paginated; `?unread=true` |
| GET | `/me/notifications/unread-count` | For the badge |
| POST | `/me/notifications/{id}/read` | Idempotent |
| POST | `/me/notifications/read-all` | Idempotent |
| GET | `/ws` | WebSocket; auth by first message |

Alerts use the same JSONB-and-validate pattern as presets: **the list does not
re-parse trees, but opening or editing does**. One broken alert must not take
down the page that lists them.

## Data model

All of these follow `CLAUDE.md`'s data conventions: UUIDv7 keys,
`TimestampMixin`, `user_id` with `ON DELETE CASCADE`, and enums as
`VARCHAR` + CHECK + `StrEnum`.

### `alerts`

| Column | Type | Notes |
|---|---|---|
| `id`, `user_id` | | Per convention |
| `name` | `VARCHAR(80)` | Not unique: two alerts named "AAPL" are fine |
| `kind` | `VARCHAR(16)` | `intraday`, `end_of_day`, `screen` |
| `symbol_id` | `UUID` null | FK → `symbols`. Required unless `kind = screen` |
| `preset_id` | `UUID` null | FK → `screener_presets`, `ON DELETE CASCADE`. Required iff `kind = screen` |
| `condition` | `JSONB` null | Required unless `kind = screen` |
| `trigger_mode` | `VARCHAR(8)` | `once`, `rearm` |
| `cooldown_minutes` | `INTEGER` | CHECK 5–1440 |
| `status` | `VARCHAR(16)` | `active`, `paused`, `triggered`, `disabled` |
| `disabled_reason` | `VARCHAR(200)` null | |
| `last_state` | `BOOLEAN` null | |
| `last_matched_symbol_ids` | `UUID[]` null | `screen` only |
| `last_evaluated_at`, `last_triggered_at` | `TIMESTAMPTZ` null | |

**A CHECK constraint ties `kind` to which of `symbol_id`, `preset_id`, and
`condition` must be present.** The schema layer enforces this too, but the
database is the last line of defence against a worker writing a half-formed
row.

Indexes: `(user_id, created_at)`; a partial index on `(kind, symbol_id) WHERE
status = 'active'`, because both evaluation jobs ask "active alerts of this kind
for these symbols".

Deleting a preset deletes its screen alerts. The preset-delete confirmation in
the screener UI says how many alerts will go with it. That is a link to a count
endpoint or a field on the preset response, never an import from
`features/alerts`.

### `alert_triggers`

`id`, `alert_id` (`ON DELETE CASCADE`), `user_id` (denormalised, per the
user-scoped convention), `triggered_at`, `as_of` (the bar date or the quote's
`fetched_at`), `snapshot JSONB`. Index `(alert_id, triggered_at)` and `(user_id,
triggered_at)`. Kept indefinitely, because the history is part of the feature.

### `notifications`

`id`, `user_id`, `kind`, `title VARCHAR(200)`, `body TEXT`, `payload JSONB` (the
link target: alert id, ticker), `alert_id` null (`ON DELETE SET NULL`, so the
notification outlives the alert), and `read_at` null.

Indexes: `(user_id, created_at, id)` for the cursor, and a partial index on
`(user_id) WHERE read_at IS NULL` for the badge.

Ordinary CRUD for all three, so there are no repositories. The exception is the
evaluation queries in the jobs, which are not CRUD and get one
`alert_repository.py`.

## Settings

New nested groups, each with defaults that work on a fresh clone:

- `REDIS__URL` — default `redis://localhost:6379/0`; Compose overrides the host.
- `WORKER__QUOTE_POLL_SECONDS` (60), `WORKER__EOD_INGEST_TIME` (`17:30`),
  `WORKER__MAX_INGEST_GAP_DAYS` (30).
- `ALERTS__MAX_ACTIVE_PER_USER` (50), `ALERTS__DEFAULT_COOLDOWN_MINUTES` (60).

`.env.example` gains all of them, with comments matching the existing ones.

## Frontend work

- **The filter builder moves to `src/components/filter-builder/`**, along with
  `buildFilterTree` / `parseFilterTree` and their tests. Today it lives in
  `features/screener`, and alerts need it too. This is the `SymbolSearch` lesson
  that just landed: a shared control moves up before a second feature reaches
  for it, not three phases afterwards. Its field list still comes from an
  endpoint (now one of two), and there is still no TypeScript copy.
- **`src/features/alerts/`** — the `/alerts` list (status, last evaluated, last
  triggered, condition summary), a create/edit dialog built on the shared
  builder, and a trigger history view with snapshots. The **symbol detail page
  links to `/alerts/new?ticker=X`** rather than importing an alerts dialog, and
  the screener's preset menu links to `/alerts/new?preset=<id>`.
- **The bell lives in `src/components/notifications/`**, because `AppLayout` is
  in `components/` and must not import a feature. It shows an unread badge and a
  popover of recent items. The full list at `/notifications` is
  `src/features/notifications/`, and it may import the bell's hooks from
  `components/`.
- **`src/lib/realtime/`** — one socket per tab, opened by `AppLayout` once
  authenticated. It handles the auth handshake, re-auths on every token change
  (`session.ts` gains an `onChange` listener), reconnects with backoff and
  jitter, and turns messages into `queryClient.invalidateQueries` calls using
  keys from `lib/queryKeys.ts`. It never writes data into the cache.
- **Polling becomes the fallback rather than going away.** The roadmap and
  `useWatchlists.ts` both say "the interval goes away", which would leave the
  grid frozen whenever Redis or the socket is down. So `GRID_POLL_INTERVAL_MS`
  stays and is applied **only while the socket is not connected**. Update that
  comment.
- Every new key goes in `src/lib/queryKeys.ts`. `make gen-api` and commit
  `schema.ts`.

## Testing

- **The market calendar**, using frozen instants: both DST transitions; a
  holiday; a weekend; 09:29:59, 09:30:00, 15:59:59, and 16:00:00 ET.
  `last_completed_session` at 15:59 and at 16:00 on the same day.
- **`Fake`'s intraday path**: deterministic for a given minute; stays within
  `[low, high]`; **equals the bar exactly at 16:00**; today has no bar before
  the close. These go in the contract suite, so a future provider inherits the
  frozen-clock fixture.
- **Edge triggering**: false→true fires; true→true does not; true→null→true does
  not; the first evaluation never fires; editing the condition resets the state;
  `once` sets `triggered`; a `rearm` edge inside the cooldown is suppressed while
  `last_state` still updates, and is not suppressed one minute after the cooldown
  ends.
- **Intraday alerts ignore stale quotes**: with the provider's breaker open, a
  `poll_quotes` run evaluates nothing, even when `latest_quotes` holds a row
  that would match.
- **Isolation**: a run containing one alert with an unresolvable field and one
  valid alert disables the first, notifies its owner, and fires the second.
- **Screen alerts**: the first run only records the set; a new entrant fires and
  lists itself; a symbol leaving and re-entering fires again.
- **Advisory locks**: with the lock held on a second connection, the job logs
  `job_skipped` and writes nothing. After that connection is closed, the next run
  goes ahead.
- **`ingest_eod`** fetches only the gap, skips a symbol more than 30 days
  behind, and a second run the same night changes nothing.
  **`refresh_symbols`** deactivates a vanished ticker and disables its alerts
  with a notification.
- **The Redis limiter** is atomic. Two concurrent `allow` calls at one remaining
  token: exactly one succeeds. With Redis unreachable it falls back to in-process
  rather than allowing everything. Test the login limiter specifically for that.
- **WebSocket**: no auth message within the window → 4401; a bad token → 4401;
  an expired token closes the socket, and a re-auth before expiry keeps it open;
  a deactivated user is closed at the next expiry; a foreign `Origin` is
  rejected; **user A's socket never receives user B's `notification_created`**.
- **Cross-user registries.** `CROSS_USER_RESOURCES` gains `GET`/`PATCH`/`DELETE
  /alerts/{id}`, `GET /alerts/{id}/triggers`, and `POST
  /me/notifications/{id}/read`. That last one has a `/me/` prefix but takes an
  id, so it is a forged-id case, and putting it in the caller-scoped list would
  assert something false. `CALLER_SCOPED_RESOURCES` gains `GET
  /me/notifications`, `unread-count`, and `read-all`. Fixtures build the other
  user's alert and notification through the API and a job run, not by inserting
  rows directly.
- **`/ready`** with Redis stopped and with the heartbeat stale: `200`, `ready`,
  with the check named in `degraded`.
- **Frontend**: the socket invalidates the right keys and nothing else; polling
  resumes when the socket drops and stops when it reconnects; a 4401 goes
  through the shared refresh. **Two simultaneous 401s plus a 4401 must produce
  exactly one refresh call.** Also: the bell's count updates on a push; the builder still round-trips
  after the move.

## Out of scope

**Alert levels drawn on the chart.** Phase 3 deferred these "to Phase 5", and
they are deferred again, to Phase 7, with a reason: a price line is a new
colour identity on the chart. The charting conventions allow exactly three
validated overlay colours, so a fourth has to be validated in both modes before
any line exists. That is a small change, but it is a chart change, and this
phase does not otherwise touch the chart.

**Email notifications, moved to Phase 7.** In-app is the only channel this
phase. Email cannot ship on its own: sending alert mail to an address nobody has
proved they own turns the app into a mail cannon (anyone could register someone
else's address and point 50 rearming alerts at it), so the email channel needs
verification first. Phase 7 already owns verification (`users.is_verified` is
documented as waiting for it), and `auth_service.py`'s
registration-enumeration fix is waiting for email delivery too. That is three
pieces of work that need the same infrastructure (SMTP sender, a local catcher
such as Mailpit, an outbox, a delivery job, verification), so they land
together. See *Notifications* for how this phase leaves room for it.

Also out: SMS, browser push, and webhooks as channels · pre-market and
after-hours quotes · storing intraday bars · a real market data provider ·
running more than one `api` replica, which this design supports but nothing
tests · ARQ, which the roadmap names as the upgrade path if jobs ever become a
queue rather than a schedule.

## Definition of done

- [x] `make check` green; `make migration-check` reports no drift; migrations
      round-trip `downgrade base` → `upgrade head`.
- [ ] `make up` starts `db`, `redis`, `api`, `worker`, and `web` on a
      fresh clone with **no** credentials, and all report healthy.
- [x] Provider rate limit and breaker are shared through Redis; stopping Redis
      degrades `/ready` without failing it, and login limiting falls back rather
      than opening.
- [ ] The worker runs every job under an advisory lock; starting a second
      worker produces `job_skipped` lines and no duplicate writes.
- [x] One real `ingest_eod` run over the full universe, **runtime recorded in
      the PR** alongside Phase 4's 8m19s backfill.
- [x] `poll_quotes` hot-set size and per-run duration logged; timing with 200
      active alerts recorded in the PR.
- [ ] In a browser, during market hours (or with the worker's clock pinned
      inside a session): create an intraday alert on a ticker just past its
      current `Fake` price, watch it fire within a few minutes, see the bell
      update **without a reload**. Stop the worker; the alert's "last checked" visibly ages and `/ready` names
      `worker`.
- [x] The watchlist grid updates from a push while the socket is up and falls
      back to polling when it is down.
- [x] Every cross-user and caller-scoped entry listed in *Testing* is in its
      registry.
- [x] `schema.ts` regenerated and committed.
- [x] `docs/ROADMAP.md` Phase 5 marked complete; `CLAUDE.md` updated with the
      conventions this phase settles, including the Redis rule, the clock rule,
      advisory-lock keys, edge-triggering, and push-as-invalidation; the stale
      "Phase 5" comments in `rate_limit.py`, the two scripts, the `Makefile`,
      and `useWatchlists.ts` rewritten to describe what is now true.

**Not yet verified as worded** (open items above):

- *`make up` on a fresh clone*: every service is healthy on this machine's
  existing stack; a clean clone has not been tried.
- *A second worker*: the lock is covered by
  `tests/integration/test_jobs.py` (a held lock gives `job_skipped` and no
  writes); two live worker containers have not been run side by side.
- *In a browser*: the same path was run live without one. An alert created
  through the API, a real worker poll with `DEV_CLOCK_OFFSET` set, and the push
  on a real WebSocket after 146 s. The bell updating on a push is covered by
  `NotificationBell.test.tsx`, and stopping the worker visibly degrades
  `/ready`.

## Open questions

Several calls were made while writing this spec and are recorded above rather
than left implicit.

**Confirmed before building: screen alerts are in.** Phase 4 promised them "in
Phase 5", and given the compiler and the nightly job they are cheap. They stay
last in the build order, because they touch nothing else.

**Decided after review: email is out.** The first draft pulled email
verification forward from Phase 7 so alerts could be emailed. That made the
phase larger by an SMTP sender, Mailpit, an outbox, a delivery job, and two
verification endpoints. In-app notifications ship alone, and email moves to
Phase 7 alongside verification (see *Out of scope*).

Decided and not open: Redis is never the source of truth; pushes are hints;
alerts reuse the filter DSL rather than growing a second one; firing is
edge-triggered, and a new alert does not fire on save; `Fake` gains an intraday
path and stops serving today's bar before the close; APScheduler stays below
4.0; and polling becomes the fallback rather than being deleted.
