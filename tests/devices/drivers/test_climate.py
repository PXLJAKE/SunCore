# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from suncore.core.control import DeviceState
from suncore.core.decisions import ModeAction, SetpointAction, SwitchAction
from suncore.core.signals import Quality
from suncore.devices.drivers import ActionNotSupportedError, ClimateDriver, WriteRefusedError
from suncore.devices.setpoint import snap_setpoint
from tests.devices.drivers.fakes import FakeHA, climate_device, intent

ENTITY = "climate.ac"
FEATURES = 1 + 8 + 32 + 128 + 256 + 512  # the live lg_thinq value 937


def make(
    *, allow_switch_on: bool = False, mode: str | None = "cool", step_c: float = 0.5
) -> tuple[ClimateDriver, FakeHA]:
    ha = FakeHA()
    device = climate_device(allow_switch_on=allow_switch_on, mode=mode, step_c=step_c)
    return ClimateDriver(device, write=ha.write, states=ha.lookup), ha


def on(ha: FakeHA, state: str = "cool", **attrs: object) -> None:
    base: dict[str, object] = {
        "temperature": 24,
        "min_temp": 18,
        "max_temp": 30,
        "hvac_modes": ["off", "cool", "heat", "auto"],
        "supported_features": FEATURES,
        "target_temp_step": 0.5,
    }
    ha.set(ENTITY, state, **{**base, **attrs})


# --- set-point ---------------------------------------------------------------------------


async def test_setpoint_payload_has_only_the_temperature() -> None:
    driver, ha = make()
    on(ha)
    context = await driver.apply(intent(SetpointAction(setpoint_c=22.5, base_c=24)))
    assert ha.sent == [("climate", "set_temperature", {"entity_id": ENTITY}, {"temperature": 22.5})]
    assert context == "ctx-1"


async def test_setpoint_is_never_written_while_the_unit_is_off() -> None:
    driver, ha = make(allow_switch_on=True)
    on(ha, "off", temperature=None)
    with pytest.raises(WriteRefusedError, match="off"):
        await driver.apply(intent(SetpointAction(setpoint_c=22, base_c=24)))
    assert ha.calls == []


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_setpoint_is_not_written_to_an_unavailable_unit(state: str) -> None:
    driver, ha = make()
    on(ha, state)
    with pytest.raises(WriteRefusedError, match="unavailable"):
        await driver.apply(intent(SetpointAction(setpoint_c=22, base_c=24)))
    assert ha.calls == []


async def test_setpoint_needs_the_target_temperature_feature() -> None:
    driver, ha = make()
    on(ha, supported_features=128 + 256)
    with pytest.raises(WriteRefusedError, match="target temperature"):
        await driver.apply(intent(SetpointAction(setpoint_c=22, base_c=24)))


async def test_lg_integer_step_rounds_toward_the_base_value() -> None:
    """lg_thinq truncates with int() for steps >= 1: 22.9 would become 22 silently."""
    driver, ha = make()
    on(ha, target_temp_step=1, temperature=24)
    await driver.apply(intent(SetpointAction(setpoint_c=22.4, base_c=24)))
    await driver.apply(intent(SetpointAction(setpoint_c=25.6, base_c=24)))
    values = [c.service_data["temperature"] for c in ha.calls if c.service_data]
    assert values == [23.0, 25.0]  # toward the base (24), never beyond what was asked


async def test_step_attribute_missing_uses_control_step() -> None:
    driver, ha = make(step_c=0.5)
    ha.set(ENTITY, "cool", temperature=24, hvac_modes=["off", "cool"])
    await driver.apply(intent(SetpointAction(setpoint_c=22.7, base_c=24)))
    assert ha.calls[0].service_data == {"temperature": 23.0}


async def test_setpoint_is_clamped_to_the_entity_range() -> None:
    driver, ha = make()
    on(ha, min_temp=22, max_temp=26, temperature=24)
    await driver.apply(intent(SetpointAction(setpoint_c=21, base_c=24)))
    assert ha.calls[0].service_data == {"temperature": 22.0}


async def test_setpoint_equal_to_the_cached_value_is_not_written() -> None:
    driver, ha = make()
    on(ha, temperature=23)
    assert await driver.apply(intent(SetpointAction(setpoint_c=23, base_c=24))) is None
    assert ha.calls == []


# --- switching ---------------------------------------------------------------------------


async def test_switch_on_uses_the_configured_hvac_mode() -> None:
    driver, ha = make(allow_switch_on=True, mode="heat")
    on(ha, "off", temperature=None)
    await driver.apply(intent(SwitchAction(on=True)))
    assert ha.sent == [("climate", "set_hvac_mode", {"entity_id": ENTITY}, {"hvac_mode": "heat"})]


async def test_switch_on_needs_the_opt_in() -> None:
    driver, ha = make(allow_switch_on=False)
    on(ha, "off", temperature=None)
    with pytest.raises(WriteRefusedError, match="not allowed"):
        await driver.apply(intent(SwitchAction(on=True)))
    assert ha.calls == []


async def test_switch_on_falls_back_to_turn_on_when_the_mode_is_not_offered() -> None:
    driver, ha = make(allow_switch_on=True, mode="heat_cool")
    on(ha, "off", temperature=None, hvac_modes=["off", "heat", "cool", "dry", "auto"])
    await driver.apply(intent(SwitchAction(on=True)))
    assert ha.sent == [("climate", "turn_on", {"entity_id": ENTITY}, None)]


async def test_switch_on_without_any_path_is_refused() -> None:
    driver, ha = make(allow_switch_on=True, mode="heat_cool")
    on(ha, "off", temperature=None, hvac_modes=["off", "heat"], supported_features=1)
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(SwitchAction(on=True)))


async def test_switch_on_when_already_on_writes_nothing() -> None:
    driver, ha = make(allow_switch_on=True)
    on(ha)
    assert await driver.apply(intent(SwitchAction(on=True))) is None
    assert ha.calls == []


async def test_switch_off_prefers_turn_off_when_advertised() -> None:
    driver, ha = make()
    on(ha)
    await driver.apply(intent(SwitchAction(on=False)))
    assert ha.sent == [("climate", "turn_off", {"entity_id": ENTITY}, None)]


async def test_switch_off_uses_hvac_mode_off_without_the_turn_off_bit() -> None:
    driver, ha = make()
    on(ha, supported_features=1 + 8)
    await driver.apply(intent(SwitchAction(on=False)))
    assert ha.sent == [("climate", "set_hvac_mode", {"entity_id": ENTITY}, {"hvac_mode": "off"})]


async def test_switch_off_when_already_off_writes_nothing() -> None:
    driver, ha = make()
    on(ha, "off", temperature=None)
    assert await driver.apply(intent(SwitchAction(on=False))) is None
    assert ha.calls == []


async def test_unsupported_actions_raise() -> None:
    driver, ha = make()
    on(ha)
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(ModeAction(mode="cool")))


# --- read-back ----------------------------------------------------------------------------


async def test_read_back_baseline_is_the_setpoint_when_on() -> None:
    driver, ha = make()
    on(ha, temperature=23.5)
    assert await driver.read_back() == SetpointAction(setpoint_c=23.5, base_c=23.5)


async def test_read_back_off_is_a_switch() -> None:
    driver, ha = make()
    on(ha, "off", temperature=None)
    assert await driver.read_back() == SwitchAction(on=False)
    assert driver.device_state() == DeviceState(on=False, mode="off", setpoint_c=None)


async def test_read_back_follows_the_kind_of_the_last_write() -> None:
    driver, ha = make(allow_switch_on=True)
    on(ha, "off", temperature=None)
    await driver.apply(intent(SwitchAction(on=True)))
    on(ha, "cool", temperature=24)
    assert await driver.read_back() == SwitchAction(on=True)  # the gate compares like with like
    await driver.apply(intent(SetpointAction(setpoint_c=23, base_c=24)))
    on(ha, "cool", temperature=23)
    assert await driver.read_back() == SetpointAction(setpoint_c=23, base_c=23)


async def test_read_back_without_temperature_reports_on() -> None:
    driver, ha = make()
    on(ha, temperature=None)
    assert await driver.read_back() == SwitchAction(on=True)


async def test_read_back_unknown_is_none() -> None:
    driver, ha = make()
    on(ha, "unavailable")
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE


# --- release ------------------------------------------------------------------------------


async def test_release_restores_the_users_setpoint() -> None:
    driver, ha = make()
    on(ha, temperature=22)
    await driver.release(SetpointAction(setpoint_c=24, base_c=24))
    assert ha.sent == [("climate", "set_temperature", {"entity_id": ENTITY}, {"temperature": 24.0})]
    on(ha, temperature=24)
    await driver.release(SetpointAction(setpoint_c=24, base_c=24))  # idempotent
    assert len(ha.calls) == 1


async def test_release_never_sends_a_setpoint_to_an_off_unit() -> None:
    driver, ha = make(allow_switch_on=False)
    on(ha, "off", temperature=None)
    await driver.release(SetpointAction(setpoint_c=24, base_c=24))
    assert ha.calls == []  # the user turned it off (we never did): leave it


async def test_release_switches_back_on_a_unit_suncore_switched_off() -> None:
    driver, ha = make()
    on(ha, "heat", temperature=21)
    await driver.read_back()  # baseline captured: mode heat
    await driver.apply(intent(SwitchAction(on=False)))
    on(ha, "off", temperature=None)
    await driver.release(SetpointAction(setpoint_c=21, base_c=21))
    assert ha.calls[-1].service == "set_hvac_mode"
    assert ha.calls[-1].service_data == {"hvac_mode": "heat"}


async def test_release_switches_on_with_the_opt_in_after_a_restart() -> None:
    driver, ha = make(allow_switch_on=True, mode="cool")
    on(ha, "off", temperature=None)
    await driver.release(SetpointAction(setpoint_c=24, base_c=24))
    assert ha.sent == [("climate", "set_hvac_mode", {"entity_id": ENTITY}, {"hvac_mode": "cool"})]


async def test_release_baseline_off_switches_off() -> None:
    driver, ha = make()
    on(ha)
    await driver.release(SwitchAction(on=False))
    assert ha.sent == [("climate", "turn_off", {"entity_id": ENTITY}, None)]
    await driver.release(None)
    await driver.release(ModeAction(mode="x"))
    assert len(ha.calls) == 1


async def test_release_baseline_on_with_unit_off_and_user_choice() -> None:
    driver, ha = make(allow_switch_on=False)
    on(ha, "off", temperature=None)
    await driver.release(SwitchAction(on=True))
    assert ha.calls == []


async def test_release_of_an_unavailable_unit_raises_so_the_gate_retries() -> None:
    driver, ha = make()
    on(ha, "unavailable")
    with pytest.raises(WriteRefusedError):
        await driver.release(SetpointAction(setpoint_c=24, base_c=24))
    with pytest.raises(WriteRefusedError):
        await driver.release(SwitchAction(on=True))


# --- pure helper ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "base", "step", "expected"),
    [
        (24.0, 24.0, 1.0, 24.0),
        (23.2, 24.0, 1.0, 24.0),  # no grid point between -> the base
        (22.9, 24.0, 1.0, 23.0),
        (25.4, 24.0, 0.5, 25.0),
        (22.74, 24.0, 0.5, 23.0),
        (24.4, 24.0, 0.5, 24.0),
    ],
)
def test_snap_setpoint(value: float, base: float, step: float, expected: float) -> None:
    assert snap_setpoint(value, base=base, step=step) == expected


def test_snap_setpoint_clamps_and_validates() -> None:
    assert snap_setpoint(35.0, base=24, step=1, maximum=30) == 30
    assert snap_setpoint(10.0, base=24, step=1, minimum=18) == 18
    with pytest.raises(ValueError, match="step"):
        snap_setpoint(1, base=1, step=0)
