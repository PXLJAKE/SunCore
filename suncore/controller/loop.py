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
     and move the surplus between battery and consumers, ``BatteryBand`` limits are not
     acted on before WP10);

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
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from types import MappingProxyType

import structlog

from suncore.controller.allocation import (
    AllocationInputs,
    Allocator,
    DeviationEvent,
    DeviceView,
)
from suncore.controller.levels import ConfigQuantiser, Quantiser
from suncore.controller.surplus import SiteSurplus, SurplusTracker
from suncore.core.balance import FlowSnapshot
from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings, DeviceState, GateInputs, Plan, PowerEstimate
from suncore.core.decisions import Decision, Intent, Reason
from suncore.core.devices import DeviceConfig

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


class Controller:
    """Deterministic controller core: surplus, allocation, hysteresis, back-off."""

    def __init__(
        self,
        devices: Iterable[DeviceConfig] = (),
        *,
        settings: ControlSettings | None = None,
        tz: tzinfo = UTC,
        quantiser: Quantiser | None = None,
    ) -> None:
        self._settings = settings if settings is not None else ControlSettings()
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

    def step(self, now: datetime, inputs: ControllerInputs) -> TickResult:
        """One deterministic tick."""
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "Controller.step needs a timezone-aware now"
            raise ValueError(msg)
        # A band applies only while a plan interval covers ``now``: a plan that ran out (or a
        # default plan) leaves the Phase 3 goal rules in charge, never the empty default band.
        interval = inputs.plan.interval_at(now)
        band = None if interval is None else interval.battery
        allocation = AllocationInputs(
            states=inputs.states,
            estimates=inputs.estimates,
            blocked=inputs.blocked,
            plan=inputs.plan,
            reserve_soc_pct=inputs.reserve_soc_pct,
            import_limit_w=inputs.import_limit_w,
        )
        outcome = self._allocator.prepare(now, allocation)
        site = self._surplus.update(
            now, inputs.flows, inputs.goals, band, controlled_draw=outcome.controlled_draw
        )
        # The gate judges this tick's intents against the free surplus *before* them
        # (each intent's own added demand is the gate's ``delta``).
        surplus = site.gate_estimate(self._settings.reserve_w, outcome.committed_w)
        outcome = self._allocator.run(now, site, allocation, outcome)
        gate_inputs = GateInputs(
            flows=inputs.flows,
            surplus=surplus,
            estimates=dict(inputs.estimates),
            import_limit_w=inputs.import_limit_w,
            reserve_soc_pct=inputs.reserve_soc_pct,
            ts=now,
        )
        return TickResult(
            ts=now,
            gate_inputs=gate_inputs,
            site=site,
            intents=tuple(outcome.intents),
            views=MappingProxyType(dict(outcome.views)),
            deviations=tuple(outcome.deviations),
            total_w=outcome.total_w,
            free_w=outcome.free_w,
            allocated_draw_w=outcome.allocated_draw_w,
            attributable_deficit_w=outcome.attributable_deficit_w,
        )

    def report(self, decision: Decision) -> bool:
        """Feed the gate's decision on an intent back; ``True`` if it was ours and pending."""
        return self._allocator.report(decision)


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
    "IntentSubmitter",
    "Poller",
    "TickResult",
]
