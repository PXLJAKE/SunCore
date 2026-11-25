# SPDX-License-Identifier: Apache-2.0
"""Composition of the HA entity surface: what SunCore offers to ``suncore.ha.mqtt`` (WP3).

The source reads the control state, the flows and the configuration; the install record lives
in ``learned_param`` (namespace ``ha_entities``). Commands from Home Assistant reach
:class:`RuntimeCommandPort`, which calls the same service methods the web UI and the REST API
call (modes, emergency stop, overrides, replan). Nothing here touches the gate, the controller or
a device directly, and nothing calls ``apply``.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Final

import structlog

from suncore import __version__
from suncore.app.ha_site_values import (
    autarky_today_pct,
    next_action,
    plan_cost_today,
    pv_forecast_error_w,
    savings_today,
)
from suncore.app.notifications import grid_flags
from suncore.core.config import MqttConfig
from suncore.core.controlstate import BatteryControlState, DeviceControlState, OverrideCommand
from suncore.core.decisions import BatteryAction, DecisionKind, OperatingMode, Reason
from suncore.core.devices import DeviceKind
from suncore.core.flex import EvMode, EvState, JobState
from suncore.ha.connection import ConnectionState
from suncore.ha.mqtt import (
    DeviceInfo,
    EntitySource,
    HaEntityService,
    InstallRecord,
    SiteEvent,
    Topology,
    supervisor_services_from_env,
)
from suncore.i18n import resolve_language
from suncore.storage.learned import LearnedParamStore

if TYPE_CHECKING:
    from suncore.app.runtime import Runtime

log = structlog.get_logger(__name__)

NAMESPACE: Final = "ha_entities"
DAY_REFRESH_S: Final = 300.0
"""The day values (savings, autarky, forecast error) read the time series: at most this often."""
EVENT_MAX_AGE_S: Final = 300.0
"""An event that waited longer than this (broker down) is stale and dropped, not replayed."""
EVENT_BUFFER: Final = 64
_JOB_STATE: Final = {
    JobState.ARMED: "armed",
    JobState.SCHEDULED: "armed",
    JobState.RUNNING: "running",
    JobState.DONE: "done",
    JobState.CANCELLED: "cancelled",
}
KEY_INSTALL: Final = "install"

SUPPORTED: Final = frozenset(
    {
        "status",
        "mode",
        "available_surplus",
        "healthy",
        "shadow_mode",
        "emergency_stop",
        "events",
        "device_mode",
        "device_power",
        "device_last_decision",
        # read-only views of the grid, battery, EV, heat pump and program state (PR 6-O)
        "month_peak",
        "grid_state",
        "para14a_active",
        "island",
        "battery_action",
        "battery_lease",
        "ev_mode_state",
        "ev_target_soc_state",
        "ev_target_energy_state",
        "ev_target_time_state",
        "ev_plugged",
        "ev_phases",
        "ev_vehicle_soc",
        "hp_sg_state",
        "hp_lift",
        "job_state",
        "planned_start",
        # site sensors with a producer since PR 7-H (read only)
        "next_action",
        "plan_cost_today",
        "savings_today",
        "autarky_today",
        "pv_forecast_error",
        # command entities (PR 6-D); announced only with a command port
        "auto",
        "replan",
        "emergency_stop_button",
        "emergency_stop_clear",
        "device_auto",
        "device_boost",
        "device_pause",
        "device_resume",
        "device_boost_minutes",
    }
)
"""Catalogue keys SunCore can fill today. Not announced yet: ``savings_month`` (no month sum
yet), the battery strategy, reserve and evening target (the config path from HA, plan WP4, is
open) and every EV, program and comfort *control* (``ev_mode``, ``target_*``, ``arm``,
``cancel``, ``finish_by``, ``comfort_*``: they need commands that arm or set things; see plan
section 8, 6-O and 7-H)."""


class SiteEventBuffer:
    """Events for the MQTT ``events`` entity that wait for the next sync.

    The notification service offers its signals here (``Runtime`` wires it); the entity source
    drains them. Bounded and age-limited so a broker that is down for hours neither grows the
    buffer nor replays old news when it returns.
    """

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._items: deque[tuple[datetime, SiteEvent]] = deque(maxlen=EVENT_BUFFER)

    def offer(self, event: SiteEvent) -> None:
        self._items.append((self._clock(), event))

    def drain(self) -> list[SiteEvent]:
        now = self._clock()
        items, self._items = list(self._items), deque(maxlen=EVENT_BUFFER)
        return [e for ts, e in items if (now - ts).total_seconds() <= EVENT_MAX_AGE_S]


class SqliteInstallStore:
    """The install id and the announced device ids, in ``learned_param``."""

    def __init__(self, store: LearnedParamStore) -> None:
        self._store = store

    async def load(self) -> InstallRecord | None:
        row = await self._store.get(NAMESPACE, KEY_INSTALL)
        if row is None:
            return None
        iid = row.payload.get("iid")
        published = row.payload.get("published", [])
        if not isinstance(iid, str) or len(iid) != 8 or not isinstance(published, list):
            return None
        return InstallRecord(iid, tuple(str(p) for p in published))

    async def save(self, record: InstallRecord) -> None:
        await self._store.put(
            NAMESPACE, KEY_INSTALL, {"iid": record.iid, "published": list(record.published)}
        )


class RuntimeCommandPort:
    """``CommandPort`` over the running application (the services layer, never a device).

    Every method is one call of the :class:`~suncore.api.services.Services` method the web UI
    uses; the mode rules, the identity check of a raise and the safety gate apply unchanged.
    """

    def __init__(self, runtime: Runtime) -> None:
        self._rt = runtime

    def stored_global_mode(self) -> OperatingMode:
        return self._rt.modes.global_mode()

    def stored_device_mode(self, device_id: str) -> OperatingMode | None:
        devices = self._rt.devices
        if devices is None or all(d.id != device_id for d in devices.devices):
            return None
        return self._rt.modes.device_mode(device_id)

    def stored_emergency_settled(self) -> bool:
        state = self._rt.control_state()
        return state.emergency_stop and not any(
            d.under_control or d.release_pending for d in state.devices
        )

    def stored_emergency_active(self) -> bool:
        return self._rt.emergency_stop_active()

    async def command_emergency_stop(self) -> None:
        await self._rt.emergency_stop()

    async def command_clear_stop(self) -> None:
        await self._rt.clear_emergency_stop()

    async def command_global_mode(self, mode: OperatingMode) -> None:
        await self._rt.set_global_mode(mode)

    async def command_device_mode(self, device_id: str, mode: OperatingMode) -> None:
        identity: str | None = None
        if mode is not OperatingMode.SHADOW:
            # The opt-in already allowed the raise; the identity is the device's current one,
            # exactly what the panel sends after the user looked at the device.
            state = next(
                (d for d in self._rt.control_state().devices if d.device_id == device_id), None
            )
            identity = None if state is None else state.identity
        await self._rt.set_device_mode(device_id, mode, identity)

    async def command_override(self, device_id: str, command: OverrideCommand) -> None:
        await self._rt.override_device(device_id, command)

    async def command_replan(self) -> None:
        await self._rt.replan()


class RuntimeEntitySource:
    """``EntitySource`` over the running application."""

    def __init__(self, runtime: Runtime, events: SiteEventBuffer | None = None) -> None:
        self._rt = runtime
        self._seen: dict[str, datetime] = {}
        self._baseline = False
        self._events = events
        self._day: dict[str, object] = {}
        self._day_at: datetime | None = None
        self._day_task: asyncio.Task[None] | None = None

    def topology(self) -> Topology:
        rt = self._rt
        devices = tuple(
            DeviceInfo(d.id, d.name, d.kind) for d in (rt.devices.devices if rt.devices else ())
        )
        return Topology(
            devices=devices,
            has_battery=rt.control_state().battery is not None,
            supported=SUPPORTED,
            currency=rt.region.currency if rt.region is not None else None,
            version=__version__,
        )

    def language(self) -> str:
        rt = self._rt
        if rt.region is not None and rt.region.language != "auto":
            return resolve_language(rt.region.language)
        return resolve_language(rt.ha_language)

    def values(self) -> Mapping[str, object]:
        rt = self._rt
        state = rt.control_state()
        ha_ok = rt.connection.state is ConnectionState.CONNECTED
        if not ha_ok:
            status = "ha_unavailable"
        elif state.emergency_stop:
            status = "emergency_stop"
        elif not state.active:
            status = "starting"
        else:
            status = "running"
        out: dict[str, object] = {
            "status": status,
            "mode": state.global_mode.value,
            "healthy": ha_ok and state.active,
            "shadow_mode": state.global_mode.value == "shadow",
            "emergency_stop": state.emergency_stop,
            "auto": rt.modes.global_mode() is OperatingMode.AUTOMATIC,
        }
        if state.site is not None and state.site.available_w is not None:
            out["available_surplus"] = state.site.available_w
        out.update(self._grid_values())
        out.update(self._site_values())
        out.update(self._battery_values(state.battery))
        for device in state.devices:
            out.update(self._flex_values(device))
            out[f"{device.device_id}_mode"] = device.effective_mode.value
            out[f"{device.device_id}_auto"] = device.device_mode is OperatingMode.AUTOMATIC
            if device.estimate is not None:
                out[f"{device.device_id}_power"] = device.estimate.value_w
            last = device.last_decision
            out[f"{device.device_id}_last_decision"] = "none" if last is None else last.kind.value
        return out

    def _site_values(self) -> dict[str, object]:
        """Plan cost, next action and the day values (read only; a missing source is no key)."""
        rt = self._rt
        plan = None if rt.planning is None else rt.planning.plan
        now = rt.clock()
        tz = rt.timezone()
        out: dict[str, object] = {}
        cost = plan_cost_today(plan, now, tz)
        if cost is not None:
            out["plan_cost_today"] = cost
        action = next_action(plan, now, tz, self.language())
        if action is not None:
            out["next_action"] = action
        self._schedule_day_refresh(now)
        out.update(self._day)
        return out

    def _schedule_day_refresh(self, now: datetime) -> None:
        if self._day_task is not None and not self._day_task.done():
            return
        if self._day_at is not None and (now - self._day_at).total_seconds() < DAY_REFRESH_S:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._day_at = now
        self._day_task = loop.create_task(self.refresh_day_values(), name="suncore-ha-day-values")

    async def refresh_day_values(self) -> None:
        """Savings and autarky from today's ``day_summary`` preview, the forecast error from the
        newest ``accuracy_day`` rows. A failing read keeps the last values."""
        values: dict[str, object] = {}
        try:
            row = await self._rt.today_summary()
            savings = savings_today(row)
            if savings is not None:
                values["savings_today"] = savings
            autarky = autarky_today_pct(row)
            if autarky is not None:
                values["autarky_today"] = autarky
            rows, blended = await self._rt.recent_pv_accuracy()
            error = pv_forecast_error_w(rows, blended)
            if error is not None:
                values["pv_forecast_error"] = error
        except Exception:
            log.exception("ha_entities.day_values_failed")
            return
        self._day = values

    def _grid_values(self) -> dict[str, object]:
        """Island, section 14a and the month's peak from the grid monitor (read only)."""
        rt = self._rt
        monitor = None if rt.stack is None else rt.stack.grid_monitor
        if monitor is None:
            return {}
        out: dict[str, object] = {}
        island, limited = grid_flags(monitor)
        if island is not None:
            out["island"] = island
        if limited is not None:
            out["para14a_active"] = limited
        if island:
            out["grid_state"] = "island"
        elif limited:
            out["grid_state"] = "limited"
        elif island is not None:
            out["grid_state"] = "normal"
        peak = monitor.month_peak_w(rt.clock())
        if peak is not None:
            out["month_peak"] = peak
        return out

    @staticmethod
    def _battery_values(battery: BatteryControlState | None) -> dict[str, object]:
        """What SunCore holds on the battery: its own settings (``none``/``idle``) unless a
        takeover is active; forced grid charging is ``charge``, any other held limit ``hold``."""
        if battery is None:
            return {}
        held = battery.held if battery.under_control else None
        if held is None:
            action = "idle"
        elif isinstance(held, BatteryAction) and (held.grid_charge_w or 0.0) > 0.0:
            action = "charge"
        else:
            action = "hold"
        return {
            "battery_action": action,
            "battery_lease": "held" if battery.under_control else "none",
        }

    def _flex_values(self, device: DeviceControlState) -> dict[str, object]:
        """EV, heat pump and program views of one device (stored runtime state only)."""
        if device.kind is DeviceKind.WALLBOX:
            return self._ev_values(device)
        if device.kind is DeviceKind.HEAT_PUMP:
            return self._heat_pump_values(device)
        if device.kind is DeviceKind.PROGRAM:
            return self._program_values(device)
        return {}

    def _ev_values(self, device: DeviceControlState) -> dict[str, object]:
        prefix = device.device_id
        ev: EvState | None = self._rt.ev_state_snapshot().get(prefix)
        out: dict[str, object] = {
            f"{prefix}_ev_mode_state": (EvMode.OFF if ev is None else ev.mode).value
        }
        target = None if ev is None else ev.target
        if target is not None:
            if target.soc_pct is not None:
                out[f"{prefix}_ev_target_soc_state"] = target.soc_pct
            if target.energy_wh is not None:
                out[f"{prefix}_ev_target_energy_state"] = target.energy_wh / 1000.0
            out[f"{prefix}_ev_target_time_state"] = target.by.isoformat()
        live = device.state
        if live is not None:
            if live.connected is not None:
                out[f"{prefix}_ev_plugged"] = live.connected
            if live.phases is not None:
                out[f"{prefix}_ev_phases"] = live.phases
            if live.soc_pct is not None:
                out[f"{prefix}_ev_vehicle_soc"] = live.soc_pct
        return out

    @staticmethod
    def _heat_pump_values(device: DeviceControlState) -> dict[str, object]:
        prefix = device.device_id
        live = device.state
        out: dict[str, object] = {}
        if live is not None and live.step in (1, 2, 3, 4):
            out[f"{prefix}_hp_sg_state"] = live.step
        if live is not None and live.setpoint_c is not None and device.base_setpoint_c is not None:
            out[f"{prefix}_hp_lift"] = round(live.setpoint_c - device.base_setpoint_c, 1)
        return out

    def _program_values(self, device: DeviceControlState) -> dict[str, object]:
        prefix = device.device_id
        job = self._rt.program_jobs.current(prefix)
        out: dict[str, object] = {
            f"{prefix}_job_state": "idle" if job is None else _JOB_STATE[job.state]
        }
        if job is not None and job.planned_start is not None:
            out[f"{prefix}_planned_start"] = job.planned_start.isoformat()
        return out

    def drain_events(self) -> list[SiteEvent]:
        """``device_switched``, ``proposal`` and ``error`` from the gate's newest decisions, then
        the signals of the notification rules (island, section 14a, programs, cheap windows, EV
        target at risk)."""
        events: list[SiteEvent] = []
        for device in self._rt.control_state().devices:
            last = device.last_decision
            if last is None:
                continue
            known = self._seen.get(device.device_id)
            self._seen[device.device_id] = last.ts
            if not self._baseline or (known is not None and last.ts <= known):
                continue
            kind = {
                DecisionKind.ACCEPTED: "device_switched",
                DecisionKind.PROPOSED: "proposal",
                DecisionKind.FAILED: "error",
            }.get(last.kind)
            if kind is not None:
                events.append(SiteEvent(kind, {"device": device.device_id, "name": device.name}))
        self._baseline = True
        if self._events is not None:
            events.extend(self._events.drain())
        return events


def build_ha_entities(
    runtime: Runtime,
    on_refusal: Callable[[str, Reason], None] | None = None,
    events: SiteEventBuffer | None = None,
) -> HaEntityService | None:
    """The service for ``runtime`` (``None`` in demo mode, unless ``SUNCORE_DEMO_MQTT=1``, or
    without the learned store)."""
    if (runtime.demo and not runtime.settings.demo_mqtt) or runtime.learned is None:
        return None

    async def load_config() -> MqttConfig | None:
        stored = await runtime.config.get("mqtt")
        return stored if isinstance(stored, MqttConfig) else None

    source: EntitySource = RuntimeEntitySource(runtime, events)
    return HaEntityService(
        source=source,
        load_config=load_config,
        store=SqliteInstallStore(runtime.learned),
        services=supervisor_services_from_env(),
        commands=RuntimeCommandPort(runtime),
        on_refusal=on_refusal,
    )
