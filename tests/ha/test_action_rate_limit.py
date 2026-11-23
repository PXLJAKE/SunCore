# SPDX-License-Identifier: Apache-2.0
"""Rate limit of ``call_action_response`` per (domain, service) (Phase 4 follow-up A3)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from suncore.ha.connection import (
    DEFAULT_ACTION_INTERVAL_S,
    ERR_ACTION_RATE_LIMITED,
    ActionNotAllowedError,
    ActionRateLimitedError,
    ActionRateLimiter,
)
from suncore.ha.transport import DirectTransport
from suncore.ha.websocket import WebSocketConnection
from tests.ha.fakes import TOKEN, FakeClock, FakeHA


def _limiter(clock: FakeClock, interval: float = 30.0) -> ActionRateLimiter:
    return ActionRateLimiter(interval, clock=clock.monotonic, sleep=clock.sleep)


# --- the limiter ---------------------------------------------------------------------


def test_default_interval_is_30_seconds() -> None:
    assert DEFAULT_ACTION_INTERVAL_S == 30.0


def test_try_acquire_admits_once_per_interval() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    assert limiter.try_acquire("tibber.get_prices")
    assert not limiter.try_acquire("tibber.get_prices")
    assert limiter.retry_after("tibber.get_prices") == 30.0
    clock.now = 29.9
    assert not limiter.try_acquire("tibber.get_prices")
    clock.now = 30.0
    assert limiter.try_acquire("tibber.get_prices")
    assert limiter.retry_after("tibber.get_prices") == 30.0


def test_a_refused_try_does_not_move_the_slot() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    assert limiter.try_acquire("a.b")
    clock.now = 20.0
    assert not limiter.try_acquire("a.b")
    assert limiter.retry_after("a.b") == 10.0


def test_keys_are_independent() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    assert limiter.try_acquire("tibber.get_prices")
    assert limiter.try_acquire("weather.get_forecasts")
    assert not limiter.try_acquire("tibber.get_prices")
    assert limiter.retry_after("never.used") == 0.0


def test_a_caller_supplied_interval_applies_to_the_next_slot() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    assert limiter.try_acquire("a.b", 5.0)
    assert limiter.retry_after("a.b") == 5.0
    clock.now = 5.0
    assert limiter.try_acquire("a.b", 0.0)
    assert limiter.try_acquire("a.b", 0.0)  # interval 0: never limited


def test_negative_intervals_are_rejected() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError, match="default_interval_s"):
        ActionRateLimiter(-1.0, clock=clock.monotonic, sleep=clock.sleep)
    with pytest.raises(ValueError, match="interval_s"):
        _limiter(clock).try_acquire("a.b", -1.0)


async def test_acquire_without_deadline_is_a_try() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    assert await limiter.acquire("a.b")
    assert not await limiter.acquire("a.b")
    assert clock.requested == []  # never slept


async def test_acquire_waits_for_a_slot_within_the_deadline() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    assert await limiter.acquire("a.b")
    clock.now = 10.0
    task = asyncio.create_task(limiter.acquire("a.b", max_wait_s=25.0))
    await clock.settle()
    assert clock.requested == [20.0]
    assert not task.done()
    await clock.advance(20.0)
    assert await task is True
    assert limiter.retry_after("a.b") == 30.0


async def test_acquire_refuses_when_the_slot_is_beyond_the_deadline() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    assert await limiter.acquire("a.b")
    assert not await limiter.acquire("a.b", max_wait_s=10.0)
    assert clock.requested == []
    assert limiter.retry_after("a.b") == 30.0  # nothing was reserved by the refusal


async def test_concurrent_waiters_get_consecutive_slots() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, 10.0)
    assert await limiter.acquire("a.b")
    first = asyncio.create_task(limiter.acquire("a.b", max_wait_s=100.0))
    second = asyncio.create_task(limiter.acquire("a.b", max_wait_s=100.0))
    third = asyncio.create_task(limiter.acquire("a.b", max_wait_s=15.0))
    await clock.settle()
    assert sorted(clock.requested) == [10.0, 20.0]  # two slots reserved in order
    assert await third is False  # its slot (30 s) is beyond its 15 s deadline
    await clock.advance(20.0)
    assert await first is True
    assert await second is True


# --- on the connection ---------------------------------------------------------------


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


def _calls(ha: FakeHA) -> int:
    return len([m for m in ha.received if m["type"] == "call_service"])


async def test_second_call_within_the_interval_is_refused_not_sent(
    conn: WebSocketConnection, ha: FakeHA, clock: FakeClock
) -> None:
    ha.service_response = lambda _m: {"ok": True}
    assert await conn.call_action_response("tibber", "get_prices") == {"ok": True}
    with pytest.raises(ActionRateLimitedError) as info:
        await conn.call_action_response("tibber", "get_prices")
    assert info.value.code == "rate_limited"
    assert info.value.action == "tibber.get_prices"
    assert info.value.retry_after_s == pytest.approx(30.0)
    assert _calls(ha) == 1
    clock.now += 30.0
    assert await conn.call_action_response("tibber", "get_prices") == {"ok": True}
    assert _calls(ha) == 2


async def test_other_actions_are_not_affected(conn: WebSocketConnection, ha: FakeHA) -> None:
    ha.service_response = lambda _m: {}
    await conn.call_action_response("tibber", "get_prices")
    await conn.call_action_response("weather", "get_forecasts")
    assert _calls(ha) == 2


async def test_configured_interval_and_deadline(
    conn: WebSocketConnection, ha: FakeHA, clock: FakeClock
) -> None:
    ha.service_response = lambda _m: {}
    await conn.call_action_response("tibber", "get_prices", min_interval_s=5.0)
    with pytest.raises(ActionRateLimitedError) as info:
        await conn.call_action_response("tibber", "get_prices", min_interval_s=5.0)
    assert info.value.retry_after_s == pytest.approx(5.0)
    waiter = asyncio.create_task(
        conn.call_action_response("tibber", "get_prices", min_interval_s=5.0, max_wait_s=6.0)
    )
    await clock.settle()
    await clock.advance(5.0)
    assert await waiter == {}
    assert _calls(ha) == 2


async def test_not_allowed_is_checked_before_the_limiter(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    with pytest.raises(ActionNotAllowedError):
        await conn.call_action_response("switch", "turn_on")
    with pytest.raises(ActionNotAllowedError):
        await conn.call_action_response("switch", "turn_on")  # not "rate limited" either
    assert _calls(ha) == 0


def test_the_refusal_has_a_catalogue_key() -> None:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    locales = Path(__file__).resolve().parents[2] / "frontend/src/lib/i18n/locales"
    for name in ("en", "de"):
        catalogue = json.loads((locales / f"{name}.json").read_text(encoding="utf-8"))
        assert catalogue[ERR_ACTION_RATE_LIMITED]


def test_action_rate_key_default_and_sub_key() -> None:
    from suncore.ha.connection import action_rate_key  # noqa: PLC0415

    assert action_rate_key("tibber", "get_prices") == "tibber.get_prices"
    assert action_rate_key("tibber", "get_prices", None) == "tibber.get_prices"
    assert action_rate_key("tibber", "get_prices", "") == "tibber.get_prices"
    assert action_rate_key("weather", "get_forecasts", "weather.home") == (
        "weather.get_forecasts#weather.home"
    )


async def test_sub_keys_do_not_block_each_other(conn: WebSocketConnection, ha: FakeHA) -> None:
    ha.service_response = lambda _m: {}
    await conn.call_action_response("weather", "get_forecasts", rate_key="weather.home")
    await conn.call_action_response("weather", "get_forecasts", rate_key="weather.cabin")
    with pytest.raises(ActionRateLimitedError) as info:
        await conn.call_action_response("weather", "get_forecasts", rate_key="weather.home")
    assert info.value.action == "weather.get_forecasts#weather.home"
    await conn.call_action_response("weather", "get_forecasts")  # the plain key: its own slot
    with pytest.raises(ActionRateLimitedError):
        await conn.call_action_response("weather", "get_forecasts")
    assert _calls(ha) == 3


async def test_a_cancelled_waiter_keeps_its_reservation() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, 10.0)
    assert await limiter.acquire("a.b")
    waiter = asyncio.create_task(limiter.acquire("a.b", max_wait_s=100.0))
    await clock.settle()
    waiter.cancel()
    await clock.settle()
    assert limiter.retry_after("a.b") == 20.0  # its slot (10 s) + the interval stays taken


async def test_the_fake_connection_is_limited_like_the_real_one() -> None:
    from datetime import UTC, datetime  # noqa: PLC0415

    from suncore.sim.fake_ha import FakeHAConnection  # noqa: PLC0415
    from suncore.sim.scenario import load_scenario  # noqa: PLC0415

    now = [datetime(2026, 6, 21, tzinfo=UTC)]
    fake = FakeHAConnection(
        load_scenario("summer_day"), clock=lambda: now[0], autorun=False, speed=1.0
    )
    await fake.connect()
    try:
        await fake.call_action_response("tibber", "get_prices")
        with pytest.raises(ActionRateLimitedError):
            await fake.call_action_response("tibber", "get_prices")
        await fake.call_action_response("tibber", "get_prices", rate_key="other")
        now[0] = datetime(2026, 6, 21, 0, 0, 31, tzinfo=UTC)
        await fake.call_action_response("tibber", "get_prices")
    finally:
        await fake.close()


async def test_not_connected_does_not_consume_a_limiter_slot(ha: FakeHA, clock: FakeClock) -> None:
    from suncore.ha.connection import NotConnectedError  # noqa: PLC0415

    connection = WebSocketConnection(
        DirectTransport.from_base_url(ha.base_url, TOKEN),
        clock=clock.monotonic,
        sleep=clock.sleep,
    )
    with pytest.raises(NotConnectedError):
        await connection.call_action_response("tibber", "get_prices")
    assert connection._action_limiter.retry_after("tibber.get_prices") == 0.0
    with pytest.raises(NotConnectedError):  # and not "rate limited" the second time
        await connection.call_action_response("tibber", "get_prices")
