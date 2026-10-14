# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from suncore.core.decisions import ModeAction, PowerSetpointAction, StepAction
from suncore.core.signals import Quality
from suncore.devices.drivers import (
    ActionNotSupportedError,
    StepsDriver,
    WriteRefusedError,
    create_driver,
)
from tests.devices.drivers.fakes import (
    FakeHA,
    intent,
    number_steps_device,
    onoff_device,
    relays_device,
    select_steps_device,
)

SELECT = "select.boiler_level"
NUMBER = "number.boiler_level"


def make_select() -> tuple[StepsDriver, FakeHA]:
    ha = FakeHA()
    return StepsDriver(select_steps_device(), write=ha.write, states=ha.lookup), ha


def make_number() -> tuple[StepsDriver, FakeHA]:
    ha = FakeHA()
    return StepsDriver(number_steps_device(), write=ha.write, states=ha.lookup), ha


def make_relays() -> tuple[StepsDriver, FakeHA]:
    ha = FakeHA()
    driver = StepsDriver(relays_device(), write=ha.write, states=ha.lookup)
    for i in (1, 2, 3):
        ha.set(f"switch.heater_{i}", "off")
    return driver, ha


# --- select -------------------------------------------------------------------------------


async def test_select_option_payload() -> None:
    driver, ha = make_select()
    ha.set(SELECT, "off", options=["off", "low", "high"])
    context = await driver.apply(intent(StepAction(step=2)))
    assert ha.sent == [("select", "select_option", {"entity_id": SELECT}, {"option": "high"})]
    assert context == "ctx-1"


async def test_select_writes_only_on_change() -> None:
    driver, ha = make_select()
    ha.set(SELECT, "low")
    assert await driver.apply(intent(StepAction(step=1))) is None
    assert ha.calls == []


async def test_select_read_back_maps_to_the_step_or_a_foreign_option() -> None:
    driver, ha = make_select()
    ha.set(SELECT, "low")
    assert await driver.read_back() == StepAction(step=1)
    ha.set(SELECT, "auto")
    assert await driver.read_back() == ModeAction(mode="auto")  # the user's own option
    ha.set(SELECT, "unavailable")
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE


async def test_select_device_state() -> None:
    driver, ha = make_select()
    ha.set(SELECT, "high")
    state = driver.device_state()
    assert (state.step, state.on) == (2, True)
    ha.set(SELECT, "off")
    state = driver.device_state()
    assert (state.step, state.on) == (0, False)


async def test_select_release_restores_step_or_foreign_option() -> None:
    driver, ha = make_select()
    ha.set(SELECT, "high")
    await driver.release(StepAction(step=0))
    assert ha.sent[-1] == ("select", "select_option", {"entity_id": SELECT}, {"option": "off"})
    ha.set(SELECT, "off")
    await driver.release(ModeAction(mode="auto"))
    assert ha.sent[-1][3] == {"option": "auto"}
    ha.set(SELECT, "auto")
    n = len(ha.calls)
    await driver.release(ModeAction(mode="auto"))
    await driver.release(None)
    assert len(ha.calls) == n


async def test_unknown_step_and_unsupported_action() -> None:
    driver, ha = make_select()
    ha.set(SELECT, "off")
    with pytest.raises(WriteRefusedError, match="step 7"):
        await driver.apply(intent(StepAction(step=7)))
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(PowerSetpointAction(power_w=5)))
    assert ha.calls == []


async def test_unavailable_select_is_not_written() -> None:
    driver, ha = make_select()
    ha.set(SELECT, "unavailable")
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(StepAction(step=1)))
    assert ha.calls == []


# --- number -------------------------------------------------------------------------------


async def test_number_set_value_payload() -> None:
    driver, ha = make_number()
    ha.set(NUMBER, "0.0")
    await driver.apply(intent(StepAction(step=2)))
    assert ha.sent == [("number", "set_value", {"entity_id": NUMBER}, {"value": 2.5})]


async def test_number_read_back() -> None:
    driver, ha = make_number()
    ha.set(NUMBER, "1.0")
    assert await driver.read_back() == StepAction(step=1)
    ha.set(NUMBER, "2.5")
    assert await driver.read_back() == StepAction(step=2)
    ha.set(NUMBER, "1.7")
    assert await driver.read_back() == ModeAction(mode="1.7")
    ha.set(NUMBER, "unknown")
    assert await driver.read_back() is None
    ha.set(NUMBER, "garbage")
    assert await driver.read_back() is None


async def test_number_writes_only_on_change_and_restores_foreign_values() -> None:
    driver, ha = make_number()
    ha.set(NUMBER, "1.0")
    assert await driver.apply(intent(StepAction(step=1))) is None
    await driver.release(ModeAction(mode="1.7"))
    assert ha.sent == [("number", "set_value", {"entity_id": NUMBER}, {"value": 1.7})]
    ha.set(NUMBER, "1.7")
    await driver.release(ModeAction(mode="1.7"))
    assert len(ha.calls) == 1


async def test_number_step_value_that_is_not_numeric_is_refused() -> None:
    driver, ha = make_number()
    ha.set(NUMBER, "0")
    object.__setattr__(driver._control.steps[1], "value", "x")
    with pytest.raises(WriteRefusedError, match="not a number"):
        await driver.apply(intent(StepAction(step=1)))


# --- relays -------------------------------------------------------------------------------


async def test_relays_switch_exactly_the_wanted_set() -> None:
    driver, ha = make_relays()
    context = await driver.apply(intent(StepAction(step=2)))
    assert ha.sent == [
        ("switch", "turn_on", {"entity_id": "switch.heater_1"}, None),
        ("switch", "turn_on", {"entity_id": "switch.heater_2"}, None),
    ]
    assert context == "ctx-2"
    assert driver.context_ids == ("ctx-1", "ctx-2")


async def test_relays_turn_off_first_when_stepping_down() -> None:
    driver, ha = make_relays()
    for i in (1, 2, 3):
        ha.set(f"switch.heater_{i}", "on")
    await driver.apply(intent(StepAction(step=1)))
    assert ha.sent == [
        ("switch", "turn_off", {"entity_id": "switch.heater_2"}, None),
        ("switch", "turn_off", {"entity_id": "switch.heater_3"}, None),
    ]


async def test_relays_mixed_change_switches_off_before_on() -> None:
    driver, ha = make_relays()
    ha.set("switch.heater_3", "on")  # step with r3 only does not exist: go to step 1 (r1)
    await driver.apply(intent(StepAction(step=1)))
    assert [c.service for c in ha.calls] == ["turn_off", "turn_on"]
    assert ha.calls[0].target == {"entity_id": "switch.heater_3"}
    assert ha.calls[1].target == {"entity_id": "switch.heater_1"}


async def test_relays_write_only_on_change() -> None:
    driver, ha = make_relays()
    assert await driver.apply(intent(StepAction(step=0))) is None
    assert ha.calls == []


async def test_relays_refuse_while_a_relay_is_unavailable() -> None:
    driver, ha = make_relays()
    ha.set("switch.heater_2", "unavailable")
    with pytest.raises(WriteRefusedError, match="relay"):
        await driver.apply(intent(StepAction(step=1)))
    assert ha.calls == []
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE


async def test_relays_read_back_maps_the_relay_set() -> None:
    driver, ha = make_relays()
    assert await driver.read_back() == StepAction(step=0)
    ha.set("switch.heater_1", "on")
    assert await driver.read_back() == StepAction(step=1)
    ha.set("switch.heater_2", "on")
    assert await driver.read_back() == StepAction(step=2)
    ha.set("switch.heater_2", "off")
    ha.set("switch.heater_3", "on")  # a combination no step has
    assert await driver.read_back() == ModeAction(mode="relays:r1,r3")
    state = driver.device_state()
    assert state.on is None
    assert state.mode == "relays:r1,r3"


async def test_relays_release_restores_a_step_or_a_foreign_combination() -> None:
    driver, ha = make_relays()
    ha.set("switch.heater_1", "on")
    ha.set("switch.heater_2", "on")
    await driver.release(StepAction(step=0))
    assert [c.service for c in ha.calls] == ["turn_off", "turn_off"]
    ha.calls.clear()
    for i in (1, 2, 3):
        ha.set(f"switch.heater_{i}", "off")
    await driver.release(ModeAction(mode="relays:r1,r3"))
    assert {(c.service, c.target["entity_id"]) for c in ha.calls} == {
        ("turn_on", "switch.heater_1"),
        ("turn_on", "switch.heater_3"),
    }
    ha.calls.clear()
    await driver.release(ModeAction(mode="somewhere else"))
    await driver.release(None)
    assert ha.calls == []


# --- wiring -------------------------------------------------------------------------------


def test_wiring_errors() -> None:
    ha = FakeHA()
    with pytest.raises(TypeError, match="steps or relays"):
        StepsDriver(onoff_device(), write=ha.write, states=ha.lookup)
    bad = select_steps_device().model_copy(
        update={
            "entities": {
                "level": select_steps_device()
                .entities["level"]
                .model_copy(update={"entity_id": "sensor.x"})
            }
        }
    )
    with pytest.raises(ValueError, match="select or number"):
        StepsDriver(bad, write=ha.write, states=ha.lookup)
    assert isinstance(create_driver(relays_device(), write=ha.write, states=ha.lookup), StepsDriver)
