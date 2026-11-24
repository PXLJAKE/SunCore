# SPDX-License-Identifier: Apache-2.0
"""PR 6-J: the MQTT link to evcc (real ``aiomqtt`` against the in-process stub broker)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from suncore.app.evcc_link import EvccLink, subscriptions_of, targets_of
from suncore.core.config import MqttConfig
from suncore.core.devices import (
    DeviceConfig,
    DeviceKind,
    LoadpointControl,
    PowerModel,
    PowerModelKind,
)
from tests.ha.mqtt.stub_broker import StubBroker

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def lp_device(
    device_id: str = "lp", *, prefix: str = "evcc", loadpoint_id: int = 1
) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="evcc.loadpoint",
        name=device_id,
        kind=DeviceKind.WALLBOX,
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_040),
        control=LoadpointControl(kind="loadpoint", loadpoint_id=loadpoint_id, topic_prefix=prefix),
    )


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def config_for(broker: StubBroker) -> MqttConfig:
    return MqttConfig(broker="manual", host="127.0.0.1", port=broker.port)


@contextlib.asynccontextmanager
async def running(
    broker: StubBroker, clock: Clock, devices: list[DeviceConfig]
) -> AsyncIterator[EvccLink]:
    async def load() -> MqttConfig:
        return config_for(broker)

    link = EvccLink(load_config=load, services=None, clock=clock, retry_s=0.05)
    link.configure(devices)
    await link.start()
    try:
        await broker.wait_for(lambda: link.connected)
        yield link
    finally:
        await link.stop()


def heartbeat(clock: Clock, *, age_s: float = 0.0) -> str:
    return str(int((clock.now - timedelta(seconds=age_s)).timestamp()))


def test_targets_and_subscriptions_follow_the_loadpoints() -> None:
    devices = [
        lp_device("a"),
        lp_device("b", loadpoint_id=2),
        lp_device("c", prefix="other"),
    ]
    assert targets_of(devices) == {("evcc", 1), ("evcc", 2), ("other", 1)}
    assert dict(subscriptions_of(targets_of(devices))) == {
        "evcc/loadpoints/1/+": 0,
        "evcc/loadpoints/2/+": 0,
        "evcc/updated": 0,
        "other/loadpoints/1/+": 0,
        "other/updated": 0,
    }


async def test_retained_values_and_the_heartbeat_make_evcc_alive() -> None:
    clock = Clock()
    async with StubBroker() as broker:
        broker.inject("evcc/loadpoints/1/mode", "smart", retain=True)
        broker.inject("evcc/updated", heartbeat(clock), retain=True)
        async with running(broker, clock, [lp_device()]) as link:
            await broker.wait_for(lambda: link.alive("evcc"))
            assert link.value("evcc/loadpoints/1/mode") == "smart"
            assert link.value("evcc/loadpoints/1/maxCurrent") is None
            assert link.alive("other") is False


async def test_a_stale_retained_heartbeat_never_makes_a_dead_evcc_look_alive() -> None:
    clock = Clock()
    async with StubBroker() as broker:
        broker.inject("evcc/updated", heartbeat(clock, age_s=600), retain=True)
        async with running(broker, clock, [lp_device()]) as link:
            await broker.wait_for(lambda: link.value("evcc/updated") is not None)
            assert link.alive("evcc") is False


async def test_evcc_goes_silent_when_the_heartbeat_ages_out() -> None:
    clock = Clock()
    async with StubBroker() as broker:
        broker.inject("evcc/updated", heartbeat(clock), retain=True)
        async with running(broker, clock, [lp_device()]) as link:
            await broker.wait_for(lambda: link.alive("evcc"))
            clock.now += timedelta(seconds=121)
            assert link.alive("evcc") is False
            broker.inject("evcc/updated", heartbeat(clock))
            await broker.wait_for(lambda: link.alive("evcc"))


async def test_an_empty_payload_erases_a_value_and_a_garbage_heartbeat_is_dead() -> None:
    clock = Clock()
    async with StubBroker() as broker, running(broker, clock, [lp_device()]) as link:
        broker.inject("evcc/loadpoints/1/mode", "now")
        await broker.wait_for(lambda: link.value("evcc/loadpoints/1/mode") == "now")
        broker.inject("evcc/loadpoints/1/mode", "")
        await broker.wait_for(lambda: link.value("evcc/loadpoints/1/mode") is None)
        broker.inject("evcc/updated", "not a number")
        await asyncio.sleep(0.1)
        assert link.alive("evcc") is False


async def test_listeners_hear_loadpoint_topics_but_not_the_heartbeat() -> None:
    clock = Clock()
    heard: list[str] = []
    async with StubBroker() as broker, running(broker, clock, [lp_device()]) as link:
        remove = link.add_listener(heard.append)
        broker.inject("evcc/updated", heartbeat(clock))
        broker.inject("evcc/loadpoints/1/mode", "off")
        await broker.wait_for(lambda: heard)
        assert heard == ["evcc/loadpoints/1/mode"]
        remove()
        broker.inject("evcc/loadpoints/1/mode", "now")
        await broker.wait_for(lambda: link.value("evcc/loadpoints/1/mode") == "now")
        assert heard == ["evcc/loadpoints/1/mode"]


async def test_publish_set_is_not_retained_and_reaches_the_broker() -> None:
    clock = Clock()
    async with StubBroker() as broker:
        async with running(broker, clock, [lp_device()]) as link:
            assert await link.publish_set("evcc/loadpoints/1/mode/set", "smart") is True
            await broker.wait_for(lambda: broker.messages("evcc/loadpoints/1/mode/set"))
        message = broker.messages("evcc/loadpoints/1/mode/set")[0]
        assert (message.payload, message.retain) == (b"smart", False)
        assert "evcc/loadpoints/1/mode/set" not in broker.retained


async def test_without_a_connection_nothing_is_published_and_evcc_is_not_alive() -> None:
    async def load() -> MqttConfig:
        return MqttConfig(enabled=False)

    link = EvccLink(load_config=load, services=None)
    link.configure([lp_device()])
    assert link.alive("evcc") is False
    assert await link.publish_set("evcc/loadpoints/1/mode/set", "off") is False
    await link.start()
    await asyncio.sleep(0.05)
    assert link.connected is False
    assert link.reason == "mqtt.reason.not_enabled"
    await link.stop()


async def test_a_dropped_connection_forgets_everything_and_reconnects() -> None:
    clock = Clock()
    async with StubBroker() as broker:
        broker.inject("evcc/loadpoints/1/mode", "smart", retain=True)
        broker.inject("evcc/updated", heartbeat(clock), retain=True)
        async with running(broker, clock, [lp_device()]) as link:
            await broker.wait_for(lambda: link.alive("evcc"))
            await broker.drop_connections()
            await broker.wait_for(lambda: not link.connected)
            assert link.alive("evcc") is False
            await broker.wait_for(lambda: link.alive("evcc"), seconds=10.0)
            assert link.value("evcc/loadpoints/1/mode") == "smart"


@pytest.mark.parametrize("change", ["targets", "mqtt"])
async def test_a_changed_configuration_restarts_the_session(change: str) -> None:
    clock = Clock()
    async with StubBroker() as broker, running(broker, clock, [lp_device()]) as link:
        broker.inject("evcc/loadpoints/2/mode", "now")
        await asyncio.sleep(0.1)
        assert link.value("evcc/loadpoints/2/mode") is None  # not subscribed
        if change == "targets":
            link.configure([lp_device(), lp_device("b", loadpoint_id=2)])
        else:
            link.reconfigure()
        await broker.wait_for(lambda: link.connected)
        if change == "targets":
            broker.inject("evcc/loadpoints/2/mode", "off", retain=True)
            await broker.wait_for(lambda: link.value("evcc/loadpoints/2/mode") == "off")
