# SPDX-License-Identifier: Apache-2.0
"""The real drivers behind the real safety gate: kinds, baselines and release line up."""

from __future__ import annotations

import dataclasses

from suncore.core.control import ControlSettings
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    OperatingMode,
    Reason,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig
from suncore.devices.drivers import create_driver
from suncore.safety.gate import GateInputs, SafetyGate
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import MemoryRuntimeStore
from tests.devices.drivers.fakes import (
    Call,
    FakeHA,
    climate_device,
    intent,
    onoff_device,
    relays_device,
)
from tests.safety.helpers import FakeClock, flows, surplus


def build(device: DeviceConfig, ha: FakeHA) -> tuple[SafetyGate, list[Decision], FakeClock]:
    clock = FakeClock()
    emitted: list[Decision] = []

    async def sink(decision: Decision) -> None:
        emitted.append(decision)

    driver = create_driver(device, write=ha.write, states=ha.lookup)
    inputs = GateInputs(flows=flows(), surplus=surplus(5000.0, 100.0))
    gate = SafetyGate(
        clock=clock,
        sink=sink,
        actuators={device.id: driver},
        modes=StaticModes(OperatingMode.AUTOMATIC, {device.id: OperatingMode.AUTOMATIC}),
        devices={device.id: device}.get,
        states=lambda _id: driver.device_state(),
        inputs=lambda: dataclasses.replace(inputs, ts=clock.now),
        store=MemoryRuntimeStore(),
        settings=ControlSettings(min_write_interval_s=5.0, default_settle_s=2.0),
        sleep=clock.sleep,
    )
    return gate, emitted, clock


def climate_follows(ha: FakeHA) -> None:
    def apply(call: Call) -> None:
        state = ha.states["climate.ac"]
        attrs = dict(state.attributes)
        if call.service == "set_temperature" and call.service_data:
            attrs["temperature"] = call.service_data["temperature"]
        ha.set("climate.ac", state.state, **attrs)

    ha.on_call = apply


async def test_setpoint_shift_is_accepted_and_released_to_the_users_value() -> None:
    ha = FakeHA()
    ha.set(
        "climate.ac",
        "cool",
        temperature=24,
        min_temp=18,
        max_temp=30,
        target_temp_step=0.5,
        hvac_modes=["off", "cool"],
        supported_features=937,
    )
    climate_follows(ha)
    gate, emitted, clock = build(climate_device(), ha)
    decision = await gate.submit(intent(SetpointAction(setpoint_c=22.5, base_c=24), "ac"))
    assert decision.kind is DecisionKind.ACCEPTED, decision
    assert [c.service for c in ha.calls] == ["set_temperature"]
    clock.advance(30)
    released = await gate.release_device("ac", _reason())
    assert released.kind is DecisionKind.ACCEPTED
    assert ha.calls[-1].service_data == {"temperature": 24.0}  # the takeover baseline
    assert emitted[-1].kind is DecisionKind.ACCEPTED


async def test_setpoint_for_an_off_unit_is_rejected_by_the_gate_or_fails_in_the_driver() -> None:
    ha = FakeHA()
    ha.set(
        "climate.ac", "off", temperature=None, hvac_modes=["off", "cool"], supported_features=937
    )
    gate, _, _ = build(climate_device(), ha)
    decision = await gate.submit(intent(SetpointAction(setpoint_c=22.5, base_c=24), "ac"))
    assert decision.kind in (DecisionKind.REJECTED, DecisionKind.FAILED)
    assert ha.calls == []  # nothing reached Home Assistant


async def test_switch_off_then_release_switches_back_on() -> None:
    ha = FakeHA()
    ha.set("climate.ac", "cool", temperature=24, hvac_modes=["off", "cool"], supported_features=937)

    def follow(call: Call) -> None:
        if call.service == "turn_off":
            ha.set(
                "climate.ac",
                "off",
                temperature=None,
                hvac_modes=["off", "cool"],
                supported_features=937,
            )
        elif call.service == "set_hvac_mode" and call.service_data:
            ha.set(
                "climate.ac",
                call.service_data["hvac_mode"],
                temperature=24,
                hvac_modes=["off", "cool"],
                supported_features=937,
            )

    ha.on_call = follow
    gate, _, clock = build(climate_device(), ha)
    decision = await gate.submit(intent(SwitchAction(on=False), "ac"))
    assert decision.kind is DecisionKind.ACCEPTED, decision
    clock.advance(600)
    released = await gate.release_device("ac", _reason())
    assert released.kind is DecisionKind.ACCEPTED
    assert [c.service for c in ha.calls] == ["turn_off", "set_hvac_mode"]
    assert ha.calls[-1].service_data == {"hvac_mode": "cool"}


async def test_on_off_driver_through_the_gate() -> None:
    ha = FakeHA()
    ha.set("switch.pump", "off")
    ha.on_call = lambda call: ha.set("switch.pump", "on" if call.service == "turn_on" else "off")
    gate, _, clock = build(onoff_device(), ha)
    decision = await gate.submit(intent(SwitchAction(on=True), "pump"))
    assert decision.kind is DecisionKind.ACCEPTED, decision
    clock.advance(600)
    await gate.release_device("pump", _reason())
    assert [c.service for c in ha.calls] == ["turn_on", "turn_off"]


async def test_relays_through_the_gate() -> None:
    ha = FakeHA()
    for i in (1, 2, 3):
        ha.set(f"switch.heater_{i}", "off")

    def follow(call: Call) -> None:
        ha.set(call.target["entity_id"], "on" if call.service == "turn_on" else "off")

    ha.on_call = follow
    gate, _, _ = build(relays_device(), ha)
    decision = await gate.submit(intent(StepAction(step=2), "boiler"))
    assert decision.kind is DecisionKind.ACCEPTED, decision
    assert decision.observed == StepAction(step=2)


def _reason() -> Reason:
    return Reason(key="safety.mode_shadow")
