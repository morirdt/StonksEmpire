# Phase 3 — Analysis & Charting

Implementable spec. Read `CLAUDE.md` first for the data, auth, market data, and
layering conventions — this document does not repeat them. Phase 2 settled the
data this phase draws: `daily_bars` and `daily_indicators` are already populated
and already correct, so nothing here recomputes or refetches anything.

**Goal:** a signed-in user can open a symbol, read its price history as a
candlestick chart with volume and indicators, change the range, choose which
indicators are shown, and have that choice remembered on their next visit.

## The shape of this phase

Smaller than Phase 2 and deliberately so. It introduces **no new backend
concepts**: two read endpoints over tables that already exist, plus one tiny
preferences resource. Almost all the work — and all of the risk — is on the
frontend, in a charting library the project has not used before.

Build in this order:

1. **The read endpoints**, which are thin enough to finish in an afternoon.
2. **The chart component**, in isolation, against a fixed bar series.
3. **The symbol detail page** that composes them.
4. **Preferences**, last, because everything works without them.

## Dependencies to add

Frontend (`cd frontend && pnpm add …`):

| Package | Purpose |
|---|---|
| `lightweight-charts` | Candlestick, volume, and line series |

Backend: **none.**

### Why `lightweight-charts`

Chosen over ECharts, visx, and Recharts. It is purpose-built for exactly this
chart, ~45kb, Apache-2.0, and renders years of bars without complaint. Recharts
has no candlestick series at all and would mean hand-rolling one from custom
shapes; visx would mean building scales, axes, and crosshair from primitives;
ECharts brings a much larger bundle for a superset nothing here needs.

Three costs come with it, and they are accepted rather than worked around:

- **It is imperative.** It wants a DOM node and returns a chart object with
  `setData` methods. That gets wrapped once (see *Frontend work*) and never
  touched directly by a page.
- **It renders to canvas.** There is no DOM for Testing Library to assert on, so
  frontend tests cover the data shaping and the controls, never the pixels. This
  is a real reduction in coverage and the reason the shaping logic is a separate,
  directly-tested pure function.
- **It knows nothing about MUI.** Every colour is passed in explicitly from the
  theme, per mode. See *Chart design*.

## Data model

One new table. Nothing else changes.

### `user_chart_preferences`

Scoped **globally per user**, not per symbol: one row, applied to every chart.
Someone who wants MACD wants it everywhere, and a per-symbol table grows with
idle browsing while leaving every newly-visited symbol back at the defaults.

| Column | Type | Notes |
|---|---|---|
| `user_id` | `UUID` PK | FK → `users.id`, `ON DELETE CASCADE`. The PK *is* the FK — one row per user |
| `default_range` | `VARCHAR(8)` | `StrEnum` + CHECK — `1M`, `3M`, `6M`, `1Y`, `2Y`, `MAX` |
| `active_overlays` | `JSONB` | List of price-overlay keys, e.g. `["sma_20", "sma_50"]` |
| `active_oscillators` | `JSONB` | List of lower-pane keys, e.g. `["rsi_14", "macd"]` |

`user_id` as the primary key is what makes the write a single idempotent upsert
and makes "two rows for one user" unrepresentable.

**`JSONB` rather than a boolean column per indicator**, which is a deliberate
departure from how `daily_indicators` is shaped. That table is wide because
Phase 4 *filters* on it in SQL; this one is only ever read whole and written
whole, and is never a query predicate. Validate the contents against a
`StrEnum` in Pydantic on write — the database is not the thing enforcing it, so
the schema layer must.

## Endpoints

All under `/api/v1`, all requiring `CurrentUser`.

| Method | Path | Notes |
|---|---|---|
| GET | `/symbols/{ticker}/bars?range=&start=&end=` | OHLCV, oldest first. `404` when the ticker is unknown |
| GET | `/symbols/{ticker}/indicators?range=&start=&end=` | Same window, same ordering, aligned by `trade_date` |
| GET | `/me/chart-preferences` | The caller's row, or defaults if none exists |
| PUT | `/me/chart-preferences` | Upsert. Full replacement, not a patch |

**Two endpoints rather than one combined `/chart`.** They are two round trips,
but they are parallel ones rather than N+1, and they stay independently useful:
Phase 4 wants indicators without bars, and a future export wants bars without
indicators. Combining them would optimise away one request at the cost of an
endpoint that only ever serves one screen.

`range` is a convenience over `start`/`end`: it accepts the same `StrEnum` the
preferences use and resolves server-side against today's date. Supplying both
`range` and an explicit window is a `422` rather than a silent precedence rule.

**`GET /me/chart-preferences` returns defaults rather than `404` when the user
has never saved any.** A client that must special-case "no preferences yet"
will get it wrong on first load, and there is no meaningful difference between
"unset" and "set to the defaults".

Cap the response at the number of bars two years can hold and reject a wider
window; there is no pagination, because there is no case where a chart wants
more rows than a screen can plot.

## Chart design

This section is not decoration. The colour choices below were **validated with
`scripts/validate_palette.js` from the `dataviz` skill**, in both modes, against
this project's actual surfaces — not chosen by eye. Re-run it if any value here
changes.

### Layout — three stacked panes, one shared time axis

1. **Price** — candlesticks, plus up to three moving-average overlays.
2. **Volume** — its own pane.
3. **Oscillators** — RSI and/or MACD, each in its own pane when enabled.

**Volume gets a pane; it is never overlaid on price with a second y-axis.** A
dual-axis chart is the single most common charting mistake: the two scales are
independent, so any apparent crossing or divergence between price and volume is
an artefact of how the axes were scaled and means nothing. Panes share the time
axis and nothing else.

### Candlesticks — colour is not sufficient on its own

Up candles use the `profit` palette token, down candles `loss`. That much is
conventional. What is **required** is the secondary encoding:

> **Up candles are hollow (outlined in `profit`); down candles are filled
> solid with `loss`.**

The validator measures the project's existing `profit` (`#0f9d58`) against
`loss` (`#e5484d`) at **ΔE 4.4 under deuteranopia** — far below the ≥8 target.
A red-green pair is the classic colour-vision collapse, so a chart that encodes
direction in hue alone is unreadable for a substantial minority of users. Fill
carries the same information and costs nothing; it is also the older convention.

This is a pre-existing property of the Phase 0 theme, not something this phase
introduces, and it does **not** make the Phase 2 quote grid wrong — that grid
already signs its numbers (`+1.50` / `-2.25`), which is the same kind of
secondary encoding. Do not "fix" the palette tokens; add the encoding.

### Volume — one neutral hue

Volume bars are a single recessive neutral, **not** tinted by the day's
direction. Volume's job is magnitude, and magnitude takes one hue. Colouring it
green and red re-states direction the price pane has already shown, in exactly
the pair that fails colour-vision separation, and buys nothing.

### Indicator overlays — at most three at once

The price pane accepts **three concurrent overlays**, and the toggle UI enforces
it. Beyond three, lines over candles stop being readable regardless of palette.

Colour is assigned **per indicator identity, fixed** — `sma_50` is amber whether
it is the only overlay or the third one enabled. Colour must never follow the
order things were switched on, or turning one line off repaints the others.

> **Which forces the overlay set to be exactly the three SMAs.** Fixed
> per-identity colour and three palette slots together mean the number of
> overlay *identities* cannot exceed three, or some pair shares a colour
> permanently — and assigning slots by position instead is the repainting this
> paragraph rules out. `PriceOverlay` is therefore `sma_20` / `sma_50` /
> `sma_200`, and the stored EMAs are not offered on the price pane: they exist
> to feed MACD, which has a pane of its own. A fourth overlay needs a fourth
> validated colour first.

| Slot | Dark | Light |
|---|---|---|
| 1 — blue (`brand[400]` / `brand[500]`) | `#3480fb` | `#1565d8` |
| 2 — amber | `#bf8200` | `#a86f00` |
| 3 — magenta | `#d55181` | `#c2185b` |

Validated all-pairs in both modes: worst CVD ΔE 12.3 dark / 10.0 light, worst
normal-vision ΔE 19.2 / 20.1, all ≥ 3:1 against their surface. These are
validated **among themselves**, which is the right comparison — they are 2px
continuous lines against discrete candle bodies, and mark shape already
separates them from the candles.

> **Two dark values were corrected during implementation.** The table
> originally read `#5f9dff` (slot 1) and `#eda100` (slot 2). Re-running the
> validator before writing any chart code showed both sitting **above the dark
> lightness band** (OKLCH L 0.699 and 0.764 against a ceiling of 0.67), which
> is a FAIL the original figures did not mention — they reported only the CVD
> and contrast checks. Snapping each to the nearest passing step gives the
> values above: one step down the same brand ramp for the blue, a darker amber
> for slot 2. The magenta was already inside the band and is unchanged. Both
> modes now report ALL CHECKS PASS with no warnings, all-pairs. Re-run before
> changing anything here:
>
> ```
> node scripts/validate_palette.js "#3480fb,#bf8200,#d55181" \
>   --mode dark --surface "#141922" --pairs all
> node scripts/validate_palette.js "#1565d8,#a86f00,#c2185b" \
>   --mode light --surface "#ffffff" --pairs all
> ```

### Oscillator panes

- **RSI** — one line, so no legend box; the pane title names it. Reference
  lines at 30 and 70 in a recessive neutral, and a fixed `0–100` scale, because
  an auto-scaled RSI hides the only thing it is read for.
- **MACD** — the MACD line in slot 1 (blue) and the signal line in slot 2
  (amber). Validated as a pair: CVD ΔE 28.8 dark, 29.1 light. The histogram is
  **diverging around zero** — `profit` above, `loss` below, with the zero line
  as the neutral midpoint. Position above or below zero is itself the secondary
  encoding, so the histogram needs no fill trick.

### Everything else the chart owes the reader

- **A crosshair and tooltip, by default.** On hover it reports the date, OHLC,
  volume, and the value of every active indicator at that bar. A chart of 500
  candles cannot be read to a precise value without one.
- **A legend whenever two or more series share a pane**, so identity is never
  carried by colour alone.
- ~~**A table view.**~~ **Cut during implementation, at the user's request.**
  The reasoning for it still stands and is worth recording: the chart is canvas,
  so it is invisible to a screen reader and unselectable, and a table of the
  same rows was the accessible equivalent. What remains is the crosshair
  readout, which is real text and reports the selected bar — one row at a time
  rather than the whole series. Phase 7's accessibility pass should revisit
  this.
- **Light and dark are selected, not flipped.** Each mode has its own validated
  steps, above. Read them from the MUI theme at render time and rebuild the
  series options when the scheme changes.
- **Empty, loading, and error states**, per `CLAUDE.md`. A symbol with no
  backfilled bars is the common case here — the universe is seeded but only
  some symbols are backfilled — so "no price history yet for this symbol,
  run `make backfill`" is a real state, not a corner case.

## Frontend work

- **`src/components/chart/`** — the imperative wrapper and nothing
  symbol-specific: a `CandlestickChart` that takes already-shaped series and
  theme colours as props, owns the `createChart` lifecycle, and disposes on
  unmount. It lives in `components/`, not in a feature, because Phase 6's
  equity curve needs the same wrapper and **features may not import each
  other**.
- **`src/features/symbols/`** gains `routes/SymbolDetailPage.tsx` and its
  controls. The detail page is a symbol feature; the chart is shared machinery.
- **Route `/symbols/:ticker`**, behind `AuthGuard`. The Phase 2 watchlist grid
  links each ticker to it — a link, not an import, so the feature boundary
  holds.
- **A pure `toChartSeries(bars, indicators, activeKeys)`** in
  `features/symbols/lib/`, converting API rows to the library's series format.
  This is where the canvas coverage gap is paid back: it is pure, so it is
  tested directly and thoroughly.
- Preferences load once and seed the controls; changing a control writes back
  optimistically. A failed write must not silently discard the user's choice.
- Add every new key to `src/lib/queryKeys.ts`. Bars and indicators are keyed by
  ticker **and** resolved range.
- `make gen-api` and commit `schema.ts`.

## Testing

- **`toChartSeries` against known input** — including a series whose leading
  indicator values are null, which is every series (a 200-day average has no
  value for its first 199 bars). Nulls must produce gaps, never zeros; a zero
  plots a line to the bottom of the chart and looks like a crash.
- Range resolution: each `StrEnum` value maps to the window it claims, and
  `range` together with explicit `start`/`end` is a `422`.
- Bars and indicators for the same window return the **same dates in the same
  order**, since the chart aligns them positionally.
- An unknown ticker is `404` on both endpoints.
- Preferences: defaults come back before anything is saved, a `PUT` round-trips,
  a second `PUT` replaces rather than merges, and an unknown indicator key is
  rejected by the schema.
- **Append the preferences endpoints to `CROSS_USER_RESOURCES`.** They take no
  path id, so there is no row to forge — assert instead that user A's `PUT`
  leaves user B's row untouched, which is the failure this resource can actually
  have.
- Frontend: the range selector, the indicator toggles, the three-overlay cap,
  the crosshair readout, and the empty/loading/error states. **Not** the
  rendered pixels — say so in the test file, so the gap is visible rather than
  assumed.

## Out of scope

Drawing tools (trendlines, fibs, annotations) · multi-symbol compare ·
intraday or sub-daily bars, which Phase 2 also excluded and which would need new
tables and a new ingest path · chart PNG export — `lightweight-charts` exposes
`takeScreenshot()`, so this is close to free whenever it is wanted, but it is
not wanted yet · alerts drawn on the chart, which belong with Phase 5 · anything
that recomputes indicators, which is Phase 2's job and already done.

## Definition of done

- [ ] `make check` green; `make migration-check` reports no drift.
- [ ] Migration round-trips: `alembic downgrade base` → `upgrade head`.
- [ ] Bars and indicators endpoints return aligned, ordered windows; unknown
      ticker is `404`; `range` plus explicit dates is `422`.
- [ ] Preferences return defaults before first save, and a `PUT` round-trips.
- [ ] `CROSS_USER_RESOURCES` covers the preferences endpoints and one user's
      write provably cannot reach another's row.
- [ ] The palette in *Chart design* re-validates clean in both modes if changed.
- [ ] Candles carry direction by **fill as well as hue**; volume is one neutral
      hue; no pane uses a second y-axis.
- [ ] In a browser: open a symbol from a watchlist, change the range, toggle
      indicators, read a value off the crosshair, reload and find the choices
      remembered.
- [x] ~~The table view shows the same rows as the chart.~~ Cut; see above.
- [ ] `schema.ts` regenerated and committed.
- [ ] `docs/ROADMAP.md` Phase 3 marked complete; `CLAUDE.md` updated with any
      new convention settled here.

## Open questions

None outstanding. The four that shaped this spec were answered before it was
written: `lightweight-charts` as the library, overlays on price with RSI and
MACD in their own panes, preferences stored **server-side and global per user**,
and drawing tools, multi-symbol compare, intraday, and PNG export all out of
scope.
