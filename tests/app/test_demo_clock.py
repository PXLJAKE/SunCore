# SPDX-License-Identifier: Apache-2.0
"""The accelerated demo clock: one time base for the simulated house and the controller."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from suncore.app.demo import DEMO_MAX_TICK_S, DemoClock, demo_tick_s

START = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)


def test_the_clock_runs_speed_times_faster_than_the_wall() -> None:
    wall = [100.0]
    clock = DemoClock(START, 60.0, monotonic=lambda: wall[0])
    assert clock() == START
    wall[0] += 1.0
    assert clock() == START + timedelta(seconds=60)
    wall[0] += 0.5
    assert clock() == START + timedelta(seconds=90)
    assert clock.speed == 60.0


def test_the_clock_needs_an_aware_start_and_a_positive_speed() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        DemoClock(START.replace(tzinfo=None), 60.0)
    with pytest.raises(ValueError, match="positive"):
        DemoClock(START, 0.0)


async def test_sleep_waits_simulated_seconds() -> None:
    clock = DemoClock(START, 600.0)
    loop = asyncio.get_running_loop()
    began = loop.time()
    await clock.sleep(60.0)  # one simulated minute = 0.1 s
    assert 0.05 < loop.time() - began < 1.0
    await clock.sleep(-5)  # never negative


@pytest.mark.parametrize(
    ("configured", "speed", "expected"),
    [(5.0, 1.0, 5.0), (5.0, 60.0, 6.0), (5.0, 600.0, 60.0), (5.0, 6000.0, DEMO_MAX_TICK_S)],
)
def test_demo_tick_is_not_faster_than_a_tenth_of_a_real_second(
    configured: float, speed: float, expected: float
) -> None:
    assert demo_tick_s(configured, speed) == expected
