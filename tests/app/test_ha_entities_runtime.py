# SPDX-License-Identifier: Apache-2.0
"""The HA entity surface inside the real runtime (fake HA, stub MQTT broker)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from suncore.api import create_app
from suncore.app.ha_entities import (
    SUPPORTED,
    RuntimeEntitySource,
    SqliteInstallStore,
    build_ha_entities,
)
from suncore.app.main import build_runtime
from suncore.app.runtime import Runtime
from suncore.app.settings import Settings
from suncore.core.config import MqttConfig
from suncore.core.devices import DeviceKind
from suncore.core.haentities import BrokerState
from suncore.ha.mqtt import InstallRecord
from suncore.storage.learned import LearnedParamStore
from tests.api.conftest import client_for
from tests.app.conftest import RecordingConnection
from tests.ha.mqtt.stub_broker import StubBroker


@pytest.fixture
async def broker() -> AsyncIterator[StubBroker]:
    async with StubBroker() as stub:
        yield stub


@pytest.fixture
async def live_runtime(tmp_path: Path, broker: StubBroker) -> AsyncIterator[Runtime]:
    """A non-demo runtime (so the entity surface runs) on the fake house."""
    settings = Settings(
        demo_mode=False, data_dir=tmp_path / "data", static_dir=tmp_path / "no-dist"
    )
    del broker  # only ordering: the broker outlives the runtime's goodbye messages
    runtime = await build_runtime(settings, env={}, connection=RecordingConnection(6000.0))
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_demo_runtime_has_no_entity_surface(demo_runtime: Runtime) -> None:
    assert build_ha_entities(demo_runtime) is None
    assert demo_runtime.ha_entities is None
    assert demo_runtime.ha_entities_status().state is BrokerState.DISABLED
    assert await demo_runtime.ha_entities_republish() is False


async def test_source_reports_devices_and_values(demo_runtime: Runtime) -> None:
    source = RuntimeEntitySource(demo_runtime)
    for _ in range(300):
        if demo_runtime.control_state().devices:
            break
        await asyncio.sleep(0.01)
    topology = source.topology()
    assert topology.supported == SUPPORTED
    assert topology.version
    assert {d.id for d in topology.devices} == {
        d.id
        for d in demo_runtime.devices.devices  # type: ignore[union-attr]
    }
    assert all(d.kind in DeviceKind for d in topology.devices)
    values = source.values()
    assert values["mode"] == "shadow"
    assert values["shadow_mode"] is True
    assert values["emergency_stop"] is False
    assert values["status"] in {"starting", "running", "ha_unavailable"}
    for device in topology.devices:
        assert values[f"{device.id}_mode"] in {"shadow", "assisted", "automatic"}
    assert source.language() in {"en", "de"}


async def test_every_supported_key_exists_in_the_catalogue() -> None:
    from suncore.ha.mqtt.catalogue import spec_by_key  # noqa: PLC0415

    assert all(spec_by_key(k) is not None for k in SUPPORTED)
    assert all(not spec_by_key(k).command for k in SUPPORTED)  # type: ignore[union-attr]


async def test_events_follow_new_decisions(demo_runtime: Runtime) -> None:
    source = RuntimeEntitySource(demo_runtime)
    for _ in range(300):
        if any(d.last_decision for d in demo_runtime.control_state().devices):
            break
        await asyncio.sleep(0.01)
    assert source.drain_events() == []  # the first call only sets the baseline
    assert source.drain_events() == []  # shadow decisions are not events


async def test_install_store_roundtrip(demo_runtime: Runtime) -> None:
    assert demo_runtime.learned is not None
    store = SqliteInstallStore(LearnedParamStore(demo_runtime.db))
    assert await store.load() is None
    await store.save(InstallRecord("0a1b2c3d", ("suncore_0a1b2c3d", "suncore_0a1b2c3d_x")))
    assert await store.load() == InstallRecord(
        "0a1b2c3d", ("suncore_0a1b2c3d", "suncore_0a1b2c3d_x")
    )


async def test_without_a_broker_the_runtime_reports_it_and_the_check_warns(
    live_runtime: Runtime,
) -> None:
    for _ in range(300):
        if live_runtime.ha_entities_status().state is not BrokerState.DISABLED:
            break
        await asyncio.sleep(0.01)
    status = live_runtime.ha_entities_status()
    # Not running as an app (no SUPERVISOR_TOKEN) and no manual broker: nothing to connect to.
    assert status.state is BrokerState.ERROR
    assert status.reason_key == "mqtt.reason.not_supervised"
    items = await live_runtime.system_check(None)
    assert any(i.reason.key == "wizard.check.mqtt_error" for i in items)
    async with client_for(create_app(live_runtime)) as client:
        body = (await client.get("/api/v1/ha-entities/status")).json()
    assert body["state"] == "error"
    assert body["entity_count"] == 0


async def test_manual_broker_publishes_entities_and_states(
    live_runtime: Runtime, broker: StubBroker
) -> None:
    await live_runtime.put_section(
        "mqtt", MqttConfig(broker="manual", host="127.0.0.1", port=broker.port)
    )
    for _ in range(500):
        status = live_runtime.ha_entities_status()
        if status.state is BrokerState.CONNECTED and status.entity_count:
            break
        await asyncio.sleep(0.01)
    status = live_runtime.ha_entities_status()
    assert status.state is BrokerState.CONNECTED
    assert status.source == "manual"
    assert status.entity_count > 0
    configs = [m for m in broker.published if "/device/" in m.topic]
    assert configs
    site = next(m for m in configs if m.topic.count("_") == 1)
    payload = json.loads(site.payload)
    assert payload["dev"]["name"] == "SunCore"
    assert "emergency_stop" in payload["cmps"]
    await broker.wait_for(lambda: any(m.topic.endswith("/mode/state") for m in broker.published))
    items = await live_runtime.system_check(None)
    assert any(i.reason.key == "wizard.check.mqtt_ok" for i in items)
    assert await live_runtime.ha_entities_republish() is True
    # Switching the feature off removes the entities again.
    await live_runtime.put_section(
        "mqtt", MqttConfig(enabled=False, broker="manual", host="127.0.0.1", port=broker.port)
    )
    await broker.wait_for(
        lambda: live_runtime.ha_entities_status().state is BrokerState.DISABLED, seconds=5
    )
    assert any(m.payload == b"" and "/device/" in m.topic for m in broker.published)
