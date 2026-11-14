"""Evaluating alerts inside the worker's jobs, and recording what fired.

**One bad alert stops nothing.** Each alert is evaluated inside its own
savepoint. An alert whose stored tree no longer validates (its preset names a
field that has since been removed, the case Phase 4 called out), or whose
symbol has been delisted, is set to ``disabled`` with a reason, and its owner
gets a notification. An unexpected error rolls back that alert's savepoint
alone and is logged. Either way the run continues. A worker that crashed on one
stale preset would stop every user's alerts, with nothing visible to show for
it.

**Recording a firing is one transaction**, which the caller commits: the
alert's new state, an ``alert_triggers`` row with a snapshot of the values that
made it fire, and the ``notifications`` row. Nothing outside Postgres happens
inside it. The ``notification_created`` hints are collected here and published
by the caller *after* the commit, through :func:`publish_hints`. A crash after
the commit loses at most a push. A crash before it loses nothing, because the
next run finds the same edge.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AlertKind, AlertStatus, NotificationKind, TriggerMode
from app.core.events import Publisher
from app.core.exceptions import ValidationError
from app.models.alert import Alert, AlertTrigger
from app.models.notification import Notification
from app.models.screener import ScreenerPreset
from app.models.symbol import Symbol
from app.repositories.alert_repository import AlertRepository, Evaluation
from app.schemas.screener import FilterNode, parse_stored_filters
from app.services.alerts.evaluation import decide, describe_condition, new_entrants
from app.services.screener.fields import FIELDS, INTRADAY_FIELDS, KNOWN_FIELDS

__all__ = ["AlertEvaluator", "FiringReport", "publish_hints"]

logger = structlog.stdlib.get_logger(__name__)

#: How many new entrants a screen notification names before "and N more".
_NAMED_ENTRANTS: Final = 10


@dataclass
class FiringReport:
    """What one evaluation pass did. Logged with the job's result."""

    evaluated: int = 0
    fired: int = 0
    suppressed: int = 0
    disabled: int = 0
    failed: int = 0
    #: ``(user_id, notification_id)`` to publish once the caller has committed.
    hints: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)

    def merge(self, other: FiringReport) -> FiringReport:
        return FiringReport(
            evaluated=self.evaluated + other.evaluated,
            fired=self.fired + other.fired,
            suppressed=self.suppressed + other.suppressed,
            disabled=self.disabled + other.disabled,
            failed=self.failed + other.failed,
            hints=[*self.hints, *other.hints],
        )


async def publish_hints(report: FiringReport, publish: Publisher) -> None:
    """Publish ``notification_created`` for each firing. Call after the commit."""
    for user_id, notification_id in report.hints:
        await publish(
            {
                "type": "notification_created",
                "user_id": str(user_id),
                "notification_id": str(notification_id),
            }
        )


class AlertEvaluator:
    """Evaluates batches of alerts at one instant, in the caller's session."""

    def __init__(self, session: AsyncSession, now: datetime) -> None:
        self._session = session
        self._now = now
        self._alerts = AlertRepository(session)

    # ---------------------------------------------------------------- batches

    async def run_intraday(
        self,
        symbol_ids: list[uuid.UUID],
        *,
        fetched_since: datetime,
        indicators_as_of: date | None,
    ) -> FiringReport:
        """Active intraday alerts on symbols this poll refreshed."""
        ids = await self._alerts.active_ids(AlertKind.INTRADAY, symbol_ids=symbol_ids)

        async def evaluate(tree: FilterNode, symbol_id: uuid.UUID) -> Evaluation:
            return await self._alerts.evaluate_intraday(
                tree,
                symbol_id,
                fetched_since=fetched_since,
                indicators_as_of=indicators_as_of,
            )

        return await self._each(ids, lambda alert, report: self._single(alert, report, evaluate))

    async def run_end_of_day(self, as_of: date) -> FiringReport:
        ids = await self._alerts.active_ids(AlertKind.END_OF_DAY)

        async def evaluate(tree: FilterNode, symbol_id: uuid.UUID) -> Evaluation:
            return await self._alerts.evaluate_end_of_day(tree, symbol_id, as_of)

        return await self._each(
            ids, lambda alert, report: self._single(alert, report, evaluate, as_of=as_of)
        )

    async def run_screens(self, as_of: date) -> FiringReport:
        ids = await self._alerts.active_ids(AlertKind.SCREEN)
        return await self._each(ids, lambda alert, report: self._screen(alert, report, as_of))

    async def disable_on_symbols(self, symbol_ids: list[uuid.UUID]) -> FiringReport:
        """Disable every live alert on these (just deactivated) symbols."""
        ids = await self._alerts.live_ids_on_symbols(symbol_ids)

        async def disable(alert: Alert, report: FiringReport) -> None:
            ticker = await self._ticker(alert.symbol_id)
            self._disable(alert, report, f"{ticker} is no longer listed, so this alert stopped.")

        return await self._each(ids, disable, require_active=False)

    async def _each(
        self,
        ids: list[uuid.UUID],
        handle: Callable[[Alert, FiringReport], Awaitable[None]],
        *,
        require_active: bool = True,
    ) -> FiringReport:
        report = FiringReport()
        for alert_id in ids:
            try:
                async with self._session.begin_nested():
                    alert = await self._session.get(Alert, alert_id, populate_existing=True)
                    if alert is None or (require_active and alert.status != "active"):
                        continue
                    await handle(alert, report)
            except Exception:
                report.failed += 1
                logger.exception("alert_evaluation_failed", alert_id=str(alert_id))
        return report

    # ------------------------------------------------------------ one alert

    async def _single(
        self,
        alert: Alert,
        report: FiringReport,
        evaluate: Callable[[FilterNode, uuid.UUID], Awaitable[Evaluation]],
        *,
        as_of: date | None = None,
    ) -> None:
        """An intraday or end-of-day alert: one symbol, one condition."""
        symbol = await self._session.get(Symbol, alert.symbol_id)
        if symbol is None or not symbol.is_active:
            ticker = symbol.ticker if symbol is not None else "This symbol"
            self._disable(alert, report, f"{ticker} is no longer listed, so this alert stopped.")
            return

        intraday = alert.kind == AlertKind.INTRADAY.value
        try:
            tree = parse_stored_filters(
                alert.condition,
                INTRADAY_FIELDS if intraday else FIELDS,
                catalogue="GET /alerts/fields",
                what="alert",
            )
        except ValidationError as exc:
            self._disable(alert, report, exc.detail)
            return

        state, values = await evaluate(tree, symbol.id)
        report.evaluated += 1
        outcome = decide(
            last_state=alert.last_state,
            new_state=state,
            trigger_mode=TriggerMode(alert.trigger_mode),
            cooldown_minutes=alert.cooldown_minutes,
            last_triggered_at=alert.last_triggered_at,
            now=self._now,
        )
        alert.last_state = outcome.last_state
        alert.last_evaluated_at = self._now
        if outcome.suppressed:
            report.suppressed += 1
            logger.info("alert_edge_in_cooldown", alert_id=str(alert.id))
        if not outcome.fire:
            return

        summary = describe_condition(alert.condition) or ""
        readings = " · ".join(
            f"{KNOWN_FIELDS[key].label} {value}" for key, value in values.items() if value
        )
        self._record_firing(
            alert,
            report,
            snapshot={"ticker": symbol.ticker, "condition": summary, "values": values},
            title=f"{symbol.ticker}: {alert.name}",
            body=f"{summary}. {readings}" if readings else summary,
            payload={"ticker": symbol.ticker},
            as_of_date=None if intraday else as_of,
            as_of_time=self._now if intraday else None,
        )

    async def _screen(self, alert: Alert, report: FiringReport, as_of: date) -> None:
        """A screen alert: fires on symbols that newly match its preset."""
        preset = await self._session.get(ScreenerPreset, alert.preset_id)
        if preset is None:  # pragma: no cover - the FK cascades the alert away
            return
        try:
            tree = parse_stored_filters(preset.filters)
        except ValidationError as exc:
            self._disable(alert, report, exc.detail)
            return

        matches = await self._alerts.screen_matches(tree, as_of)
        report.evaluated += 1
        entrants = new_entrants(alert.last_matched_symbol_ids, [sid for sid, _ in matches])
        alert.last_matched_symbol_ids = [sid for sid, _ in matches]
        alert.last_evaluated_at = self._now
        if not entrants:
            return

        tickers_by_id = dict(matches)
        tickers = [tickers_by_id[sid] for sid in entrants]
        named = ", ".join(tickers[:_NAMED_ENTRANTS])
        more = len(tickers) - _NAMED_ENTRANTS
        noun = "match" if len(tickers) == 1 else "matches"
        self._record_firing(
            alert,
            report,
            snapshot={
                "preset": preset.name,
                "entrants": tickers,
                "matched": len(matches),
                "as_of": as_of.isoformat(),
            },
            title=f"{preset.name}: {len(tickers)} new {noun}",
            body=f"{named} and {more} more now match." if more > 0 else f"{named} now match.",
            payload={"preset_id": str(preset.id), "tickers": tickers},
            as_of_date=as_of,
        )

    # ---------------------------------------------------------------- helpers

    async def _ticker(self, symbol_id: uuid.UUID | None) -> str:
        symbol = await self._session.get(Symbol, symbol_id) if symbol_id else None
        return symbol.ticker if symbol is not None else "This symbol"

    def _disable(self, alert: Alert, report: FiringReport, reason: str) -> None:
        alert.status = AlertStatus.DISABLED.value
        alert.disabled_reason = reason[:200]
        alert.last_evaluated_at = self._now
        notification = Notification(
            id=uuid.uuid7(),
            user_id=alert.user_id,
            kind=NotificationKind.ALERT_DISABLED.value,
            title=f"Alert disabled: {alert.name}"[:200],
            body=reason,
            payload={"alert_id": str(alert.id)},
            alert_id=alert.id,
        )
        self._session.add(notification)
        report.disabled += 1
        report.hints.append((alert.user_id, notification.id))
        logger.info("alert_disabled", alert_id=str(alert.id), reason=reason)

    def _record_firing(
        self,
        alert: Alert,
        report: FiringReport,
        *,
        snapshot: dict[str, object],
        title: str,
        body: str,
        payload: dict[str, object],
        as_of_date: date | None = None,
        as_of_time: datetime | None = None,
    ) -> None:
        alert.last_triggered_at = self._now
        if alert.trigger_mode == TriggerMode.ONCE.value:
            alert.status = AlertStatus.TRIGGERED.value
        self._session.add(
            AlertTrigger(
                alert_id=alert.id,
                user_id=alert.user_id,
                triggered_at=self._now,
                as_of_date=as_of_date,
                as_of_time=as_of_time,
                snapshot=snapshot,
            )
        )
        notification = Notification(
            id=uuid.uuid7(),
            user_id=alert.user_id,
            kind=NotificationKind.ALERT_TRIGGERED.value,
            title=title[:200],
            body=body,
            payload={"alert_id": str(alert.id), **payload},
            alert_id=alert.id,
        )
        self._session.add(notification)
        report.fired += 1
        report.hints.append((alert.user_id, notification.id))
