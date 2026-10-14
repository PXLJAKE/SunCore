# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from suncore.core.control import DeviceState
from suncore.core.decisions import ModeAction, PowerSetpointAction, StepAction
from suncore.core.signals import Quality
from suncore.devices.drivers import (
    ActionNotSupportedError,
    ModeDriver,
    NumberDriver,
    WriteRefusedError,
    create_driver,
)
from tests.devices.drivers.fakes import FakeHA, intent, mode_device, number_device, onoff_device

ROD = "number.rod"


def make(unit: str = "W", **kw: float) -> tuple[NumberDriver, FakeHA]:
    ha = FakeHA()
    return NumberDriver(number_device(unit, **kw), write=ha.write, states=ha.lookup), ha


# --- number ---------------------------------------------------------------------------------


async def test_watts_payload_and_snap_down_to_the_step() -> None:
    driver, ha = make()
    ha.set(ROD, "0.0", min=0, max=3000, step=100)
    context = await driver.apply(intent(PowerSetpointAction(power_w=1750)))
    assert ha.sent == [("number", "set_value", {"entity_id": ROD}, {"value": 1700.0})]
    assert context == "ctx-1"


async def test_clamped_to_control_and_entity_limits() -> None:
    driver, ha = make()
    ha.set(ROD, "0", min=0, max=2000, step=100)
    await driver.apply(intent(PowerSetpointAction(power_w=9999)))
    assert ha.calls[-1].service_data == {"value": 2000.0}
    ha.set(ROD, "0", min=500, max=3000, step=100)
    await driver.apply(intent(PowerSetpointAction(power_w=100)))
    assert ha.calls[-1].service_data == {"value": 500.0}  # the entity cannot go lower
    await driver.apply(intent(PowerSetpointAction(power_w=-5)))
    assert ha.calls[-1].service_data == {"value": 500.0}


async def test_step_defaults_to_the_control_step() -> None:
    driver, ha = make()
    ha.set(ROD, "0")
    await driver.apply(intent(PowerSetpointAction(power_w=1234)))
    assert ha.calls[0].service_data == {"value": 1200.0}


async def test_amperes_three_phase() -> None:
    driver, ha = make("A")
    ha.set(ROD, "6", min=6, max=16, step=1)
    await driver.apply(intent(PowerSetpointAction(power_w=5000)))  # 5000 / 690 = 7.25 A
    assert ha.calls[0].service_data == {"value": 7.0}
    assert await driver.read_back() == PowerSetpointAction(power_w=6 * 230 * 3)


async def test_percent_of_nominal() -> None:
    driver, ha = make("%", nominal_w=2000)
    ha.set(ROD, "0", min=0, max=100, step=5)
    await driver.apply(intent(PowerSetpointAction(power_w=1000)))
    assert ha.calls[0].service_data == {"value": 50.0}
    ha.set(ROD, "50")
    assert await driver.read_back() == PowerSetpointAction(power_w=1000)


async def test_kilowatt_entity_is_converted() -> None:
    driver, ha = make()
    ha.set(ROD, "0", min=0, max=3, step=0.1, unit_of_measurement="kW")
    await driver.apply(intent(PowerSetpointAction(power_w=1500)))
    assert ha.calls[0].service_data == {"value": 1.5}
    ha.set(ROD, "1.5", unit_of_measurement="kW")
    assert await driver.read_back() == PowerSetpointAction(power_w=1500)
    assert driver.device_state() == DeviceState(value=1500.0, on=True)


async def test_same_value_is_not_written_and_unavailable_is_refused() -> None:
    driver, ha = make()
    ha.set(ROD, "1000", min=0, max=3000, step=100)
    assert await driver.apply(intent(PowerSetpointAction(power_w=1040))) is None
    assert ha.calls == []
    ha.set(ROD, "unavailable")
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(PowerSetpointAction(power_w=1000)))


async def test_read_back_text_that_is_not_a_number() -> None:
    driver, ha = make()
    ha.set(ROD, "unavailable")
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE
    ha.set(ROD, "abc")
    assert await driver.read_back() is None


async def test_release_restores_the_value_without_the_control_limits() -> None:
    driver, ha = make()
    ha.set(ROD, "2000", min=0, max=3000, step=100)
    await driver.release(PowerSetpointAction(power_w=250))  # not on the 100 W grid: verbatim
    assert ha.sent == [("number", "set_value", {"entity_id": ROD}, {"value": 250.0})]
    ha.set(ROD, "250")
    await driver.release(PowerSetpointAction(power_w=250))
    await driver.release(None)
    await driver.release(StepAction(step=0))
    assert len(ha.calls) == 1


async def test_number_unsupported_action_and_wiring() -> None:
    driver, ha = make()
    ha.set(ROD, "0")
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(StepAction(step=1)))
    with pytest.raises(TypeError, match="continuous"):
        NumberDriver(onoff_device(), write=ha.write, states=ha.lookup)
    wrong = number_device().model_copy(
        update={
            "entities": {
                "power": number_device()
                .entities["power"]
                .model_copy(update={"entity_id": "sensor.rod"})
            }
        }
    )
    with pytest.raises(ValueError, match="number entity"):
        NumberDriver(wrong, write=ha.write, states=ha.lookup)
    assert isinstance(
        create_driver(number_device(), write=ha.write, states=ha.lookup), NumberDriver
    )


# --- mode -----------------------------------------------------------------------------------


def make_mode(entity: str = "select.mode") -> tuple[ModeDriver, FakeHA]:
    ha = FakeHA()
    return ModeDriver(mode_device(entity), write=ha.write, states=ha.lookup), ha


async def test_select_mode_payload() -> None:
    driver, ha = make_mode()
    ha.set("select.mode", "eco")
    context = await driver.apply(intent(ModeAction(mode="boost")))
    assert ha.sent == [
        ("select", "select_option", {"entity_id": "select.mode"}, {"option": "boost"})
    ]
    assert context == "ctx-1"


async def test_climate_preset_payload() -> None:
    driver, ha = make_mode("climate.hp")
    ha.set("climate.hp", "heat", preset_mode="eco")
    await driver.apply(intent(ModeAction(mode="boost")))
    assert ha.sent == [
        ("climate", "set_preset_mode", {"entity_id": "climate.hp"}, {"preset_mode": "boost"})
    ]
    assert await driver.read_back() == ModeAction(mode="eco")
    assert driver.device_state() == DeviceState(mode="eco")


async def test_mode_only_on_change_and_only_configured_modes() -> None:
    driver, ha = make_mode()
    ha.set("select.mode", "eco")
    assert await driver.apply(intent(ModeAction(mode="eco"))) is None
    with pytest.raises(WriteRefusedError, match="not configured"):
        await driver.apply(intent(ModeAction(mode="turbo")))
    assert ha.calls == []


async def test_mode_read_back_and_release() -> None:
    driver, ha = make_mode()
    ha.set("select.mode", "eco")
    assert await driver.read_back() == ModeAction(mode="eco")
    ha.set("select.mode", "boost")
    await driver.release(ModeAction(mode="manual"))  # the user's own, unconfigured, mode
    assert ha.sent == [
        ("select", "select_option", {"entity_id": "select.mode"}, {"option": "manual"})
    ]
    ha.set("select.mode", "manual")
    await driver.release(ModeAction(mode="manual"))
    await driver.release(None)
    assert len(ha.calls) == 1
    ha.set("select.mode", "unavailable")
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(ModeAction(mode="eco")))


async def test_preset_without_attribute_is_unknown() -> None:
    driver, ha = make_mode("climate.hp")
    ha.set("climate.hp", "heat", preset_mode=None)
    assert await driver.read_back() is None


def test_mode_wiring() -> None:
    ha = FakeHA()
    with pytest.raises(TypeError, match="mode control"):
        ModeDriver(onoff_device(), write=ha.write, states=ha.lookup)
    with pytest.raises(ValueError, match="select or climate"):
        ModeDriver(mode_device("sensor.x"), write=ha.write, states=ha.lookup)
    assert isinstance(create_driver(mode_device(), write=ha.write, states=ha.lookup), ModeDriver)


async def test_mode_driver_rejects_other_actions() -> None:
    driver, ha = make_mode()
    ha.set("select.mode", "eco")
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(StepAction(step=1)))
    assert ha.calls == []


# --- snap_power_w (review 3-B: the controller asks for what will be written) ---------------


async def test_snap_power_w_equals_the_written_value() -> None:
    driver, ha = make()
    ha.set(ROD, "0", min=0, max=2000, step=100)
    assert driver.snap_power_w(1750) == 1700.0
    assert driver.snap_power_w(9999) == 2000.0
    await driver.apply(intent(PowerSetpointAction(power_w=driver.snap_power_w(1750))))
    assert ha.calls[-1].service_data == {"value": 1700.0}
    ha.set(ROD, "1700", min=0, max=2000, step=100)  # the cache follows the write
    assert await driver.read_back() == PowerSetpointAction(power_w=1700.0)


async def test_snap_power_w_in_amperes_and_percent_round_trips_through_read_back() -> None:
    driver, ha = make("A")
    ha.set(ROD, "6", min=6, max=16, step=1)
    power = driver.snap_power_w(7000)  # 7000 W / 690 W per A = 10.14 A -> 10 A
    assert power == 10 * 230 * 3
    ha.set(ROD, "10", min=6, max=16, step=1)
    assert await driver.read_back() == PowerSetpointAction(power_w=power)
    pct, ha2 = make("%")
    ha2.set(ROD, "0", min=0, max=100, step=5)
    assert pct.snap_power_w(1000) == 30 * 3000 / 100  # 33.3 % -> 30 %


def test_snap_power_w_without_a_cached_state_uses_the_control_limits() -> None:
    driver, _ = make()
    assert driver.snap_power_w(1750) == 1700.0  # control step 100, max 3000
    assert driver.snap_power_w(float("nan")) == 0.0
