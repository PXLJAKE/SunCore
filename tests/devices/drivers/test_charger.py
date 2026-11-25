# SPDX-License-Identifier: Apache-2.0
"""PR 6-I: the charger driver (current number, pause switch, phase select, vehicle sensors).

Exact service calls against the fake HA, write-on-change, read-back as a full ``ChargeAction``
and the release that restores the user's current, phases and switch (E9).
"""

from __future__ import annotations

import pytest

from suncore.core.decisions import ChargeAction, ReleaseAction
from suncore.core.devices import ChargerControl, DeviceConfig, DeviceKind, EvSpec
from suncore.core.signals import Quality
from suncore.devices.drivers import WriteRefusedError, create_driver
from suncore.devices.drivers.charger import ChargerDriver
from tests.devices.drivers.fakes import FakeHA, _device, intent

CUR = "number.wb_current"
EN = "switch.wb_charging"
PH = "select.wb_phases"
SOC = "sensor.car_soc"
PLUG = "binary_sensor.car_plugged"


def wallbox(*, enable: bool = True, phases: bool = True) -> DeviceConfig:
    entities = {"current": CUR, "soc": SOC, "plug": PLUG}
    if enable:
        entities["enable"] = EN
    if phases:
        entities["phases"] = PH
    return _device(
        "dev",
        DeviceKind.WALLBOX,
        entities,
        ChargerControl(
            kind="charger",
            role="current",
            enable_role="enable" if enable else None,
            phases_role="phases" if phases else None,
            phase_option_1="1p" if phases else None,
            phase_option_3="3p" if phases else None,
            phase_switching=phases,
            max_current_a=16,
        ),
        nominal_w=11_040,
        ev=EvSpec(capacity_wh=60_000, vehicle_soc_role="soc", connected_role="plug"),
    )


def make(**kw: bool) -> tuple[ChargerDriver, FakeHA]:
    ha = FakeHA()
    ha.set(CUR, "16.0", min=0, max=32, step=1)
    ha.set(EN, "off")
    ha.set(PH, "3p", options=["1p", "3p"])
    ha.set(SOC, "42.5")
    ha.set(PLUG, "on")
    driver = create_driver(wallbox(**kw), write=ha.write, states=ha.lookup)
    assert isinstance(driver, ChargerDriver)
    return driver, ha


async def test_charge_sets_the_snapped_current_then_enables() -> None:
    driver, ha = make()
    await driver.apply(intent(ChargeAction(current_a=10.7)))
    assert ha.sent == [
        ("number", "set_value", {"entity_id": CUR}, {"value": 10.0}),
        ("switch", "turn_on", {"entity_id": EN}, None),
    ]


async def test_current_is_clamped_to_the_control_range() -> None:
    driver, ha = make()
    ha.set(CUR, "10.0", min=0, max=32, step=1)
    await driver.apply(intent(ChargeAction(current_a=40)))
    assert ha.calls[0].service_data == {"value": 16.0}
    assert driver.snap_current_a(3) == 6.0  # the control's minimum
    assert driver.snap_current_a(0) == 0.0


async def test_write_only_on_change() -> None:
    driver, ha = make()
    ha.set(CUR, "10.0", min=0, max=32, step=1)
    ha.set(EN, "on")
    await driver.apply(intent(ChargeAction(current_a=10)))
    assert ha.calls == []


async def test_pause_turns_the_switch_off_and_keeps_the_current() -> None:
    driver, ha = make()
    ha.set(EN, "on")
    await driver.apply(intent(ChargeAction(current_a=0)))
    assert ha.sent == [("switch", "turn_off", {"entity_id": EN}, None)]


async def test_pause_without_a_switch_writes_zero_only_where_the_entity_allows_it() -> None:
    driver, ha = make(enable=False)
    await driver.apply(intent(ChargeAction(current_a=0)))
    assert ha.sent == [("number", "set_value", {"entity_id": CUR}, {"value": 0.0})]
    ha.calls.clear()
    ha.set(CUR, "16.0", min=6, max=32, step=1)
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(ChargeAction(current_a=0)))
    assert ha.calls == []


async def test_phases_change_first_then_the_rest() -> None:
    driver, ha = make()
    await driver.apply(intent(ChargeAction(current_a=0, phases=1)))
    assert ha.sent == [("select", "select_option", {"entity_id": PH}, {"option": "1p"})]


async def test_read_back_and_device_state() -> None:
    driver, ha = make()
    assert await driver.read_back() == ChargeAction(current_a=16, phases=3, enabled=False)
    state = driver.device_state()
    assert state.on is False  # 16 A set, but paused
    assert state.phases == 3
    assert state.connected is True
    assert state.soc_pct == 42.5
    ha.set(PH, "weird")
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE


async def test_release_restores_current_phases_and_switch() -> None:
    """E9: the takeover baseline is the full state; the release writes all of it back."""
    driver, ha = make()
    baseline = await driver.read_back()  # 16 A, 3 phases, paused by the user
    assert isinstance(baseline, ChargeAction)
    ha.set(CUR, "6.0", min=0, max=32, step=1)
    ha.set(EN, "on")
    ha.set(PH, "1p", options=["1p", "3p"])
    await driver.release(baseline)
    assert ha.sent == [
        ("select", "select_option", {"entity_id": PH}, {"option": "3p"}),
        ("number", "set_value", {"entity_id": CUR}, {"value": 16.0}),
        ("switch", "turn_off", {"entity_id": EN}, None),
    ]


async def test_release_of_a_charging_baseline_resumes_it() -> None:
    driver, ha = make()
    ha.set(EN, "on")
    baseline = await driver.read_back()
    ha.set(EN, "off")
    ha.set(CUR, "8.0", min=0, max=32, step=1)
    await driver.release(baseline)
    assert ha.sent == [
        ("number", "set_value", {"entity_id": CUR}, {"value": 16.0}),
        ("switch", "turn_on", {"entity_id": EN}, None),
    ]


async def test_other_actions_are_not_supported() -> None:
    driver, ha = make()
    with pytest.raises(Exception, match=r".*"):
        await driver.apply(intent(ReleaseAction()))
    assert ha.calls == []


@pytest.mark.parametrize("unit", ["mA", "kW", "%"])
async def test_a_current_entity_in_another_unit_is_refused(unit: str) -> None:
    """Review 6-I H2: amperes are never written into an entity in another unit."""
    driver, ha = make()
    ha.set(CUR, "16.0", min=0, max=32, step=1, unit_of_measurement=unit)
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(ChargeAction(current_a=10)))
    with pytest.raises(WriteRefusedError):
        await driver.release(ChargeAction(current_a=16, phases=3, enabled=False))
    assert ha.calls == []


async def test_a_current_entity_in_amperes_is_written() -> None:
    driver, ha = make()
    ha.set(CUR, "16.0", min=0, max=32, step=1, unit_of_measurement="A")
    await driver.apply(intent(ChargeAction(current_a=10)))
    assert ha.sent[0] == ("number", "set_value", {"entity_id": CUR}, {"value": 10.0})
