# SPDX-License-Identifier: Apache-2.0
"""The simulator's ``tibber.get_prices``: the documented response, the negative-price scenario."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from suncore.sim.demo_devices import sim_price
from suncore.sim.fake_ha import FakeHAConnection
from suncore.sim.scenario import PriceParams, load_scenario, parse_scenario

pytestmark = pytest.mark.simulator

Q = timedelta(minutes=15)


async def started(name: str, hour: int) -> FakeHAConnection:
    never = asyncio.Event()

    async def sleep(_: float) -> None:
        await never.wait()

    conn = FakeHAConnection(
        load_scenario(name),
        clock=lambda: datetime(2026, 6, 21, tzinfo=UTC),
        sleep=sleep,
        start_offset_s=hour * 3600,
    )
    await conn.connect()
    return conn


@pytest.fixture
async def morning() -> AsyncIterator[FakeHAConnection]:
    conn = await started("summer_day", 9)
    yield conn
    await conn.close()


@pytest.fixture
async def afternoon() -> AsyncIterator[FakeHAConnection]:
    conn = await started("summer_day", 14)
    yield conn
    await conn.close()


def starts(response: dict[str, object]) -> list[datetime]:
    slots = response["prices"]["Home"]  # type: ignore[index]
    return [datetime.fromisoformat(s["start_time"]) for s in slots]


async def test_default_window_is_local_today_in_15_minute_slots(
    morning: FakeHAConnection,
) -> None:
    out = await morning.call_action_response("tibber", "get_prices")
    stamps = starts(out)
    assert len(stamps) == 96
    assert stamps[0] == datetime(2026, 6, 21, 0, 0, tzinfo=stamps[0].tzinfo)
    assert {b - a for a, b in itertools.pairwise(stamps)} == {Q}
    assert stamps[0].utcoffset() == timedelta(hours=2)  # the scenario's local offset
    first = out["prices"]["Home"][0]
    assert first == {"start_time": stamps[0].isoformat(), "price": sim_price(stamps[0])}


async def test_tomorrow_exists_only_after_the_publication_hour(
    morning: FakeHAConnection, afternoon: FakeHAConnection
) -> None:
    window = {"start": "2026-06-21T00:00:00+02:00", "end": "2026-06-23T00:00:00+02:00"}
    early = starts(await morning.call_action_response("tibber", "get_prices", window))
    late = starts(await afternoon.call_action_response("tibber", "get_prices", window))
    assert len(early) == 96  # 09:00 local: tomorrow is not published yet
    assert len(late) == 192  # 14:00 local: today and tomorrow
    assert late[-1] == datetime(2026, 6, 22, 23, 45, tzinfo=late[-1].tzinfo)


async def test_start_and_end_filter_the_slots_like_the_integration(
    afternoon: FakeHAConnection,
) -> None:
    out = await afternoon.call_action_response(
        "tibber",
        "get_prices",
        {"start": "2026-06-21T10:00:00+02:00", "end": "2026-06-21T11:00:00+02:00"},
    )
    assert [s.hour for s in starts(out)] == [10, 10, 10, 10]  # start <= t < end
    inverted = await afternoon.call_action_response(
        "tibber",
        "get_prices",
        {"start": "2026-06-21T10:00:00+02:00", "end": "2026-06-21T09:00:00+02:00"},
    )
    assert len(starts(inverted)) == 96  # start >= end: end = start + 1 day, capped to what exists
    naive = await afternoon.call_action_response(
        "tibber", "get_prices", {"start": "2026-06-21 12:00:00", "end": "2026-06-21 12:30:00"}
    )
    assert len(starts(naive)) == 2


async def test_the_negative_prices_scenario_dips_below_zero_at_noon() -> None:
    conn = await started("negative_prices", 9)
    try:
        out = await conn.call_action_response("tibber", "get_prices")
    finally:
        await conn.close()
    prices = {
        datetime.fromisoformat(s["start_time"]).hour: s["price"] for s in out["prices"]["Home"]
    }
    assert prices[13] < 0
    assert min(prices.values()) == pytest.approx(-0.08, abs=0.005)
    assert prices[3] > 0
    assert prices[19] > 0.3


def test_the_default_profile_never_goes_negative() -> None:
    day = datetime(2026, 6, 21, tzinfo=UTC)
    assert min(sim_price(day + Q * i) for i in range(96)) > 0
    assert min(sim_price(day + Q * i, "negative_midday") for i in range(96)) < 0


def test_the_price_profile_is_part_of_the_scenario_schema() -> None:
    assert load_scenario("summer_day").prices == PriceParams()
    assert load_scenario("negative_prices").prices.profile == "negative_midday"
    base = "name: x\nstart: '2026-06-21T00:00:00+02:00'\nduration_s: 60\nstep_s: 60\n"
    assert (
        parse_scenario(base + "prices: {profile: negative_midday}\n").prices.profile
        == "negative_midday"
    )
    with pytest.raises(ValueError, match="profile"):
        parse_scenario(base + "prices: {profile: sideways}\n")
    with pytest.raises(ValueError, match="tomorrow_after_hour"):
        parse_scenario(base + "prices: {tomorrow_after_hour: 24}\n")
