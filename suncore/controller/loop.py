# SPDX-License-Identifier: Apache-2.0
"""The controller's tick (plan WP7): pure core plus a thin async loop.

:class:`Controller.step` is deterministic: ``(now, inputs) -> TickResult`` with the
intents to submit, the :class:`~suncore.core.control.GateInputs` snapshot for the safety
gate, a view per device and deviation events. It never sleeps and never reads a clock.
:class:`ControllerLoop.tick` runs one step and does the I/O around it through injected
callables: it submits every intent to the gate (as a task, so a settle wait never stalls
the tick), feeds the decisions back, and calls the gate's ``poll``.

**Per-tick call order** (every ``ControlSettings.tick_s``; WP8 simulator and WP9 runtime
must follow it, ``tests/controller/sim.py`` is the reference):

1. ``now = clock()`` - one injected timestamp for the whole tick (timezone-aware);
2. inputs, all read from the entity cache, never ``get_states``:

   * ``flows = compute_flows(site, samples)``;
   * per device: ``state = driver.device_state()``; ``model.observe(now, state, samples)``
     then ``estimate = model.estimate(now, state, samples)``;
   * ``blocked``: every device the gate holds (override), has a release pending for, or
     whose scope the watchdog paused - id -> the gate's reason;
   * ``reserve_soc_pct``, ``import_limit_w`` from the site/battery config, ``goals``,
     ``plan`` (``Plan.default()`` until the planner's first plan, afterwards the current plan
     read **once** for the tick - ``ControlStack.tick``; a plan can only restrict the devices
     and move the surplus between battery and consumers); ``battery`` (WP10: the adapter's
     capabilities and ranges and what the gate holds) - the band of a fresh plan becomes a
     ``BatteryAction`` intent (``controller.battery``);

3. ``result = await loop.tick(now, ControllerInputs(...))``. Inside, in this order:
   ``controller.step`` (surplus, allocation, back-off) -> ``loop.gate_inputs`` is the
   new snapshot (the gate's ``InputsProvider`` must return ``loop.gate_inputs``; it is
   stamped with ``now`` - the gate treats an unstamped or old snapshot as unknown
   surplus) -> deviation events to ``on_deviation`` -> every intent is submitted to
   ``gate.submit`` as a task and its decision fed back with ``controller.report`` ->
   ``await gate.poll()`` (deferred releases; called every tick, also without intents);
4. publish ``result.views`` / ``loop.last`` to the API and the live feed.

Outside the tick: ``gate.observe`` from the entity cache listener, ``gate.recover`` after
the first connect, ``connection_lost``/``connection_restored`` from the connection
listener, ``loop.close()`` before ``gate.shutdown()`` on stop. ``controller.set_devices``
after a configuration change (protection history is kept). The composition root wires the
:class:`~suncore.controller.levels.Quantiser` to the drivers
(``ClimateDriver.effective_step_c`` / ``NumberDriver.snap_power_w``).

The controller imports ``core`` only: it receives the gate as two callables
(:data:`IntentSubmitter`, :data:`Poller`) and knows nothing about drivers.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, tzinfo
from types import MappingProxyType

import structlog

from suncore.controller.allocation import (
    AllocationInputs,
    Allocator,
    DeviationEvent,
    DeviceView,
)
from suncore.controller.battery import (
    BatteryControlInputs,
    BatteryControlSettings,
    BatteryIntents,
    BatteryView,
)
from suncore.controller.ev import battery_holds_lock
from suncore.controller.evcc import INTENT_PREFIX as EVCC_INTENT_PREFIX
from suncore.controller.evcc import EvccIntents, LoadpointTick, LoadpointView
from suncore.controller.export import (
    DeviceHeadroom,
    ExportChain,
    ExportControlInputs,
    ExportControlSettings,
    ExportView,
    absorb_headroom_w,
    export_over_limit,
)
from suncore.controller.grid import GridHolds, GridInputs, situation_reasons
from suncore.controller.hysteresis import Phase
from suncore.controller.levels import ConfigQuantiser, Quantiser
from suncore.controller.programs import (
    INTENT_PREFIX as PROGRAM_INTENT_PREFIX,
)
from suncore.controller.programs import (
    ProgramController,
    ProgramEvent,
    ProgramInputs,
)
from suncore.controller.surplus import SiteSurplus, SurplusTracker
from suncore.core.balance import FlowSnapshot
from suncore.core.config import GoalsConfig
from suncore.core.control import (
    ControlSettings,
    DeviceState,
    GateInputs,
    Plan,
    PowerEstimate,
    device_band_now,
)
from suncore.core.decisions import Decision, Intent, Reason
from suncore.core.devices import DeviceConfig, LoadpointControl
from suncore.core.flex import EV_MODE_DEFAULT, EvMode, EvTarget, ProgramJob
from suncore.core.grid import (
    GridMemory,
    GridState,
    advance_grid_memory,
    island_hold_s,
    merge_grid_memory,
    para14a_hold_s,
)
from suncore.core.signals import Quality

log = structlog.get_logger(__name__)

IntentSubmitter = Callable[[Intent], Awaitable[Decision]]
"""``SafetyGate.submit``."""
Poller = Callable[[], Awaitable[Sequence[Decision]]]
"""``SafetyGate.poll`` (deferred releases)."""
DeviationSink = Callable[[DeviationEvent], None]


@dataclass(frozen=True, slots=True)
class ControllerInputs:
    """Everything one tick looks at; plain data, built by the composition root."""

    flows: FlowSnapshot | None = None
    states: Mapping[str, DeviceState] = field(default_factory=dict)
    """Observed state per device id (``driver.device_state()``)."""
    estimates: Mapping[str, PowerEstimate] = field(default_factory=dict)
    """Current power per device id (power model)."""
    blocked: Mapping[str, Reason] = field(default_factory=dict)
    """Devices the controller must leave alone and why (held, release pending, paused)."""
    import_limit_w: float | None = None
    reserve_soc_pct: float | None = None
    goals: GoalsConfig = field(default_factory=GoalsConfig)
    plan: Plan = field(default_factory=Plan.default)
    battery: BatteryControlInputs | None = None
    """The battery adapter's capabilities, ranges and what the gate holds (``None`` = no battery
    adapter: the battery is only observed, no battery intents; Phase 4 WP10)."""
    inverter: ExportControlInputs | None = None
    """PR 6-G: the inverter limit adapter (``None`` = none configured: no inverter intents,
    the export limit stays the inverter's own)."""
    grid: GridInputs | None = None
    """PR 6-F: effective import limit (fuse and peak), §14a, island
    (``controller.grid.resolve_grid_inputs``); ``None`` = only ``import_limit_w`` (Phase 3)."""
    ev_modes: Mapping[str, EvMode] = field(default_factory=dict)
    """PR 6-I: the runtime EV mode per charger (``flex_state``; missing = the default mode)."""
    ev_targets: Mapping[str, EvTarget] = field(default_factory=dict)
    """PR 6-J: the runtime EV target per charger or loadpoint (``flex_state``; mode ``target``)."""
    battery_present: bool = True
    """PR 6-I (E6): the site has a home battery (the conservative default assumes one)."""
    jobs: Mapping[str, Sequence[ProgramJob]] = field(default_factory=dict)
    """PR 6-L: the open program jobs per device (``flex_state``)."""

    @property
    def grid_inputs(self) -> GridInputs:
        return self.grid if self.grid is not None else GridInputs.from_limit(self.import_limit_w)


@dataclass(frozen=True, slots=True)
class GridView:
    """The grid situation of a tick for the API and the "Why?" view (PR 6-F)."""

    inputs: GridInputs
    reasons: tuple[Reason, ...]
    holds: GridHolds


@dataclass(frozen=True, slots=True)
class TickResult:
    ts: datetime
    gate_inputs: GateInputs
    site: SiteSurplus
    intents: tuple[Intent, ...]
    views: Mapping[str, DeviceView]
    deviations: tuple[DeviationEvent, ...]
    total_w: float | None
    """Total available to the controlled devices (``None`` = unknown surplus)."""
    free_w: float | None
    """Free surplus before this tick's intents (``gate_inputs.surplus`` unclamped)."""
    allocated_draw_w: float
    attributable_deficit_w: float
    battery: BatteryView | None = None
    """The battery decision of the tick (``None`` without a battery adapter)."""
    grid: GridView | None = None
    program_jobs: tuple[ProgramJob, ...] = ()
    """PR 6-L: program jobs that changed this tick (the composition root persists them)."""
    program_events: tuple[ProgramEvent, ...] = ()
    """PR 6-L: program starts, ends and "start now" requests (notifications)."""
    loadpoints: Mapping[str, LoadpointView] = field(default_factory=dict)
    """PR 6-J: what the controller wants of each evcc loadpoint this tick."""
    export: ExportView | None = None
    """The export-chain decision of the tick (``None`` without an inverter adapter)."""


class Controller:
    """Deterministic controller core: surplus, allocation, hysteresis, back-off."""

    def __init__(
        self,
        devices: Iterable[DeviceConfig] = (),
        *,
        settings: ControlSettings | None = None,
        tz: tzinfo = UTC,
        quantiser: Quantiser | None = None,
        battery: BatteryControlSettings | None = None,
        export: ExportControlSettings | None = None,
    ) -> None:
        self._settings = settings if settings is not None else ControlSettings()
        self._battery = BatteryIntents(battery)
        self._export = ExportChain(export)
        self._tz = tz
        devices = list(devices)
        if quantiser is None:
            self._config_quantiser: ConfigQuantiser | None = ConfigQuantiser(
                {d.id: d for d in devices}
            )
            quantiser = self._config_quantiser
        else:
            self._config_quantiser = None
        self._quantiser = quantiser
        self._surplus = SurplusTracker(self._settings.smoothing_s)
        self._allocator = Allocator(self._settings, quantiser, tz=tz)
        self._allocator.set_devices(devices)
        self._programs = ProgramController(devices, tz=tz)
        self._para14a_ids = frozenset(d.id for d in devices if d.para14a)
        self._grid_memory = GridMemory()
        self._evcc = EvccIntents()
        self._loadpoints = tuple(d for d in devices if isinstance(d.control, LoadpointControl))

    @property
    def settings(self) -> ControlSettings:
        return self._settings

    @property
    def allocator(self) -> Allocator:
        return self._allocator

    def set_devices(self, devices: Iterable[DeviceConfig]) -> None:
        """Replace the configured devices (a config edit); protection history is kept."""
        devices = list(devices)
        if self._config_quantiser is not None:
            self._config_quantiser.update({d.id: d for d in devices})
        self._allocator.set_devices(devices)
        self._programs.set_devices(devices)
        self._para14a_ids = frozenset(d.id for d in devices if d.para14a)
        self._loadpoints = tuple(d for d in devices if isinstance(d.control, LoadpointControl))

    @property
    def programs(self) -> ProgramController:
        return self._programs

    @property
    def grid_memory(self) -> GridMemory:
        return self._grid_memory

    def seed_grid_memory(self, memory: GridMemory) -> None:
        """Add a restored island/§14a history (mirrors ``SafetyGate.seed_grid_memory``)."""
        self._grid_memory = merge_grid_memory(self._grid_memory, memory)

    def step(self, now: datetime, inputs: ControllerInputs) -> TickResult:
        """One deterministic tick."""
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "Controller.step needs a timezone-aware now"
            raise ValueError(msg)
        # A band applies only while a plan interval covers ``now``: a plan that ran out (or a
        # default plan) leaves the Phase 3 goal rules in charge, never the empty default band.
        interval = inputs.plan.interval_at(now)
        band = None if interval is None else interval.battery
        grid = inputs.grid_inputs
        self._grid_memory = advance_grid_memory(
            self._grid_memory, now, grid.grid_state, grid.para14a.status
        )
        cooldown = self._settings.safe_state_cooldown_s
        holds = GridHolds(
            island_s=island_hold_s(self._grid_memory, now, cooldown),
            para14a_s=para14a_hold_s(self._grid_memory, now, cooldown),
        )
        soc_flow = None if inputs.flows is None else inputs.flows.battery_soc_pct
        # Only an OK reading counts (review 4-G minor 7): a stale SoC never sets a floor.
        soc = soc_flow.value if soc_flow is not None and soc_flow.quality is Quality.OK else None
        held = None if inputs.battery is None else inputs.battery.held
        allocation = AllocationInputs(
            ev_modes=inputs.ev_modes,
            # E6 as the gate judges it (review 6-I H1): a lock being released is no lock.
            battery_locked=not inputs.battery_present
            or (
                inputs.battery is not None
                and not inputs.battery.release_pending
                and battery_holds_lock(held, soc)
            ),
            states=inputs.states,
            estimates=inputs.estimates,
            blocked=inputs.blocked,
            plan=inputs.plan,
            reserve_soc_pct=inputs.reserve_soc_pct,
            import_limit_w=grid.import_limit_w,
            grid=grid,
            grid_holds=holds,
        )
        outcome = self._allocator.prepare(now, allocation)
        site = self._surplus.update(
            now,
            inputs.flows,
            inputs.goals,
            band,
            controlled_draw=outcome.controlled_draw,
            grid_charging=held is not None and (held.grid_charge_w or 0.0) > 0,
        )
        # The gate judges this tick's intents against the free surplus *before* them
        # (each intent's own added demand is the gate's ``delta``).
        surplus = site.gate_estimate(self._settings.reserve_w, outcome.committed_w)
        outcome = self._allocator.run(now, site, allocation, outcome)
        battery_locked = allocation.battery_locked
        loadpoint_intents = self._evcc.step(
            now,
            self._loadpoint_ticks(
                now,
                inputs,
                grid,
                holds,
                site_trusted=site.trusted,
                battery_locked=battery_locked,
            ),
        )
        battery_intent = self._battery.step(
            now,
            inputs.plan,
            inputs.battery,
            soc,
            grid=grid,
            holds=holds,
            deficit_w=_house_deficit_w(inputs.flows),
            reserve_w=self._settings.reserve_w,
            ev_lock=(outcome.ev_lock or self._evcc.lock_wanted) and inputs.battery_present,
            export_over_limit=export_over_limit(inputs.flows, grid),
        )
        grid_flow = None if inputs.flows is None else inputs.flows.grid_w
        programs = self._programs.step(
            now,
            ProgramInputs(
                jobs=inputs.jobs,
                states=inputs.states,
                estimates=inputs.estimates,
                blocked=inputs.blocked,
                plan=inputs.plan,
                site_ok=grid_flow is not None
                and grid_flow.quality is Quality.OK
                and grid_flow.value is not None
                and surplus is not None
                and surplus.quality is Quality.OK,
                island=grid.grid_state is GridState.ISLAND,
            ),
        )
        headroom = _headroom_w(outcome.views, inputs)
        export_intent = self._export.step(now, inputs.inverter, inputs.flows, grid, headroom)
        intents = (
            *outcome.intents,
            *programs.intents,
            *loadpoint_intents,
            *(i for i in (battery_intent, export_intent) if i is not None),
        )
        views = dict(outcome.views)
        for device_id, why in programs.reasons.items():
            view = views.get(device_id)
            if view is not None:
                views[device_id] = replace(view, reasons=why)
        gate_inputs = GateInputs(
            flows=inputs.flows,
            surplus=surplus,
            estimates=dict(inputs.estimates),
            import_limit_w=grid.import_limit_w,
            reserve_soc_pct=inputs.reserve_soc_pct,
            ts=now,
            export_limit_w=grid.export_limit_w,
            para14a_budget_w=grid.para14a.budget_w,
            para14a_status=grid.para14a.status,
            grid_state=grid.grid_state,
            fuse_limit_w=grid.fuse_limit_w,
            para14a_devices=self._para14a_ids,
            battery_present=inputs.battery_present,
            export_headroom_w=headroom,
        )
        return TickResult(
            ts=now,
            gate_inputs=gate_inputs,
            site=site,
            intents=intents,
            views=MappingProxyType(views),
            deviations=tuple(outcome.deviations),
            total_w=outcome.total_w,
            free_w=outcome.free_w,
            allocated_draw_w=outcome.allocated_draw_w,
            attributable_deficit_w=outcome.attributable_deficit_w,
            battery=self._battery.view,
            grid=GridView(grid, situation_reasons(grid), holds),
            program_jobs=programs.jobs,
            program_events=programs.events,
            loadpoints=MappingProxyType(dict(self._evcc.views)),
            export=self._export.view,
        )

    def _loadpoint_ticks(
        self,
        now: datetime,
        inputs: ControllerInputs,
        grid: GridInputs,
        holds: GridHolds,
        *,
        site_trusted: bool,
        battery_locked: bool,
    ) -> list[LoadpointTick]:
        flow = None if inputs.flows is None else inputs.flows.grid_w
        import_w = flow.value if flow is not None and flow.quality is Quality.OK else None
        ticks: list[LoadpointTick] = []
        for device in self._loadpoints:
            spec = device.ev
            default = spec.default_mode if spec is not None else EV_MODE_DEFAULT
            ticks.append(
                LoadpointTick(
                    device=device,
                    state=inputs.states.get(device.id),
                    mode=inputs.ev_modes.get(device.id, default),
                    target=inputs.ev_targets.get(device.id),
                    band=device_band_now(inputs.plan, device.id, now),
                    battery_locked=battery_locked,
                    site_trusted=site_trusted,
                    grid=grid,
                    holds=holds,
                    import_w=import_w,
                    blocked=inputs.blocked.get(device.id),
                )
            )
        return ticks

    def report(self, decision: Decision) -> bool:
        """Feed the gate's decision on an intent back; ``True`` if it was ours and pending."""
        if self._battery.report(decision):
            return True
        if self._programs.report(decision):
            return True
        if self._evcc.report(decision):
            return True
        if self._export.report(decision):
            return True
        return self._allocator.report(decision)

    @property
    def battery(self) -> BatteryIntents:
        return self._battery

    @property
    def evcc(self) -> EvccIntents:
        return self._evcc

    @property
    def export(self) -> ExportChain:
        return self._export


def _headroom_w(views: Mapping[str, DeviceView], inputs: ControllerInputs) -> float:
    """``GateInputs.export_headroom_w``: chain steps 1 and 2 not yet exhausted (X3)."""
    devices = [
        DeviceHeadroom(
            v.device_id,
            rising=v.phase is Phase.PENDING_UP
            or (
                v.phase is Phase.IN_FLIGHT
                and v.pending_level is not None
                and v.pending_level > v.level
            ),
            step_w=0.0,
        )
        for v in views.values()
    ]
    battery = inputs.battery
    flow = None if inputs.flows is None else inputs.flows.battery_w
    battery_w = flow.value if flow is not None and flow.quality is Quality.OK else None
    high = None
    if battery is not None and battery.ranges.max_charge_w is not None:
        high = battery.ranges.max_charge_w.high
    return absorb_headroom_w(
        devices,
        None if battery is None else battery.held,
        battery_w,
        battery_blocked=battery is None or battery.blocked is not None,
        battery_high_w=high,
    )


def _house_deficit_w(flows: FlowSnapshot | None) -> float | None:
    """``max(0, house - pv)`` from ``OK`` readings: what the grid must cover without the battery."""
    if flows is None:
        return None
    house, pv = flows.house_w, flows.pv_w
    if house.value is None or pv.value is None:
        return None
    if house.quality is not Quality.OK or pv.quality is not Quality.OK:
        return None
    return max(0.0, house.value - pv.value)


class ControllerLoop:
    """One tick of I/O around :class:`Controller`: submit, report, poll."""

    def __init__(
        self,
        controller: Controller,
        *,
        submit: IntentSubmitter,
        poll: Poller | None = None,
        on_deviation: DeviationSink | None = None,
    ) -> None:
        self._controller = controller
        self._submit = submit
        self._poll = poll
        self._on_deviation = on_deviation
        self._tasks: set[asyncio.Task[None]] = set()
        self._last: TickResult | None = None

    @property
    def controller(self) -> Controller:
        return self._controller

    @property
    def last(self) -> TickResult | None:
        return self._last

    @property
    def gate_inputs(self) -> GateInputs | None:
        """What the gate's ``InputsProvider`` should return (the newest tick's snapshot)."""
        return None if self._last is None else self._last.gate_inputs

    @property
    def views(self) -> Mapping[str, DeviceView]:
        return {} if self._last is None else self._last.views

    async def tick(self, now: datetime, inputs: ControllerInputs) -> TickResult:
        result = self._controller.step(now, inputs)
        self._last = result
        for event in result.deviations:
            log.info("controller.deviation", kind=event.kind, device_id=event.device_id)
            if self._on_deviation is not None:
                self._on_deviation(event)
        for intent in result.intents:
            task = asyncio.create_task(self._submit_one(intent))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        if self._poll is not None:
            await self._poll()
        return result

    async def _submit_one(self, intent: Intent) -> None:
        try:
            decision = await self._submit(intent)
        except Exception as exc:
            # The gate records its own failures; a transport error here would otherwise
            # leave the device "in flight" until the machine's timeout.
            log.warning("controller.submit_failed", intent_id=intent.id, error=str(exc)[:200])
            if intent.id.startswith("battery:"):
                self._controller.battery.reset_pending()
            elif intent.id.startswith(PROGRAM_INTENT_PREFIX):
                self._controller.programs.reset_pending(intent)
            elif intent.id.startswith(EVCC_INTENT_PREFIX):
                self._controller.evcc.reset_pending()
            elif intent.id.startswith("inverter:"):
                self._controller.export.reset_pending()
            return
        self._controller.report(decision)

    async def drain(self) -> None:
        """Wait for the submissions in flight (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


__all__ = [
    "Controller",
    "ControllerInputs",
    "ControllerLoop",
    "DeviationSink",
    "GridView",
    "IntentSubmitter",
    "Poller",
    "TickResult",
]
