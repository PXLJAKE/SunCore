# SPDX-License-Identifier: Apache-2.0
"""Commands from Home Assistant inside the real runtime: the services layer, nothing else."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from suncore.app.ha_entities import RuntimeCommandPort, RuntimeEntitySource
from suncore.app.runtime import Runtime
from suncore.core.config import MqttConfig
from suncore.core.controlstate import OverrideCommand
from suncore.core.decisions import OperatingMode
from suncore.core.haentities import BrokerState
from tests.app.test_ha_entities_runtime import live_runtime  # noqa: F401
from tests.ha.mqtt.stub_broker import StubBroker


@pytest.fixture
async def broker() -> AsyncIterator[StubBroker]:
    async with StubBroker() as stub:
        yield stub


async def _devices(runtime: Runtime) -> list[str]:
    for _ in range(300):
        if runtime.control_state().devices:
            break
        await asyncio.sleep(0.01)
    return [d.device_id for d in runtime.control_state().devices]


async def test_port_mode_commands_use_the_services_and_the_identity_rule(
    demo_runtime: Runtime,
) -> None:
    port = RuntimeCommandPort(demo_runtime)
    device = (await _devices(demo_runtime))[0]
    assert port.stored_global_mode() is OperatingMode.SHADOW
    assert port.stored_device_mode(device) is OperatingMode.SHADOW
    assert port.stored_device_mode("no-such-device") is None
    await port.command_device_mode(device, OperatingMode.AUTOMATIC)
    assert port.stored_device_mode(device) is OperatingMode.AUTOMATIC
    state = next(d for d in demo_runtime.control_state().devices if d.device_id == device)
    assert state.device_mode is OperatingMode.AUTOMATIC
    values = RuntimeEntitySource(demo_runtime).values()
    assert values[f"{device}_auto"] is True
    assert values["auto"] is False  # global is still shadow, so nothing is switched
    await port.command_device_mode(device, OperatingMode.SHADOW)
    assert port.stored_device_mode(device) is OperatingMode.SHADOW
    await port.command_global_mode(OperatingMode.ASSISTED)
    assert port.stored_global_mode() is OperatingMode.ASSISTED


async def test_port_stop_clear_override_and_replan(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = RuntimeCommandPort(demo_runtime)
    await _devices(demo_runtime)
    device = "pool_pump"
    await port.command_emergency_stop()
    assert demo_runtime.control_state().emergency_stop is True
    assert RuntimeEntitySource(demo_runtime).values()["emergency_stop"] is True
    await port.command_clear_stop()
    assert demo_runtime.control_state().emergency_stop is False
    results = []
    original = Runtime.override_device

    async def spy(self: Runtime, device_id: str, command: OverrideCommand):  # type: ignore[no-untyped-def]
        results.append(await original(self, device_id, command))
        return results[-1]

    monkeypatch.setattr(Runtime, "override_device", spy)
    await port.command_override(device, OverrideCommand(action="pause"))
    await port.command_override(device, OverrideCommand(action="resume"))
    assert len(results) == 2  # one service call per command
    assert results[0].held  # the pause held the device; the service decides what resume shows
    await port.command_replan()


async def _connected(runtime: Runtime, broker: StubBroker, **config: object) -> str:
    await runtime.put_section(
        "mqtt",
        MqttConfig(broker="manual", host="127.0.0.1", port=broker.port, **config),  # type: ignore[arg-type]
    )
    for _ in range(500):
        status = runtime.ha_entities_status()
        if status.state is BrokerState.CONNECTED and status.entity_count:
            break
        await asyncio.sleep(0.01)
    assert runtime.learned is not None
    row = await runtime.learned.get("ha_entities", "install")
    assert row is not None
    iid = str(row.payload["iid"])
    await broker.wait_for(lambda: f"suncore/{iid}/+/set" in broker.subscribed)
    return iid


async def test_ha_emergency_stop_works_and_clearing_is_refused_by_default(
    live_runtime: Runtime,  # noqa: F811
    broker: StubBroker,
) -> None:
    iid = await _connected(live_runtime, broker)
    configs = [m for m in broker.published if "/device/" in m.topic and m.payload]
    assert any(b'"emergency_stop_button"' in m.payload for m in configs)
    assert not any(b'"emergency_stop_clear"' in m.payload for m in configs)  # opt-in only
    broker.inject(f"suncore/{iid}/emergency_stop_button/set", "PRESS")
    await broker.wait_for(lambda: live_runtime.control_state().emergency_stop)
    # Clearing from HA and raising the global mode are refused with the opt-in off.
    broker.inject(f"suncore/{iid}/emergency_stop_clear/set", "PRESS")
    broker.inject(f"suncore/{iid}/auto/set", "ON")
    await asyncio.sleep(0.3)
    assert live_runtime.control_state().emergency_stop is True
    assert live_runtime.modes.global_mode() is OperatingMode.SHADOW


async def test_ha_may_raise_and_clear_with_the_opt_in(
    live_runtime: Runtime,  # noqa: F811
    broker: StubBroker,
) -> None:
    iid = await _connected(live_runtime, broker, allow_mode_raise=True)
    broker.inject(f"suncore/{iid}/emergency_stop_button/set", "PRESS")
    await broker.wait_for(lambda: live_runtime.control_state().emergency_stop)
    broker.inject(f"suncore/{iid}/emergency_stop_clear/set", "PRESS")
    await broker.wait_for(lambda: not live_runtime.control_state().emergency_stop)
    broker.inject(f"suncore/{iid}/auto/set", "ON")
    await broker.wait_for(lambda: live_runtime.modes.global_mode() is OperatingMode.AUTOMATIC)
    broker.inject(f"suncore/{iid}/auto/set", "OFF")
    await broker.wait_for(lambda: live_runtime.modes.global_mode() is OperatingMode.SHADOW)
    topic = f"suncore/{iid}/auto/state"
    await broker.wait_for(lambda: broker.retained[topic].payload == b"OFF")
