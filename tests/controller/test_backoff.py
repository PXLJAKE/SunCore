# SPDX-License-Identifier: Apache-2.0
"""Deficit back-off (plan §6 "No grid import caused by SunCore") in the closed loop.

Controlled devices may only use real surplus; the battery buffers a short deficit; after
``deficit_delay_s`` (or when ``deficit_budget_wh`` is spent, or at once at the battery
reserve) the controller backs off one step at a time, lowest priority first: set-point
toward the base, then lower steps, then off - an on/off device only after ``min_run_s``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
from collections.abc import Callable
from datetime import datetime, timedelta

from suncore.controller import reasons as rs
from suncore.core.control import ControlSettings, DeviceState
from suncore.core.decisions import Decision, SetpointAction, StepAction, SwitchAction
from tests.controller.sim import Battery, Sim, build_sim
from tests.safety.helpers import T0, climate_device, steps_device, switch_device

DROP_S = 600


def drop(at_s: float, before: float, after: float) -> Callable[[datetime], float]:
    def pv(now: datetime) -> float:
        return before if (now - T0).total_seconds() < at_s else after

    return pv


def devices(*, min_run_s: int = 0, delay_s: int | None = None) -> list:  # type: ignore[type-arg]
    pool = switch_device("pool", nominal_w=1000, min_run_s=min_run_s)
    heater = steps_device("heater", settle_s=None).model_copy(update={"priority": 1})
    ac = climate_device("ac", nominal_w=800).model_copy(update={"priority": 2})
    if delay_s is not None:
        pool, heater, ac = (
            d.model_copy(update={"hysteresis_delay_s": delay_s}) for d in (pool, heater, ac)
        )
    return [pool, heater, ac]


def after(sim: Sim, seconds: float) -> list[Decision]:
    start = T0 + timedelta(seconds=seconds)
    return [d for d in sim.accepted() if d.ts >= start]


BACKOFF_KEYS = (rs.DEFICIT_BACKOFF, rs.DEFICIT_BUDGET)
PRIORITY = {"pool": 0, "heater": 1, "ac": 2}


def test_back_off_steps_lowest_priority_first() -> None:
    sim = build_sim(devices(min_run_s=900), pv=drop(DROP_S, 6500.0, 1000.0))
    sim.run_sync(DROP_S)
    assert sim.devices["pool"].state.on
    assert sim.devices["heater"].state.step == 2
    assert sim.devices["ac"].state.setpoint_c == 23.0
    pool_on = next(d for d in sim.accepted("pool") if d.intent.action == SwitchAction(on=True))
    sim.run_sync(900)

    decisions = after(sim, DROP_S)
    backoffs = [d for d in decisions if d.intent.reasons[0].key in BACKOFF_KEYS]
    assert backoffs, "no back-off"
    first = backoffs[0]
    # The battery-less house imports at once; the controller waits deficit_delay_s.
    assert (first.ts - T0).total_seconds() - DROP_S >= sim.settings.deficit_delay_s
    assert first.intent.asset_id == "ac"
    # One step at a time, spaced by the device's settle time plus a tick ...
    spacing = sim.settings.default_settle_s + sim.settings.tick_s
    for a, b in itertools.pairwise(backoffs):
        assert (b.ts - a.ts).total_seconds() >= spacing
    # ... lowest priority first.
    priorities = [PRIORITY[d.intent.asset_id] for d in backoffs]
    assert priorities == sorted(priorities, reverse=True)
    # The climate unit walks back to its base one step at a time.
    ac_setpoints = [
        d.intent.action.setpoint_c
        for d in decisions
        if d.intent.asset_id == "ac" and isinstance(d.intent.action, SetpointAction)
    ]
    assert ac_setpoints == [22.5, 22.0, 21.5, 21.0]
    assert all(
        isinstance(d.intent.action, StepAction) for d in decisions if d.intent.asset_id == "heater"
    )
    # The pool is switched off only after its minimum run time.
    pool_off = [d for d in decisions if d.intent.asset_id == "pool"]
    assert [d.intent.action for d in pool_off] == [SwitchAction(on=False)]
    run_s = (pool_off[0].ts - pool_on.ts).total_seconds()
    assert 900 <= run_s <= 900 + sim.settings.tick_s
    # In the end nothing SunCore controls causes import any more.
    assert sim.grid_log[-1][1] <= 0
    assert sim.rejected() == []


def test_the_budget_ends_a_long_small_deficit() -> None:
    settings = ControlSettings(deficit_delay_s=600, deficit_budget_wh=25)
    # The hysteresis delay of an hour keeps the allocation's own fall out of the way.
    pool = switch_device("pool", nominal_w=1000).model_copy(update={"hysteresis_delay_s": 3600})
    # Running at the start (adopted), 1000 W PV - 300 W base - 1000 W pool: 300 W import.
    sim = build_sim([pool], pv=1000.0, settings=settings, states={"pool": DeviceState(on=True)})
    sim.run_sync(900)
    off = [d for d in sim.accepted("pool") if d.intent.action == SwitchAction(on=False)]
    assert off
    assert off[0].intent.reasons[0].key == rs.DEFICIT_BUDGET
    elapsed = (off[0].ts - T0).total_seconds()
    # 25 Wh at 300 W: 300 s - the budget decides, long before the 600 s delay.
    assert 300 - sim.settings.tick_s <= elapsed <= 300 + 2 * sim.settings.tick_s


def test_no_buffering_at_the_battery_reserve() -> None:
    sim = build_sim(
        devices()[:1],
        pv=drop(DROP_S, 2500.0, 500.0),
        battery=Battery(soc_pct=15.0, empty_pct=5.0),
        reserve_soc_pct=20.0,
    )
    sim.run_sync(DROP_S)
    assert sim.devices["pool"].state.on  # the battery charged: the reserve did not block
    sim.run_sync(120)
    off = [d for d in after(sim, DROP_S) if d.intent.action == SwitchAction(on=False)]
    assert off
    assert off[0].intent.reasons[0].key == rs.BATTERY_RESERVE
    # At once: the first tick that sees the discharge (no deficit delay).
    assert (off[0].ts - T0).total_seconds() - DROP_S <= sim.settings.tick_s
    # And no rise while the battery discharges at the reserve.
    sim.run_sync(600)
    assert len(sim.accepted("pool")) == 2


def test_the_battery_buffers_a_short_deficit() -> None:
    def short_cloud(now: datetime) -> float:
        s = (now - T0).total_seconds()
        return 800.0 if DROP_S <= s < DROP_S + 20 else 2500.0

    sim = build_sim(devices()[:1], pv=short_cloud, battery=Battery(soc_pct=60.0))
    sim.run_sync(DROP_S + 300)
    assert len(sim.accepted("pool")) == 1  # switched on once, never backed off
    assert all(g <= 0 for _, g in sim.grid_log)


def test_above_the_import_limit_the_back_off_is_immediate() -> None:
    sim = build_sim(devices()[:1], pv=drop(DROP_S, 2500.0, 0.0))
    sim.run_sync(DROP_S)
    assert sim.devices["pool"].state.on

    async def go() -> None:
        for _ in range(3):
            inputs = dataclasses.replace(sim.inputs(), import_limit_w=1000.0)
            await sim.loop.tick(sim.now, inputs)
            await sim.loop.drain()
            sim.now += timedelta(seconds=sim.settings.tick_s)

    asyncio.run(go())
    off = [d for d in after(sim, DROP_S) if d.intent.action == SwitchAction(on=False)]
    assert [d.intent.reasons[0] for d in off] == [rs.import_limit(1300.0, 1000.0)]
    assert off[0].ts == T0 + timedelta(seconds=DROP_S)
