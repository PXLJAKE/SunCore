# SPDX-License-Identifier: Apache-2.0
"""``HaEntityService`` end to end against the stub broker (real client, real protocol)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from suncore.core.config import MqttConfig
from suncore.core.devices import DeviceKind
from suncore.ha.mqtt import (
    BrokerState,
    DeviceInfo,
    HaEntityService,
    InstallRecord,
    SiteEvent,
)
from suncore.ha.mqtt.broker import ServiceNotEnabledError

from .stub_broker import Message, StubBroker
from .support import FakeSource, MemoryStore, topology


class Services:
    def __init__(self, broker: StubBroker | None) -> None:
        self.broker = broker
        self.calls = 0

    async def mqtt_service(self) -> Mapping[str, Any]:
        self.calls += 1
        if self.broker is None:
            raise ServiceNotEnabledError("not enabled")
        return {"host": "127.0.0.1", "port": self.broker.port}


class Harness:
    def __init__(self, broker: StubBroker, source: FakeSource, store: MemoryStore) -> None:
        self.broker = broker
        self.source = source
        self.store = store
        self.config: MqttConfig | None = MqttConfig()
        self.services = Services(broker)
        self.service = HaEntityService(
            source=source,
            load_config=self._load,
            store=store,
            services=self.services,
            retry_s=0.1,
            poll_s=0.05,
            birth_delay=(0.0, 0.0),
            min_interval_s=0.0,
        )

    async def _load(self) -> MqttConfig | None:
        return self.config

    @property
    def iid(self) -> str:
        assert self.store.record is not None
        return self.store.record.iid

    def configs(self) -> dict[str, Message]:
        """The latest message per discovery config topic."""
        out: dict[str, Message] = {}
        for m in self.broker.published:
            if "/device/" in m.topic:
                out[m.topic] = m
        return out

    async def connected(self) -> None:
        await self.broker.wait_for(lambda: self.service.status.state is BrokerState.CONNECTED)
        await self.broker.wait_for(lambda: bool(self.configs()))


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    async with StubBroker() as broker:
        source = FakeSource()
        source.data = {"status": "running", "mode": "shadow", "healthy": True}
        h = Harness(broker, source, MemoryStore())
        await h.service.start()
        try:
            yield h
        finally:
            await h.service.stop()


async def test_announces_online_configs_and_states(harness: Harness) -> None:
    await harness.connected()
    iid = harness.iid
    assert len(iid) == 8
    status = harness.broker.messages(f"suncore/{iid}/status")
    assert [(m.payload, m.retain) for m in status] == [(b"online", True)]
    topics = set(harness.configs())
    assert f"homeassistant/device/suncore_{iid}/config" in topics
    assert f"homeassistant/device/suncore_{iid}_heater/config" in topics
    for message in harness.configs().values():
        assert message.retain is False  # configs are not retained (ADR-0016)
        assert message.qos == 1
        json.loads(message.payload)
    await harness.broker.wait_for(lambda: f"suncore/{iid}/mode/state" in harness.broker.retained)
    assert harness.broker.retained[f"suncore/{iid}/mode/state"].payload == b"Shadow"
    assert harness.broker.retained[f"suncore/{iid}/healthy/state"].payload == b"ON"
    assert harness.service.status.entity_count > 10


async def test_install_id_is_stable_across_sessions() -> None:
    async with StubBroker() as broker:
        store = MemoryStore(InstallRecord("deadbeef"))
        h = Harness(broker, FakeSource(), store)
        await h.service.start()
        try:
            await h.connected()
        finally:
            await h.service.stop()
        assert h.iid == "deadbeef"
        assert "homeassistant/device/suncore_deadbeef/config" in h.configs()


async def test_birth_message_republishes_configs_and_states(harness: Harness) -> None:
    await harness.connected()
    before = len(harness.broker.published)
    harness.broker.inject("homeassistant/status", "online")
    await harness.broker.wait_for(lambda: len(harness.broker.published) > before + 3)
    new = harness.broker.published[before:]
    assert any("/device/" in m.topic for m in new)
    assert any(m.topic.endswith("/mode/state") for m in new)  # states follow the configs


async def test_birth_delay_is_random_between_1_and_5_seconds() -> None:
    delays: list[tuple[float, float]] = []

    def rng(a: float, b: float) -> float:
        delays.append((a, b))
        return 0.0

    async with StubBroker() as broker:
        h = Harness(broker, FakeSource(), MemoryStore())
        h.service = HaEntityService(
            source=h.source,
            load_config=h._load,
            store=h.store,
            services=h.services,
            retry_s=0.1,
            poll_s=0.05,
            rng=rng,
        )
        await h.service.start()
        try:
            await h.connected()
            broker.inject("homeassistant/status", "online")
            await broker.wait_for(lambda: delays)
        finally:
            await h.service.stop()
    assert delays[0] == (1.0, 5.0)


async def test_offline_birth_payload_is_ignored(harness: Harness) -> None:
    await harness.connected()
    await asyncio.sleep(0.1)
    before = len(harness.broker.published)
    harness.broker.inject("homeassistant/status", "offline")
    await asyncio.sleep(0.2)
    assert len(harness.broker.published) == before


async def test_changed_values_are_published_on_change_only(harness: Harness) -> None:
    await harness.connected()
    iid = harness.iid
    topic = f"suncore/{iid}/mode/state"
    await harness.broker.wait_for(lambda: topic in harness.broker.retained)
    harness.source.data["mode"] = "automatic"
    harness.service.refresh()
    await harness.broker.wait_for(lambda: len(harness.broker.messages(topic)) == 2)
    assert harness.broker.messages(topic)[-1].payload == b"Automatic"
    harness.service.refresh()
    await asyncio.sleep(0.2)
    assert len(harness.broker.messages(topic)) == 2


async def test_events_are_published_to_the_event_topic_not_retained(harness: Harness) -> None:
    await harness.connected()
    iid = harness.iid
    harness.source.events.append(SiteEvent("device_switched", {"device": "heater", "on": True}))
    harness.service.refresh()
    await harness.broker.wait_for(lambda: harness.broker.messages(f"suncore/{iid}/event"))
    message = harness.broker.messages(f"suncore/{iid}/event")[0]
    assert message.retain is False
    assert json.loads(message.payload) == {
        "event_type": "device_switched",
        "device": "heater",
        "on": True,
    }


async def test_a_removed_device_is_removed_with_an_empty_config(harness: Harness) -> None:
    await harness.connected()
    iid = harness.iid
    harness.source.topo = topology(devices=(DeviceInfo("heater", "Heater", DeviceKind.SWITCH),))
    harness.service.refresh()
    gone = f"homeassistant/device/suncore_{iid}_car/config"
    await harness.broker.wait_for(
        lambda: any(m.payload == b"" for m in harness.broker.messages(gone))
    )
    assert harness.store.record is not None
    assert f"suncore_{iid}_car" not in harness.store.record.published
    assert f"suncore_{iid}_heater" in harness.store.record.published


async def test_devices_left_from_a_previous_run_are_removed() -> None:
    async with StubBroker() as broker:
        store = MemoryStore(InstallRecord("cafebabe", ("suncore_cafebabe_oldone",)))
        h = Harness(broker, FakeSource(), store)
        await h.service.start()
        try:
            await h.connected()
            topic = "homeassistant/device/suncore_cafebabe_oldone/config"
            await broker.wait_for(lambda: broker.messages(topic))
        finally:
            await h.service.stop()
        assert broker.messages(topic)[0].payload == b""


async def test_language_change_republishes_names(harness: Harness) -> None:
    await harness.connected()
    iid = harness.iid
    site = f"homeassistant/device/suncore_{iid}/config"
    harness.source.lang = "de"
    harness.service.refresh()
    await harness.broker.wait_for(lambda: len(harness.broker.messages(site)) == 2)
    payload = json.loads(harness.broker.messages(site)[-1].payload)
    assert payload["cmps"]["mode"]["name"] == "Betriebsart"
    await harness.broker.wait_for(
        lambda: harness.broker.retained[f"suncore/{iid}/mode/state"].payload == b"Schatten"
    )


async def test_republish_button(harness: Harness) -> None:
    await harness.connected()
    before = len(harness.broker.published)
    assert await harness.service.republish() is True
    assert len(harness.broker.published) > before


async def test_stop_publishes_offline_and_keeps_the_configs(harness: Harness) -> None:
    await harness.connected()
    iid = harness.iid
    await harness.service.stop()
    last = harness.broker.messages(f"suncore/{iid}/status")[-1]
    assert (last.payload, last.retain) == (b"offline", True)
    assert not any(m.payload == b"" and "/device/" in m.topic for m in harness.broker.published)


async def test_disabling_removes_the_entities() -> None:
    async with StubBroker() as broker:
        h = Harness(broker, FakeSource(), MemoryStore())
        await h.service.start()
        try:
            await h.connected()
            iid = h.iid
            h.config = MqttConfig(enabled=False)
            h.service.reconfigure()
            await broker.wait_for(lambda: h.service.status.state is BrokerState.DISABLED)
            removed = {
                m.topic for m in broker.published if m.payload == b"" and "/device/" in m.topic
            }
            assert f"homeassistant/device/suncore_{iid}/config" in removed
            assert broker.messages(f"suncore/{iid}/status")[-1].payload == b"offline"
        finally:
            await h.service.stop()


async def test_no_broker_status_and_retry_until_one_appears() -> None:
    async with StubBroker() as broker:
        h = Harness(broker, FakeSource(), MemoryStore())
        h.services.broker = None  # no Mosquitto app installed
        await h.service.start()
        try:
            await broker.wait_for(lambda: h.service.status.state is BrokerState.NO_BROKER)
            assert h.service.status.reason_key == "mqtt.reason.no_broker_service"
            assert h.service.status.entity_count == 0
            assert await h.service.republish() is False
            h.services.broker = broker  # the user installs it
            await h.connected()
            assert h.service.status.reason_key is None
        finally:
            await h.service.stop()


async def test_auth_failure_is_an_error_with_a_reason_key() -> None:
    async with StubBroker(username="u", password="p") as broker:
        h = Harness(broker, FakeSource(), MemoryStore())
        await h.service.start()
        try:
            await broker.wait_for(lambda: h.service.status.state is BrokerState.ERROR)
            assert h.service.status.reason_key == "mqtt.reason.auth_failed"
        finally:
            await h.service.stop()


async def test_a_new_connection_announces_again() -> None:
    async with StubBroker() as broker:
        h = Harness(broker, FakeSource(), MemoryStore())
        await h.service.start()
        try:
            await h.connected()
            site = f"homeassistant/device/suncore_{h.iid}/config"
            await broker.drop_connections()
            await broker.wait_for(lambda: len(broker.messages(site)) == 2, seconds=8)
            payloads = [m.payload for m in broker.messages(f"suncore/{h.iid}/status")]
            assert payloads == [b"online", b"offline", b"online"]  # the will in between
        finally:
            await h.service.stop()


async def test_the_surface_never_writes_to_devices(harness: Harness) -> None:
    await harness.connected()
    # Only SunCore's own topics and the discovery prefix are ever published.
    for message in harness.broker.published:
        assert message.topic.startswith((f"suncore/{harness.iid}/", "homeassistant/device/"))
