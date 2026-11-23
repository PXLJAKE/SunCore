# SPDX-License-Identifier: Apache-2.0
"""Registry, Energy dashboard, registry events and read-only action commands on the wire."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from suncore.core.actions import READ_ONLY_ACTIONS
from suncore.ha.connection import (
    REGISTRY_EVENT_TYPES,
    ActionNotAllowedError,
    CommandError,
    ProtocolError,
)
from suncore.ha.transport import DirectTransport
from suncore.ha.websocket import WebSocketConnection
from tests.ha.fakes import TOKEN, FakeClock, FakeHA, wait_until


@pytest.fixture
async def ha() -> AsyncIterator[FakeHA]:
    server = FakeHA()
    await server.start()
    server.entity_registry = {
        "entity_categories": {"0": "config", "1": "diagnostic"},
        "entities": [{"ei": "sensor.a", "pl": "demo", "di": "d1", "ec": 1}],
    }
    server.device_registry = [{"id": "d1", "name": "Dev", "config_entry_id": "e1"}]
    server.area_registry = [{"area_id": "a1", "name": "Kitchen"}]
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


async def test_registry_list_commands(conn: WebSocketConnection, ha: FakeHA) -> None:
    display = await conn.entity_registry_list_for_display()
    assert display["entities"][0]["ei"] == "sensor.a"
    assert (await conn.device_registry_list())[0]["id"] == "d1"
    assert (await conn.area_registry_list())[0]["area_id"] == "a1"
    types = [m["type"] for m in ha.received]
    assert "config/entity_registry/list_for_display" in types
    assert "config/device_registry/list" in types
    assert "config/area_registry/list" in types


async def test_registry_command_rejects_wrong_shapes(conn: WebSocketConnection, ha: FakeHA) -> None:
    ha.entity_registry = ["not", "an", "object"]  # type: ignore[assignment]
    with pytest.raises(ProtocolError):
        await conn.entity_registry_list_for_display()
    ha.device_registry = {"nope": 1}  # type: ignore[assignment]
    with pytest.raises(ProtocolError):
        await conn.device_registry_list()
    ha.device_registry = [{"id": "ok"}, "junk"]  # type: ignore[list-item]
    assert await conn.device_registry_list() == [{"id": "ok"}]


async def test_registry_command_error_propagates(conn: WebSocketConnection, ha: FakeHA) -> None:
    ha.fail_commands["config/device_registry/list"] = ("unauthorized", "no")
    with pytest.raises(CommandError) as err:
        await conn.device_registry_list()
    assert err.value.code == "unauthorized"


async def test_energy_prefs_none_when_not_configured(conn: WebSocketConnection, ha: FakeHA) -> None:
    assert ha.energy_prefs is None
    assert await conn.energy_get_prefs() is None  # not_found "No prefs"
    ha.energy_prefs = {"energy_sources": [], "device_consumption": []}
    assert await conn.energy_get_prefs() == {"energy_sources": [], "device_consumption": []}


async def test_energy_prefs_other_errors_raise(conn: WebSocketConnection, ha: FakeHA) -> None:
    ha.fail_commands["energy/get_prefs"] = ("unknown_command", "Unknown command.")
    with pytest.raises(CommandError) as err:
        await conn.energy_get_prefs()
    assert err.value.code == "unknown_command"


async def test_energy_prefs_non_object_is_a_protocol_error(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    ha.energy_prefs = []  # type: ignore[assignment]
    with pytest.raises(ProtocolError):
        await conn.energy_get_prefs()


async def test_registry_events_are_delivered_and_resubscribed(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    seen: list[str] = []
    remove = await conn.subscribe_registry_updates(seen.append)
    subs = [m for m in ha.received if m["type"] == "subscribe_events"]
    assert [m["event_type"] for m in subs] == list(REGISTRY_EVENT_TYPES)

    await ha.push_registry_event("area_registry_updated", {"action": "create", "area_id": "x"})
    await wait_until(lambda: seen == ["area_registry_updated"])
    await ha.push_registry_event("entity_registry_updated")
    await wait_until(lambda: seen[-1] == "entity_registry_updated")

    remove()
    await ha.push_registry_event("device_registry_updated")
    await asyncio.sleep(0.05)
    assert "device_registry_updated" not in seen

    # A second subscriber does not subscribe again on the wire.
    count = len(subs)
    await conn.subscribe_registry_updates(lambda _t: None)
    assert len([m for m in ha.received if m["type"] == "subscribe_events"]) == count


async def test_registry_events_survive_a_reconnect(
    conn: WebSocketConnection, ha: FakeHA, clock: FakeClock
) -> None:
    seen: list[str] = []
    await conn.subscribe_registry_updates(seen.append)
    before = ha.connection_count
    await ha.drop_connections()
    await wait_until(lambda: 1.0 in clock.requested)  # back-off sleep is pending
    await clock.advance(1.0)
    await wait_until(lambda: ha.connection_count > before and len(ha.event_subscriptions) == 1)
    await ha.push_registry_event("entity_registry_updated")
    await wait_until(lambda: seen == ["entity_registry_updated"])


async def test_subscribe_failure_is_tolerated(ha: FakeHA) -> None:
    ha.fail_commands["subscribe_events"] = ("unauthorized", "no")
    clock = FakeClock()
    conn = WebSocketConnection(
        DirectTransport.from_base_url(ha.base_url, TOKEN),
        clock=clock.monotonic,
        sleep=clock.sleep,
        heartbeat_interval=777.0,
        resync_interval=555.0,
    )
    try:
        await conn.subscribe_registry_updates(lambda _t: None)
        await asyncio.wait_for(conn.connect(), 5)
        assert conn.state.value == "connected"
    finally:
        await conn.close()


async def test_call_action_response_uses_return_response(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    ha.service_response = lambda msg: {"prices": {"Home": []}, "seen": msg["service"]}
    out = await conn.call_action_response(
        "tibber", "get_prices", {"start": "x"}, target={"entity_id": "sensor.t"}
    )
    assert out == {"prices": {"Home": []}, "seen": "get_prices"}
    sent = next(m for m in ha.received if m["type"] == "call_service")
    assert sent["return_response"] is True
    assert sent["service_data"] == {"start": "x"}
    assert sent["target"] == {"entity_id": "sensor.t"}


@pytest.mark.parametrize(
    ("domain", "service"),
    [
        ("switch", "turn_on"),
        ("tibber", "set_price"),
        ("light", "turn_off"),
        ("", ""),
        # near-misses of the allowlisted `tibber.get_prices`: exact match only
        ("Tibber", "get_prices"),
        ("tibber ", "get_prices"),
        ("tibber", "get_prices\n"),
        ("tibber.get_prices", ""),
        ("", "tibber.get_prices"),
        ("tibber", "get_prices.extra"),
    ],
)
async def test_call_action_response_refuses_everything_off_the_allowlist(
    conn: WebSocketConnection, ha: FakeHA, domain: str, service: str
) -> None:
    with pytest.raises(ActionNotAllowedError):
        await conn.call_action_response(domain, service, {})
    assert not [m for m in ha.received if m["type"] == "call_service"]


async def test_call_action_response_needs_an_object_response(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    ha.service_response = lambda _msg: None
    with pytest.raises(ProtocolError):
        await conn.call_action_response("weather", "get_forecasts", {})


def test_allowlist_is_exactly_the_verified_read_only_set() -> None:
    """Adding an action needs a PR that verifies it against the integration source."""
    assert (
        frozenset(
            {
                "tibber.get_prices",
                "nordpool.get_prices_for_date",
                "solcast_solar.query_forecast_data",
                "weather.get_forecasts",
                "workday.check_date",
            }
        )
        == READ_ONLY_ACTIONS
    )
    assert all("." in a and a == a.lower() for a in READ_ONLY_ACTIONS)
