"""Alerts, and the history of their firings.

An alert is a one-symbol screen with a schedule: its ``condition`` is a Phase 4
filter tree, stored as ``JSONB`` and validated on the way in *and* out, exactly
like a screener preset's. A ``screen`` alert has no condition of its own; it
watches a preset and fires on symbols that newly match it.

Firing is edge-triggered. ``last_state`` is the last *definite* result (true or
false) the condition produced. A ``NULL`` evaluation leaves it unchanged, and
an alert fires only on a move from false to true. The evaluation rules are in
``app/services/alerts/evaluation.py``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AlertKind, AlertStatus, TriggerMode
from app.db.base import Base, TimestampMixin


def _in(column: str, values: type[AlertKind] | type[AlertStatus] | type[TriggerMode]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v.value}'" for v in values))


#: Which of symbol_id, preset_id, and condition each kind requires. The schema
#: layer enforces this too; the database is the last line of defence against a
#: worker, or a later migration, writing a half-formed row.
_SHAPE = (
    "(kind = 'screen' AND preset_id IS NOT NULL AND symbol_id IS NULL "
    "AND condition IS NULL AND trigger_mode = 'rearm') "
    "OR (kind IN ('intraday', 'end_of_day') AND symbol_id IS NOT NULL "
    "AND condition IS NOT NULL AND preset_id IS NULL)"
)


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(_in("kind", AlertKind), name="kind_valid"),
        CheckConstraint(_in("trigger_mode", TriggerMode), name="trigger_mode_valid"),
        CheckConstraint(_in("status", AlertStatus), name="status_valid"),
        CheckConstraint(_SHAPE, name="shape_matches_kind"),
        CheckConstraint("cooldown_minutes BETWEEN 5 AND 1440", name="cooldown_in_range"),
        # A disabled alert always says why, and nothing else carries a reason.
        CheckConstraint(
            "(status = 'disabled') = (disabled_reason IS NOT NULL)",
            name="disabled_reason_iff_disabled",
        ),
        Index("ix_alerts_user_id_created_at", "user_id", "created_at"),
        # Both evaluation jobs ask "active alerts of this kind for these symbols".
        Index(
            "ix_alerts_active_kind_symbol_id",
            "kind",
            "symbol_id",
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_alerts_preset_id", "preset_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Not unique: two alerts named "AAPL" are fine.
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Symbols are deactivated, never deleted, so this never dangles.
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("symbols.id"), nullable=True
    )
    preset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("screener_presets.id", ondelete="CASCADE"), nullable=True
    )
    condition: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    trigger_mode: Mapped[str] = mapped_column(String(8), nullable=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    disabled_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_state: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: ``screen`` alerts only: yesterday's matches, to tell today's new entrants.
    last_matched_symbol_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(Uuid), nullable=True
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Alert {self.id} {self.kind} {self.status}>"


class AlertTrigger(Base):
    """One firing, with a snapshot of the values that made it fire.

    The snapshot is the Phase 4 rule that a result shows *why*: "AAPL crossed
    above its 200-day" carries the price and the average at that minute. Kept
    indefinitely, because the history is part of the feature.
    """

    __tablename__ = "alert_triggers"
    __table_args__ = (
        Index("ix_alert_triggers_alert_id_triggered_at", "alert_id", "triggered_at"),
        Index("ix_alert_triggers_user_id_triggered_at", "user_id", "triggered_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    alert_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    #: Denormalised, per the user-scoped convention.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The bar date for end-of-day and screen alerts.
    as_of_date: Mapped[date | None] = mapped_column(nullable=True)
    #: The quote's ``fetched_at`` for intraday alerts.
    as_of_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    def __repr__(self) -> str:
        return f"<AlertTrigger {self.id} alert={self.alert_id}>"
