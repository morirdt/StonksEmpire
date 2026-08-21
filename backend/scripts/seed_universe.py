"""Seed the tradable universe from the configured provider.

    uv run python -m scripts.seed_universe [--limit N]

Idempotent: every row is upserted on ``ticker``, so running it twice changes
nothing but ``last_refreshed_at``. Nothing runs it automatically — scheduling
arrives with the Phase 5 worker, and an unattended universe fetch on ``make up``
would spend a free-tier quota before anyone noticed.
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.config import get_settings
from app.db.session import SessionFactory, dispose_engine
from app.services.market_data_service import MarketDataService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of symbols seeded (defaults to MARKET_DATA__UNIVERSE_MAX_SYMBOLS).",
    )
    return parser.parse_args()


async def seed(limit: int | None) -> int:
    settings = get_settings()
    async with SessionFactory() as session:
        service = MarketDataService(session)
        count = await service.seed_universe(limit=limit)

    print(f"seeded {count} symbols from provider {settings.market_data.provider!r}")
    return count


async def main() -> int:
    args = _parse_args()
    try:
        await seed(args.limit)
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
