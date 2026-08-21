"""The curated symbol universe, loaded from a data file.

Normally ``list_symbols()`` would ask a provider what exists. With ``fake`` as
the only implementation there is no upstream to ask, so the universe ships in
the repo instead — roughly 530 liquid US names and ETFs, which is broad enough
for Phase 3 charting and Phase 4 screening to be interesting and small enough to
backfill in minutes.

A CSV rather than a Python literal because it is data, not code: it diffs
readably, and a real provider's output can be dumped into the same shape when
one arrives.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from app.core.enums import AssetType
from app.integrations.market_data.base import SymbolInfo

__all__ = ["load_universe"]

_UNIVERSE_PATH = Path(__file__).parent / "data" / "universe.csv"


@lru_cache(maxsize=1)
def load_universe(path: Path | None = None) -> tuple[SymbolInfo, ...]:
    """Read the curated universe. Cached — the file never changes at runtime."""
    source = path or _UNIVERSE_PATH
    with source.open(encoding="utf-8", newline="") as handle:
        return tuple(
            SymbolInfo(
                ticker=row["ticker"].strip().upper(),
                name=row["name"].strip(),
                exchange=row["exchange"].strip() or None,
                asset_type=AssetType(row["asset_type"].strip()),
                currency=row["currency"].strip().upper(),
            )
            for row in csv.DictReader(handle)
        )
