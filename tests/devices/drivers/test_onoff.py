# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from suncore.core.control import DeviceState
from suncore.core.decisions import (
    ModeAction,
    PowerSetpointAction,
    ReleaseAction,
    SwitchAction,
)
from suncore.core.signals import Quality
from suncore.devices.drivers import (
    ActionNotSupportedError,
    OnOffDriver,
    WriteRefusedError,
    create_driver,
)
from suncore.safety.actuator import Actuator
from tests.devices.drivers.fakes import FakeHA, intent, onoff_device


def make(entity: str = "switch.pump") -> tuple[OnOffDriver, FakeHA]:
    ha = FakeHA()
    return OnOffDriver(onoff_device(entity), write=ha.write, states=ha.lookup), ha


def test_driver_is_an_actuator() -> None:
    driver, _ = make()
    assert isinstance(driver, Actuator)
    assert driver.asset_id == "pump"


async def test_turn_on_payload_and_context_id() -> None:
    driver, ha = make()
    ha.set("switch.pump", "off")
    context = await driver.apply(intent(SwitchAction(on=True)))
    assert ha.sent == [("switch", "turn_on", {"entity_id": "switch.pump"}, None)]
    assert context == "ctx-1"
    assert driver.context_ids == ("ctx-1",)


async def test_turn_off_payload() -> None:
    driver, ha = make()
    ha.set("switch.pump", "on")
    await driver.apply(intent(SwitchAction(on=False)))
    assert ha.sent == [("switch", "turn_off", {"entity_id": "switch.pump"}, None)]


async def test_input_boolean_uses_its_own_domain() -> None:
    driver, ha = make("input_boolean.sim_pump")
    ha.set("input_boolean.sim_pump", "off")
    await driver.apply(intent(SwitchAction(on=True)))
    assert ha.sent == [("input_boolean", "turn_on", {"entity_id": "input_boolean.sim_pump"}, None)]


async def test_writes_only_on_change() -> None:
    driver, ha = make()
    ha.set("switch.pump", "on")
    assert await driver.apply(intent(SwitchAction(on=True))) is None
    assert ha.calls == []


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_unavailable_entity_is_not_written(state: str) -> None:
    driver, ha = make()
    ha.set("switch.pump", state)
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(SwitchAction(on=True)))
    assert ha.calls == []
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE


async def test_entity_missing_from_the_cache_is_unavailable() -> None:
    driver, ha = make()
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(SwitchAction(on=False)))
    assert ha.calls == []


@pytest.mark.parametrize(
    "action", [PowerSetpointAction(power_w=5), ModeAction(mode="x"), ReleaseAction()]
)
async def test_other_actions_are_unsupported(action) -> None:  # type: ignore[no-untyped-def]
    driver, ha = make()
    ha.set("switch.pump", "off")
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(action))
    assert ha.calls == []


async def test_failed_service_call_propagates() -> None:
    driver, ha = make()
    ha.set("switch.pump", "off")
    ha.fail = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        await driver.apply(intent(SwitchAction(on=True)))


async def test_read_back_maps_the_cached_state() -> None:
    driver, ha = make()
    ha.set("switch.pump", "on")
    assert await driver.read_back() == SwitchAction(on=True)
    assert driver.device_state() == DeviceState(on=True)
    ha.set("switch.pump", "off")
    assert await driver.read_back() == SwitchAction(on=False)
    ha.set("switch.pump", "weird")
    assert await driver.read_back() is None


async def test_release_restores_the_baseline_and_is_idempotent() -> None:
    driver, ha = make()
    ha.set("switch.pump", "on")
    await driver.release(SwitchAction(on=False))
    assert ha.sent == [("switch", "turn_off", {"entity_id": "switch.pump"}, None)]
    ha.set("switch.pump", "off")
    await driver.release(SwitchAction(on=False))
    await driver.release(None)
    await driver.release(ReleaseAction())
    assert len(ha.calls) == 1


def test_wrong_wiring_is_rejected() -> None:
    ha = FakeHA()
    with pytest.raises(ValueError, match="supports"):
        OnOffDriver(onoff_device("light.lamp"), write=ha.write, states=ha.lookup)
    from tests.devices.drivers.fakes import climate_device  # noqa: PLC0415

    with pytest.raises(TypeError, match="on_off"):
        OnOffDriver(climate_device(), write=ha.write, states=ha.lookup)
    assert isinstance(create_driver(onoff_device(), write=ha.write, states=ha.lookup), OnOffDriver)
