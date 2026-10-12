# SPDX-License-Identifier: Apache-2.0
"""WebSocket client against the fake HA server (real sockets on localhost, fake clock)."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog
import structlog.testing

from suncore.ha import websocket as ws_module
from suncore.ha.cache import EntityCache
from suncore.ha.connection import (
    AuthError,
    CommandError,
    CommandTimeoutError,
    ConnectionState,
    EntityState,
    EntityUpdate,
    HAConnection,
    NotConnectedError,
    RateLimiter,
    ServiceCallerProvider,
)
from suncore.ha.redact import RedactingFilter, SecretRegistry, redact_processor
from suncore.ha.transport import DirectTransport
from suncore.ha.websocket import Backoff, WebSocketConnection
from tests.ha.fakes import TOKEN, FakeClock, FakeHA, compress, wait_until

# Timer values that identify non-back-off sleeps in FakeClock.requested.
HB_INTERVAL, HB_TIMEOUT, RESYNC, COMMAND, CONNECT = 777.0, 666.0, 555.0, 888.0, 999.0
OTHER_TIMERS = {HB_INTERVAL, HB_TIMEOUT, RESYNC, COMMAND, CONNECT}


@pytest.fixture
async def ha() -> AsyncIterator[FakeHA]:
    server = FakeHA()
    await server.start()
    server.set_state("sensor.a", "100", {"unit_of_measurement": "W"}, lc=1000.0)
    server.set_state("sensor.b", "55", {"unit_of_measurement": "%"}, lc=1000.0)
    server.set_state("sensor.other", "1", {}, lc=1000.0)
    yield server
    await server.stop()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


Make = Callable[..., WebSocketConnection]


@pytest.fixture
async def make(ha: FakeHA, clock: FakeClock) -> AsyncIterator[Make]:
    created: list[WebSocketConnection] = []

    def factory(token: str = TOKEN, url: str | None = None, **kw: Any) -> WebSocketConnection:
        transport = DirectTransport.from_base_url(url or ha.base_url, token)
        options: dict[str, Any] = {
            "clock": clock.monotonic,
            "sleep": clock.sleep,
            "rng": lambda: 0.5,  # zero jitter
            "heartbeat_interval": HB_INTERVAL,
            "heartbeat_timeout": HB_TIMEOUT,
            "resync_interval": RESYNC,
            "command_timeout": COMMAND,
            "connect_timeout": CONNECT,
        }
        options.update(kw)
        conn = WebSocketConnection(transport, **options)
        created.append(conn)
        return conn

    yield factory
    for conn in created:
        await conn.close()


async def up(conn: WebSocketConnection) -> None:
    await asyncio.wait_for(conn.connect(), 5)


def record(conn: WebSocketConnection) -> list[EntityUpdate]:
    updates: list[EntityUpdate] = []
    conn.add_entity_listener(updates.append)
    return updates


def backoff_delays(clock: FakeClock) -> list[float]:
    return [d for d in clock.requested if d not in OTHER_TIMERS]


def test_implements_protocols(make: Make) -> None:
    conn = make()
    assert isinstance(conn, HAConnection)
    assert isinstance(conn, ServiceCallerProvider)


# --- handshake and subscription -------------------------------------------------


async def test_handshake_subscribe_and_initial_snapshot(ha: FakeHA, make: Make) -> None:
    conn = make()
    states: list[ConnectionState] = []
    conn.add_state_listener(states.append)
    updates = record(conn)
    await conn.subscribe_entities(["sensor.b", "sensor.a", "sensor.a"])  # before connect
    assert conn.state is ConnectionState.DISCONNECTED
    await up(conn)

    assert conn.state is ConnectionState.CONNECTED
    assert states == [ConnectionState.CONNECTING, ConnectionState.CONNECTED]
    assert conn.ha_version == "2026.11.0"
    kinds = [(m["id"], m["type"]) for m in ha.received]
    assert kinds == [(1, "supported_features"), (2, "subscribe_entities"), (3, "get_states")]
    assert ha.received[0]["features"] == {"coalesce_messages": 1}
    assert ha.received[1]["entity_ids"] == ["sensor.a", "sensor.b"]
    first = updates[0]
    assert first.full
    assert set(first.changed) == {"sensor.a", "sensor.b"}
    assert first.changed["sensor.a"].state == "100"
    assert first.changed["sensor.a"].last_changed == 1000.0
    assert first.changed["sensor.a"].last_updated == 1000.0  # lu omitted by HA when equal
    # resync snapshot is restricted to the subscription and carries last_reported
    assert set(updates[-1].changed) == {"sensor.a", "sensor.b"}
    assert updates[-1].changed["sensor.a"].last_reported == 1000.0


async def test_message_ids_strictly_increase(ha: FakeHA, make: Make) -> None:
    conn = make()
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    await conn.get_config()
    await conn.get_states()
    ids = [m["id"] for m in ha.received]
    assert ids == sorted(set(ids))
    assert ids[0] == 1


async def test_subscribe_while_connected_replaces_subscription(ha: FakeHA, make: Make) -> None:
    conn = make()
    updates = record(conn)
    await up(conn)
    assert not [m for m in ha.received if m["type"] == "subscribe_entities"]
    await conn.subscribe_entities(["sensor.a"])
    await conn.subscribe_entities(["sensor.b"])
    types = [m["type"] for m in ha.received]
    assert types.count("subscribe_entities") == 2
    assert "unsubscribe_events" in types
    unsub = next(m for m in ha.received if m["type"] == "unsubscribe_events")
    first_sub = next(m for m in ha.received if m["type"] == "subscribe_entities")
    assert unsub["subscription"] == first_sub["id"]
    assert set(updates[-1].changed) == {"sensor.b"}
    await conn.subscribe_entities([])
    assert updates[-1].full
    assert not updates[-1].changed


async def test_coalesced_messages_are_unpacked(ha: FakeHA, make: Make) -> None:
    ha.coalesce_replies = True
    conn = make()
    updates = record(conn)
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    assert updates[0].changed["sensor.a"].state == "100"


async def test_supported_features_failure_is_tolerated(ha: FakeHA, make: Make) -> None:
    ha.fail_commands["supported_features"] = ("unknown_command", "nope")
    conn = make()
    await up(conn)
    assert conn.state is ConnectionState.CONNECTED


# --- compressed format ---------------------------------------------------------------


async def test_compressed_diffs(ha: FakeHA, make: Make) -> None:
    conn = make()
    updates = record(conn)
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    base = len(updates)

    await ha.change("sensor.a", state="200", attributes={"friendly_name": "A"}, lc=1010.0)
    await wait_until(lambda: len(updates) == base + 1)
    s = updates[-1].changed["sensor.a"]
    assert not updates[-1].full
    assert (s.state, s.last_changed, s.last_updated) == ("200", 1010.0, 1010.0)
    assert s.attributes == {"unit_of_measurement": "W", "friendly_name": "A"}

    # attribute-only change: only lu moves, state/lc are inherited
    await ha.change("sensor.a", attributes={"x": 1}, lu=1020.0)
    await wait_until(lambda: len(updates) == base + 2)
    s = updates[-1].changed["sensor.a"]
    assert (s.state, s.last_changed, s.last_updated) == ("200", 1010.0, 1020.0)
    assert s.attributes["x"] == 1

    # removed attribute keys
    await ha.change("sensor.a", remove_attributes=["x", "friendly_name"])
    await wait_until(lambda: len(updates) == base + 3)
    assert updates[-1].changed["sensor.a"].attributes == {"unit_of_measurement": "W"}

    # a: add, r: remove
    await ha.push_event({"a": {"sensor.new": compress({"state": "7", "attributes": {}})}})
    await wait_until(lambda: len(updates) == base + 4)
    assert updates[-1].changed["sensor.new"].state == "7"
    await ha.push_event({"r": ["sensor.new"]})
    await wait_until(lambda: len(updates) == base + 5)
    assert updates[-1].removed == ("sensor.new",)
    assert not updates[-1].changed


async def test_diff_for_unknown_entity_triggers_get_states_resync(ha: FakeHA, make: Make) -> None:
    conn = make()
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    before = [m["type"] for m in ha.received].count("get_states")
    await ha.push_event({"c": {"sensor.ghost": {"+": {"s": "1"}}}})
    await wait_until(lambda: [m["type"] for m in ha.received].count("get_states") == before + 1)


@pytest.mark.parametrize(
    "event",
    [
        {"a": ["not", "a", "mapping"]},
        {"a": {"sensor.x": 5}},
        {"c": "text"},
        {"c": {"sensor.a": {"+": {"a": "not-a-mapping"}}}},
        {"c": {"sensor.a": ["diff"]}},
        {"r": [["unhashable"]]},
        {"r": "sensor.a"},
    ],
    ids=["a-list", "a-value", "c-str", "c-attrs", "c-diff-list", "r-unhashable", "r-str"],
)
async def test_malformed_entity_event_is_ignored_and_resynced(
    ha: FakeHA, make: Make, event: dict[str, Any]
) -> None:
    conn = make()
    updates = record(conn)
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    before = [m["type"] for m in ha.received].count("get_states")
    await ha.push_event(event)
    await wait_until(lambda: [m["type"] for m in ha.received].count("get_states") == before + 1)
    assert conn.state is ConnectionState.CONNECTED
    assert updates[-1].changed["sensor.a"].state == "100"  # resync restored a clean view
    await ha.change("sensor.a", state="5")  # the stream still works
    await wait_until(
        lambda: "sensor.a" in updates[-1].changed and updates[-1].changed["sensor.a"].state == "5"
    )


async def test_unexpected_exception_in_dispatch_reconnects(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    conn = make()
    states: list[ConnectionState] = []
    conn.add_state_listener(states.append)
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    original = conn._dispatch
    calls = 0

    def boom(msg: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected " + TOKEN)
        original(msg)

    conn._dispatch = boom  # type: ignore[method-assign]
    with structlog.testing.capture_logs() as logs:
        await ha.push_event({"c": {"sensor.a": {"+": {"s": "1"}}}})
        await wait_until(lambda: conn.state is ConnectionState.RECONNECTING)
    crashed = [e for e in logs if e["event"] == "ha.connection_crashed"]
    assert [e["reason"] for e in crashed] == ["RuntimeError"]
    assert TOKEN not in repr(crashed).replace("injected " + TOKEN, "")  # message text not logged
    await clock.advance(1.0)
    await wait_until(lambda: conn.state is ConnectionState.CONNECTED)
    assert states[-3:] == [
        ConnectionState.RECONNECTING,
        ConnectionState.CONNECTING,
        ConnectionState.CONNECTED,
    ]
    assert ha.connection_count == 2


# --- commands ------------------------------------------------------------------------


async def test_get_states_and_config(ha: FakeHA, make: Make) -> None:
    conn = make()
    await up(conn)
    states = {s.entity_id: s for s in await conn.get_states()}
    assert states["sensor.a"] == EntityState(
        "sensor.a",
        "100",
        {"unit_of_measurement": "W"},
        last_changed=1000.0,
        last_updated=1000.0,
        last_reported=1000.0,
    )
    assert (await conn.get_config())["version"] == "2026.11.0"


async def test_error_result_raises_command_error(ha: FakeHA, make: Make) -> None:
    ha.fail_commands["get_config"] = ("unauthorized", "Unauthorized")
    conn = make()
    await up(conn)
    with pytest.raises(CommandError) as info:
        await conn.get_config()
    assert info.value.code == "unauthorized"


async def test_command_without_connection(make: Make) -> None:
    conn = make()
    with pytest.raises(NotConnectedError):
        await conn.get_states()


# --- auth -----------------------------------------------------------------------------


async def test_auth_invalid_is_typed_and_never_retried(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    conn = make(token="wrong-token-value")
    with pytest.raises(AuthError):
        await up(conn)
    assert conn.state is ConnectionState.AUTH_FAILED
    await clock.advance(3600)
    await asyncio.sleep(0.05)
    assert ha.auth_attempts == 1
    assert ha.connection_count == 1
    assert backoff_delays(clock) == []


# --- reconnect, resync, back-off ------------------------------------------------------------


async def test_reconnect_resubscribes_with_full_resync(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    conn = make()
    updates = record(conn)
    states: list[ConnectionState] = []
    conn.add_state_listener(states.append)
    await conn.subscribe_entities(["sensor.a", "sensor.b"])
    await up(conn)
    updates.clear()

    # change the world while the link is down
    await ha.drop_connections()
    await wait_until(lambda: conn.state is ConnectionState.RECONNECTING)
    del ha.states["sensor.b"]
    ha.states["sensor.a"]["state"] = "999"
    ha.received.clear()

    await clock.advance(1.0)  # back-off 1 s with zero jitter
    await wait_until(lambda: conn.state is ConnectionState.CONNECTED)

    assert states[-3:] == [
        ConnectionState.RECONNECTING,
        ConnectionState.CONNECTING,
        ConnectionState.CONNECTED,
    ]
    assert ha.connection_count == 2
    kinds = [(m["id"], m["type"]) for m in ha.received]
    assert kinds[:3] == [(1, "supported_features"), (2, "subscribe_entities"), (3, "get_states")]
    full = updates[0]
    assert full.full
    assert set(full.changed) == {"sensor.a"}  # b vanished: not in the snapshot
    assert full.changed["sensor.a"].state == "999"


async def test_cache_follows_reconnect(ha: FakeHA, make: Make, clock: FakeClock) -> None:
    conn = make()
    cache = EntityCache(clock=clock.wall, max_age_s=100)
    cache.attach(conn)
    await conn.subscribe_entities(["sensor.a", "sensor.b"])
    await up(conn)
    assert len(cache) == 2
    await ha.drop_connections()
    await wait_until(lambda: conn.state is ConnectionState.RECONNECTING)
    del ha.states["sensor.b"]
    await clock.advance(1.0)
    await wait_until(lambda: conn.state is ConnectionState.CONNECTED)
    assert "sensor.b" not in cache
    assert "sensor.a" in cache


async def test_backoff_sequence_when_server_is_down(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    url = ha.base_url
    await ha.stop()
    conn = make(url=url)
    task = asyncio.create_task(conn.connect())
    expected = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    for i, delay in enumerate(expected):
        await wait_until(lambda i=i: len(backoff_delays(clock)) > i)
        assert backoff_delays(clock)[i] == delay
        assert conn.state is ConnectionState.RECONNECTING
        await clock.advance(delay)
    await wait_until(lambda: len(backoff_delays(clock)) > len(expected))
    assert not task.done()  # connect() keeps waiting for the first successful connection
    await conn.close()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_backoff_resets_only_after_a_stable_connection(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    conn = make(stable_after=31.0)
    await up(conn)

    async def drop_and_recover() -> None:
        before = ha.connection_count
        await ha.drop_connections()
        await wait_until(lambda: len(backoff_delays(clock)) > len(seen))
        seen.append(backoff_delays(clock)[-1])
        await clock.advance(seen[-1])
        await wait_until(lambda: conn.state is ConnectionState.CONNECTED)
        assert ha.connection_count == before + 1

    seen: list[float] = []
    await drop_and_recover()  # 1 s, connection was up 0 s -> no reset
    await drop_and_recover()  # 2 s
    await clock.advance(31.0)  # stable now
    await drop_and_recover()  # reset -> 1 s again
    assert seen == [1.0, 2.0, 1.0]


def test_backoff_unit() -> None:
    values = iter([0.0, 1.0, 0.5, 0.5, 0.5])
    b = Backoff(initial=1.0, maximum=60.0, jitter=0.25, rng=lambda: next(values))
    assert b.next_delay() == pytest.approx(0.75)  # 1 * (1 - 0.25)
    assert b.next_delay() == pytest.approx(2.5)  # 2 * (1 + 0.25)
    assert b.next_delay() == pytest.approx(4.0)
    b.reset()
    assert b.next_delay() == pytest.approx(1.0)
    capped = Backoff(rng=lambda: 1.0)
    assert max(capped.next_delay() for _ in range(100)) == 60.0


# --- heartbeat ------------------------------------------------------------------------------


async def test_heartbeat_pong_keeps_connection(ha: FakeHA, make: Make, clock: FakeClock) -> None:
    conn = make(heartbeat_interval=30.0, heartbeat_timeout=10.0)
    await up(conn)
    await clock.advance(30.0)
    await wait_until(lambda: any(m["type"] == "ping" for m in ha.received))
    await asyncio.sleep(0.05)
    await clock.advance(10.0)
    assert conn.state is ConnectionState.CONNECTED
    assert ha.connection_count == 1


async def test_missing_pong_forces_reconnect(ha: FakeHA, make: Make, clock: FakeClock) -> None:
    ha.respond_pong = False
    conn = make(heartbeat_interval=30.0, heartbeat_timeout=10.0)
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    await clock.advance(30.0)
    await wait_until(lambda: any(m["type"] == "ping" for m in ha.received))
    await clock.advance(10.0)  # pong timeout
    await wait_until(lambda: conn.state is ConnectionState.RECONNECTING)
    ha.respond_pong = True
    await clock.advance(1.0)
    await wait_until(lambda: conn.state is ConnectionState.CONNECTED)
    assert ha.connection_count == 2


# --- periodic resync and freshness ------------------------------------------------------------


async def test_periodic_resync_keeps_constant_sensor_fresh(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    iso = lambda s: datetime.fromtimestamp(s, UTC).isoformat()  # noqa: E731
    ha.states["sensor.a"]["last_reported"] = iso(1000.0)
    ha.states["sensor.b"]["last_reported"] = iso(1000.0)
    conn = make(resync_interval=60.0)
    cache = EntityCache(clock=clock.wall, max_age_s=100)
    cache.attach(conn)
    await conn.subscribe_entities(["sensor.a", "sensor.b"])
    await up(conn)
    for step in range(1, 4):  # 180 s: a keeps reporting the same value, b is dead
        ha.states["sensor.a"]["last_reported"] = iso(1000.0 + step * 60)
        await clock.advance(60.0)
        await wait_until(
            lambda step=step: [m["type"] for m in ha.received].count("get_states") == step + 1
        )
        await wait_until(
            lambda step=step: (
                (e := cache.get("sensor.a")) is not None
                and e.state.last_reported == 1000.0 + step * 60
            )
        )
    assert not cache.is_stale("sensor.a")
    assert cache.is_stale("sensor.b")


async def test_diff_then_identical_resync_does_not_refresh(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    """Stream states mirror HA (last_reported = last_updated) so resyncs compare equal."""
    iso = lambda s: datetime.fromtimestamp(s, UTC).isoformat()  # noqa: E731
    ha.states["sensor.a"]["last_reported"] = iso(1000.0)
    conn = make(resync_interval=60.0)
    cache = EntityCache(clock=clock.wall, max_age_s=100)
    cache.attach(conn)
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    await clock.advance(30.0)
    # HA: new state at 2000 -> last_changed = last_updated = last_reported = 2000
    ha.states["sensor.a"].update(lc=2000.0, last_reported=iso(2000.0))
    await ha.change("sensor.a", state="150", lc=2000.0)
    await wait_until(lambda: (e := cache.get("sensor.a")) is not None and e.state.state == "150")
    entry = cache.get("sensor.a")
    assert entry is not None
    assert entry.state.last_reported == 2000.0
    refreshed = entry.refreshed_at
    assert refreshed == clock.wall()

    await clock.advance(30.0)  # t=60: periodic resync with the identical last_reported
    await wait_until(lambda: [m["type"] for m in ha.received].count("get_states") == 2)
    await wait_until(lambda: (e := cache.get("sensor.a")) is not None and e.state.state == "150")
    again = cache.get("sensor.a")
    assert again is not None
    assert again.refreshed_at == refreshed
    assert not cache.is_stale("sensor.a")

    await clock.advance(60.0)  # t=120: age 90 s, another identical resync
    await wait_until(lambda: [m["type"] for m in ha.received].count("get_states") == 3)
    assert cache.get("sensor.a").refreshed_at == refreshed  # type: ignore[union-attr]
    assert not cache.is_stale("sensor.a")
    await clock.advance(11.0)  # age 101 s > max_age 100 s
    assert cache.is_stale("sensor.a")


# --- subscribe timeout ----------------------------------------------------------------------


async def test_late_initial_event_is_a_command_timeout_and_keeps_the_sub_id(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    conn = make()
    await up(conn)
    ha.withhold_initial_event = True
    task = asyncio.create_task(conn.subscribe_entities(["sensor.a"]))
    await wait_until(lambda: any(m["type"] == "subscribe_entities" for m in ha.received))
    stuck_id = next(m["id"] for m in ha.received if m["type"] == "subscribe_entities")
    await wait_until(lambda: COMMAND in clock.requested)
    await clock.advance(COMMAND)
    with pytest.raises(CommandTimeoutError):
        await asyncio.wait_for(task, 5)
    assert conn.state is ConnectionState.CONNECTED

    ha.withhold_initial_event = False
    ha.received.clear()
    await conn.subscribe_entities(["sensor.a"])
    unsub = [m for m in ha.received if m["type"] == "unsubscribe_events"]
    assert [m["subscription"] for m in unsub] == [stuck_id]


# --- shutdown ---------------------------------------------------------------------------------


async def test_clean_shutdown(ha: FakeHA, make: Make) -> None:
    conn = make()
    await conn.subscribe_entities(["sensor.a"])
    await up(conn)
    await conn.close()
    assert conn.state is ConnectionState.CLOSED
    await wait_until(lambda: not ha.connections)
    leftover = [t for t in asyncio.all_tasks() if t.get_name().startswith("suncore-ha")]
    assert not leftover
    await conn.close()  # idempotent


async def test_cancelled_connect_closes(ha: FakeHA, make: Make) -> None:
    url = ha.base_url
    await ha.stop()
    conn = make(url=url)
    task = asyncio.create_task(conn.connect())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert conn.state is ConnectionState.CLOSED


# --- service capability -------------------------------------------------------------------------


async def test_service_caller_rate_limits_and_reads_back(
    ha: FakeHA, make: Make, clock: FakeClock
) -> None:
    conn = make(write_interval=1.5)
    await up(conn)
    caller = conn.service_caller()
    first = await caller.call_service(
        "switch",
        "turn_on",
        target={"entity_id": "switch.x"},
        service_data={"a": 1},
        readback_entity_id="sensor.a",
    )
    sent = next(m for m in ha.received if m["type"] == "call_service")
    assert (sent["domain"], sent["service"]) == ("switch", "turn_on")
    assert sent["target"] == {"entity_id": "switch.x"}
    assert sent["service_data"] == {"a": 1}
    assert "return_response" not in sent
    assert first.readback is not None
    assert first.readback.state == "100"
    assert first.response["echo"]["domain"] == "switch"

    second = asyncio.create_task(caller.call_service("switch", "turn_off", return_response=True))
    await wait_until(lambda: 1.5 in clock.requested)  # limiter waits for the interval
    assert sum(m["type"] == "call_service" for m in ha.received) == 1
    await clock.advance(1.5)
    result = await asyncio.wait_for(second, 5)
    assert result.readback is None
    assert sum(m["type"] == "call_service" for m in ha.received) == 2
    assert ha.received[-1]["return_response"] is True


async def test_rate_limiter_spacing() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock.monotonic, sleep=clock.sleep)
    await limiter.acquire()  # first call passes immediately
    assert clock.requested == []
    task = asyncio.create_task(limiter.acquire())
    await clock.settle()
    assert clock.requested == [2.0]
    await clock.advance(2.0)
    await task
    with pytest.raises(ValueError, match="min_interval"):
        RateLimiter(-1)


# --- token never reaches the logs ---------------------------------------------------------


@pytest.fixture
def captured_logs() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    structlog.configure(
        processors=[redact_processor, structlog.processors.KeyValueRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=stream),
    )
    yield stream
    structlog.reset_defaults()


async def test_token_never_appears_in_logs(
    ha: FakeHA, make: Make, clock: FakeClock, captured_logs: io.StringIO
) -> None:
    stdlib = io.StringIO()
    handler = logging.StreamHandler(stdlib)
    registry = SecretRegistry()
    registry.register(TOKEN)
    handler.addFilter(RedactingFilter(registry))
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        bad = make(token="wrong-token-value")
        with pytest.raises(AuthError):
            await up(bad)
        good = make()
        await good.subscribe_entities(["sensor.a"])
        await up(good)
        await ha.drop_connections()
        await wait_until(lambda: good.state is ConnectionState.RECONNECTING)
        # the connector's own logging must stay clean even when a secret is injected
        ws_module.log.warning("probe", detail=f"Bearer {TOKEN}", access_token=TOKEN)
        logging.getLogger("aiohttp.client").error("probe %s", TOKEN)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    out = captured_logs.getvalue()
    assert "ha.auth_failed" in out
    assert "probe" in out
    for text in (out, stdlib.getvalue()):
        assert TOKEN not in text
        assert "wrong-token-value" not in text
    assert "probe" in stdlib.getvalue()
