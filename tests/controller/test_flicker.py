# SPDX-License-Identifier: Apache-2.0
"""Cloud flicker against the closed loop (plan §1.1): bounded switching, no oscillation.

The house (``tests.controller.sim``) runs the real controller behind the real safety gate.
PV alternates clear stretches (5-25 min, slow ramp) with flicker episodes (2-15 min of
5-40 s sun/cloud segments, cloud = 25 % of the clear value). Evidence per run:

* every on/off device stays within ``max_switches_per_hour`` in every rolling hour;
* no on -> off shorter than ``min_run_s``, no off -> on shorter than ``min_pause_s``, so
  no full cycle is shorter than ``min_run_s + min_pause_s``;
* the gate rejects nothing: the controller never proposes what the gate would refuse;
* during a long pure flicker episode no device is switched on at all.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from suncore.core.decisions import DecisionKind, SetpointAction
from tests.controller.sim import Battery, Sim, build_sim
from tests.safety.helpers import T0, climate_device, steps_device, switch_device

HOURS = 4
CLOUD_SHARE = 0.25


def flicker_pv(seed: int, hours: float = HOURS) -> Callable[[datetime], float]:
    rng = random.Random(seed)
    segments: list[tuple[float, float, bool]] = []  # (start, end, sunny)
    t = 0.0
    while t < hours * 3600:
        clear = rng.randint(300, 1500)
        segments.append((t, t + clear, True))
        t += clear
        end = t + rng.randint(120, 900)
        sunny = False
        while t < end:
            d = rng.randint(5, 40)
            segments.append((t, t + d, sunny))
            t += d
            sunny = not sunny

    def pv(now: datetime) -> float:
        s = (now - T0).total_seconds()
        clear = 2500.0 + 1500.0 * math.sin(s / 3600.0 * math.pi / hours)
        for start, end, sunny in segments:
            if start <= s < end:
                return clear if sunny else CLOUD_SHARE * clear
        return clear

    return pv


def pure_flicker_pv(seed: int) -> Callable[[datetime], float]:
    rng = random.Random(seed)
    edges: list[tuple[float, bool]] = []
    t, sunny = 0.0, True
    while t < HOURS * 3600:
        edges.append((t, sunny))
        t += rng.randint(5, 40)
        sunny = not sunny

    def pv(now: datetime) -> float:
        s = (now - T0).total_seconds()
        state = True
        for start, sunny_ in edges:
            if start > s:
                break
            state = sunny_
        return 3800.0 if state else 900.0

    return pv


POOL = switch_device(
    "pool", nominal_w=1000, min_run_s=300, min_pause_s=300, max_switches_per_hour=4
)
HEATER = steps_device("heater")
AC = climate_device("ac", nominal_w=800)


def assert_bounded(sim: Sim, device_id: str) -> int:
    device = sim.devices[device_id].config
    limit = device.max_switches_per_hour or sim.settings.default_max_switches_per_hour
    times = sim.switch_times(device_id)
    for i, (ts, _) in enumerate(times):
        in_hour = [t for t, _ in times[: i + 1] if t > ts - timedelta(hours=1)]
        assert len(in_hour) <= limit, f"{device_id}: {len(in_hour)} switches in an hour"
    for (a, on_a), (b, _) in itertools.pairwise(times):
        gap = (b - a).total_seconds()
        if on_a:
            assert gap >= device.min_run_s, f"{device_id}: ran {gap} s"
        else:
            assert gap >= device.min_pause_s, f"{device_id}: paused {gap} s"
    for (a, on_a), _, (c, _) in zip(times, times[1:], times[2:], strict=False):
        if on_a:
            assert (c - a).total_seconds() >= device.min_run_s + device.min_pause_s
    return len(times)


@pytest.mark.simulator
@pytest.mark.parametrize(
    "seed", [1, 2, pytest.param(3, marks=pytest.mark.slow), pytest.param(4, marks=pytest.mark.slow)]
)
@pytest.mark.parametrize("with_battery", [False, True])
def test_cloud_flicker_switching_is_bounded(seed: int, *, with_battery: bool) -> None:
    sim = build_sim(
        [POOL, HEATER, AC], pv=flicker_pv(seed), battery=Battery() if with_battery else None
    )
    sim.run_sync(HOURS * 3600)

    assert sim.rejected() == []
    assert [d for d in sim.decisions if d.kind is DecisionKind.FAILED] == []
    pool_switches = assert_bounded(sim, "pool")
    heater_switches = assert_bounded(sim, "heater")
    # The loop does use the clear stretches (the test is not passing by doing nothing).
    assert pool_switches >= 2
    assert heater_switches >= 2
    # Set-point writes: one per level change, never two in a row to the same value.
    setpoints = [
        d.intent.action.setpoint_c
        for d in sim.accepted("ac")
        if isinstance(d.intent.action, SetpointAction)
    ]
    assert setpoints
    assert all(a != b for a, b in itertools.pairwise(setpoints))


@pytest.mark.simulator
def test_pure_flicker_switches_nothing_on() -> None:
    """Four hours of 5-40 s sun/cloud alternation: the downside spread keeps everything off."""
    sim = build_sim([POOL, HEATER, AC], pv=pure_flicker_pv(7))
    sim.run_sync(HOURS * 3600)
    assert sim.decisions == []
    assert max(g for _, g in sim.grid_log) <= 0


@pytest.mark.simulator
def test_steady_sun_switches_once_and_holds() -> None:
    heater = HEATER.model_copy(update={"priority": 1})
    sim = build_sim([POOL, heater], pv=3800.0)
    sim.run_sync(2 * 3600)
    assert len(sim.switch_times("pool")) == 1
    assert len(sim.switch_times("heater")) == 1
    # 3400 W total: the pool (1000 W, first) and the heater's 1000 W step - not its 3000 W.
    assert sim.devices["heater"].state.step == 1
    assert max(g for _, g in sim.grid_log) <= 0
