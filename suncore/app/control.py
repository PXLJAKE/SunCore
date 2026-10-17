# SPDX-License-Identifier: Apache-2.0
"""The control stack: cache, drivers, power models, controller, gate and watchdog on one connection.

This is the composition both the production runtime (``suncore.app.runtime``) and the closed-loop
simulator (``suncore.sim.stack``, the demo and the scenario runner) use (Phase 3 WP9; the
simulator's ``ControlStack`` of WP8 was generalised here instead of being duplicated). It wires

* an :class:`~suncore.ha.cache.EntityCache` (the runtime's own, or a private one) with per-entity
  maximum ages for slow sensors, whose update feed also drives ``gate.observe`` for the devices'
  control entities with the state's context id (override detection);
* one real driver per device (``create_driver(write=<the connection's write capability>,
  states=<entity cache>)``, built through :func:`suncore.safety.wiring.build_actuators` so this
  module never names the write capability, boundary rule 3) and one real power model
  (``create_power_model``), restored from and saved to the ``PowerModelStateStore`` when given;
* the real :class:`~suncore.controller.Controller` / ``ControllerLoop`` and the real
  :class:`~suncore.safety.gate.SafetyGate` with its watchdog, the injected mode provider and
  runtime store;
* the connection life cycle: ``gate.recover()`` after the first successful connect (never before),
  ``connection_lost`` / ``connection_restored`` from the state listener, ``loop.close()`` before
  ``gate.shutdown()`` on stop.

:meth:`ControlStack.tick` is one controller tick in the order documented in
``suncore.controller.loop``. While the connection is lost it does nothing: no device check, no
release, no controller step (a release cannot succeed without Home Assistant, and trying it every
tick logged a failed decision per device per tick for the whole outage; the watchdog is already in
its safe state through ``gate.connection_lost()``). :meth:`run` is the periodic driver; time is
injected twice (``clock`` = the now, ``sleep`` = the gate's settle wait and the tick period), so the
simulator runs it in virtual time and the demo in accelerated time. A tick may *block* on time (a
release waits for the device's service call), so :meth:`run` starts the next tick only when the
previous one has finished.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import datetime, tzinfo
from typing import Final, cast

import structlog

from suncore.controller import (
    CallableQuantiser,
    Controller,
    ControllerInputs,
    ControllerLoop,
    DeviationEvent,
)
from suncore.controller.loop import TickResult
from suncore.core.balance import FlowSnapshot, compute_flows
from suncore.core.config import GoalsConfig
from suncore.core.control import (
    TEMPERATURE_ROLE,
    ControlSettings,
    DeviceState,
    PowerEstimate,
    has_heat_limit,
)
from suncore.core.controlstate import ControlState, DeviceControlState, SiteControlState
from suncore.core.decisions import Action, Decision, Reason
from suncore.core.devices import DeviceConfig, control_roles
from suncore.core.identity import identity_token
from suncore.core.measurements import (
    SITE_BATTERY_W,
    SITE_GRID_W,
    SITE_HOUSE_W,
    SITE_PV_W,
    SITE_SOC_PCT,
    device_power_key,
)
from suncore.core.signals import Quality, Sample
from suncore.core.site import SiteConfig
from suncore.devices.drivers import ClimateDriver, NumberDriver, create_driver
from suncore.devices.power import create_power_model, restore_power_model
from suncore.devices.power.common import PowerEstimator
from suncore.ha.cache import EntityCache
from suncore.ha.connection import (
    ConnectionState,
    EntityState,
    EntityUpdate,
    HAConnection,
    Unsubscribe,
)
from suncore.safety.gate import (
    REASON_EMERGENCY_STOP,
    REASON_ORPHANED,
    REASON_OVERRIDE_HELD,
    REASON_RELEASE_PENDING,
    DecisionSink,
    SafetyGate,
)
from suncore.safety.modes import ModeProvider
from suncore.safety.runtime import MemoryRuntimeStore, RuntimeStore
from suncore.safety.watchdog import Watchdog
from suncore.safety.wiring import StatefulActuator, WriterFn, build_actuators
from suncore.storage import PowerModelStateStore

log = structlog.get_logger(__name__)

DEFAULT_POWER_SAVE_INTERVAL_S: Final = 300.0
"""The learned power models are saved this often (and on shutdown)."""
DEVIATION_HISTORY: Final = 100
CELSIUS: Final = "°C"
"""The only probe unit judged (HA ``UnitOfTemperature.CELSIUS``); °F counts as unknown."""

DeviceActuator = StatefulActuator
"""What ``create_driver`` returns, structurally: the gate's actuator plus ``device_state``."""

StateChangeListener = Callable[[], None]
MeasurementSink = Callable[[datetime, Mapping[str, float | None]], None]
"""Receives ``(tick time, {measurement key: value or None})`` after every tick."""


def probe_temperature_c(cache: EntityCache, device: DeviceConfig) -> float | None:
    """The reading of a thermal device's ``temperature`` probe for ``core.control.heat_capped``.

    Only a fresh, numeric reading in °C counts; anything else is ``None`` (= a full tank for the
    controller and the gate). The drivers do not read the probe: it is not part of the control,
    and only the cache knows its age.
    """
    ref = device.entities.get(TEMPERATURE_ROLE)
    if ref is None:
        return None
    entry = cache.get(ref.entity_id)
    sample = cache.sample(ref.entity_id)
    if entry is None or sample is None or sample.quality is not Quality.OK:
        return None
    if entry.state.attributes.get("unit_of_measurement") != CELSIUS:
        return None
    return sample.value


def grid_heartbeats(site: SiteConfig | None) -> tuple[tuple[str, ...], ...]:
    """The grid meter as one heartbeat group: the signals that must keep reporting.

    One signed sensor, or an import/export pair (alive while either side reports).
    """
    if site is None:
        return ()
    grid = site.grid
    ids = tuple(r.entity_id for r in (grid.power, grid.import_power, grid.export_power) if r)
    return (ids,) if ids else ()


class ControlStack:
    """Cache, drivers, power models, controller, gate and watchdog on one connection.

    Args:
        connection: the Home Assistant connection (real, or the simulator's fake). Its write
            capability, if any, goes to the drivers through ``safety.wiring``.
        site: the site configuration (grid, PV, battery, house entities); ``None`` while none is
            stored (nothing to balance: the stack only keeps the gate, e.g. for the emergency stop).
        devices: the controlled devices.
        clock: the injected now (timezone-aware).
        sleep: the gate's settle wait and the tick period (virtual or accelerated time).
        settings: controller and safety settings.
        modes: the operating-mode provider.
        store: the runtime store (kept across a simulated crash; default in memory).
        tz: the site time zone (override resume time is local time).
        goals: the user's goals.
        import_limit_w: ``grid.import_limit_w`` of the site, if any.
        reserve_soc_pct: the battery reserve guard (``battery.min_soc_pct``), if any.
        cadences: entity id -> expected update period in seconds of the power-model sensors
            (``suncore.app.cadence``); sets the entity cache's maximum age and the watchdog's
            staleness judgement.
        decisions: if given, every decision of the gate is appended to it (simulator evidence);
            without ``decisions`` and ``sink`` the stack keeps its own list.
        sink: receives every decision of the gate (the decision log).
        cache: the entity cache to read (the runtime's); ``None`` = a private one that this
            stack feeds itself.
        power_store: persists the learned power-model state; ``None`` = not persisted.
        power_save_interval_s: how often the power models are saved.
        attach: subscribe to the connection in :meth:`start`; the runtime passes ``False`` and
            forwards the events (:meth:`on_update`, :meth:`on_state`) after it fed the cache.
        after_handover: this stack replaces one stopped by :meth:`handover` (a configuration
            change): releases that stack deferred for a protection time stay deferred
            (``gate.recover(after_handover=True)``).
        predecessor: the gate of the replaced stack: devices it could not release (removed or
            re-mapped while Home Assistant was down) are released with its actuators.
        on_measurements: called after every tick with the tick time and the site and device
            measurements of that tick (``suncore.core.measurements`` keys; ``None`` = no valid
            value). Synchronous and cheap by contract; an exception is logged and swallowed -
            recording can never disturb the control loop.
        defer_recover: wait for :meth:`allow_recover` before ``gate.recover()`` (the runtime
            first learns Home Assistant's time zone, whose change would rebuild the stack in
            the middle of the recovery).
    """

    def __init__(  # noqa: PLR0915 - the composition root of the control layer
        self,
        connection: HAConnection,
        *,
        site: SiteConfig | None,
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
        sink: DecisionSink | None = None,
        cache: EntityCache | None = None,
        power_store: PowerModelStateStore | None = None,
        power_save_interval_s: float = DEFAULT_POWER_SAVE_INTERVAL_S,
        attach: bool = True,
        after_handover: bool = False,
        predecessor: SafetyGate | None = None,
        defer_recover: bool = False,
        on_measurements: MeasurementSink | None = None,
    ) -> None:
        self._connection = connection
        self._on_measurements = on_measurements
        self._recover_allowed = not defer_recover
        self._after_handover = after_handover
        self._site = site
        self._devices = {d.id: d for d in devices}
        self._clock = clock
        self._sleep = sleep
        self._settings = settings
        self._goals = goals if goals is not None else GoalsConfig(climate_before_export=True)
        self._import_limit_w = import_limit_w
        self._reserve_soc_pct = reserve_soc_pct
        self._cadences = dict(cadences or {})
        self._extra_sink = sink
        self._power_store = power_store
        self._power_save_interval_s = power_save_interval_s
        self._attach = attach
        self.decisions: list[Decision] | None = (
            decisions if decisions is not None else ([] if sink is None else None)
        )
        self.store: RuntimeStore = store if store is not None else MemoryRuntimeStore()
        self.last_decisions: dict[str, Decision] = {}
        """The newest decision of the gate per device (the "Why?" view)."""

        ages = {
            entity: period * settings.stale_cadence_factor
            for entity, period in self._cadences.items()
        }
        self._owns_cache = cache is None
        if cache is None:
            self.cache = EntityCache(clock=clock, max_age_overrides=ages)
        else:
            self.cache = cache
            self.cache.set_max_age_overrides(ages)
        # Only the grid meter has to keep reporting; values that change rarely (state of
        # charge, idle power sensors) stay valid while available (see ``ha/cache.py``).
        self.cache.set_heartbeats(grid_heartbeats(site))
        self.drivers: dict[str, DeviceActuator] = build_actuators(
            connection, devices, self._make_driver
        )
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
            predecessor=predecessor,
        )
        self.deviations: deque[DeviationEvent] = deque(maxlen=DEVIATION_HISTORY)
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
        self._listeners: list[StateChangeListener] = []
        self._lifecycle = asyncio.Lock()
        self._runner: asyncio.Task[None] | None = None
        self._tick_task: asyncio.Task[TickResult | None] | None = None
        self._last_save: datetime | None = None
        self._lost = False
        self.recovered = False
        """``gate.recover()`` has run (after the first successful connect)."""
        self.stopped = False
        self.last: TickResult | None = None
        self.estimates: dict[str, PowerEstimate] = {}
        """The power estimates of the latest tick."""
        self.states: dict[str, DeviceState] = {}
        """The observed device states of the latest tick."""

    # --- wiring helpers ----------------------------------------------------------------------

    def _make_driver(self, device: DeviceConfig, write: WriterFn) -> DeviceActuator:
        return cast("DeviceActuator", create_driver(device, write=write, states=self._lookup))

    def _lookup(self, entity_id: str) -> EntityState | None:
        entry = self.cache.get(entity_id)
        return None if entry is None else entry.state

    def _device_state(self, device_id: str) -> DeviceState | None:
        return self._with_temperature(
            self._devices[device_id], self.drivers[device_id].device_state()
        )

    def _with_temperature(self, device: DeviceConfig, state: DeviceState) -> DeviceState:
        if not has_heat_limit(device):
            return state
        return state.model_copy(update={"temperature_c": probe_temperature_c(self.cache, device)})

    def _power_cadence(self, device: DeviceConfig) -> float | None:
        role = device.power_model.power_role
        if role is None or role not in device.entities:
            return None
        return self._cadences.get(device.entities[role].entity_id)

    async def _sink(self, decision: Decision) -> None:
        self.last_decisions[decision.intent.asset_id] = decision
        if self.decisions is not None:
            self.decisions.append(decision)
        try:
            if self._extra_sink is not None:
                await self._extra_sink(decision)
        finally:
            self._notify()

    @property
    def entity_ids(self) -> list[str]:
        """Every entity the stack subscribes to."""
        ids: dict[str, None] = {}
        if self._site is not None:
            ids.update(dict.fromkeys(r.entity_id for r in self._site.entity_refs()))
        for device in self._devices.values():
            ids.update(dict.fromkeys(r.entity_id for r in device.entity_refs()))
        return list(ids)

    @property
    def device_configs(self) -> Mapping[str, DeviceConfig]:
        return self._devices

    @property
    def settings(self) -> ControlSettings:
        return self._settings

    @property
    def lost(self) -> bool:
        """The connection is gone (reconnecting or disconnected); ticks are skipped."""
        return self._lost

    @property
    def ready(self) -> bool:
        """Devices recovered, connection up: the stack may tick and write."""
        return (
            self.recovered
            and not self._lost
            and not self.stopped
            and self._connection.state is ConnectionState.CONNECTED
        )

    def set_goals(self, goals: GoalsConfig) -> None:
        self._goals = goals

    def add_listener(self, listener: StateChangeListener) -> Unsubscribe:
        """Call ``listener`` whenever the control state may have changed (tick, decision)."""
        self._listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                log.exception("control.listener_failed")

    # --- lifecycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        """Attach to the connection (if asked to), subscribe and, when connected, recover.

        ``gate.recover()`` (release what a crash left under control, load the emergency-stop
        flag) runs only once the connection is up: before that no release could succeed. With
        ``attach=False`` the owner subscribes and forwards the events itself.
        """
        if self._attach:
            self._unsubscribe.append(self._connection.add_entity_listener(self.on_update))
            self._unsubscribe.append(self._connection.add_state_listener(self.on_state))
            await self._connection.subscribe_entities(self.entity_ids)
        if self._connection.state is ConnectionState.CONNECTED:
            await self.recover()

    def allow_recover(self) -> None:
        """End ``defer_recover``: recover now if the link is up (else on the first connect)."""
        if self._recover_allowed:
            return
        self._recover_allowed = True
        if self._connection.state is ConnectionState.CONNECTED and not self.stopped:
            self._spawn(self._connected())

    async def recover(self) -> None:
        """Restore the learned power models, then ``gate.recover()``; idempotent."""
        async with self._lifecycle:
            if self.recovered or self.stopped or not self._recover_allowed:
                return
            await self._restore_models()
            await self.gate.recover(after_handover=self._after_handover)
            self.recovered = True
        self._notify()

    def start_ticking(self) -> None:
        """Start the periodic tick (:meth:`run`) as a task owned by the stack."""
        if self._runner is None:
            self._runner = asyncio.create_task(self.run(), name="suncore-control")

    async def run(self) -> None:
        """Tick every ``settings.tick_s`` while :attr:`ready`; recover when the link comes up."""
        while not self.stopped:
            if (
                self._connection.state is ConnectionState.CONNECTED
                and self._recover_allowed
                and (not self.recovered or self._lost)
            ):
                # Retries a failed recover and ends an outage whose restore it had cut short.
                try:
                    await self._connected()
                except Exception:
                    log.exception("control.restore_failed")
            if self.ready and (self._tick_task is None or self._tick_task.done()):
                self._tick_task = asyncio.ensure_future(self._tick_logged())
            await self._sleep(self._settings.tick_s)

    async def _tick_logged(self) -> TickResult | None:
        try:
            return await self.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("control.tick_failed")
            return None

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _stop_tasks(self) -> None:
        for task in (self._runner, self._tick_task):
            if task is not None:
                task.cancel()
        for task in (self._runner, self._tick_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._runner = self._tick_task = None

    async def shutdown(self) -> None:
        """Graceful stop: stop ticking, release every device (baseline restored), save models."""
        self.stopped = True
        await self._stop_tasks()
        await self.loop.close()
        await self.gate.shutdown()
        await self.save_models()
        self._detach()
        await self._cancel_observers()

    async def handover(self, keep: Collection[str]) -> None:
        """Stop for a configuration change; the successor stack takes over the devices.

        Like :meth:`shutdown`, but the devices in ``keep`` (the same device in the new
        configuration) are released without breaking their minimum run/pause time
        (``gate.handover``): a harmless edit must not short-cycle a compressor.
        """
        self.stopped = True
        await self._stop_tasks()
        await self.loop.close()
        await self.gate.handover(keep)
        await self.save_models()
        self._detach()
        await self._cancel_observers()

    async def crash(self) -> None:
        """Simulate a process kill: everything stops at once, nothing is released.

        The runtime store (and Home Assistant) survive; a new stack on the same store calls
        ``gate.recover()`` in :meth:`start`.
        """
        self.stopped = True
        self._detach()
        await self._stop_tasks()
        await self._cancel_observers()
        await self.loop.close()

    async def _cancel_observers(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _detach(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []

    # --- power model persistence ----------------------------------------------------------------

    async def _restore_models(self) -> None:
        if self._power_store is None:
            return
        for device_id, model in self.models.items():
            try:
                restore_power_model(model, await self._power_store.get(device_id))
            except Exception:
                log.exception("control.power_model_restore_failed", device_id=device_id)

    async def save_models(self) -> None:
        """Persist every model that has something to keep; failures are logged, never raised."""
        if self._power_store is None:
            return
        self._last_save = self._clock()
        for device_id, model in self.models.items():
            try:
                state = model.dump_state()
                if state:
                    await self._power_store.put(device_id, state)
            except Exception:
                log.exception("control.power_model_save_failed", device_id=device_id)

    def _maybe_save_models(self, now: datetime) -> None:
        if self._power_store is None:
            return
        due = self._last_save is None or (
            (now - self._last_save).total_seconds() >= self._power_save_interval_s
        )
        if due:
            self._last_save = now
            self._spawn(self.save_models())

    # --- connection events -------------------------------------------------------------------

    def on_state(self, state: ConnectionState) -> None:
        """Connection state listener: first connect -> recover, loss/restore -> the gate."""
        if state is ConnectionState.CONNECTED:
            self._spawn(self._connected())
        elif (
            state in (ConnectionState.RECONNECTING, ConnectionState.DISCONNECTED) and not self._lost
        ):
            self._lost = True
            self._spawn(self._disconnected())

    async def _connected(self) -> None:
        # Recover first (a start-up with a crash left devices under control), then the restore.
        # Both under the lifecycle order the lock gives; a failure is retried by `run`.
        try:
            await self.recover()
        except Exception:
            log.exception("control.recover_failed")
            return
        # A handler of an earlier CONNECTED may run late (``recover`` awaited) after the link
        # dropped again: only a link that is up now ends the outage.
        if self._lost and self._connection.state is ConnectionState.CONNECTED:
            self._lost = False
            await self.gate.connection_restored()
        self._notify()

    async def _disconnected(self) -> None:
        await self.gate.connection_lost()
        self._notify()

    # --- entity feed -------------------------------------------------------------------------

    def on_update(self, update: EntityUpdate) -> None:
        """Entity listener: feed the cache (if it is ours) and the override detection.

        Observations reach the gate only after :meth:`recover`: before it the gate would judge a
        persisted runtime against a state it has not reconciled yet (and miss the persisted
        emergency stop). The next change or resync (60 s) is observed normally.
        """
        if self._owns_cache:
            self.cache.ingest(update)
        if not self.recovered:
            return
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

    def _watched(self, device: DeviceConfig, model: PowerEstimator) -> list[str]:
        """The entities the watchdog judges: what the power model reads, never a control entity.

        An informational sensor (a daily ``energy_yesterday``, a tank temperature nobody uses)
        must not be able to put the device into its safe state by being quiet.
        """
        return [
            device.entities[key].entity_id
            for key in model.inputs
            if key in device.entities and device.entities[key].entity_id not in self._owner
        ]

    def _blocked(self, now: datetime) -> dict[str, Reason]:
        blocked: dict[str, Reason] = {}
        for device_id in self._devices:
            runtime = self.gate.runtime_of(device_id)
            if self.gate.emergency_stopped:
                blocked[device_id] = REASON_EMERGENCY_STOP
            elif (pause := self.watchdog.pause_reason(device_id, now)) is not None:
                blocked[device_id] = pause
            elif self.gate.is_orphaned(device_id):
                blocked[device_id] = REASON_ORPHANED
            elif runtime is not None and runtime.release_pending:
                blocked[device_id] = REASON_RELEASE_PENDING
            elif runtime is not None and runtime.is_held(now):
                blocked[device_id] = REASON_OVERRIDE_HELD
        return blocked

    async def tick(self) -> TickResult | None:
        """One controller tick (``suncore.controller.loop`` documents the order).

        ``None`` while the connection is lost: nothing can be written or released then, and the
        watchdog already holds the site in its safe state (``gate.connection_lost``).
        """
        if self._lost:
            return None
        now = self._clock()
        samples = self.cache.samples(self.entity_ids)
        flows = None
        if self._site is not None:
            await self.gate.check_site(self._site, samples)
            flows = compute_flows(self._site, samples)
        states: dict[str, DeviceState] = {}
        estimates: dict[str, PowerEstimate] = {}
        for device_id, device in self._devices.items():
            model = self.models[device_id]
            state = self._device_state(device_id)
            assert state is not None
            dev_samples = self._device_samples(device, model)
            await self.gate.check_device(
                device_id,
                {e: s for e in self._watched(device, model) if (s := samples.get(e)) is not None},
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
        self._record_measurements(now, flows, estimates)
        self.last = await self.loop.tick(now, inputs)
        self._maybe_save_models(now)
        self._notify()
        return self.last

    def _record_measurements(
        self,
        now: datetime,
        flows: FlowSnapshot | None,
        estimates: Mapping[str, PowerEstimate],
    ) -> None:
        """Hand this tick's values to the recorder; only ``OK`` data counts (a gap otherwise)."""
        if self._on_measurements is None:
            return
        values: dict[str, float | None] = {}
        if flows is not None:
            for key, flow in (
                (SITE_GRID_W, flows.grid_w),
                (SITE_PV_W, flows.pv_w),
                (SITE_BATTERY_W, flows.battery_w),
                (SITE_SOC_PCT, flows.battery_soc_pct),
                (SITE_HOUSE_W, flows.house_w),
            ):
                values[key] = flow.value if flow.quality is Quality.OK else None
        for device_id, estimate in estimates.items():
            values[device_power_key(device_id)] = (
                estimate.value_w if estimate.quality is Quality.OK else None
            )
        try:
            self._on_measurements(now, values)
        except Exception:
            log.exception("control.measurement_sink_failed")

    async def drain(self) -> None:
        """Wait for the submissions and observations in flight (they wait on simulated time)."""
        await self.loop.drain()
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # --- the control state for the API -----------------------------------------------------------

    def control_state(self) -> ControlState:
        """The per-device and site state as of now (latest tick + live gate and mode data)."""
        now = self._clock()
        last = self.last
        devices = [self._device_control_state(device, now) for device in self._devices.values()]
        site = None
        if last is not None:
            surplus = last.site
            site = SiteControlState(
                available_w=surplus.available_w,
                total_w=last.total_w,
                free_w=last.free_w,
                allocated_draw_w=last.allocated_draw_w,
                deficit_w=last.attributable_deficit_w,
                uncertainty_w=surplus.uncertainty_w,
                quality=surplus.quality,
                grid_w=surplus.grid_w,
                battery_w=surplus.battery_w,
                soc_pct=surplus.soc_pct,
            )
        return ControlState(
            ts=None if last is None else last.ts,
            active=self.ready,
            emergency_stop=self.gate.emergency_stopped,
            global_mode=self.gate.mode,
            site=site,
            devices=devices,
            proposals=list(self.gate.proposals()),
        )

    def _device_control_state(self, device: DeviceConfig, now: datetime) -> DeviceControlState:
        modes = self.gate.modes_of(device.id)
        runtime = self.gate.runtime_of(device.id)
        view = self.loop.views.get(device.id)
        held = runtime is not None and runtime.is_held(now)
        paused: Reason | None = None
        if self.gate.emergency_stopped:
            paused = REASON_EMERGENCY_STOP
        else:
            paused = self.watchdog.pause_reason(device.id, now)
        return DeviceControlState(
            device_id=device.id,
            name=device.name,
            kind=device.kind,
            identity=identity_token(device),
            global_mode=modes.global_mode,
            device_mode=modes.device_mode,
            effective_mode=modes.effective,
            state=self._device_state(device.id),
            estimate=self.estimates.get(device.id),
            phase=None if view is None else view.phase.value,
            level=None if view is None else view.level,
            top=None if view is None else view.top,
            expected_power_w=None if view is None else view.power_w,
            committed_w=None if view is None else view.committed_w,
            base_setpoint_c=None if view is None else view.base_setpoint_c,
            under_control=runtime is not None and runtime.under_control,
            held=held,
            held_until=runtime.override_until if held and runtime is not None else None,
            release_pending=runtime is not None and runtime.release_pending,
            release_due=None if runtime is None else runtime.release_due_ts,
            paused=paused,
            reasons=[] if view is None else list(view.reasons),
            since=None if view is None else view.since,
            last_decision=self.last_decisions.get(device.id),
        )


__all__ = [
    "DEFAULT_POWER_SAVE_INTERVAL_S",
    "ControlStack",
    "DeviceActuator",
    "probe_temperature_c",
]
