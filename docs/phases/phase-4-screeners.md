# Phase 4 — Screeners

Implementable spec. Read `CLAUDE.md` first for the data, auth, market data,
layering, and testing conventions — this document does not repeat them.

**Goal:** a signed-in user can build a set of filters over the stored universe,
run it, see the matching symbols with the values that made them match, sort the
result, save the filter set under a name, and re-run it later.

## The shape of this phase

This is the first phase whose hard part is **a compiler**. Phases 2 and 3 moved
rows around; this one turns user-supplied JSON into SQL, which is the first
place in the project where user input decides the *shape* of a query rather than
just its parameters. Everything else here — presets, the results table — is
CRUD the project has done twice already.

That risk is answered by one rule, and the whole design follows from it:

> **No user-supplied string ever reaches SQL.** A filter names a field by key;
> the key is looked up in a registry that maps it to a real SQLAlchemy column.
> An unknown key fails Pydantic validation before the service is called. There
> is no branch in which a field name is interpolated into a string.

Build in this order:

1. **The universe backfill** — see *Prerequisite*, and do it first, because
   every later step is untestable against four symbols.
2. **The three new indicator columns**, which the screener treats as ordinary
   fields and the chart gets for free.
3. **The field registry and the compiler**, in isolation, tested directly.
4. **The run endpoint** over them.
5. **Presets**, last, because running works without saving.
6. **The filter-builder UI**, generated from the registry the compiler uses.

## Dependencies to add

**None**, on either side. This phase is SQLAlchemy, Pydantic, and MUI
components the project already has.

## Prerequisite — the universe has almost no data

Measured on the dev database at the end of Phase 3:

| | |
|---|---|
| Symbols in `symbols` | 533 |
| Symbols with any row in `daily_bars` | **4** |
| Rows in `latest_quotes` | **1** |

Phase 3 never noticed, because a chart asks for one symbol at a time and the
four that mattered were backfilled by hand. A screener asks the whole universe
at once, so it is the first feature that a sparse table makes *silently wrong*
rather than visibly empty: a screen that returns three names looks like a
selective filter, not like missing data.

**Phase 4 therefore begins by backfilling the full active universe**, which
costs nothing but time — `Fake` is the provider, so there is no network and no
quota:

```bash
make backfill days=730
```

Roughly 530 symbols × ~500 trading days ≈ 265k bars and as many indicator rows.
Measure the runtime on the first run and record it in the PR; if it is slow
enough to be annoying, that is a fact Phase 5's nightly ingest needs to know
before it schedules the same work.

This is a prerequisite, not a feature. Phase 5 still owns *scheduling* it.

## What the screener reads, and what it deliberately does not

**One row per symbol, at one shared date.** The screen joins `symbols`,
`daily_bars`, and `daily_indicators` at a single `as_of` date and evaluates
every filter against that row.

`as_of` is `max(trade_date)` across `daily_bars` — one date for the whole run,
not each symbol's own latest. Per-symbol latest would quietly compare a symbol
that stopped ingesting three weeks ago against one priced yesterday, and the
result would look like a screen rather than like a bug. A symbol with no bar on
`as_of` is simply not in the universe for that run, and the response says how
many symbols were considered so the number is visible rather than assumed.

**`latest_quotes` is not read.** It is a TTL cache populated as a side effect of
someone viewing a watchlist grid — one row today — so screening off it would
return whichever symbols happened to have been looked at recently. History is
what `daily_bars` is for, and that is what this reads.

**Anything that needs more than one row per symbol is an indicator column, not
a screener query.** This is the rule that keeps the compiler simple, and it is
why the phase adds three columns rather than three window functions. A screen
must not be the only place a derived value exists, or the chart and the screen
end up disagreeing about the same symbol — which is the reason indicators are
computed centrally in the first place.

### `daily_indicators` gains three columns

Computed by the existing `indicator_service` over the whole stored series, like
every other column there. A `make backfill` re-run fills them, which is the same
run the *Prerequisite* already requires — so this costs one migration and no
extra ingest.

| Column | Type | Notes |
|---|---|---|
| `change_percent_1d` | `NUMERIC(18, 6)` | Percent change from the previous bar's close. Null on a symbol's first bar |
| `high_52w` | `NUMERIC(18, 6)` | Highest `high` over the trailing 252 trading days |
| `low_52w` | `NUMERIC(18, 6)` | Lowest `low` over the trailing 252 trading days |

252 rather than 365 because the window is counted in **bars, not calendar
days** — unlike `ChartRange`, which is calendar-based because a toolbar label
promises calendar time. Both conventions are correct for their own job; write
the comment saying so, because the next reader will assume one is a mistake.

Nullable like every other indicator column: the trailing window has no value
until it is full.

### Indexing

`daily_bars` and `daily_indicators` are keyed `(symbol_id, trade_date)`, a
primary key that leads with `symbol_id`. Every query so far filtered on
`symbol_id` first, so that was the right order. **The screener filters on
`trade_date` first and `symbol_id` not at all**, which that index cannot serve.

Add to both tables:

```
Index("ix_daily_bars_trade_date", "trade_date")
Index("ix_daily_indicators_trade_date", "trade_date")
```

Confirm with `EXPLAIN` that the run query uses them rather than sequentially
scanning a 265k-row table, and put the plan in the PR. At this size a seq scan
is fast enough to hide the problem in dev and slow enough to matter later.

## The filter DSL

A filter set is a **tree**, serialized as JSON, validated by a Pydantic
discriminated union on `kind`, and compiled to a SQLAlchemy boolean expression.

### Node kinds

| `kind` | Shape | Compiles to |
|---|---|---|
| `numeric` | `{field, op, value}` / `{field, op: "between", value, value2}` | `column <op> :value` |
| `compare` | `{left, op, right}` | `column <op> column` |
| `category` | `{field, op, values}` | `column IN (…)` / `NOT IN (…)` |
| `group` | `{op: "and"\|"or", children: [...]}` | `AND` / `OR` over children |

Operators: `numeric` and `compare` take `gt`, `gte`, `lt`, `lte`, `eq`, `neq`;
`numeric` additionally takes `between`. `category` takes `in` and `not_in`.
Each is a `StrEnum` in `app/core/enums.py`, so an unknown operator is a 422 the
same way an unknown field is.

**`compare` is the point of the whole feature.** "Price above its 200-day
average" is the screen everyone actually wants, and it is a comparison between
two columns, not between a column and a number. A DSL that only supports
`field op literal` looks complete and cannot express it.

`compare` is restricted to two fields **of the same unit** — both prices, or
both percentages. `close > volume` parses fine and means nothing, so the
registry carries a unit per field and the validator rejects a mismatch. This is
a 422 with a message naming both units, not a silent always-false result.

### Values cross the wire as strings

Prices are `NUMERIC`/`Decimal` everywhere in this project and must not
round-trip through a float — a screen for `close >= 100.10` that compares
against `100.09999999999999` is exactly the class of bug the convention exists
to prevent. Declare the field as `Decimal` in Pydantic and let it parse the
string; do not accept a JSON number.

### Caps

A recursive schema with no ceiling is a denial-of-service shaped like a feature.

| Limit | Value | Why |
|---|---|---|
| Max nesting depth | 3 | Deeper than this is unbuildable in the UI and unreadable in JSON |
| Max total nodes | 25 | A 25-predicate screen is already past useful |
| Max `values` per `category` node | 50 | Longer than the list of exchanges |

Enforce all three in the schema layer with a validator that walks the tree once,
so the limit is a 422 and never an `OperationalError`. Test the boundary at
exactly the cap and exactly one past it.

### Nulls do not match, and that is correct

Every indicator column is nullable, and SQL three-valued logic means
`close > sma_200` is `NULL` — not `TRUE` — when `sma_200` has no value. So a
symbol with fewer than 200 bars silently drops out of that screen.

This is the right behaviour and it must not be "fixed" with `COALESCE`, which
would substitute a fabricated number and produce matches that are not true. It
must, however, be **visible**: the response reports `universe_size` and
`total_matched`, and the UI says how many symbols were considered. Assert the
behaviour in a test with a deliberately short series, so the next person to find
it reads a test rather than guessing.

Join `daily_indicators` with a **LEFT JOIN**, not an inner join, for the same
reason: a symbol with bars but no indicator row should fail an indicator
predicate on its merits, not vanish before the predicate runs.

## The field registry

One module — `app/services/screener/fields.py` — mapping each field key to its
column, unit, and permitted node kind. It is the **only** place that decides
what is screenable, and it has two consumers:

- the compiler, which resolves a key to a column;
- `GET /screener/fields`, which serves the catalogue to the filter builder.

One source, so the UI cannot offer a field the compiler will reject, and a new
field is one entry rather than a change in three files.

| Field | Source | Unit | Kind |
|---|---|---|---|
| `close`, `open`, `high`, `low` | `daily_bars` | price | numeric, compare |
| `volume` | `daily_bars` | shares | numeric, compare |
| `sma_20`, `sma_50`, `sma_200` | `daily_indicators` | price | numeric, compare |
| `ema_12`, `ema_26` | `daily_indicators` | price | numeric, compare |
| `high_52w`, `low_52w` | `daily_indicators` | price | numeric, compare |
| `atr_14` | `daily_indicators` | price | numeric, compare |
| `volume_sma_20` | `daily_indicators` | shares | numeric, compare |
| `rsi_14` | `daily_indicators` | ratio 0–100 | numeric |
| `macd`, `macd_signal`, `macd_histogram` | `daily_indicators` | price | numeric, compare |
| `change_percent_1d` | `daily_indicators` | percent | numeric |
| `exchange`, `asset_type` | `symbols` | — | category |

Every entry carries a human label and, for `category`, its permitted values —
which come from `AssetType` for `asset_type` and from a `DISTINCT` over
`symbols` for `exchange`, so the builder offers what actually exists.

Only `is_active` symbols are ever in the universe. It is not a field, because a
screen over delisted names is not a thing anyone wants and making it optional
invites getting it wrong.

### Derived fields are registry entries, not node kinds

`pct_from_sma_50`, `pct_from_sma_200`, `pct_from_52w_high`, and `atr_percent`
are same-row arithmetic — `(close - sma_50) / sma_50 * 100` and friends — so
they are registry entries whose "column" is a SQLAlchemy expression rather than
a `Column`. Unit `percent`, numeric only.

They are worth the entries because they are what makes a comparison *scale-free*:
`close > sma_50` is true for a stock a cent above its average, while
`pct_from_sma_50 >= 5` is the screen someone meant. Guard each against division
by zero and against a null denominator; a `NULLIF` on the denominator gives the
null-does-not-match behaviour above, which is the one already specified.

## Endpoints

All under `/api/v1`, all requiring `CurrentUser`.

| Method | Path | Notes |
|---|---|---|
| GET | `/screener/fields` | The catalogue: keys, labels, units, operators, category values |
| POST | `/screener/run` | Run an ad-hoc filter tree. Returns matches |
| GET | `/screener/presets` | The caller's presets, newest first |
| POST | `/screener/presets` | Create. `409` on a duplicate name |
| GET | `/screener/presets/{id}` | One preset |
| PATCH | `/screener/presets/{id}` | Rename and/or replace the filter tree |
| DELETE | `/screener/presets/{id}` | `204` |
| POST | `/screener/presets/{id}/run` | Run a saved preset |

**`POST` for a read.** A filter tree does not fit in a query string legibly, and
URL-encoding JSON into a `GET` trades a readable body for an unreadable URL and
a length limit. The cost — no HTTP caching — is not a cost here, because the
underlying data changes once a day and the client caches through TanStack Query
anyway.

**`/presets/{id}/run` exists rather than making the client fetch-then-post.** It
is the primary path from the UI, it halves the round trips, and it removes the
window in which a client can run something subtly different from what is saved
under that name.

### Request and response

`POST /screener/run` takes the tree plus sorting:

```json
{
  "filters": { "kind": "group", "op": "and", "children": [ … ] },
  "sort": { "field": "change_percent_1d", "direction": "desc" },
  "limit": 100
}
```

`sort.field` must be a registry key; direction is a `StrEnum`. Default sort is
`ticker` ascending — stable, and not a claim about which match is best.

The response:

```json
{
  "as_of": "2026-08-21",
  "universe_size": 530,
  "total_matched": 412,
  "rows": [ { "ticker": "MSFT", "name": "…", "close": "1086.890000", … } ]
}
```

**`total_matched` is the count before the row cap**, so the UI can say "412
matches, showing 100" — which is the signal that a screen is too loose. That is
the entire reason there is no pagination: a screen returning 412 of 530 wants
narrowing, not a second page. Cap `limit` at 200.

**Each row carries a fixed core plus every field the run referenced** — ticker,
name, exchange, asset_type, close, volume, change_percent_1d, then any field
named in a filter or in the sort. So the results table shows the numbers that
caused the match, and a user can see *why* a symbol is there without opening it.

## Data model

### `screener_presets`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | `uuid.uuid7()` |
| `user_id` | `UUID` | FK → `users.id`, `ON DELETE CASCADE` |
| `name` | `VARCHAR(80)` | Unique per user, like `watchlists.name` |
| `filters` | `JSONB` | The validated tree |
| `sort_field` | `VARCHAR(40)` | Registry key |
| `sort_direction` | `VARCHAR(4)` | `StrEnum` + CHECK — `asc`, `desc` |

Plus `TimestampMixin` and `Index("ix_screener_presets_user_id_created_at",
"user_id", "created_at")`.

`JSONB` for `filters` for the same reason `user_chart_preferences` uses it: read
whole, written whole, never a query predicate. The database enforces nothing
about its contents, so the Pydantic layer must — on the way in *and* on the way
out.

**A stored tree can go stale.** If a later phase removes an indicator column, a
preset naming it no longer validates. Handle it where it is cheapest to
tolerate: `GET /screener/presets` returns name, id, and timestamps **without
re-validating the tree**, so the list page always loads; opening or running a
preset validates and fails with a message naming the offending field. A user
whose one broken preset makes the whole screener page 500 has no way back.

Ordinary CRUD, so per `CLAUDE.md` no repository — the service uses the session
directly. The compiler is not CRUD and gets its own module.

## Frontend work

- **`src/features/screener/`** — `api/`, `components/`, `routes/`, and a `lib/`
  for anything pure.
- **Route `/screener`**, behind `AuthGuard`, linked from the app shell.
- **The filter builder is generated from `GET /screener/fields`.** Do not
  hand-maintain a field list in TypeScript: it would be a duplicate of the
  registry that drifts the first time a column is added, and the whole point of
  the registry is that there is one list.
- **A pure `buildFilterTree` / `parseFilterTree` pair** in `lib/`, converting
  between the builder's flat row-per-filter UI state and the nested wire format.
  This is where the phase's testable logic lives — the same role `toChartSeries`
  played in Phase 3 — so keep it pure and test it directly.
- **The results table links each ticker to `/symbols/:ticker`** — a link, not an
  import. `features/screener` must not import from `features/symbols`.
- Prices and percentages are parsed only in `lib/format.ts`, at the point of
  display or sorting, and are never passed around as numbers.
- Empty states are three different sentences and must not collapse into one:
  *no filters yet*, *no symbol matched*, and *the universe is empty — run the
  backfill*. The third is the one that would otherwise be diagnosed as the
  second.
- Add every new key to `src/lib/queryKeys.ts`. A run is keyed by the **filter
  tree and sort**, so two different screens do not share a cache entry.
- `make gen-api` and commit `schema.ts`.

## Testing

- **The compiler, directly and hardest.** Every operator; `compare` between two
  columns; nested `and`/`or` producing the right grouping — including the case
  that proves precedence is explicit, where `a AND (b OR c)` and `(a AND b) OR c`
  must return different sets.
- **The caps**, at exactly the limit and one past it, for depth, node count, and
  `values` length.
- **Unknown field, unknown operator, and a unit-mismatched `compare` are each a
  422**, raised by the schema, before any service runs.
- **Nulls do not match**: a symbol with a series too short for `sma_200` is
  absent from a `close > sma_200` screen and present in the `universe_size`.
- **`as_of` is shared**: a symbol whose latest bar is older than `max(trade_date)`
  is excluded rather than compared against a stale row.
- **`total_matched` exceeds `len(rows)`** when the cap bites, and equals it when
  it does not.
- **Append the preset endpoints to `CROSS_USER_RESOURCES`** — they take a path
  id, so this is the forge-another-user's-id shape, not the caller-scoped one.
  That means `GET`, `PATCH`, `DELETE`, and `POST /{id}/run`, and a fixture that
  builds the other user's preset through the API.
- **A preset with an unresolvable field**: the list endpoint still returns it,
  and running it is a clean 422 rather than a 500.
- Frontend: the builder adds, edits, and removes filters; the tree round-trips
  through `buildFilterTree`/`parseFilterTree`; the three empty states are
  distinguishable; the results table renders the referenced columns.

## Out of scope

**Fundamentals — P/E, market cap, sector, industry, dividend yield.** This is
the conspicuous absence, so it gets a reason rather than a bullet: `symbols` has
no such columns, and there is no provider account to fill them (see Phase 2 —
`Fake` is the only implementation). Adding them is a migration plus a provider
integration plus a second ingest path, which is a phase, not a filter. The DSL
does not need to change to accommodate them later: they are numeric and
categorical fields like any other, and arrive as registry entries.

Also out: intraday screening, which needs tables Phase 2 excluded · screening
*as of a past date*, which the data supports and which is a different feature
with its own UI · alerting on a screen, which is Phase 5 · sharing a preset
between users · exporting results to CSV · ranking or scoring matches, as
opposed to filtering them.

## Definition of done

- [ ] `make check` green; `make migration-check` reports no drift.
- [ ] Migrations round-trip: `alembic downgrade base` → `upgrade head`.
- [ ] `make backfill days=730` has been run over the full active universe, and
      the screen runs against **hundreds** of symbols rather than four. Runtime
      recorded in the PR.
- [ ] The three new indicator columns are populated by the existing backfill,
      not by a bespoke script.
- [ ] `EXPLAIN` of the run query shows the new `trade_date` indexes in use;
      plan pasted in the PR.
- [ ] No user-supplied string reaches SQL: every field and operator resolves
      through the registry, and an unknown one is a 422 from the schema layer.
- [ ] Depth, node-count, and `values` caps enforced with tests at the boundary.
- [ ] Nulls do not match, are not `COALESCE`d, and the behaviour has a test.
- [ ] `total_matched` is the pre-cap count, and the UI shows it against the
      returned row count.
- [ ] `CROSS_USER_RESOURCES` covers all four preset endpoints that take an id.
- [ ] The filter builder is driven by `GET /screener/fields`, with no field list
      duplicated in TypeScript.
- [ ] In a browser: build a two-filter screen including one `compare`, run it,
      sort it, save it, reload, re-run it from the saved preset, and click
      through to a symbol's chart.
- [ ] `schema.ts` regenerated and committed.
- [ ] `docs/ROADMAP.md` Phase 4 marked complete; `CLAUDE.md` updated with the
      conventions this phase settles.

## Open questions

None blocking. Four were answered while writing this and are recorded above
rather than left implicit: the screen reads **stored bars at one shared
`as_of`**, never `latest_quotes`; anything needing history is **an indicator
column, not a window function in the query**; `compare` between two columns is
**in scope and is the point**; and fundamentals are **out of scope with a
reason**, not forgotten.

One thing was left to decide *during* the phase rather than at spec time:
whether `pct_from_52w_high` reads better as a positive distance below the high
or as a negative percentage.

**Decided: signed, negative below the high** — `-0.50` for a stock half a
percent off its high. With a real table to look at, the deciding argument was
not this field on its own but the family it belongs to. There are four
`pct_from_*` fields, and `pct_from_sma_50` is unambiguously positive above its
reference; a positive-distance-*below* reading for this one would make the two
columns disagree about which way is up while sharing a naming convention and a
unit. The label stays `% from 52-week high` and the sign is what says which
direction — spelling out "below" for one of four otherwise identical columns
would trade a small ambiguity for a larger inconsistency. `pct_from_52w_low`
was added alongside it for the same symmetry.
