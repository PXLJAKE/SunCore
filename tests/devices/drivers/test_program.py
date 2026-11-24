# SPDX-License-Identifier: Apache-2.0
"""PR 6-L: the program driver (plug, remote start, notify-only)."""

from __future__ import annotations

import pytest

from suncore.core.decisions import SetpointAction, SwitchAction
from suncore.core.devices import DeviceConfig
from suncore.core.signals import Quality
from suncore.devices.drivers import (
    ActionNotSupportedError,
    ProgramDriver,
    WriteRefusedError,
    create_driver,
)
from suncore.safety.actuator import Actuator
from tests.devices.drivers.fakes import FakeHA, intent
from tests.safety.helpers import NOTIFY, PLUG, REMOTE

PLUG_ENTITY = "switch.washer_switch"
REMOTE_ENTITY = "switch.washer_start"


def make(device: DeviceConfig = PLUG) -> tuple[ProgramDriver, FakeHA]:
    ha = FakeHA()
    return ProgramDriver(device, write=ha.write, states=ha.lookup), ha


def test_the_factory_builds_a_program_driver_for_every_start_method() -> None:
    ha = FakeHA()
    for device in (PLUG, REMOTE, NOTIFY):
        driver = create_driver(device, write=ha.write, states=ha.lookup)
        assert isinstance(driver, ProgramDriver)
        assert isinstance(driver, Actuator)


async def test_plug_switches_on_and_off_and_writes_only_on_change() -> None:
    driver, ha = make()
    ha.set(PLUG_ENTITY, "off")
    await driver.apply(intent(SwitchAction(on=True)))
    assert ha.sent == [("switch", "turn_on", {"entity_id": PLUG_ENTITY}, None)]
    ha.set(PLUG_ENTITY, "on")
    assert await driver.apply(intent(SwitchAction(on=True))) is None
    await driver.apply(intent(SwitchAction(on=False)))
    assert ha.sent[-1] == ("switch", "turn_off", {"entity_id": PLUG_ENTITY}, None)
    ha.set(PLUG_ENTITY, "off")
    assert await driver.read_back() == SwitchAction(on=False)


async def test_plug_release_restores_the_baseline_and_none_keeps_it() -> None:
    driver, ha = make()
    ha.set(PLUG_ENTITY, "off")
    await driver.release(None)
    assert ha.calls == []
    await driver.release(SwitchAction(on=True))
    assert ha.sent == [("switch", "turn_on", {"entity_id": PLUG_ENTITY}, None)]


async def test_remote_start_is_never_switched_off_and_release_writes_nothing() -> None:
    driver, ha = make(REMOTE)
    ha.set(REMOTE_ENTITY, "on")
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(SwitchAction(on=False)))
    await driver.release(SwitchAction(on=False))
    await driver.release(SwitchAction(on=True))
    assert ha.calls == []
    ha.set(REMOTE_ENTITY, "off")
    await driver.apply(intent(SwitchAction(on=True)))
    assert ha.sent == [("switch", "turn_on", {"entity_id": REMOTE_ENTITY}, None)]


async def test_notify_never_writes() -> None:
    driver, ha = make(NOTIFY)
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(SwitchAction(on=True)))
    await driver.release(SwitchAction(on=True))
    assert ha.calls == []
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.OK


async def test_only_switches_are_applied() -> None:
    driver, ha = make()
    ha.set(PLUG_ENTITY, "off")
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(SetpointAction(setpoint_c=20.0, base_c=20.0)))
    assert ha.calls == []


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_an_unavailable_plug_is_neither_written_nor_known(state: str) -> None:
    driver, ha = make()
    ha.set(PLUG_ENTITY, state)
    with pytest.raises(WriteRefusedError):
        await driver.apply(intent(SwitchAction(on=True)))
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE
