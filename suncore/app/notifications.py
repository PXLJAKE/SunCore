# SPDX-License-Identifier: Apache-2.0
"""Composition of the notification service (Phase 6 WP5): what ``suncore.notify`` observes and
what it may ask of the application.

The observation source only reads (control state, planner status, MQTT status, prices). The
proposal port is the whole confirm path: ``open_proposals`` and one call of the services layer's
``confirm_proposal`` (the method the web UI uses). It has no method that changes a mode, clears
the emergency stop or writes a device.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Final

import structlog

from suncore.core.battery import BATTERY_ASSET_ID
from suncore.core.config import NotificationsConfig
from suncore.core.controlstate import UnknownProposalError
from suncore.core.decisions import DecisionKind, OperatingMode
from suncore.core.haentities import BrokerState
from suncore.i18n import resolve_language
from suncore.notify import (
    ConfirmOutcome,
    DeviceObs,
    NotificationService,
    Observation,
    ProposalObs,
    ProposalView,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from suncore.app.runtime import Runtime

log = structlog.get_logger(__name__)

PRICE_REFRESH_S: Final = 300.0
WATCHDOG_PREFIX: Final = "safety.watchdog"


class RuntimeProposalPort:
    """``ProposalPort`` over the running application (services layer only)."""

    def __init__(self, runtime: Runtime) -> None:
        self._rt = runtime

    def _name(self, asset_id: str) -> str:
        if asset_id == BATTERY_ASSET_ID:
            return asset_id
        devices = self._rt.devices
        for device in () if devices is None else devices.devices:
            if device.id == asset_id:
                return device.name
        return asset_id

    def open_proposals(self) -> Sequence[ProposalView]:
        return [
            ProposalView(
                d.intent.id,
                self._name(d.intent.asset_id),
                d.expires_at,
                d.mode is OperatingMode.ASSISTED,
            )
            for d in self._rt.proposals()
            if d.kind is DecisionKind.PROPOSED and d.expires_at is not None
        ]

    async def confirm_proposal(self, intent_id: str) -> ConfirmOutcome:
        try:
            decision = await self._rt.confirm_proposal(intent_id)
        except UnknownProposalError:
            return ConfirmOutcome.GONE
        if decision.kind is DecisionKind.ACCEPTED:
            return ConfirmOutcome.CONFIRMED
        return ConfirmOutcome.REFUSED


class RuntimeObservationSource:
    """``ObservationSource`` over the running application."""

    def __init__(self, runtime: Runtime) -> None:
        self._rt = runtime
        self._since: datetime | None = None
        self._prices: list[tuple[datetime, float | None]] = []
        self._prices_at: datetime | None = None

    def language(self) -> str:
        rt = self._rt
        if rt.region is not None and rt.region.language != "auto":
            return resolve_language(rt.region.language)
        return resolve_language(rt.ha_language)

    def timezone(self) -> tzinfo:
        return self._rt.timezone()

    def observe(self, now: datetime) -> Observation:
        rt = self._rt
        local = now.astimezone(self.timezone())
        if self._since is None:
            self._since = now
        state = rt.control_state()
        names = {d.device_id: d.name for d in state.devices}
        devices = [
            DeviceObs(
                d.device_id,
                d.name,
                None if d.last_decision is None else d.last_decision.kind.value,
                None if d.last_decision is None else d.last_decision.ts,
                d.paused is not None and d.paused.key.startswith(WATCHDOG_PREFIX),
            )
            for d in state.devices
        ]
        proposals = [
            ProposalObs(p.intent.id, names.get(p.intent.asset_id, p.intent.asset_id), p.expires_at)
            for p in state.proposals
            if p.kind is DecisionKind.PROPOSED and p.expires_at is not None
        ]
        mqtt = rt.ha_entities_status().state
        return Observation(
            now=local,
            emergency_stop=state.emergency_stop,
            global_mode=state.global_mode.value,
            devices=devices,
            proposals=proposals,
            planner_failing_s=self._planner_failing(now),
            mqtt_enabled=mqtt in (BrokerState.CONNECTING, BrokerState.CONNECTED, BrokerState.ERROR),
            mqtt_connected=mqtt is BrokerState.CONNECTED,
            prices=self._price_slots(now),
        )

    def _planner_failing(self, now: datetime) -> float | None:
        planning = self._rt.planning
        if planning is None:
            return None
        status = planning.status()
        if status.consecutive_failures <= 0:
            return None
        since = status.last_ok_at or self._since or now
        return max(0.0, (now - since).total_seconds())

    def _price_slots(self, now: datetime) -> list[tuple[datetime, float | None]]:
        if (
            self._prices_at is not None
            and (now - self._prices_at).total_seconds() < PRICE_REFRESH_S
        ):
            return self._prices
        self._prices_at = now
        tariff = self._rt.tariff
        if tariff is None:
            self._prices = []
            return self._prices
        try:
            report = tariff.report(hours=24.0, now=now)
        except Exception:
            log.warning("notify.prices_unavailable")
            self._prices = []
        else:
            self._prices = [(s.start, s.import_price) for s in report.slots]
        return self._prices


def build_notifications(runtime: Runtime) -> NotificationService | None:
    """The service for ``runtime`` (``None`` in demo mode: nothing may leave the demo)."""
    if runtime.demo:
        return None

    async def load_config() -> NotificationsConfig | None:
        stored = await runtime.config.get("notifications")
        return stored if isinstance(stored, NotificationsConfig) else None

    return NotificationService(
        connection=runtime.connection,
        source=RuntimeObservationSource(runtime),
        proposals=RuntimeProposalPort(runtime),
        load_config=load_config,
        clock=runtime.clock,
    )
