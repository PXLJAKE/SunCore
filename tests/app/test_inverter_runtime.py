# SPDX-License-Identifier: Apache-2.0
"""The inverter limit in the runtime (PR 6-G): wiring, control state, mode with identity, a new
adapter drops the mode and the verification."""

from __future__ import annotations

import pytest

from suncore.app.runtime import Runtime
from suncore.core.config import InverterConfig, OperationConfig
from suncore.core.controlstate import (
    DeviceIdentityChangedError,
    InverterNotControllableError,
    ModeRaiseRejectedError,
)
from suncore.core.decisions import OperatingMode
from suncore.core.site import EntityRef


def generic_limit(entity: str = "number.inverter_export_limit") -> InverterConfig:
    return InverterConfig(
        adapter="generic_number",
        capability="export_limit",
        limit_entity=EntityRef(entity_id=entity),
    )


async def test_without_an_adapter_the_inverter_mode_cannot_be_raised(
    demo_runtime: Runtime,
) -> None:
    assert demo_runtime.control_state().inverter is None
    with pytest.raises(InverterNotControllableError):
        await demo_runtime.set_inverter_mode(OperatingMode.ASSISTED, "anything")
    shadow = await demo_runtime.set_inverter_mode(OperatingMode.SHADOW, None)
    assert shadow.inverter_mode is OperatingMode.SHADOW


async def test_inverter_mode_needs_the_identity_and_a_new_adapter_drops_it(
    demo_runtime: Runtime,
) -> None:
    await demo_runtime.put_section("inverter", generic_limit())
    state = demo_runtime.control_state().inverter
    assert state is not None
    assert state.inverter_mode is OperatingMode.SHADOW  # the inverter starts in shadow
    assert state.verified is False
    with pytest.raises(DeviceIdentityChangedError):
        await demo_runtime.set_inverter_mode(OperatingMode.ASSISTED, "stale")
    stored = await demo_runtime.set_inverter_mode(OperatingMode.ASSISTED, state.identity)
    assert stored.inverter_mode is OperatingMode.ASSISTED
    # A full operation document may only keep or lower the inverter mode.
    with pytest.raises(ModeRaiseRejectedError):
        await demo_runtime.put_section(
            "operation", stored.model_copy(update={"inverter_mode": OperatingMode.AUTOMATIC})
        )
    # Another limit entity is another adapter: back to shadow, unverified.
    await demo_runtime.put_section("inverter", generic_limit("number.other_export_limit"))
    operation = await demo_runtime.get_section("operation")
    assert isinstance(operation, OperationConfig)
    assert operation.inverter_mode is OperatingMode.SHADOW
    after = demo_runtime.control_state().inverter
    assert after is not None
    assert after.identity != state.identity


async def test_an_unverified_adapter_is_capped_at_assisted(demo_runtime: Runtime) -> None:
    await demo_runtime.put_section("inverter", generic_limit())
    state = demo_runtime.control_state().inverter
    assert state is not None
    await demo_runtime.set_inverter_mode(OperatingMode.AUTOMATIC, state.identity)
    capped = demo_runtime.control_state().inverter
    assert capped is not None
    assert capped.mode_capped  # X6
    assert capped.effective_mode is not OperatingMode.AUTOMATIC
