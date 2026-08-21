"""Backfill daily bars and recompute indicators.

    uv run python -m scripts.backfill_bars [--days N] [--tickers AAPL,MSFT]

Idempotent. Bars upsert on ``(symbol_id, trade_date)`` and indicators are
recomputed from the full stored series, so a second run over the same window
produces byte-identical rows — which the integration suite asserts rather than
assumes.

Two years is the default because it covers ``sma_200`` with roughly 250 bars of
headroom. Deeper history is just a bigger ``--days``; it is not a migration.

Like the seed, this never runs on its own. Phase 5's worker is what schedules it.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, dispose_engine
from app.models.symbol import Symbol
from app.repositories.symbol_repository import SymbolRepository
from app.services.market_data_service import MarketDataService

DEFAULT_DAYS = 730


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"How much history to fetch, in calendar days (default {DEFAULT_DAYS}).",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated tickers. Defaults to the whole active universe.",
    )
    return parser.parse_args()


async def _resolve_symbols(session: AsyncSession, tickers: str | None) -> list[Symbol]:
    repository = SymbolRepository(session)
    if tickers is None:
        return await repository.list_active()

    wanted = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    found = await repository.get_many_by_ticker(wanted)
    missing = sorted(set(wanted) - {s.ticker for s in found})
    if missing:
        # Warn rather than abort: backfilling the twelve tickers that do exist
        # is more useful than refusing because one was mistyped.
        print(f"warning: not in the universe, skipping: {', '.join(missing)}")
    return found


async def backfill(days: int, tickers: str | None) -> tuple[int, int]:
    started = time.perf_counter()
    total_bars = total_indicators = 0

    async with SessionFactory() as session:
        symbols = await _resolve_symbols(session, tickers)
        if not symbols:
            print("nothing to backfill — is the universe seeded? (make seed)")
            return 0, 0

        service = MarketDataService(session)
        for index, symbol in enumerate(symbols, start=1):
            bars, indicators = await service.backfill_symbol(symbol, days=days)
            total_bars += bars
            total_indicators += indicators
            # Progress matters here: a full-universe backfill is minutes long,
            # and a silent process looks identical to a hung one.
            print(
                f"[{index}/{len(symbols)}] {symbol.ticker}: "
                f"{bars} bars, {indicators} indicator rows",
                flush=True,
            )

    elapsed = time.perf_counter() - started
    print(f"backfilled {total_bars} bars and {total_indicators} indicator rows in {elapsed:.1f}s")
    return total_bars, total_indicators


async def main() -> int:
    args = _parse_args()
    try:
        await backfill(args.days, args.tickers)
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
