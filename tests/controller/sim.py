# SPDX-License-Identifier: Apache-2.0
"""A small closed-loop house for the controller tests: controller -> real gate -> devices.

Deterministic: the clock is a plain ``datetime`` advanced by the test, the gate's settle
wait is a no-op, the devices follow every write at once. The house draws
``base_load + sum(device power) - pv``; an optional battery absorbs surplus and covers
deficits up to its power limit (above / below the given SoC it stops).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from suncore.controller import Controller, ControllerInputs, ControllerLoop
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings, DeviceState, GateInputs, PowerEstimate
from suncore.core.conversion import power_to_value, value_to_power_w
from suncore.core.decisions import (
    Action,
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    PowerSetpointAction,
    Reason,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import (
    ContinuousControl,
    DeviceConfig,
    OnOffControl,
    RelaysControl,
    SetpointControl,
    StepsControl,
    step_power_w,
)
from suncore.core.signals import Quality
from suncore.safety.gate import SafetyGate
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import MemoryRuntimeStore
from tests.safety.helpers import T0

EstimateFn = Callable[[str, datetime, float, DeviceState], PowerEstimate | None]
"""``(device_id, now, true power, state) -> estimate``; the default is a 2 % sensor."""


def measured(_: str, now: datetime, power_w: float, state: DeviceState) -> PowerEstimate:
    del now, state
    return PowerEstimate(value_w=power_w, uncertainty_w=10.0 + 0.02 * power_w)


def initial_state(device: DeviceConfig) -> DeviceState:
    control = device.control
    if isinstance(control, SetpointControl):
        return DeviceState(on=True, mode="heat", setpoint_c=21.0)
    if isinstance(control, StepsControl | RelaysControl):
        return DeviceState(on=False, step=0)
    if isinstance(control, ContinuousControl):
        return DeviceState(on=False, value=control.min)
    return DeviceState(on=False)


@dataclass
class SimDevice:
    """One device's physics: power from the state, writes change the state."""

    config: DeviceConfig
    state: DeviceState
    base_setpoint_c: float | None = None
    """Climate: the user's own set-point (draw at base = ``base_share`` x nominal)."""
    base_share: float = 0.4
    last: Action | None = None
    writes: list[Action] = field(default_factory=list)

    def power_w(self) -> float:
        control = self.config.control
        nominal = self.config.power_model.nominal_w or 0.0
        s = self.state
        if isinstance(control, OnOffControl):
            return nominal if s.on else 0.0
        if isinstance(control, StepsControl | RelaysControl):
            return step_power_w(control, s.step or 0) or 0.0
        if isinstance(control, ContinuousControl):
            return value_to_power_w(s.value or 0.0, control, nominal)
        if isinstance(control, SetpointControl):
            if not s.on:
                return 0.0
            base = self.base_setpoint_c if self.base_setpoint_c is not None else s.setpoint_c
            shifted = (
                s.setpoint_c is not None and base is not None and abs(s.setpoint_c - base) > 0.01
            )
            return nominal if shifted else self.base_share * nominal
        return 0.0

    def write(self, action: Action) -> None:
        self.writes.append(action)
        self.last = action
        s = self.state
        match action:
            case SwitchAction(on=on):
                mode = (self.config.switch_on_hvac_mode or "heat") if on else "off"
                is_climate = isinstance(self.config.control, SetpointControl)
                self.state = s.model_copy(update={"on": on, "mode": mode if is_climate else None})
            case StepAction(step=step):
                self.state = s.model_copy(update={"step": step, "on": step > 0})
            case PowerSetpointAction(power_w=power):
                control = self.config.control
                assert isinstance(control, ContinuousControl)
                value = power_to_value(power, control, self.config.power_model.nominal_w)
                self.state = s.model_copy(update={"value": value, "on": power > 0})
            case SetpointAction(setpoint_c=sp):
                self.state = s.model_copy(update={"setpoint_c": sp})
            case _:
                pass

    def read_back(self) -> Action | None:
        if self.last is not None and not isinstance(self.last, SetpointAction):
            return self.last
        s = self.state
        control = self.config.control
        if isinstance(control, SetpointControl):
            if not s.on or s.setpoint_c is None:
                return SwitchAction(on=bool(s.on))
            base = self.base_setpoint_c if self.base_setpoint_c is not None else s.setpoint_c
            return SetpointAction(setpoint_c=s.setpoint_c, base_c=base)
        if isinstance(control, StepsControl | RelaysControl):
            return StepAction(step=s.step or 0)
        if isinstance(control, ContinuousControl):
            return PowerSetpointAction(power_w=self.power_w())
        return SwitchAction(on=bool(s.on))


@dataclass
class SimActuator:
    device: SimDevice

    @property
    def asset_id(self) -> str:
        return self.device.config.id

    async def apply(self, intent: Intent) -> str | None:
        self.device.write(intent.action)
        return None

    async def read_back(self) -> Action | None:
        return self.device.read_back()

    async def release(self, baseline: Action | None) -> None:
        if baseline is not None:
            self.device.write(baseline)
            self.device.last = None


@dataclass
class Battery:
    soc_pct: float = 50.0
    max_charge_w: float = 3000.0
    max_discharge_w: float = 3000.0
    capacity_wh: float = 10_000.0
    full_pct: float = 100.0
    empty_pct: float = 5.0

    def flow(self, net_w: float) -> float:
        """Battery power (+ charging) for a house net export ``net_w`` (+ = surplus)."""
        if net_w > 0:
            return min(net_w, self.max_charge_w) if self.soc_pct < self.full_pct else 0.0
        return max(net_w, -self.max_discharge_w) if self.soc_pct > self.empty_pct else 0.0

    def integrate(self, battery_w: float, dt_s: float) -> None:
        self.soc_pct = min(
            100.0, max(0.0, self.soc_pct + battery_w * dt_s / 3600.0 / self.capacity_wh * 100)
        )


@dataclass
class Sim:
    devices: Mapping[str, SimDevice]
    controller: Controller
    loop: ControllerLoop
    gate: SafetyGate
    settings: ControlSettings
    pv: Callable[[datetime], float]
    base_load_w: float = 300.0
    battery: Battery | None = None
    goals: GoalsConfig = field(default_factory=GoalsConfig)
    estimate: EstimateFn = measured
    reserve_soc_pct: float | None = None
    now: datetime = T0
    decisions: list[Decision] = field(default_factory=list)
    grid_log: list[tuple[datetime, float]] = field(default_factory=list)
    battery_log: list[tuple[datetime, float]] = field(default_factory=list)
    blocked: dict[str, Reason] = field(default_factory=dict)
    flows_override: FlowSnapshot | None = None

    # --- physics -------------------------------------------------------------------

    def device_power_w(self) -> float:
        return sum(d.power_w() for d in self.devices.values())

    def site(self) -> tuple[float, float]:
        """(grid_w, battery_w) now."""
        net = self.pv(self.now) - self.base_load_w - self.device_power_w()
        battery_w = self.battery.flow(net) if self.battery is not None else 0.0
        return battery_w - net, battery_w

    def flows(self) -> FlowSnapshot:
        if self.flows_override is not None:
            return self.flows_override
        grid, battery = self.site()
        soc = self.battery.soc_pct if self.battery is not None else None
        return FlowSnapshot(
            grid_w=Flow(grid, Quality.OK),
            pv_w=Flow(self.pv(self.now), Quality.OK),
            battery_w=Flow(battery, Quality.OK),
            house_w=Flow(None, Quality.UNAVAILABLE),
            battery_soc_pct=Flow(soc, Quality.OK if soc is not None else Quality.UNAVAILABLE),
            ts=self.now,
        )

    def inputs(self) -> ControllerInputs:
        estimates: dict[str, PowerEstimate] = {}
        for device_id, d in self.devices.items():
            est = self.estimate(device_id, self.now, d.power_w(), d.state)
            if est is not None:
                estimates[device_id] = est
        return ControllerInputs(
            flows=self.flows(),
            states={k: d.state for k, d in self.devices.items()},
            estimates=estimates,
            blocked=dict(self.blocked),
            reserve_soc_pct=self.reserve_soc_pct,
            goals=self.goals,
        )

    # --- running -------------------------------------------------------------------

    async def tick(self) -> None:
        await self.loop.tick(self.now, self.inputs())
        await self.loop.drain()
        grid, battery = self.site()
        self.grid_log.append((self.now, grid))
        self.battery_log.append((self.now, battery))

    async def run(self, seconds: float) -> None:
        dt = self.settings.tick_s
        steps = round(seconds / dt)
        for _ in range(steps):
            await self.tick()
            if self.battery is not None:
                _, battery = self.site()
                self.battery.integrate(battery, dt)
            self.now += timedelta(seconds=dt)

    def run_sync(self, seconds: float) -> None:
        asyncio.run(self.run(seconds))

    # --- evidence ------------------------------------------------------------------

    def accepted(self, device_id: str | None = None) -> list[Decision]:
        return [
            d
            for d in self.decisions
            if d.kind is DecisionKind.ACCEPTED
            and (device_id is None or d.intent.asset_id == device_id)
        ]

    def rejected(self) -> list[Decision]:
        return [d for d in self.decisions if d.kind is DecisionKind.REJECTED]

    def switch_times(self, device_id: str) -> list[tuple[datetime, bool]]:
        """(ts, on) of every accepted on/off transition of ``device_id``."""
        device = self.devices[device_id].config
        out: list[tuple[datetime, bool]] = []
        on = False
        for d in self.accepted(device_id):
            target = _turns_on(d.intent.action, device)
            if target is not None and target != on:
                out.append((d.ts, target))
                on = target
        return out


def _turns_on(action: Action, device: DeviceConfig) -> bool | None:
    match action:
        case SwitchAction(on=on):
            return on
        case StepAction(step=step):
            return (step_power_w(device.control, step) or 0.0) > 0
        case PowerSetpointAction(power_w=power):
            return power > 0
    return None


def build_sim(
    devices: list[DeviceConfig],
    *,
    pv: Callable[[datetime], float] | float = 3000.0,
    base_load_w: float = 300.0,
    settings: ControlSettings | None = None,
    mode: OperatingMode = OperatingMode.AUTOMATIC,
    battery: Battery | None = None,
    goals: GoalsConfig | None = None,
    estimate: EstimateFn = measured,
    states: Mapping[str, DeviceState] | None = None,
    reserve_soc_pct: float | None = None,
) -> Sim:
    settings = settings or ControlSettings()
    sim_devices = {
        d.id: SimDevice(d, (states or {}).get(d.id) or initial_state(d)) for d in devices
    }
    for d in sim_devices.values():
        if isinstance(d.config.control, SetpointControl):
            d.base_setpoint_c = d.state.setpoint_c
    controller = Controller(devices, settings=settings)
    holder: dict[str, ControllerLoop] = {}

    def gate_inputs() -> GateInputs | None:
        return holder["loop"].gate_inputs

    decisions: list[Decision] = []

    async def sink(decision: Decision) -> None:
        decisions.append(decision)

    async def no_sleep(_: float) -> None:
        return None

    sim_ref: dict[str, Sim] = {}
    gate = SafetyGate(
        clock=lambda: sim_ref["sim"].now,
        sink=sink,
        actuators={k: SimActuator(d) for k, d in sim_devices.items()},
        modes=StaticModes(mode, dict.fromkeys(sim_devices, mode)),
        devices={d.id: d for d in devices}.get,
        states=lambda device_id: sim_devices[device_id].state,
        inputs=gate_inputs,
        store=MemoryRuntimeStore(),
        settings=settings,
        sleep=no_sleep,
    )
    loop = ControllerLoop(controller, submit=gate.submit, poll=gate.poll)
    holder["loop"] = loop
    pv_fn = pv if callable(pv) else (lambda _now, _w=float(pv): _w)
    sim = Sim(
        devices=sim_devices,
        controller=controller,
        loop=loop,
        gate=gate,
        settings=settings,
        pv=pv_fn,
        base_load_w=base_load_w,
        battery=battery,
        goals=goals or GoalsConfig(climate_before_export=True),
        estimate=estimate,
        reserve_soc_pct=reserve_soc_pct,
        decisions=decisions,
    )
    sim_ref["sim"] = sim
    return sim


def replace_settings(settings: ControlSettings, **update: object) -> ControlSettings:
    return settings.model_copy(update=update)


__all__ = ["Battery", "Sim", "SimDevice", "build_sim", "measured"]
