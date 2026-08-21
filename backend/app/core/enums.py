"""Enumerations shared across layers.

These live in ``core`` rather than beside a model because both the ORM and the
provider dataclasses need them, and ``app/integrations`` must not import from
``app/models`` — the point of the provider seam is that it knows nothing about
persistence.

Per ``CLAUDE.md`` these are stored as ``VARCHAR`` plus a CHECK constraint, never
a native Postgres enum: the value sets here change often and ``ALTER TYPE`` is
painful.
"""

from __future__ import annotations

from enum import StrEnum


class AssetType(StrEnum):
    """What kind of instrument a symbol is.

    Deliberately coarse. The distinctions that matter downstream are "can it be
    charted like an equity" and "is it a fund", not the full taxonomy a provider
    reports.
    """

    COMMON_STOCK = "common_stock"
    ETF = "etf"
    ADR = "adr"
    REIT = "reit"
    PREFERRED = "preferred"
    OTHER = "other"
