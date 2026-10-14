# SPDX-License-Identifier: Apache-2.0
"""The full control stack on a fake Home Assistant: the reference composition (Phase 3 WP8).

:class:`ControlStack` wires, on top of any connection that is an ``HAConnection`` *and* a
``ServiceCallerProvider`` (``FakeHAConnection``), exactly what the production composition
root will wire (WP9):

* an :class:`~suncore.ha.cache.EntityCache` fed by the connection (per-entity maximum ages for
  slow sensors), whose listener also feeds ``gate.observe`` for the devices' control entities
  with the state's context id (override detection);
* one real driver per device (``create_driver(write=ServiceCaller.write, states=cache)``) and one
  real power model (``create_power_model``);
* the real :class:`~suncore.controller.Controller` / ``ControllerLoop`` and the real
  :class:`~suncore.safety.gate.SafetyGate` with its watchdog, the injected mode provider and
  runtime store;
* the connection state listener (``connection_lost`` / ``connection_restored``) and the
  start-up ``gate.recover()``.

:meth:`ControlStack.tick` is one controller tick in the order documented in
``suncore.controller.loop``. Time is injected twice: ``clock`` (the simulated now) and
``sleep`` (the gate's settle wait); nothing here sleeps on its own. A tick may *block* on
simulated time (a release waits for the device's service call), so the owner runs it as a task
and keeps the clock moving - ``suncore.sim.runner`` does exactly that; the WP9 demo does it with
the real loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, tzinfo
from typing import Final, Protocol, cast

import structlog

from suncore.controller import (
    CallableQuantiser,
    Controller,
    ControllerInputs,
    ControllerLoop,
    DeviationEvent,
)
from suncore.controller.loop import TickResult
from suncore.core.balance import compute_flows
from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings, DeviceState, PowerEstimate
from suncore.core.decisions import Action, Decision, Intent, Reason
from suncore.core.devices import DeviceConfig, control_roles
from suncore.core.signals import Sample
from suncore.core.site import SiteConfig
from suncore.devices.drivers import ClimateDriver, NumberDriver, create_driver
from suncore.devices.power import create_power_model
from suncore.devices.power.common import PowerEstimator
from suncore.ha.cache import EntityCache
from suncore.ha.connection import (
    ConnectionState,
    EntityState,
    EntityUpdate,
    HAConnection,
    ServiceCallerProvider,
    Unsubscribe,
)
from suncore.safety.gate import (
    REASON_EMERGENCY_STOP,
    REASON_OVERRIDE_HELD,
    REASON_RELEASE_PENDING,
    SafetyGate,
)
from suncore.safety.modes import ModeProvider
from suncore.safety.runtime import MemoryRuntimeStore, RuntimeStore
from suncore.safety.watchdog import Watchdog

log = structlog.get_logger(__name__)

DEFAULT_SLOW_CADENCE_S: Final = 300.0


class SimConnection(HAConnection, ServiceCallerProvider, Protocol):
    """A connection the stack can run on: reads, entity updates and the write capability."""


class DeviceActuator(Protocol):
    """What ``create_driver`` returns, structurally (``suncore.safety.actuator.Actuator`` plus
    the driver's ``device_state``); the stack only reads from it, the gate writes."""

    @property
    def asset_id(self) -> str: ...

    def device_state(self) -> DeviceState: ...

    async def apply(self, intent: Intent) -> str | None: ...

    async def read_back(self) -> Action | None: ...

    async def release(self, baseline: Action | None) -> None: ...


class ControlStack:
    """Cache, drivers, power models, controller, gate and watchdog on one connection.

    Args:
        connection: the (fake) Home Assistant connection; connect it before :meth:`start`.
        site: the site configuration (grid, PV, battery, house entities).
        devices: the controlled devices.
        clock: the injected now (timezone-aware).
        sleep: the gate's settle wait (virtual time in the simulator).
        settings: controller and safety settings.
        modes: the operating-mode provider (``StaticModes`` in the simulator).
        store: the runtime store (kept across a simulated crash).
        tz: the site time zone (override resume time is local time).
        goals: the user's goals.
        import_limit_w: ``grid.import_limit_w`` of the site, if any.
        reserve_soc_pct: the battery reserve guard, if any.
        cadences: entity id -> expected update period in seconds of slow sensors (a polled
            power sensor: 300; an hourly counter: 3600).
        decisions: receives every decision of the gate (default: kept in :attr:`decisions`).
    """

    def __init__(
        self,
        connection: SimConnection,
        *,
        site: SiteConfig,
        devices: Sequence[DeviceConfig],
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]],
        settings: ControlSettings,
        modes: ModeProvider,
        store: RuntimeStore | None = None,
        tz: tzinfo,
        goals: GoalsConfig | None = None,
        import_limit_w: float | None = None,
        reserve_soc_pct: float | None = None,
        cadences: Mapping[str, float] | None = None,
        decisions: list[Decision] | None = None,
    ) -> None:
        self._connection = connection
        self._site = site
        self._devices = {d.id: d for d in devices}
        self._clock = clock
        self._settings = settings
        self._goals = goals if goals is not None else GoalsConfig(climate_before_export=True)
        self._import_limit_w = import_limit_w
        self._reserve_soc_pct = reserve_soc_pct
        self._cadences = dict(cadences or {})
        self.decisions: list[Decision] = decisions if decisions is not None else []
        self.store: RuntimeStore = store if store is not None else MemoryRuntimeStore()

        ages = {
            entity: period * settings.stale_cadence_factor
            for entity, period in self._cadences.items()
        }
        self.cache = EntityCache(clock=clock, max_age_overrides=ages)
        write = connection.service_caller().write
        self.drivers: dict[str, DeviceActuator] = {
            d.id: cast("DeviceActuator", create_driver(d, write=write, states=self._lookup))
            for d in devices
        }
        self.models: dict[str, PowerEstimator] = {
            d.id: create_power_model(d, power_cadence_s=self._power_cadence(d)) for d in devices
        }
        self.watchdog = Watchdog(settings, cadence_s=self._cadences)
        quantiser = CallableQuantiser(
            setpoint_steps={
                k: v.effective_step_c
                for k, v in self.drivers.items()
                if isinstance(v, ClimateDriver)
            },
            power_snaps={
                k: v.snap_power_w for k, v in self.drivers.items() if isinstance(v, NumberDriver)
            },
        )
        self.controller = Controller(devices, settings=settings, tz=tz, quantiser=quantiser)
        self.gate = SafetyGate(
            clock=clock,
            sink=self._sink,
            actuators=self.drivers,
            modes=modes,
            devices=self._devices.get,
            states=self._device_state,
            inputs=lambda: self.loop.gate_inputs,
            store=self.store,
            settings=settings,
            watchdog=self.watchdog,
            tz=tz,
            sleep=sleep,
        )
        self.deviations: list[DeviationEvent] = []
        self.loop = ControllerLoop(
            self.controller,
            submit=self.gate.submit,
            poll=self.gate.poll,
            on_deviation=self.deviations.append,
        )
        self._owner: dict[str, str] = {
            device.entities[role].entity_id: device.id
            for device in devices
            for role in control_roles(device.control)
        }
        self._last_observed: dict[str, Action] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._unsubscribe: list[Unsubscribe] = []
        self._lost = False
        self.stopped = False
        self.last: TickResult | None = None
        self.estimates: dict[str, PowerEstimate] = {}
        """The power estimates of the latest tick."""
        self.states: dict[str, DeviceState] = {}
        """The observed device states of the latest tick."""

    # --- wiring helpers ----------------------------------------------------------------------

    def _lookup(self, entity_id: str) -> EntityState | None:
        entry = self.cache.get(entity_id)
        return None if entry is None else entry.state

    def _device_state(self, device_id: str) -> DeviceState | None:
        return self.drivers[device_id].device_state()

    def _power_cadence(self, device: DeviceConfig) -> float | None:
        role = device.power_model.power_role
        if role is None or role not in device.entities:
            return None
        return self._cadences.get(device.entities[role].entity_id)

    async def _sink(self, decision: Decision) -> None:
        self.decisions.append(decision)

    @property
    def entity_ids(self) -> list[str]:
        """Every entity the stack subscribes to."""
        ids: dict[str, None] = dict.fromkeys(r.entity_id for r in self._site.entity_refs())
        for device in self._devices.values():
            ids.update(dict.fromkeys(r.entity_id for r in device.entity_refs()))
        return list(ids)

    @property
    def device_configs(self) -> Mapping[str, DeviceConfig]:
        return self._devices

    # --- lifecycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe, attach the listeners and recover what a crash left under control."""
        self._unsubscribe.append(self._connection.add_entity_listener(self._on_update))
        self._unsubscribe.append(self._connection.add_state_listener(self._on_state))
        await self._connection.subscribe_entities(self.entity_ids)
        await self.gate.recover()

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        """Graceful stop: the loop first, then release every device (baseline restored)."""
        self.stopped = True
        await self.loop.close()
        await self.gate.shutdown()
        self._detach()

    async def crash(self) -> None:
        """Simulate a process kill: everything stops at once, nothing is released.

        The runtime store (and Home Assistant) survive; a new stack on the same store calls
        ``gate.recover()`` in :meth:`start`.
        """
        self.stopped = True
        self._detach()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.loop.close()

    def _detach(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []

    # --- connection events -------------------------------------------------------------------

    def _on_state(self, state: ConnectionState) -> None:
        if state is ConnectionState.CONNECTED:
            if self._lost:
                self._lost = False
                self._spawn(self.gate.connection_restored())
        elif (
            state in (ConnectionState.RECONNECTING, ConnectionState.DISCONNECTED) and not self._lost
        ):
            self._lost = True
            self._spawn(self.gate.connection_lost())

    def _on_update(self, update: EntityUpdate) -> None:
        self.cache.ingest(update)
        for entity_id, state in update.changed.items():
            device_id = self._owner.get(entity_id)
            if device_id is not None:
                context = state.context.id if state.context is not None else None
                self._spawn(self._observe(device_id, context))

    async def _observe(self, device_id: str, context_id: str | None) -> None:
        """Feed the device's observed state to the gate's override detection."""
        observed = await self.drivers[device_id].read_back()
        if observed is None or observed == self._last_observed.get(device_id):
            return
        self._last_observed[device_id] = observed
        await self.gate.observe(device_id, observed, context_id=context_id)

    # --- one tick ----------------------------------------------------------------------------

    def _device_samples(self, device: DeviceConfig, model: PowerEstimator) -> dict[str, Sample]:
        out: dict[str, Sample] = {}
        for key in model.inputs:
            ref = device.entities.get(key)
            sample = self.cache.sample(ref.entity_id) if ref is not None else None
            if sample is not None:
                out[key] = sample
        return out

    def _blocked(self, now: datetime) -> dict[str, Reason]:
        blocked: dict[str, Reason] = {}
        for device_id in self._devices:
            runtime = self.gate.runtime_of(device_id)
            if self.gate.emergency_stopped:
                blocked[device_id] = REASON_EMERGENCY_STOP
            elif (pause := self.watchdog.pause_reason(device_id, now)) is not None:
                blocked[device_id] = pause
            elif runtime is not None and runtime.release_pending:
                blocked[device_id] = REASON_RELEASE_PENDING
            elif runtime is not None and runtime.is_held(now):
                blocked[device_id] = REASON_OVERRIDE_HELD
        return blocked

    async def tick(self) -> TickResult:
        """One controller tick (``suncore.controller.loop`` documents the order)."""
        now = self._clock()
        samples = self.cache.samples(self.entity_ids)
        await self.gate.check_site(self._site, samples)
        flows = compute_flows(self._site, samples)
        states: dict[str, DeviceState] = {}
        estimates: dict[str, PowerEstimate] = {}
        for device_id, device in self._devices.items():
            model = self.models[device_id]
            state = self.drivers[device_id].device_state()
            dev_samples = self._device_samples(device, model)
            await self.gate.check_device(
                device_id,
                {
                    ref.entity_id: s
                    for ref in device.entity_refs()
                    if (s := samples.get(ref.entity_id)) is not None
                    and ref.entity_id not in self._owner
                },
                state,
            )
            model.observe(now, state, dev_samples)
            states[device_id] = state
            estimates[device_id] = model.estimate(now, state, dev_samples)
        inputs = ControllerInputs(
            flows=flows,
            states=states,
            estimates=estimates,
            blocked=self._blocked(now),
            import_limit_w=self._import_limit_w,
            reserve_soc_pct=self._reserve_soc_pct,
            goals=self._goals,
        )
        self.estimates = estimates
        self.states = states
        self.last = await self.loop.tick(now, inputs)
        return self.last

    async def drain(self) -> None:
        """Wait for the submissions and observations in flight (they wait on simulated time)."""
        await self.loop.drain()
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def demo_stack(
    connection: SimConnection,
    devices: Sequence[DeviceConfig],
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
    modes: ModeProvider,
    settings: ControlSettings | None = None,
    store: RuntimeStore | None = None,
    goals: GoalsConfig | None = None,
) -> ControlStack:
    """The stack on a connected ``FakeHAConnection`` and its simulated house (demo mode, WP9).

    The site is the fake's own (``demo_site_config``), the time zone and the slow-sensor cadences
    come from the connection's scenario and devices. ``clock`` must be the *simulated* now
    (``connection.sim_time``, or an accelerated clock), ``sleep`` the gate's settle wait in the same
    time base. Drive it with ``await stack.tick()`` every ``settings.tick_s`` of simulated time;
    ``modes`` decides which device writes (shadow = "would have" entries only), nothing here
    reaches Home Assistant - the fake executes the writes against the simulated house.
    """
    from suncore.sim.fake_ha import FakeHAConnection, demo_site_config  # noqa: PLC0415

    assert isinstance(connection, FakeHAConnection), "demo_stack runs on the simulator only"
    tz = connection.scenario.start.tzinfo
    assert tz is not None
    return ControlStack(
        connection,
        site=demo_site_config(),
        devices=devices,
        clock=clock,
        sleep=sleep,
        settings=settings if settings is not None else ControlSettings(),
        modes=modes,
        store=store,
        tz=tz,
        goals=goals,
        cadences=connection.demo.devices.heartbeats(),
    )


__all__ = [
    "DEFAULT_SLOW_CADENCE_S",
    "ControlStack",
    "DeviceActuator",
    "SimConnection",
    "demo_stack",
]
