# SPDX-License-Identifier: Apache-2.0
"""The controller's tick (plan WP7): pure core plus a thin async loop.

:class:`Controller.step` is deterministic: ``(now, inputs) -> TickResult`` with the
intents to submit, the :class:`~suncore.core.control.GateInputs` snapshot for the safety
gate, a view per device and deviation events. It never sleeps and never reads a clock.
:class:`ControllerLoop.tick` runs one step and does the I/O around it through injected
callables: it submits every intent to the gate (as a task, so a settle wait never stalls
the tick), feeds the decisions back, and calls the gate's ``poll``.

Per tick, the composition root (WP9) does, in this order:

1. build :class:`ControllerInputs` from the entity cache (``compute_flows``), the drivers
   (``device_state()``), the power models (``observe`` then ``estimate``) and the gate
   (``blocked``: held, release pending, watchdog pause);
2. ``await loop.tick(now, inputs)`` - which runs ``controller.step``, publishes
   ``loop.gate_inputs`` (the gate's ``InputsProvider`` reads it), submits the intents and
   calls ``poll``;
3. reads ``loop.views`` / ``loop.last`` for the API and the live feed.

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
        band = None if inputs.plan.is_default else inputs.plan.battery_band_at(now)
        site = self._surplus.update(now, inputs.flows, inputs.goals, band)
        outcome = self._allocator.run(
            now,
            site,
            AllocationInputs(
                states=inputs.states,
                estimates=inputs.estimates,
                blocked=inputs.blocked,
                plan=inputs.plan,
                reserve_soc_pct=inputs.reserve_soc_pct,
            ),
        )
        gate_inputs = GateInputs(
            flows=inputs.flows,
            surplus=site.gate_estimate(self._settings.reserve_w),
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
