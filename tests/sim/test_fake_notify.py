# SPDX-License-Identifier: Apache-2.0
"""The simulator's notification surface behaves like the real connection (allowlist, events)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from suncore.ha.connection import (
    CommandError,
    NotConnectedError,
    NotificationAction,
    NotifyNotAllowedError,
)
from suncore.sim.fake_ha import FakeHAConnection
from suncore.sim.scenario import load_scenario


@pytest.fixture
async def conn() -> FakeHAConnection:
    connection = FakeHAConnection(
        load_scenario("summer_day"), clock=lambda: datetime.now(UTC), autorun=False
    )
    await connection.connect()
    return connection


async def test_allowed_notifications_are_recorded_and_others_refused(
    conn: FakeHAConnection,
) -> None:
    conn.set_notify_services(["mobile_app_phone"])
    await conn.notify("persistent_notification", "create", {"message": "m"})
    await conn.notify("notify", "mobile_app_phone", {"message": "m"})
    assert [(r.domain, r.service) for r in conn.notify_log] == [
        ("persistent_notification", "create"),
        ("notify", "mobile_app_phone"),
    ]
    with pytest.raises(NotifyNotAllowedError):
        await conn.notify("switch", "turn_on", {"message": "m"}, {"entity_id": ["switch.x"]})
    with pytest.raises(NotifyNotAllowedError):
        await conn.notify("notify", "other", {"message": "m"})
    assert len(conn.notify_log) == 2
    assert conn.call_log == []  # not a device call


async def test_action_events_reach_listeners_of_the_right_event_type_only(
    conn: FakeHAConnection,
) -> None:
    got: list[NotificationAction] = []
    remove = await conn.subscribe_notification_actions(got.append)
    conn.fire_notification_action("A")
    conn.fire_notification_action("B", event_type="other_event")
    remove()
    conn.fire_notification_action("C")
    assert got == [NotificationAction("A")]


async def test_a_refused_subscription_and_offline_behaviour(conn: FakeHAConnection) -> None:
    conn.action_subscribe_error = CommandError("unauthorized", "no")
    with pytest.raises(CommandError):
        await conn.subscribe_notification_actions(lambda _: None)
    await conn.close()
    with pytest.raises(NotConnectedError):
        await conn.notify("persistent_notification", "create", {"message": "m"})
