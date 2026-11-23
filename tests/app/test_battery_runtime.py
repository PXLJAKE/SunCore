# SPDX-License-Identifier: Apache-2.0
"""The battery in the runtime (WP10): wiring, control state, mode with identity, re-mapping."""

from __future__ import annotations

import pytest

from suncore.app.runtime import Runtime
from suncore.core.battery import BATTERY_ASSET_ID, BatteryModel, BatterySettings
from suncore.core.config import OperationConfig
from suncore.core.controlstate import (
    BatteryNotControllableError,
    DeviceIdentityChangedError,
    ModeRaiseRejectedError,
)
from suncore.core.decisions import OperatingMode
from suncore.core.site import EntityRef


def generic_battery(entity: str = "number.battery_max_discharge") -> BatterySettings:
    return BatterySettings(
        adapter_template_id="generic.battery",
        entities={"max_discharge_number": EntityRef(entity_id=entity)},
        model=BatteryModel(capacity_wh=10_000, max_charge_w=5000, max_discharge_w=5000),
    )


async def test_an_observe_only_battery_has_no_mode_to_raise(demo_runtime: Runtime) -> None:
    assert demo_runtime.control_state().battery is None
    with pytest.raises(BatteryNotControllableError):
        await demo_runtime.set_battery_mode(OperatingMode.ASSISTED, "anything")
    shadow = await demo_runtime.set_battery_mode(OperatingMode.SHADOW, None)
    assert shadow.battery_mode is OperatingMode.SHADOW


async def test_battery_mode_needs_the_identity_and_a_remap_drops_it(
    demo_runtime: Runtime,
) -> None:
    await demo_runtime.put_section("battery", generic_battery())
    stack = demo_runtime.stack
    assert stack is not None
    assert stack.battery_driver is not None
    assert BATTERY_ASSET_ID in stack.gate.asset_ids
    state = demo_runtime.control_state().battery
    assert state is not None
    assert state.battery_mode is OperatingMode.SHADOW  # a battery starts in shadow
    assert state.capabilities == ["discharge_limit"]
    with pytest.raises(DeviceIdentityChangedError):
        await demo_runtime.set_battery_mode(OperatingMode.ASSISTED, "stale")
    stored = await demo_runtime.set_battery_mode(OperatingMode.ASSISTED, state.identity)
    assert stored.battery_mode is OperatingMode.ASSISTED
    # A full operation document may only keep or lower the battery mode.
    with pytest.raises(ModeRaiseRejectedError):
        await demo_runtime.put_section(
            "operation", stored.model_copy(update={"battery_mode": OperatingMode.AUTOMATIC})
        )
    await demo_runtime.put_section("battery", generic_battery("number.other_discharge"))
    operation = await demo_runtime.get_section("operation")
    assert isinstance(operation, OperationConfig)
    assert operation.battery_mode is OperatingMode.SHADOW
    after = demo_runtime.control_state().battery
    assert after is not None
    assert after.identity != state.identity


async def test_the_devices_reserve_guard_uses_the_effective_reserve(demo_runtime: Runtime) -> None:
    await demo_runtime.put_section("battery", generic_battery())
    battery = demo_runtime.battery_config
    assert battery is not None
    assert battery.model is not None
    expected = battery.model.reserve_pct + battery.model.bms_drift_margin_pct
    assert demo_runtime._reserve_soc_pct() is not None
    assert demo_runtime._reserve_soc_pct() >= expected  # type: ignore[operator]
