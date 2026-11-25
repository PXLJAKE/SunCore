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
from suncore.core.flex import EvMode
from suncore.core.grid import GridState, Para14aStatus
from suncore.core.haentities import BrokerState
from suncore.i18n import resolve_language
from suncore.notify import (
    ConfirmOutcome,
    DeviceObs,
    EvTargetObs,
    NotificationService,
    Observation,
    ProposalObs,
    ProposalView,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from suncore.app.grid import GridMonitor
    from suncore.app.runtime import Runtime
    from suncore.core.control import PlanInterval

log = structlog.get_logger(__name__)

PRICE_REFRESH_S: Final = 300.0
WATCHDOG_PREFIX: Final = "safety.watchdog"
EV_SHORTFALL_KEY: Final = "planner.ev.target_shortfall"
"""Reason key of the planner (``suncore.planner.reasons.EV_TARGET_SHORTFALL``): the deadline slot
of a target the plan cannot deliver. Kept as text here so ``app`` reads the plan's public reasons
without importing the planner's internals."""


def grid_flags(monitor: GridMonitor | None) -> tuple[bool | None, bool | None]:
    """``(island, section 14a active)`` from the grid monitor; ``None`` = no usable source.

    A backup entity that is configured but unavailable (``UNKNOWN``) and an unreadable §14a
    signal are no information, not "grid is back": the rules then neither start nor end an event.
    """
    if monitor is None:
        return None, None
    grid, para = monitor.grid_state, monitor.para14a_status
    island = None if grid is None or grid is GridState.UNKNOWN else grid is GridState.ISLAND
    flagged = (
        None if para is None or para is Para14aStatus.UNKNOWN else para is Para14aStatus.ACTIVE
    )
    return island, flagged


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
        stack = rt.stack
        island, para14a = grid_flags(None if stack is None else stack.grid_monitor)
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
            island=island,
            para14a=para14a,
            ev_targets=self._ev_targets(now, names),
        )

    def _ev_targets(self, now: datetime, names: dict[str, str]) -> list[EvTargetObs]:
        """Chargers in mode ``target`` with their deadline and whether the newest plan, made
        after the target was set, reports a shortfall for it."""
        rt = self._rt
        states = rt.ev_state_snapshot()
        plan = None if rt.planning is None else rt.planning.plan
        out: list[EvTargetObs] = []
        for device_id, state in states.items():
            target = state.target
            if state.mode is not EvMode.TARGET or target is None or target.by <= now:
                continue
            shortfall: tuple[float | None, float | None] | None = None
            made = None if plan is None else plan.created_at
            if plan is not None and made is not None and made >= state.updated_at:
                shortfall = self._shortfall(plan.intervals, device_id)
            out.append(
                EvTargetObs(
                    device_id,
                    names.get(device_id, device_id),
                    target.by,
                    shortfall is not None,
                    None if shortfall is None else shortfall[0],
                    None if shortfall is None else shortfall[1],
                )
            )
        return out

    @staticmethod
    def _shortfall(
        intervals: Sequence[PlanInterval], device_id: str
    ) -> tuple[float | None, float | None] | None:
        for interval in intervals:
            for reason in interval.reasons:
                if reason.key == EV_SHORTFALL_KEY and reason.args.get("device") == device_id:
                    need, planned = reason.args.get("need_kwh"), reason.args.get("planned_kwh")
                    return (
                        float(need) if isinstance(need, int | float) else None,
                        float(planned) if isinstance(planned, int | float) else None,
                    )
        return None

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
