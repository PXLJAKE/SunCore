# SPDX-License-Identifier: Apache-2.0
"""SUNCORE_DEMO_MQTT=1 (CI only, WP13): the entity surface in demo mode for the loaded smoke."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from suncore.app.ha_entities import build_ha_entities
from suncore.app.main import build_runtime
from suncore.app.settings import Settings, load_settings
from suncore.core.config import MqttConfig
from suncore.core.haentities import BrokerState
from tests.app.conftest import RecordingConnection, demo_settings
from tests.ha.mqtt.stub_broker import StubBroker


@pytest.fixture
async def broker() -> AsyncIterator[StubBroker]:
    async with StubBroker() as stub:
        yield stub


def test_the_switch_is_off_by_default_and_read_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUNCORE_OPTIONS_PATH", str(tmp_path / "none.json"))
    monkeypatch.delenv("SUNCORE_DEMO_MQTT", raising=False)
    assert load_settings().demo_mqtt is False
    assert Settings().demo_mqtt is False
    monkeypatch.setenv("SUNCORE_DEMO_MQTT", "1")
    assert load_settings().demo_mqtt is True


async def test_the_demo_has_no_entity_service_without_the_switch(tmp_path: Path) -> None:
    runtime = await build_runtime(
        demo_settings(tmp_path), env={}, connection=RecordingConnection(6000.0)
    )
    await runtime.start()
    try:
        assert build_ha_entities(runtime) is None
        assert runtime.ha_entities is None
    finally:
        await runtime.stop()


async def test_the_switch_builds_the_service_and_publishes_to_a_manual_broker(
    tmp_path: Path, broker: StubBroker
) -> None:
    settings = demo_settings(tmp_path, demo_mqtt=True)
    runtime = await build_runtime(settings, env={}, connection=RecordingConnection(6000.0))
    await runtime.start()
    try:
        assert runtime.demo is True
        assert runtime.ha_entities is not None  # built although this is the demo
        await runtime.put_section(
            "mqtt", MqttConfig(broker="manual", host="127.0.0.1", port=broker.port)
        )
        for _ in range(500):
            status = runtime.ha_entities_status()
            if status.state is BrokerState.CONNECTED and status.entity_count:
                break
            await asyncio.sleep(0.01)
        assert runtime.ha_entities_status().state is BrokerState.CONNECTED
        assert any(m.topic.startswith("homeassistant/") for m in broker.published)
    finally:
        await runtime.stop()
    # the demo never touches /data: its database is a throw-away one
    assert not (tmp_path / "data" / "suncore.db").exists()
