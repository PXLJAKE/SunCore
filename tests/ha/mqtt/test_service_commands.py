# SPDX-License-Identifier: Apache-2.0
"""Command entities and the command path end to end: real client, stub broker, fake port."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from suncore.app.ha_entities import SUPPORTED
from suncore.core.config import MqttConfig
from suncore.core.decisions import OperatingMode
from suncore.ha.mqtt import BrokerState, HaEntityService

from .stub_broker import Message, StubBroker
from .support import FakeSource, MemoryStore, topology
from .test_commands import FakePort
from .test_service import Services


class Harness:
    def __init__(self, broker: StubBroker, *, allow_raise: bool) -> None:
        self.broker = broker
        self.source = FakeSource(topology(supported=SUPPORTED))
        self.source.data = {"status": "running", "mode": "shadow", "auto": False}
        self.port = FakePort()
        self.port.device_modes = {"heater": OperatingMode.SHADOW, "car": OperatingMode.SHADOW}
        self.config = MqttConfig(allow_mode_raise=allow_raise)
        self.store = MemoryStore()
        self.service = HaEntityService(
            source=self.source,
            load_config=self._load,
            store=self.store,
            services=Services(broker),
            commands=self.port,
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

    def components(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for m in self.broker.published:
            if "/device/" in m.topic and m.payload:
                out.update(json.loads(m.payload)["cmps"])
        return out

    async def connected(self) -> None:
        await self.broker.wait_for(lambda: self.service.status.state is BrokerState.CONNECTED)
        await self.broker.wait_for(lambda: bool(self.components()))

    def command(self, object_id: str, payload: str, *, retain: bool = False) -> None:
        self.broker.inject(f"suncore/{self.iid}/{object_id}/set", payload, retain=retain)


async def run(allow_raise: bool) -> AsyncIterator[Harness]:
    async with StubBroker() as broker:
        h = Harness(broker, allow_raise=allow_raise)
        await h.service.start()
        try:
            yield h
        finally:
            await h.service.stop()


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    async for h in run(False):
        yield h


@pytest.fixture
async def opted_in() -> AsyncIterator[Harness]:
    async for h in run(True):
        yield h


async def settle() -> None:
    await asyncio.sleep(0.3)


async def test_command_entities_are_announced_with_command_topics(harness: Harness) -> None:
    await harness.connected()
    iid = harness.iid
    cmps = harness.components()
    for object_id in ("auto", "replan", "emergency_stop_button", "heater_auto", "heater_boost"):
        assert cmps[object_id]["cmd_t"] == f"suncore/{iid}/{object_id}/set"
    assert "stat_t" not in cmps["replan"]  # a button has no state
    assert cmps["auto"]["p"] == "switch"
    assert "stat_t" in cmps["auto"]
    minutes = cmps["heater_boost_minutes"]
    assert (minutes["min"], minutes["max"], minutes["step"]) == (1, 1440, 1)
    assert minutes["ent_cat"] == "config"
    # Entities of kinds whose commands do not exist yet are not announced.
    assert "car_ev_mode" not in cmps
    assert "battery_strategy" not in cmps


async def test_clear_button_only_with_the_opt_in(harness: Harness, opted_in: Harness) -> None:
    await harness.connected()
    await opted_in.connected()
    assert "emergency_stop_clear" not in harness.components()
    assert "emergency_stop_clear" in opted_in.components()


async def test_no_commands_without_a_port() -> None:
    async with StubBroker() as broker:
        h = Harness(broker, allow_raise=True)
        h.service = HaEntityService(
            source=h.source,
            load_config=h._load,
            store=h.store,
            services=Services(broker),
            retry_s=0.1,
            poll_s=0.05,
            birth_delay=(0.0, 0.0),
        )
        await h.service.start()
        try:
            await h.connected()
        finally:
            await h.service.stop()
        assert not any(c.get("cmd_t") for c in h.components().values())
        assert not any(t.endswith("/+/set") for t in broker.subscribed)


async def test_command_topics_are_subscribed(harness: Harness) -> None:
    await harness.connected()
    assert f"suncore/{harness.iid}/+/set" in harness.broker.subscribed


async def test_a_command_reaches_the_port_once(harness: Harness) -> None:
    await harness.connected()
    harness.command("replan", "PRESS")
    await harness.broker.wait_for(lambda: harness.port.calls)
    await settle()
    assert harness.port.calls == [("replan",)]


async def test_emergency_stop_from_ha(harness: Harness) -> None:
    await harness.connected()
    harness.command("emergency_stop_button", "PRESS")
    await harness.broker.wait_for(lambda: harness.port.calls)
    assert harness.port.calls == [("stop",)]


async def test_raise_is_refused_and_the_true_state_is_republished(harness: Harness) -> None:
    await harness.connected()
    iid = harness.iid
    topic = f"suncore/{iid}/auto/state"
    await harness.broker.wait_for(lambda: topic in harness.broker.retained)
    before = len(harness.broker.messages(topic))
    harness.command("auto", "ON")
    await harness.broker.wait_for(lambda: len(harness.broker.messages(topic)) > before)
    assert harness.broker.messages(topic)[-1].payload == b"OFF"  # still the true state
    assert harness.port.calls == []


async def test_raise_works_with_the_opt_in(opted_in: Harness) -> None:
    await opted_in.connected()
    opted_in.command("auto", "ON")
    await opted_in.broker.wait_for(lambda: opted_in.port.calls)
    assert opted_in.port.calls == [("global_mode", OperatingMode.AUTOMATIC)]


async def test_a_retained_command_is_ignored_and_removed(opted_in: Harness) -> None:
    await opted_in.connected()
    topic = f"suncore/{opted_in.iid}/emergency_stop_button/set"
    # A retained command waiting on the broker; it arrives (flagged retained) after a reconnect.
    opted_in.broker.retained[topic] = Message(topic, b"PRESS", 0, True)
    await opted_in.broker.drop_connections()
    await opted_in.broker.wait_for(lambda: topic not in opted_in.broker.retained)
    await settle()
    assert opted_in.port.calls == []
    assert any(m.topic == topic and m.payload == b"" for m in opted_in.broker.published)


async def test_garbage_is_ignored(harness: Harness) -> None:
    await harness.connected()
    for payload in ("", "press", "{}", "x" * 500):
        harness.command("replan", payload)
    harness.command("nonexistent", "PRESS")
    await settle()
    assert harness.port.calls == []


async def test_flood_does_not_stall_the_surface(harness: Harness) -> None:
    await harness.connected()
    for _ in range(200):
        harness.command("replan", "PRESS")
    await settle()
    assert len(harness.port.calls) <= 3  # burst of three, no refill in 0.3 s worth of flood
    # The surface still publishes state changes (no tick or sync delay).
    topic = f"suncore/{harness.iid}/mode/state"
    harness.source.data["mode"] = "automatic"
    harness.service.refresh()
    await harness.broker.wait_for(
        lambda: harness.broker.messages(topic)[-1].payload == b"Automatic"
    )


async def test_boost_minutes_state_follows_the_command(harness: Harness) -> None:
    await harness.connected()
    topic = f"suncore/{harness.iid}/heater_boost_minutes/state"
    await harness.broker.wait_for(lambda: topic in harness.broker.retained)
    assert harness.broker.retained[topic].payload == b"60"
    harness.command("heater_boost_minutes", "25")
    await harness.broker.wait_for(lambda: harness.broker.retained[topic].payload == b"25")
    assert harness.port.calls == []
