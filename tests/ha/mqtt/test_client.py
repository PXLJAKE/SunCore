# SPDX-License-Identifier: Apache-2.0
"""``MqttClient`` against the in-process stub broker (real ``aiomqtt`` and the real protocol)."""

from __future__ import annotations

import asyncio
import contextlib
import ssl

import pytest

from suncore.ha.mqtt import BrokerSettings, LinkState, MqttClient, Will, classify_error
from suncore.ha.websocket import Backoff

from .stub_broker import StubBroker


class Probe:
    def __init__(self) -> None:
        self.links: list[tuple[LinkState, str | None]] = []
        self.messages: list[tuple[str, bytes]] = []
        self.connected = 0

    async def on_connected(self) -> None:
        self.connected += 1

    def on_message(self, topic: str, payload: bytes) -> None:
        self.messages.append((topic, payload))

    def on_link(self, link: LinkState, reason: str | None) -> None:
        self.links.append((link, reason))


def _client(broker: StubBroker, probe: Probe, **kw: object) -> MqttClient:
    settings = BrokerSettings(
        "127.0.0.1",
        broker.port,
        username=kw.pop("username", None),  # type: ignore[arg-type]
        password=kw.pop("password", None),  # type: ignore[arg-type]
    )

    async def fast_sleep(_: float) -> None:
        await asyncio.sleep(0.05)

    return MqttClient(
        settings,
        client_id="suncore-test",
        will=Will("suncore/x/status", "offline"),
        subscriptions=[("homeassistant/status", 1)],
        on_connected=probe.on_connected,
        on_message=probe.on_message,
        on_link=probe.on_link,
        backoff=Backoff(initial=0.01, maximum=0.05),
        sleep=fast_sleep,
        **kw,  # type: ignore[arg-type]
    )


@contextlib.asynccontextmanager
async def running(client: MqttClient):  # type: ignore[no-untyped-def]
    task = asyncio.create_task(client.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_connects_subscribes_and_publishes() -> None:
    async with StubBroker() as broker:
        probe = Probe()
        client = _client(broker, probe)
        async with running(client):
            await broker.wait_for(lambda: client.connected and probe.connected == 1)
            assert (LinkState.CONNECTED, None) in probe.links
            assert "homeassistant/status" in broker.subscribed
            assert await client.publish("suncore/x/a", "1", qos=1, retain=True) is True
            assert await client.publish("suncore/x/b", "2", qos=0) is True
            await broker.wait_for(lambda: len(broker.published) == 2)
        assert broker.retained["suncore/x/a"].payload == b"1"
        assert broker.messages("suncore/x/b")[0].retain is False


async def test_incoming_messages_are_dispatched() -> None:
    async with StubBroker() as broker:
        probe = Probe()
        client = _client(broker, probe)
        async with running(client):
            await broker.wait_for(lambda: client.connected)
            broker.inject("homeassistant/status", "online")
            await broker.wait_for(lambda: probe.messages)
        assert probe.messages == [("homeassistant/status", b"online")]


async def test_last_will_is_published_when_the_connection_drops() -> None:
    async with StubBroker() as broker:
        probe = Probe()
        client = _client(broker, probe)
        async with running(client):
            await broker.wait_for(lambda: client.connected)
            await broker.drop_connections()
            await broker.wait_for(lambda: broker.messages("suncore/x/status"))
        will = broker.messages("suncore/x/status")[0]
        assert (will.payload, will.retain, will.qos) == (b"offline", True, 1)


async def test_reconnects_after_a_drop_and_announces_again() -> None:
    async with StubBroker() as broker:
        probe = Probe()
        client = _client(broker, probe)
        async with running(client):
            await broker.wait_for(lambda: probe.connected == 1)
            await broker.drop_connections()
            await broker.wait_for(lambda: probe.connected == 2)
        assert any(link is LinkState.ERROR for link, _ in probe.links)


async def test_clean_stop_does_not_publish_the_will() -> None:
    async with StubBroker() as broker:
        probe = Probe()
        client = _client(broker, probe)
        async with running(client):
            await broker.wait_for(lambda: client.connected)
        await asyncio.sleep(0.1)
        assert broker.messages("suncore/x/status") == []


async def test_wrong_credentials_report_auth_failed_and_keep_retrying() -> None:
    async with StubBroker(username="u", password="p") as broker:
        probe = Probe()
        client = _client(broker, probe, username="u", password="wrong")
        async with running(client):
            await broker.wait_for(lambda: len(broker.connects) >= 2)
        reasons = {reason for link, reason in probe.links if link is LinkState.ERROR}
        assert reasons == {"mqtt.reason.auth_failed"}
        assert probe.connected == 0


async def test_correct_credentials_connect() -> None:
    async with StubBroker(username="u", password="p") as broker:
        probe = Probe()
        client = _client(broker, probe, username="u", password="p")
        async with running(client):
            await broker.wait_for(lambda: client.connected)


async def test_unreachable_broker_reports_connect_refused() -> None:
    probe = Probe()
    async with StubBroker() as broker:
        pass  # the port is closed now
    client = _client(broker, probe)
    async with running(client):
        await asyncio.sleep(0.3)
    reasons = {reason for link, reason in probe.links if link is LinkState.ERROR}
    assert reasons <= {"mqtt.reason.connect_refused", "mqtt.reason.timeout"}
    assert reasons


async def test_publish_without_a_connection_returns_false() -> None:
    async with StubBroker() as broker:
        client = _client(broker, Probe())
        assert await client.publish("t", "x") is False


def test_classify_error() -> None:
    assert classify_error(ssl.SSLError("bad")) == "mqtt.reason.tls_failed"
    assert classify_error(RuntimeError("Not authorised")) == "mqtt.reason.auth_failed"
    assert classify_error(RuntimeError("Bad user name or password")) == "mqtt.reason.auth_failed"
    assert classify_error(TimeoutError()) == "mqtt.reason.timeout"
    assert classify_error(ConnectionRefusedError()) == "mqtt.reason.connect_refused"
    assert classify_error(ValueError("???")) == "mqtt.reason.unknown"
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = ssl.SSLError("inner")
    assert classify_error(wrapped) == "mqtt.reason.tls_failed"


@pytest.mark.parametrize("level", ["3.1", "3.1.1"])
async def test_both_protocol_levels_connect(level: str) -> None:
    async with StubBroker() as broker:
        probe = Probe()
        settings = BrokerSettings("127.0.0.1", broker.port, protocol=level)  # type: ignore[arg-type]
        client = MqttClient(
            settings,
            client_id="suncore-test",
            will=Will("suncore/x/status", "offline"),
            subscriptions=[],
            on_connected=probe.on_connected,
            on_message=probe.on_message,
            on_link=probe.on_link,
        )
        async with running(client):
            await broker.wait_for(lambda: client.connected)
