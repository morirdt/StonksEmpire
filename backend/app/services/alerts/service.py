"""Alert CRUD and trigger history, scoped to the caller.

Ordinary user-scoped CRUD on the session, per ``CLAUDE.md``. Another user's
alert is a **404, not a 403**, because a 403 would confirm that the row exists.

Two rules beyond plain CRUD:

- **The active cap** (``ALERTS__MAX_ACTIVE_PER_USER``, 50 by default). Every
  intraday alert can add a symbol to the worker's hot set, and that set is
  re-quoted every minute against the provider's quota. Creating, resuming, or
  re-arming past the cap is a 422.
- **Entering ``active`` resets the edge state.** That covers a new condition, a
  resume, or a re-arm. The next definite evaluation then only records, so
  none of these can fire on a stale edge from before the change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.enums import AlertKind, AlertStatus, TriggerMode
from app.core.exceptions import NotFoundError, ValidationError
from app.models.alert import Alert, AlertTrigger
from app.models.screener import ScreenerPreset
from app.models.symbol import Symbol
from app.repositories.alert_repository import AlertRepository
from app.repositories.symbol_repository import SymbolRepository
from app.schemas.alert import (
    ALERT_FIELDS_CATALOGUE,
    AlertCreateRequest,
    AlertCreateResponse,
    AlertResponse,
    AlertSummary,
    AlertTriggerPage,
    AlertTriggerResponse,
    AlertUpdateRequest,
    check_condition,
    registry_for,
)
from app.schemas.pagination import decode_cursor, encode_cursor
from app.schemas.screener import FilterNode, parse_stored_filters
from app.services.alerts.evaluation import describe_condition
from app.services.screener.service import shared_as_of

__all__ = ["AlertService"]


class AlertService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings().alerts

    # ------------------------------------------------------------------ reads

    async def list(self, user_id: uuid.UUID) -> list[AlertSummary]:
        """Newest first. Stored conditions are summarised, never parsed."""
        result = await self._session.execute(
            select(Alert, Symbol.ticker, ScreenerPreset.name)
            .outerjoin(Symbol, Symbol.id == Alert.symbol_id)
            .outerjoin(ScreenerPreset, ScreenerPreset.id == Alert.preset_id)
            .where(Alert.user_id == user_id)
            .order_by(Alert.created_at.desc(), Alert.id.desc())
        )
        return [_summary(alert, ticker, preset) for alert, ticker, preset in result.tuples()]

    async def get(self, user_id: uuid.UUID, alert_id: uuid.UUID) -> AlertResponse:
        alert = await self._owned(user_id, alert_id)
        return await self._response(alert)

    async def triggers(
        self,
        user_id: uuid.UUID,
        alert_id: uuid.UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> AlertTriggerPage:
        """Firing history, newest first, cursor-paginated on ``(triggered_at, id)``."""
        await self._owned(user_id, alert_id)
        query = select(AlertTrigger).where(AlertTrigger.alert_id == alert_id)
        after = decode_cursor(cursor)
        if after is not None:
            query = query.where(
                tuple_(AlertTrigger.triggered_at, AlertTrigger.id) < (after.at, after.id)
            )
        rows = list(
            (
                await self._session.execute(
                    query.order_by(AlertTrigger.triggered_at.desc(), AlertTrigger.id.desc()).limit(
                        limit + 1
                    )
                )
            ).scalars()
        )
        page, more = rows[:limit], len(rows) > limit
        return AlertTriggerPage(
            items=[
                AlertTriggerResponse(
                    id=t.id,
                    triggered_at=t.triggered_at,
                    as_of_date=t.as_of_date,
                    as_of_time=t.as_of_time,
                    snapshot=t.snapshot,
                )
                for t in page
            ],
            next_cursor=encode_cursor(page[-1].triggered_at, page[-1].id) if more else None,
        )

    # ----------------------------------------------------------------- writes

    async def create(self, user_id: uuid.UUID, request: AlertCreateRequest) -> AlertCreateResponse:
        await self._require_capacity(user_id)
        alert = Alert(
            user_id=user_id,
            name=request.name.strip(),
            kind=request.kind.value,
            trigger_mode=(
                request.trigger_mode
                or (TriggerMode.REARM if request.kind is AlertKind.SCREEN else TriggerMode.ONCE)
            ).value,
            cooldown_minutes=request.cooldown_minutes or self._settings.default_cooldown_minutes,
            status=AlertStatus.ACTIVE.value,
        )

        met_now: bool | None = None
        if request.preset_id is not None:
            alert.preset_id = (await self._owned_preset(user_id, request.preset_id)).id
        elif request.ticker is not None and request.condition is not None:
            symbol = await self._active_symbol(request.ticker)
            alert.symbol_id = symbol.id
            alert.condition = request.condition.model_dump(mode="json")
            met_now = await self._condition_met_now(request.kind, request.condition, symbol.id)
            if met_now is not None:
                # This *is* the first evaluation: it records the state, so the
                # worker's next one can fire on a real edge from here.
                alert.last_state = met_now
                alert.last_evaluated_at = datetime.now(UTC)

        self._session.add(alert)
        await self._session.commit()
        await self._session.refresh(alert)
        response = await self._response(alert)
        return AlertCreateResponse(**response.model_dump(), condition_met_now=met_now)

    async def update(
        self, user_id: uuid.UUID, alert_id: uuid.UUID, patch: AlertUpdateRequest
    ) -> AlertResponse:
        alert = await self._owned(user_id, alert_id)
        kind = AlertKind(alert.kind)

        if patch.name is not None:
            alert.name = patch.name.strip()
        if patch.cooldown_minutes is not None:
            alert.cooldown_minutes = patch.cooldown_minutes
        if patch.trigger_mode is not None:
            if kind is AlertKind.SCREEN and patch.trigger_mode is TriggerMode.ONCE:
                raise ValidationError("A screen alert is always 'rearm': it fires on new entrants.")
            alert.trigger_mode = patch.trigger_mode.value
        if patch.condition is not None:
            if kind is AlertKind.SCREEN:
                raise ValidationError(
                    "A screen alert has no condition of its own. Edit its preset instead."
                )
            try:
                check_condition(patch.condition, kind)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            alert.condition = patch.condition.model_dump(mode="json")
            # A new condition starts a new history: the next evaluation records.
            alert.last_state = None
        if patch.status is not None and patch.status.value != alert.status:
            await self._transition(alert, patch.status)

        await self._session.commit()
        await self._session.refresh(alert)
        return await self._response(alert)

    async def delete(self, user_id: uuid.UUID, alert_id: uuid.UUID) -> None:
        alert = await self._owned(user_id, alert_id)
        await self._session.delete(alert)
        await self._session.commit()

    async def _transition(self, alert: Alert, target: AlertStatus) -> None:
        if target is AlertStatus.PAUSED:
            if alert.status == AlertStatus.DISABLED.value:
                raise ValidationError(
                    "A disabled alert cannot be paused. Re-activate it once it is fixed."
                )
            alert.status = AlertStatus.PAUSED.value
            return

        # Into active: resume, re-arm, or re-enable. Each must still be valid.
        await self._require_capacity(alert.user_id)
        if alert.symbol_id is not None:
            symbol = await self._session.get(Symbol, alert.symbol_id)
            if symbol is None or not symbol.is_active:
                raise ValidationError("This alert's symbol is no longer listed.")
        self._stored_condition(alert)
        if alert.preset_id is not None:
            preset = await self._session.get(ScreenerPreset, alert.preset_id)
            if preset is not None:
                parse_stored_filters(preset.filters)

        alert.status = AlertStatus.ACTIVE.value
        alert.disabled_reason = None
        alert.last_state = None
        alert.last_matched_symbol_ids = None

    # ---------------------------------------------------------------- helpers

    async def _owned(self, user_id: uuid.UUID, alert_id: uuid.UUID) -> Alert:
        alert = (
            await self._session.execute(
                select(Alert).where(Alert.id == alert_id, Alert.user_id == user_id)
            )
        ).scalar_one_or_none()
        if alert is None:
            raise NotFoundError("Alert not found.")
        return alert

    async def _owned_preset(self, user_id: uuid.UUID, preset_id: uuid.UUID) -> ScreenerPreset:
        preset = (
            await self._session.execute(
                select(ScreenerPreset).where(
                    ScreenerPreset.id == preset_id, ScreenerPreset.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if preset is None:
            raise NotFoundError("Screener preset not found.")
        # A preset that no longer validates would only be disabled on its
        # first run. Refuse it now, with the reason.
        parse_stored_filters(preset.filters)
        return preset

    async def _active_symbol(self, ticker: str) -> Symbol:
        symbol = await SymbolRepository(self._session).get_by_ticker(ticker)
        if symbol is None or not symbol.is_active:
            raise ValidationError(f"Unknown ticker {ticker.upper()!r}.")
        return symbol

    async def _require_capacity(self, user_id: uuid.UUID) -> None:
        active = await self._session.scalar(
            select(func.count()).where(
                Alert.user_id == user_id, Alert.status == AlertStatus.ACTIVE.value
            )
        )
        limit = self._settings.max_active_per_user
        if (active or 0) >= limit:
            raise ValidationError(
                f"You already have {limit} active alerts, the most allowed. "
                "Pause or delete one first."
            )

    async def _condition_met_now(
        self, kind: AlertKind, tree: FilterNode, symbol_id: uuid.UUID
    ) -> bool | None:
        repository = AlertRepository(self._session)
        as_of = await shared_as_of(self._session)
        if kind is AlertKind.INTRADAY:
            # Only a quote fresh enough for the grid to serve says anything
            # about *now*. A days-old cached row would answer from a stale
            # price, and that answer would become the alert's first state.
            fresh_since = utc_now() - get_settings().market_data.quote_ttl
            state, _ = await repository.evaluate_intraday(
                tree, symbol_id, fetched_since=fresh_since, indicators_as_of=as_of
            )
            return state
        if as_of is None:
            return None
        state, _ = await repository.evaluate_end_of_day(tree, symbol_id, as_of)
        return state

    def _stored_condition(self, alert: Alert) -> FilterNode | None:
        """The stored tree, re-validated against the alert's registry, or a 422."""
        if alert.condition is None:
            return None
        return parse_stored_filters(
            alert.condition,
            registry_for(AlertKind(alert.kind)),
            catalogue=ALERT_FIELDS_CATALOGUE,
            what="alert",
        )

    async def _response(self, alert: Alert) -> AlertResponse:
        symbol = (
            await self._session.get(Symbol, alert.symbol_id)
            if alert.symbol_id is not None
            else None
        )
        ticker = symbol.ticker if symbol is not None else None
        preset = (
            await self._session.get(ScreenerPreset, alert.preset_id)
            if alert.preset_id is not None
            else None
        )
        summary = _summary(alert, ticker, preset.name if preset is not None else None)
        return AlertResponse(**summary.model_dump(), condition=self._stored_condition(alert))


def _summary(alert: Alert, ticker: str | None, preset_name: str | None) -> AlertSummary:
    return AlertSummary(
        id=alert.id,
        name=alert.name,
        kind=AlertKind(alert.kind),
        ticker=ticker,
        preset_id=alert.preset_id,
        preset_name=preset_name,
        condition_summary=describe_condition(alert.condition),
        trigger_mode=TriggerMode(alert.trigger_mode),
        cooldown_minutes=alert.cooldown_minutes,
        status=AlertStatus(alert.status),
        disabled_reason=alert.disabled_reason,
        last_state=alert.last_state,
        last_evaluated_at=alert.last_evaluated_at,
        last_triggered_at=alert.last_triggered_at,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )
