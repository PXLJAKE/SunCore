# SPDX-License-Identifier: Apache-2.0
"""Notifications and notification actions on the wire (allowlist, payloads, event filter)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from suncore.ha.connection import (
    NOTIFY_ACTION_EVENT,
    CommandError,
    ConnectionState,
    NotConnectedError,
    NotificationAction,
    NotifyNotAllowedError,
    check_notify_call,
)
from suncore.ha.transport import DirectTransport
from suncore.ha.websocket import WebSocketConnection
from tests.ha.fakes import TOKEN, FakeClock, FakeHA, wait_until


@pytest.fixture
async def ha() -> AsyncIterator[FakeHA]:
    server = FakeHA()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def conn(ha: FakeHA, clock: FakeClock) -> AsyncIterator[WebSocketConnection]:
    connection = WebSocketConnection(
        DirectTransport.from_base_url(ha.base_url, TOKEN),
        clock=clock.monotonic,
        sleep=clock.sleep,
        rng=lambda: 0.5,
        heartbeat_interval=777.0,
        heartbeat_timeout=666.0,
        resync_interval=555.0,
        command_timeout=888.0,
        connect_timeout=999.0,
    )
    await asyncio.wait_for(connection.connect(), 5)
    yield connection
    await connection.close()


def calls(ha: FakeHA) -> list[dict[str, object]]:
    return [m for m in ha.received if m["type"] == "call_service"]


# --- the allowlist (pure) ---


@pytest.mark.parametrize(
    ("domain", "service", "data", "target"),
    [
        ("persistent_notification", "create", {"message": "m"}, None),
        ("persistent_notification", "dismiss", {"notification_id": "x"}, None),
        ("notify", "send_message", {"message": "m"}, {"entity_id": ["notify.a"]}),
        ("notify", "send_message", {"message": "m"}, {"entity_id": "notify.a"}),
        ("notify", "mobile_app_phone", {"message": "m", "data": {"actions": []}}, None),
    ],
)
def test_allowed_notifications(domain: str, service: str, data: dict, target: dict | None) -> None:
    check_notify_call(domain, service, data, target, {"mobile_app_phone"})


@pytest.mark.parametrize(
    ("domain", "service", "data", "target"),
    [
        ("switch", "turn_on", {"message": "m"}, {"entity_id": ["switch.pump"]}),
        ("light", "turn_off", {}, None),
        ("homeassistant", "restart", {}, None),
        ("persistent_notification", "mark_read", {}, None),
        ("persistent_notification", "create", {"message": "m"}, {"entity_id": ["switch.a"]}),
        ("notify", "send_message", {"message": "m"}, {"entity_id": ["switch.pump"]}),
        ("notify", "send_message", {"message": "m"}, {"entity_id": ["notify.a", "switch.b"]}),
        ("notify", "send_message", {"message": "m"}, {"entity_id": ["notify.a"], "area_id": "x"}),
        ("notify", "send_message", {"message": "m"}, None),
        ("notify", "send_message", {}, {"entity_id": ["notify.a"]}),
        ("notify", "persistent_notification", {"message": "m"}, None),
        ("notify", "not_configured", {"message": "m"}, None),
        ("notify", "mobile_app_phone", {}, None),
        ("notify", "mobile_app_phone", {"message": "m"}, {"entity_id": ["switch.a"]}),
        ("notify", "", {"message": "m"}, None),
    ],
)
def test_refused_notifications(domain: str, service: str, data: dict, target: dict | None) -> None:
    with pytest.raises(NotifyNotAllowedError):
        check_notify_call(domain, service, data, target, {"mobile_app_phone"})


# --- on the wire ---


async def test_a_notification_is_one_call_service_without_readback(
    conn: WebSocketConnection, ha: FakeHA, clock: FakeClock
) -> None:
    conn.set_notify_services(["mobile_app_phone"])

    async def send(*args: object, **kwargs: object) -> None:
        task = asyncio.create_task(conn.notify(*args, **kwargs))  # type: ignore[arg-type]
        await clock.settle()
        await clock.advance(1.0)  # the 1 s spacing between notifications
        await asyncio.wait_for(task, 5)

    await send("persistent_notification", "create", {"message": "hi", "title": "t"})
    await send("notify", "send_message", {"message": "hi"}, {"entity_id": ["notify.a"]})
    await send("notify", "mobile_app_phone", {"message": "hi", "data": {"actions": []}})
    sent = calls(ha)
    assert [(m["domain"], m["service"]) for m in sent] == [
        ("persistent_notification", "create"),
        ("notify", "send_message"),
        ("notify", "mobile_app_phone"),
    ]
    assert sent[1]["target"] == {"entity_id": ["notify.a"]}
    assert "target" not in sent[0]
    assert not [m for m in ha.received if m["type"] == "get_states"]  # no read-back


async def test_a_refused_notification_never_reaches_home_assistant(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    with pytest.raises(NotifyNotAllowedError):
        await conn.notify("switch", "turn_on", {"message": "x"}, {"entity_id": ["switch.a"]})
    with pytest.raises(NotifyNotAllowedError):
        await conn.notify("notify", "mobile_app_phone", {"message": "x"})  # not configured
    assert calls(ha) == []


async def test_notify_while_offline_raises(ha: FakeHA) -> None:
    connection = WebSocketConnection(DirectTransport.from_base_url(ha.base_url, TOKEN))
    with pytest.raises(NotConnectedError):
        await connection.notify("persistent_notification", "create", {"message": "m"})


async def test_notifications_are_spaced_by_their_own_limiter(
    ha: FakeHA,
) -> None:
    clock = FakeClock()
    connection = WebSocketConnection(
        DirectTransport.from_base_url(ha.base_url, TOKEN),
        clock=clock.monotonic,
        sleep=clock.sleep,
        heartbeat_interval=777.0,
        resync_interval=555.0,
        command_timeout=888.0,
        connect_timeout=999.0,
    )
    await asyncio.wait_for(connection.connect(), 5)
    try:
        await connection.notify("persistent_notification", "create", {"message": "a"})
        task = asyncio.create_task(
            connection.notify("persistent_notification", "create", {"message": "b"})
        )
        await clock.settle()
        assert not task.done()  # waits for the 1 s spacing on the injected clock
        await clock.advance(1.0)
        await asyncio.wait_for(task, 5)
    finally:
        await connection.close()


# --- notification actions ---


async def test_action_taps_arrive_as_their_action_string_only(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    got: list[NotificationAction] = []
    await conn.subscribe_notification_actions(got.append)
    assert any(
        m["type"] == "subscribe_events" and m["event_type"] == NOTIFY_ACTION_EVENT
        for m in ha.received
    )
    data = {"action": "SUNCORE_CONFIRM_abc", "evil": "ignored", "tag": "t"}
    await ha.push_registry_event(NOTIFY_ACTION_EVENT, data)
    await wait_until(lambda: bool(got))
    assert got == [NotificationAction("SUNCORE_CONFIRM_abc")]


@pytest.mark.parametrize("data", [{}, {"action": 5}, {"action": ""}, {"action": "x" * 201}])
async def test_malformed_tap_events_are_dropped(
    conn: WebSocketConnection, ha: FakeHA, data: dict
) -> None:
    got: list[NotificationAction] = []
    await conn.subscribe_notification_actions(got.append)
    await ha.push_registry_event(NOTIFY_ACTION_EVENT, data)
    await ha.push_registry_event(NOTIFY_ACTION_EVENT, {"action": "ok"})
    await wait_until(lambda: bool(got))
    assert got == [NotificationAction("ok")]


async def test_a_refused_subscription_raises_and_is_not_kept(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    ha.fail_commands["subscribe_events"] = ("unauthorized", "Unauthorized")
    with pytest.raises(CommandError) as err:
        await conn.subscribe_notification_actions(lambda _: None)
    assert err.value.code == "unauthorized"


async def test_the_subscription_is_renewed_after_a_reconnect(
    ha: FakeHA,
) -> None:
    clock = FakeClock()
    connection = WebSocketConnection(
        DirectTransport.from_base_url(ha.base_url, TOKEN),
        clock=clock.monotonic,
        sleep=clock.sleep,
        rng=lambda: 0.5,
        heartbeat_interval=777.0,
        resync_interval=555.0,
        command_timeout=888.0,
        connect_timeout=999.0,
    )
    await asyncio.wait_for(connection.connect(), 5)
    got: list[NotificationAction] = []
    try:
        await connection.subscribe_notification_actions(got.append)
        await ha.drop_connections()
        await wait_until(lambda: connection.state is ConnectionState.RECONNECTING)
        await clock.advance(1.0)  # back-off 1 s with zero jitter
        await wait_until(lambda: connection.state is ConnectionState.CONNECTED)
        await wait_until(lambda: bool(ha.event_subscriptions))
        await ha.push_registry_event(NOTIFY_ACTION_EVENT, {"action": "again"})
        await wait_until(lambda: bool(got))
        assert got == [NotificationAction("again")]
    finally:
        await connection.close()
