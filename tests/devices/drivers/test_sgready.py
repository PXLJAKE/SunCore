# SPDX-License-Identifier: Apache-2.0
"""SG-Ready and water-heater lift drivers (PR 6-K): exact service calls, contact order, release."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.core.decisions import (
    ModeAction,
    ReleaseAction,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import SgContacts
from suncore.core.signals import Quality
from suncore.devices.drivers import (
    ActionNotSupportedError,
    SgReadyDriver,
    WaterHeaterDriver,
    WriteRefusedError,
    create_driver,
)
from tests.devices.drivers.fakes import Call, FakeHA, intent
from tests.safety.test_heatpump import BLOCK, FORCED, INTENSIFIED, NORMAL, lift_device, sg_device

A, B = "switch.hp_a", "switch.hp_b"


def make(*, forced: bool = True, contacts: SgContacts = NORMAL) -> tuple[SgReadyDriver, FakeHA]:
    ha = FakeHA()
    ha.set(A, "on" if contacts.a else "off")
    ha.set(B, "on" if contacts.b else "off")

    def follow(call: Call) -> None:  # the switch follows the call at once
        entity = call.target["entity_id"]
        ha.set(entity, "on" if call.service == "turn_on" else "off")

    ha.on_call = follow
    device = sg_device(allow_forced=forced)
    driver = create_driver(device, write=ha.write, states=ha.lookup)
    assert isinstance(driver, SgReadyDriver)
    return driver, ha


def pair(ha: FakeHA) -> SgContacts:
    return SgContacts(a=ha.states[A].state == "on", b=ha.states[B].state == "on")


async def test_state_3_and_back() -> None:
    driver, ha = make()
    assert await driver.read_back() == StepAction(step=0)
    await driver.apply(intent(StepAction(step=1), "hp"))
    assert ha.sent == [("switch", "turn_on", {"entity_id": B}, None)]
    assert await driver.read_back() == StepAction(step=1)
    assert driver.device_state().step == 1
    await driver.apply(intent(StepAction(step=1), "hp"))  # write only on change
    assert len(ha.calls) == 1


async def test_normal_to_forced_passes_state_3_not_the_block() -> None:
    driver, ha = make()
    seen: list[SgContacts] = []
    inner = ha.on_call
    assert inner is not None

    def record(call: Call) -> None:
        inner(call)
        seen.append(pair(ha))

    ha.on_call = record
    await driver.apply(intent(StepAction(step=2), "hp"))
    assert seen == [INTENSIFIED, FORCED]
    await driver.apply(intent(StepAction(step=0), "hp"))
    assert seen[2:] == [INTENSIFIED, NORMAL]
    assert BLOCK not in seen


async def test_state_4_is_not_writable_without_the_opt_in() -> None:
    driver, ha = make(forced=False)
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(StepAction(step=2), "hp"))
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(SwitchAction(on=True), "hp"))
    assert ha.calls == []


async def test_unknown_patterns_are_reported_as_a_mode() -> None:
    driver, _ = make(contacts=BLOCK)
    assert await driver.read_back() == ModeAction(mode="sg:10")
    state = driver.device_state()
    assert state.step is None
    assert state.quality is Quality.OK
    driver, ha = make()
    ha.set(B, "unavailable")
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(StepAction(step=1), "hp"))


async def test_release_restores_the_baseline_but_never_state_1() -> None:
    driver, ha = make(contacts=FORCED)
    await driver.release(StepAction(step=1))
    assert pair(ha) == INTENSIFIED
    await driver.release(ModeAction(mode="sg:10"))  # a block baseline is never written back
    assert pair(ha) == NORMAL
    calls = len(ha.calls)
    await driver.release(None)
    await driver.release(ReleaseAction())
    assert len(ha.calls) == calls


@given(
    start=st.sampled_from([NORMAL, INTENSIFIED, FORCED, BLOCK]),
    steps=st.lists(st.integers(min_value=0, max_value=2), min_size=1, max_size=6),
)
async def test_no_write_sequence_ever_shows_the_block(start: SgContacts, steps: list[int]) -> None:
    driver, ha = make(contacts=start)
    inner = ha.on_call
    assert inner is not None
    seen: list[SgContacts] = []

    def record(call: Call) -> None:
        inner(call)
        seen.append(pair(ha))

    ha.on_call = record
    for step in steps:
        try:
            await driver.apply(intent(StepAction(step=step), "hp"))
        except WriteRefusedError:
            continue
        assert pair(ha) == driver._control.steps[step]
    assert BLOCK not in seen


# --- water heater lift ---------------------------------------------------------------------------


def make_lift(state: str = "heat_pump", **attrs: object) -> tuple[WaterHeaterDriver, FakeHA]:
    ha = FakeHA()
    base = {"temperature": 50.0, "current_temperature": 47.0, "min_temp": 40.0, "max_temp": 60.0}
    ha.set("water_heater.dhw", state, **{**base, **attrs})
    driver = create_driver(lift_device(), write=ha.write, states=ha.lookup)
    assert isinstance(driver, WaterHeaterDriver)
    return driver, ha


async def test_lift_writes_water_heater_set_temperature() -> None:
    driver, ha = make_lift()
    assert await driver.read_back() == SetpointAction(setpoint_c=50.0, base_c=50.0)
    state = driver.device_state()
    assert state.on
    assert state.mode == "heat"
    assert state.setpoint_c == 50.0
    await driver.apply(intent(SetpointAction(setpoint_c=55.0, base_c=50.0), "dhw"))
    assert ha.sent == [
        (
            "water_heater",
            "set_temperature",
            {"entity_id": "water_heater.dhw"},
            {"temperature": 55.0},
        )
    ]


async def test_lift_clamps_to_the_entity_range_and_never_writes_while_off() -> None:
    driver, ha = make_lift(max_temp=53.0)
    await driver.apply(intent(SetpointAction(setpoint_c=58.0, base_c=50.0), "dhw"))
    assert ha.sent[-1][3] == {"temperature": 53.0}
    driver, ha = make_lift("off")
    assert await driver.read_back() == SwitchAction(on=False)
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(SetpointAction(setpoint_c=55.0, base_c=50.0), "dhw"))
    await driver.release(SetpointAction(setpoint_c=50.0, base_c=50.0))
    assert ha.calls == []


async def test_lift_without_operation_modes_and_release() -> None:
    driver, ha = make_lift("unknown", temperature=55.0)
    assert await driver.read_back() == SetpointAction(setpoint_c=55.0, base_c=55.0)
    await driver.release(SetpointAction(setpoint_c=50.0, base_c=50.0))
    assert ha.sent[-1][3] == {"temperature": 50.0}
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(StepAction(step=1), "dhw"))
