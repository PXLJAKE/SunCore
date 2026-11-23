# SPDX-License-Identifier: Apache-2.0
"""Battery actions reach no device driver (Phase 4 WP2, control-safety review of 4-A).

The gate rejects every :class:`BatteryAction` before a driver is involved; this is the
second line of defence: each of the five Phase 3 drivers refuses such an intent and
treats a battery action as release baseline as "nothing to restore" - no service call.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from suncore.core.decisions import BatteryAction
from suncore.core.devices import DeviceConfig
from suncore.devices.drivers import ActionNotSupportedError, create_driver
from suncore.devices.drivers.climate import ClimateDriver
from suncore.devices.drivers.mode import ModeDriver
from suncore.devices.drivers.number import NumberDriver
from suncore.devices.drivers.onoff import OnOffDriver
from suncore.devices.drivers.steps import StepsDriver
from tests.devices.drivers.fakes import (
    FakeHA,
    climate_device,
    intent,
    mode_device,
    number_device,
    onoff_device,
    select_steps_device,
)

ACTION = BatteryAction(max_charge_w=3000, grid_charge_w=2000, min_soc_pct=20)

DRIVERS: list[tuple[type[object], Callable[[], DeviceConfig], dict[str, str]]] = [
    (OnOffDriver, onoff_device, {"switch.pump": "on"}),
    (ClimateDriver, climate_device, {"climate.ac": "cool"}),
    (StepsDriver, select_steps_device, {"select.boiler_level": "low"}),
    (NumberDriver, number_device, {"number.rod": "1000"}),
    (ModeDriver, mode_device, {"select.mode": "eco"}),
]


@pytest.mark.parametrize(("kind", "make_device", "states"), DRIVERS)
async def test_driver_refuses_battery_action_and_release_writes_nothing(
    kind: type[object], make_device: Callable[[], DeviceConfig], states: dict[str, str]
) -> None:
    ha = FakeHA()
    for entity_id, state in states.items():
        ha.set(entity_id, state, temperature=24, min=0, max=3000, options=["eco", "boost"])
    device = make_device()
    driver = create_driver(device, write=ha.write, states=ha.lookup)
    assert isinstance(driver, kind)
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(ACTION, device.id))
    await driver.release(ACTION)
    assert ha.calls == []
