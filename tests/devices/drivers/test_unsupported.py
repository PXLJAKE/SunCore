# SPDX-License-Identifier: Apache-2.0
"""PR 6-B: devices of the Phase 6 controls get a driver that never writes (second line of
defence behind the gate's ``*_not_supported`` rejection)."""

from __future__ import annotations

import pytest

from suncore.core.decisions import ChargeAction, ReleaseAction, StepAction, SwitchAction
from suncore.core.signals import Quality
from suncore.devices.drivers import create_driver
from suncore.devices.drivers.base import ActionNotSupportedError
from suncore.devices.drivers.unsupported import UnsupportedDriver
from suncore.safety.actuator import Actuator
from tests.devices.drivers.fakes import FakeHA, intent
from tests.safety.test_phase6_gate import PHASE6_DEVICES, program_device


@pytest.mark.parametrize(
    "device", [d for d in PHASE6_DEVICES if d.program is None], ids=lambda d: d.id
)
async def test_phase6_controls_get_a_driver_that_never_writes(device: object) -> None:
    ha = FakeHA()
    driver = create_driver(device, write=ha.write, states=ha.lookup)  # type: ignore[arg-type]
    assert isinstance(driver, UnsupportedDriver)
    assert isinstance(driver, Actuator)
    for action in (SwitchAction(on=True), StepAction(step=1), ChargeAction(current_a=10)):
        with pytest.raises(ActionNotSupportedError):
            await driver.apply(intent(action, driver.asset_id))
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(ReleaseAction(), driver.asset_id))
    assert await driver.read_back() is None
    assert driver.device_state().quality is Quality.UNAVAILABLE
    await driver.release(SwitchAction(on=True))
    assert ha.calls == []


def test_a_program_plug_keeps_its_on_off_driver() -> None:
    """The plug driver itself is unchanged; the gate rejects program writes until 6-L."""
    ha = FakeHA()
    driver = create_driver(program_device(), write=ha.write, states=ha.lookup)
    assert not isinstance(driver, UnsupportedDriver)
