# SPDX-License-Identifier: Apache-2.0
"""The closed-loop scenario runner: the full stack in simulated time (Phase 3 WP8).

:class:`ScenarioRunner` plays a scenario file against

* a :class:`~suncore.sim.fake_ha.FakeHAConnection` (house, devices with physics, faults, manual
  user events, the verified write services, delayed pushes with contexts),
* a :class:`~suncore.sim.stack.ControlStack` (entity cache, real drivers, real power models, the
  real controller and the real safety gate, ``StaticModes`` with the devices in automatic),
* a :class:`VirtualTime` that owns the clock: every ``sleep`` (the gate's settle wait, the 2 s
  of the LG integration, the service flood floor) waits on *simulated* time, so a run takes
  milliseconds of CPU and nothing ever sleeps or touches the network.

Per scenario step (``scenario.step_s`` = the controller tick): advance virtual time to the step
(firing delayed pushes, manual events and woken sleepers in time order), step the house, let the
listeners (cache, override observation) run, then start a tick as a task - a tick may block on
simulated time (a release waits for the device), so it overlaps the following time advance like the
real loop does; a step whose previous tick is still running is skipped.

Evidence (:class:`RunResult`) is taken from the *physical* devices and Home Assistant's entity log,
not from the gate's own opinion: switch counts and hold times from the devices' on/off state,
import from the house balance, the gate's decisions for the "why".

Reuse for the demo (WP9): :func:`build_device_configs` turns a scenario's device list into
``DeviceConfig`` objects through the real templates; :class:`~suncore.sim.stack.ControlStack` is the
composition; :class:`VirtualTime` is only needed for tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings
from suncore.core.decisions import (
    Action,
    Decision,
    DecisionKind,
    OperatingMode,
    PowerSetpointAction,
    ReleaseAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig, step_power_w
from suncore.ha.connection import RateLimiter
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import MemoryRuntimeStore, RuntimeStore
from suncore.sim.devices import SimClimate, SimDevice, SimWaterHeater
from suncore.sim.fake_ha import FakeHAConnection, demo_site_config
from suncore.sim.scenario import Scenario, SimDeviceSpec
from suncore.sim.stack import ControlStack
from suncore.templates import DeviceTemplate, TemplateCatalog

SETTLE_ROUNDS: Final = 6
"""Event-loop rounds that let woken tasks run until they block again."""


# --- virtual time ------------------------------------------------------------------------


class VirtualTime:
    """The simulated clock and every sleeper waiting on it."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            msg = "start must be timezone-aware"
            raise ValueError(msg)
        self.start = start
        self.now = start
        self._waiters: list[tuple[datetime, int, asyncio.Future[None]]] = []
        self._seq = 0

    def clock(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return (self.now - self.start).total_seconds()

    async def sleep(self, seconds: float) -> None:
        """Wait ``seconds`` of simulated time (returns at once for ``<= 0``)."""
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._seq += 1
        self._waiters.append((self.now + timedelta(seconds=seconds), self._seq, future))
        self._waiters.sort(key=lambda w: (w[0], w[1]))
        await future

    @staticmethod
    async def settle() -> None:
        """Let every ready task run until it blocks (on simulated time or on nothing)."""
        for _ in range(SETTLE_ROUNDS):
            await asyncio.sleep(0)

    def next_wake(self) -> datetime | None:
        self._waiters = [w for w in self._waiters if not w[2].done()]
        return self._waiters[0][0] if self._waiters else None

    async def advance_to(
        self,
        target: datetime,
        *,
        events: Callable[[], datetime | None] | None = None,
        run_events: Callable[[datetime], None] | None = None,
    ) -> None:
        """Move to ``target``, firing sleepers and ``run_events`` (device events) in time order."""
        while True:
            candidates = [t for t in (self.next_wake(), events() if events else None) if t]
            nxt = min(candidates, default=None)
            if nxt is None or nxt > target:
                break
            self.now = max(self.now, nxt)
            if run_events is not None:
                run_events(self.now)
            while self._waiters and self._waiters[0][0] <= self.now:
                _, _, future = self._waiters.pop(0)
                if not future.done():
                    future.set_result(None)
            await self.settle()
        self.now = max(self.now, target)
        await self.settle()


# --- device configs from a scenario ------------------------------------------------------


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Overrides on top of the template defaults; ``power_model`` is replaced as a whole."""
    out = dict(base)
    for key, value in override.items():
        if key == "power_model":
            out[key] = dict(value)
        elif isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def build_device_config(
    template: DeviceTemplate, spec: SimDeviceSpec, device: SimDevice
) -> DeviceConfig:
    """A ``DeviceConfig`` from the template defaults, the simulated device's entities and the
    scenario's ``control`` overrides (what the wizard would store)."""
    d = template.defaults
    base: dict[str, Any] = {
        "id": spec.id,
        "template_id": template.id,
        "name": spec.name or spec.id.replace("_", " ").title(),
        "kind": template.device_kind.value,
        "entities": {role: {"entity_id": e} for role, e in device.roles().items()},
        "power_model": d.power_model.model_dump(mode="json", exclude_none=True),
        "control": d.control.model_dump(mode="json", exclude_none=True),
        "priority": 0,
        "min_run_s": d.min_run_s,
        "min_pause_s": d.min_pause_s,
        "max_switches_per_hour": d.max_switches_per_hour,
        "hysteresis_w": d.hysteresis_w,
        "hysteresis_delay_s": d.hysteresis_delay_s,
        "settle_s": d.settle_s,
        "comfort": d.comfort.model_dump(mode="json") if d.comfort else None,
    }
    return DeviceConfig.model_validate(_merge(base, spec.control))


def build_device_configs(
    scenario: Scenario, catalog: TemplateCatalog, devices: Mapping[str, SimDevice]
) -> list[DeviceConfig]:
    """The controlled devices of ``scenario`` (those with a ``template``)."""
    configs: list[DeviceConfig] = []
    for spec in scenario.devices:
        if spec.template is None:
            continue
        template = catalog.get(spec.template)
        if not isinstance(template, DeviceTemplate):
            msg = f"scenario {scenario.name!r}: {spec.template!r} is not a device template"
            raise TypeError(msg)
        configs.append(build_device_config(template, spec, devices[spec.id]))
    return configs


# --- evidence ----------------------------------------------------------------------------


def turns_on(action: Action, device: DeviceConfig) -> bool | None:
    """Whether an accepted action switches the device on (``True``) or off (``False``)."""
    match action:
        case SwitchAction(on=on):
            return on
        case StepAction(step=step):
            return (step_power_w(device.control, step) or 0.0) > 0
        case PowerSetpointAction(power_w=power):
            return power > 0
    return None


@dataclass(slots=True)
class StepLog:
    """The house and device state of one scenario step."""

    t: datetime
    grid_w: float
    pv_w: float
    battery_w: float
    soc_pct: float
    devices_w: float


@dataclass(slots=True)
class DeviceLog:
    """One device over the run (sampled at every scenario step)."""

    power_w: list[float] = field(default_factory=list)
    on: list[bool] = field(default_factory=list)
    setpoint_c: list[float | None] = field(default_factory=list)
    temp_c: list[float | None] = field(default_factory=list)
    estimate_w: list[float | None] = field(default_factory=list)
    """The controller's power estimate (``None`` before its first tick / not controlled)."""
    estimate_uncertainty_w: list[float | None] = field(default_factory=list)
    held_until: list[datetime | None] = field(default_factory=list)
    """When the gate holds the device (a manual override is respected until), else ``None``."""


@dataclass(slots=True)
class RunResult:
    """What a run produced; the helpers answer the questions the scenario tests ask."""

    scenario: Scenario
    steps: list[StepLog]
    devices: dict[str, DeviceLog]
    configs: dict[str, DeviceConfig]
    decisions: list[Decision]
    skipped_ticks: int = 0

    @property
    def step_s(self) -> int:
        return self.scenario.step_s

    # --- energy ------------------------------------------------------------------------------

    def import_wh(self, start_s: float = 0.0, end_s: float | None = None) -> float:
        """Grid import in Wh between two offsets from the scenario start."""
        dt = self.step_s / 3600.0
        total = 0.0
        for i, log in enumerate(self.steps):
            t = i * self.step_s
            if t >= start_s and (end_s is None or t < end_s):
                total += max(0.0, log.grid_w) * dt
        return total

    def device_wh(self, device_id: str) -> float:
        return sum(self.devices[device_id].power_w) * self.step_s / 3600.0

    # --- switching ---------------------------------------------------------------------------

    def transitions(self, device_id: str) -> list[tuple[int, bool]]:
        """``(step index, on)`` for every physical on/off change after the first step."""
        series = self.devices[device_id].on
        return [(i, series[i]) for i in range(1, len(series)) if series[i] != series[i - 1]]

    def switches_in_window(self, device_id: str, window_s: float = 3600.0) -> int:
        """The largest number of physical switches in any sliding window."""
        times = [i * self.step_s for i, _ in self.transitions(device_id)]
        best = 0
        for index, start in enumerate(times):
            best = max(best, sum(1 for t in times[index:] if t < start + window_s))
        return best

    def release_times(self, device_id: str) -> list[datetime]:
        """When the gate released the device (emergency stop, watchdog, shutdown ...)."""
        return [
            d.ts for d in self.accepted(device_id) if isinstance(d.intent.action, ReleaseAction)
        ]

    def hold_violations(
        self,
        device_id: str,
        *,
        min_run_s: float,
        min_pause_s: float,
        tol_s: float,
        exempt: Sequence[datetime] = (),
    ) -> list[str]:
        """Physical on periods shorter than ``min_run_s`` / off periods shorter than
        ``min_pause_s`` (less ``tol_s``: the gate counts from the write, the device switches a
        few seconds later). A period that ends within ``tol_s`` of an ``exempt`` instant is
        not a violation: a *release* (emergency stop, watchdog trip, shutdown) is the safe
        direction and bypasses the protection times by design."""
        out: list[str] = []
        moves = self.transitions(device_id)
        for (i, on), (j, _) in itertools.pairwise(moves):
            held = (j - i) * self.step_s
            limit = min_run_s if on else min_pause_s
            ends = self.scenario.start + timedelta(seconds=j * self.step_s)
            if any(abs((ends - e).total_seconds()) <= tol_s for e in exempt):
                continue
            if held + tol_s < limit:
                out.append(f"{'on' if on else 'off'} for {held} s at step {i} (< {limit} s)")
        return out

    def accepted(self, device_id: str | None = None) -> list[Decision]:
        return [
            d
            for d in self.decisions
            if d.kind is DecisionKind.ACCEPTED
            and (device_id is None or d.intent.asset_id == device_id)
        ]

    def of_kind(self, kind: DecisionKind, device_id: str | None = None) -> list[Decision]:
        return [
            d
            for d in self.decisions
            if d.kind is kind and (device_id is None or d.intent.asset_id == device_id)
        ]

    def accepted_switches(self, device_id: str) -> int:
        """Accepted on/off transitions the gate recorded for ``device_id``."""
        config = self.configs[device_id]
        on = False
        count = 0
        for d in self.accepted(device_id):
            target = turns_on(d.intent.action, config)
            if target is not None and target != on:
                count += 1
                on = target
        return count


# --- the runner --------------------------------------------------------------------------


Action_ = Callable[["ScenarioRunner"], Awaitable[None]]


class ScenarioRunner:
    """Plays one scenario closed-loop; see the module docstring.

    Args:
        scenario: the scenario.
        catalog: the template catalogue (``load_templates(Path("templates"))``).
        global_mode: the global operating mode (devices carry their own).
        device_modes: per-device mode overrides (default: the scenario spec's ``mode``).
        settings: ``ControlSettings`` overrides on top of the scenario's.
        store: the runtime store (pass one to survive a simulated crash).
        configs: device configurations to control instead of the scenario's own ``devices``
            (the demo: ``suncore.sim.demo_config.demo_devices(catalog).devices`` on the default
            demo devices); their modes come from ``device_modes`` (default: shadow).
    """

    def __init__(
        self,
        scenario: Scenario,
        catalog: TemplateCatalog,
        *,
        global_mode: OperatingMode = OperatingMode.AUTOMATIC,
        device_modes: Mapping[str, OperatingMode] | None = None,
        settings: Mapping[str, float | int] | None = None,
        store: RuntimeStore | None = None,
        configs: Sequence[DeviceConfig] | None = None,
    ) -> None:
        self.scenario = scenario
        self.catalog = catalog
        self.vt = VirtualTime(scenario.start)
        self.limiter = RateLimiter(clock=self.vt.monotonic, sleep=self.vt.sleep)
        self.conn = FakeHAConnection(
            scenario,
            clock=self.vt.clock,
            sleep=self.vt.sleep,
            sim_clock=self.vt.clock,
            autorun=False,
            write_limiter=self.limiter,
        )
        self.settings = ControlSettings(
            tick_s=float(scenario.step_s), **{**scenario.control.settings, **dict(settings or {})}
        )
        self.store: RuntimeStore = store if store is not None else MemoryRuntimeStore()
        self._explicit_configs = list(configs) if configs is not None else None
        if self._explicit_configs is not None:
            device_mode_map = {
                c.id: (device_modes or {}).get(c.id, OperatingMode.SHADOW)
                for c in self._explicit_configs
            }
        else:
            device_mode_map = {
                spec.id: (device_modes or {}).get(spec.id, spec.mode)
                for spec in scenario.devices
                if spec.template is not None
            }
        self.modes = StaticModes(global_mode, device_mode_map)
        self.decisions: list[Decision] = []
        self.stack: ControlStack | None = None
        self.configs: dict[str, DeviceConfig] = {}
        self._actions: list[tuple[int, Action_]] = []
        self._action_tasks: list[asyncio.Task[None]] = []
        self._tick_task: asyncio.Task[object] | None = None
        self._skipped = 0
        self._steps: list[StepLog] = []
        self._device_logs: dict[str, DeviceLog] = {}
        self._connected = False

    # --- composition -------------------------------------------------------------------------

    @property
    def sim_devices(self) -> Mapping[str, SimDevice]:
        return self.conn.demo.devices.devices

    def new_stack(self) -> ControlStack:
        """A control stack on the running connection (also used for a simulated restart)."""
        sc = self.scenario
        cadences = self.conn.demo.devices.heartbeats()
        tz = sc.start.tzinfo
        assert tz is not None
        return ControlStack(
            self.conn,
            site=demo_site_config(),
            devices=list(self.configs.values()),
            clock=self.vt.clock,
            sleep=self.vt.sleep,
            settings=self.settings,
            modes=self.modes,
            store=self.store,
            tz=tz,
            goals=GoalsConfig(climate_before_export=sc.control.climate_before_export),
            import_limit_w=sc.control.import_limit_w,
            reserve_soc_pct=sc.control.reserve_soc_pct,
            cadences=cadences,
            decisions=self.decisions,
        )

    async def start(self) -> None:
        if self._connected:
            return
        await self.conn.connect()
        self._connected = True
        configs = (
            self._explicit_configs
            if self._explicit_configs is not None
            else build_device_configs(self.scenario, self.catalog, self.sim_devices)
        )
        self.configs = {c.id: c for c in configs}
        self._device_logs = {k: DeviceLog() for k in self.sim_devices}
        self.stack = self.new_stack()
        await self.stack.start()
        await self.vt.settle()

    # --- scheduling --------------------------------------------------------------------------

    def at(self, at_s: float, action: Action_) -> None:
        """Run ``await action(runner)`` at the first step at or after ``at_s`` (as a task:
        it may wait on simulated time)."""
        self._actions.append((math.ceil(at_s / self.scenario.step_s), action))
        self._actions.sort(key=lambda a: a[0])

    async def restart(self) -> ControlStack:
        """Replace the stack by a fresh one on the same store and connection (after a crash)."""
        stack = self.new_stack()
        await stack.start()  # recover() releases what the crash left under control
        self.stack = stack  # only now does the loop tick the new stack
        return stack

    # --- running -----------------------------------------------------------------------------

    def _record(self, state_time: datetime) -> None:
        house = self.conn._house_state
        assert house is not None
        self._steps.append(
            StepLog(
                state_time,
                house.grid_w,
                house.pv_w,
                house.battery_w,
                house.soc_pct,
                house.devices_w,
            )
        )
        for device_id, device in self.sim_devices.items():
            log = self._device_logs[device_id]
            log.power_w.append(device.power_w())
            log.on.append(device.is_on())
            setpoint = device.setpoint_c if isinstance(device, SimClimate) else None
            if isinstance(device, SimClimate):
                temp: float | None = device.temp_c
            elif isinstance(device, SimWaterHeater):
                temp = device.temp_c
            else:
                temp = None
            if isinstance(device, SimClimate) and not device.is_on():
                setpoint = None
            log.setpoint_c.append(setpoint)
            log.temp_c.append(temp)
            estimate = self.stack.estimates.get(device_id) if self.stack is not None else None
            runtime = self.stack.gate.runtime_of(device_id) if self.stack is not None else None
            held = runtime is not None and runtime.is_held(state_time)
            log.held_until.append(runtime.override_until if held and runtime else None)
            log.estimate_w.append(None if estimate is None else estimate.value_w)
            log.estimate_uncertainty_w.append(None if estimate is None else estimate.uncertainty_w)

    async def _advance_to(self, when: datetime) -> None:
        await self.vt.advance_to(
            when, events=self.conn.next_event_time, run_events=self.conn.run_events_until
        )

    async def step(self, index: int) -> None:
        """Run scenario step ``index`` (0 is the connect state)."""
        when = self.scenario.start + timedelta(seconds=index * self.scenario.step_s)
        await self._advance_to(when)
        if index > 0 and not self.conn.advance():
            msg = "the scenario ended"
            raise RuntimeError(msg)
        await self.vt.settle()
        self._record(when)
        while self._actions and self._actions[0][0] <= index:
            _, action = self._actions.pop(0)
            self._action_tasks.append(asyncio.ensure_future(action(self)))
        stack = self.stack
        if stack is not None and not stack.stopped:
            if self._tick_task is None or self._tick_task.done():
                self._tick_task = asyncio.ensure_future(stack.tick())
            else:
                self._skipped += 1
        await self.vt.settle()

    async def run(self, duration_s: float | None = None) -> RunResult:
        """Play the scenario (or its first ``duration_s``) and return the evidence."""
        await self.start()
        steps = self.scenario.steps
        if duration_s is not None:
            steps = min(steps, int(duration_s // self.scenario.step_s))
        for index in range(steps):
            await self.step(index)
        return await self.finish()

    async def finish(self) -> RunResult:
        """Let the work in flight complete (simulated time keeps moving), then collect."""
        end = self.vt.now
        for _ in range(60):
            pending = [t for t in (self._tick_task, *self._action_tasks) if t and not t.done()]
            if not pending and self.vt.next_wake() is None and self.conn.next_event_time() is None:
                break
            end += timedelta(seconds=1)
            await self._advance_to(end)
        for task in (self._tick_task, *self._action_tasks):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        for task in (self._tick_task, *self._action_tasks):
            if task is not None and task.done() and not task.cancelled() and task.exception():
                raise task.exception()  # type: ignore[misc]
        return self.result()

    def result(self) -> RunResult:
        return RunResult(
            scenario=self.scenario,
            steps=self._steps,
            devices=self._device_logs,
            configs=self.configs,
            decisions=self.decisions,
            skipped_ticks=self._skipped,
        )

    async def close(self) -> None:
        stack = self.stack
        if stack is not None and not stack.stopped:
            await stack.crash()
        await self.conn.close()


__all__ = [
    "DeviceLog",
    "RunResult",
    "ScenarioRunner",
    "StepLog",
    "VirtualTime",
    "build_device_config",
    "build_device_configs",
    "turns_on",
]
