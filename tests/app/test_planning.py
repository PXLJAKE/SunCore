# SPDX-License-Identifier: Apache-2.0
"""The planner runtime: cadence, events, debounce, single flight, failure handling (WP9)."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from suncore.app.planning import (
    PlanningService,
    PlanningSettings,
    floor_quarter,
    planned_net_w,
    planned_soc_at,
    run_in_daemon_thread,
)
from suncore.core.control import Plan
from suncore.planner import reasons as rk
from suncore.planner.run import PlannerRun, run
from suncore.sim.planner_scenarios import situation, situation_inputs
from tests.app.planning_helpers import START, World, broken_solver, service


async def poll(svc: PlanningService, world: World, seconds: float, step: float = 5.0) -> int:
    """Tick every ``step`` simulated seconds; returns how many runs happened."""
    runs = 0
    for _ in range(int(seconds / step)):
        runs += await svc.tick()
        world.clock.advance(step)
    return runs


QUARTER_S = 920  # a quarter hour plus the 20 s offset


def real_solver(inputs: Any, budget: float, gap: float, previous: Plan | None) -> PlannerRun:
    return run(inputs, budget_s=budget, mip_gap=gap, warm_start=previous)


async def test_first_run_then_every_quarter_hour() -> None:
    world = World()
    svc = service(world)
    assert await svc.tick()
    first = svc.plan
    assert first is not None
    assert first.source in ("solver", "heuristic")
    assert svc.status().last_trigger == "start"
    assert await poll(svc, world, 600) == 0  # nothing in between
    world.clock.now = START.replace(minute=14, second=50)
    assert await poll(svc, world, 60) == 1  # the quarter (06:15:20) triggers one run
    assert svc.status().last_trigger == "schedule"
    assert await poll(svc, world, 600) == 0
    assert svc.plan is not first


async def test_runs_are_aligned_to_the_quarter_plus_offset() -> None:
    world = World()
    svc = service(world, settings=PlanningSettings(offset_s=20.0))
    await svc.tick()
    world.clock.now = START.replace(minute=15, second=10)
    assert not await svc.tick()  # 10 s past the quarter: before the offset
    world.clock.now = START.replace(minute=15, second=21)
    assert await svc.tick()
    assert svc.status().next_scheduled_at == START.replace(minute=30, second=20)


async def test_events_are_debounced_and_merged() -> None:
    world = World()
    svc = service(world)
    await svc.tick()
    started = svc.status().last_started_at
    assert started is not None
    world.clock.advance(10)
    svc.request("prices")
    svc.request("forecast")
    ack = svc.request("config")
    assert not await svc.tick()  # less than 60 s after the last run
    assert ack.earliest_start_at == started + timedelta(seconds=60)
    assert svc.status().pending == ["config", "prices", "forecast"]
    world.clock.advance(55)
    assert await svc.tick()
    assert svc.status().last_trigger == "config"  # one run for all three, highest priority
    assert svc.status().pending == []
    assert not await svc.tick()


async def test_a_manual_replan_follows_the_same_debounce() -> None:
    world = World()
    svc = service(world)
    await svc.tick()
    first_start = svc.status().last_started_at
    assert first_start is not None
    world.clock.advance(30)
    svc.request("manual")
    assert not await svc.tick()
    world.clock.advance(31)
    assert await svc.tick()
    assert svc.status().last_trigger == "manual"
    later = svc.status().last_started_at
    assert later is not None
    assert later > first_start


async def test_single_flight_events_during_a_run_wait_for_its_end() -> None:
    world = World()
    started = threading.Event()
    release = threading.Event()

    def slow(inputs: Any, budget: float, gap: float, previous: Plan | None) -> PlannerRun:
        started.set()
        release.wait(5)
        return real_solver(inputs, budget, gap, previous)

    svc = service(world, solve=slow)
    task = asyncio.create_task(svc.tick())
    while not started.is_set():  # noqa: ASYNC110
        await asyncio.sleep(0.005)
    assert svc.status().running
    svc.request("prices")
    assert not await svc.tick()  # a second tick while running starts nothing
    release.set()
    assert await task
    assert not svc.status().running
    assert svc.status().pending == ["prices"]  # kept for the next run
    world.clock.advance(61)
    assert await svc.tick()
    assert svc.status().last_trigger == "prices"


async def test_the_solve_runs_off_the_event_loop() -> None:
    world = World()
    seen: list[str] = []

    def spy(inputs: Any, budget: float, gap: float, previous: Plan | None) -> PlannerRun:
        seen.append(threading.current_thread().name)
        return real_solver(inputs, budget, gap, previous)

    svc = service(world, solve=spy)
    await svc.tick()
    assert seen == ["suncore-planner"]
    assert threading.current_thread().name != "suncore-planner"


async def test_warm_start_hands_the_previous_plan_to_the_solver() -> None:
    world = World()
    previous_seen: list[Plan | None] = []

    def spy(inputs: Any, budget: float, gap: float, previous: Plan | None) -> PlannerRun:
        previous_seen.append(previous)
        return real_solver(inputs, budget, gap, previous)

    svc = service(world, solve=spy)
    await svc.tick()
    world.clock.advance(QUARTER_S)
    await svc.tick()
    assert previous_seen[0] is None
    assert previous_seen[1] is not None
    assert previous_seen[1].created_at == START


# --- triggers: deviation -----------------------------------------------------------------


async def test_a_soc_deviation_over_ten_points_triggers_a_run() -> None:
    world = World()
    svc = service(world)
    await svc.tick()
    world.clock.advance(120)
    plan = svc.plan
    assert plan is not None
    planned = planned_soc_at(plan, world.clock())
    assert planned is not None
    world.soc_pct = planned + 9.0
    assert not await svc.tick()
    assert svc.status().pending == []
    world.soc_pct = planned + 11.0
    assert await svc.tick()
    assert svc.status().last_trigger == "deviation"


async def test_a_sustained_net_power_deviation_triggers_after_30_minutes() -> None:
    """Judged on the tracker directly: the quarter-hour replans would reset it in a full run
    (a new plan starts from the live state, so the deviation counts from there)."""
    world = World()
    svc = service(world)
    await svc.tick()
    first_at: timedelta | None = None
    for k in range(1, 120):
        world.clock.advance(30)
        svc._update_net_deviation(world.clock(), 3000.0)
        if "deviation" in svc.status().pending:
            first_at = timedelta(seconds=30 * k)
            break
    assert first_at is not None, "a +3 kW deviation for an hour must trigger a replan"
    assert timedelta(minutes=30) <= first_at <= timedelta(minutes=32)


async def test_a_short_power_spike_does_not_trigger() -> None:
    world = World()
    svc = service(world, settings=PlanningSettings(offset_s=0.0))
    await svc.tick()
    plan = svc.plan
    assert plan is not None
    for k in range(40):
        world.clock.advance(5)
        world.grid_w = (planned_net_w(plan, world.clock()) or 0.0) + (4000.0 if k < 6 else 0.0)
        world.soc_pct = planned_soc_at(plan, world.clock()) or world.soc_pct
        await svc.tick()
    assert svc.status().pending == []


# --- failure handling --------------------------------------------------------------------


async def test_a_failed_run_keeps_the_last_good_plan() -> None:
    world = World()
    svc = service(world)
    await svc.tick()
    good = svc.plan
    svc._solve = broken_solver  # type: ignore[assignment]
    world.clock.advance(QUARTER_S)
    assert await svc.tick()
    status = svc.status()
    assert status.last_outcome == "failed"
    assert status.reason is not None
    assert status.reason.key == rk.STATUS_RUN_FAILED
    assert status.consecutive_failures == 1
    assert svc.plan is good
    assert svc.active_plan() is good  # still fresh: still followed


async def test_a_collector_error_is_a_failed_run_not_a_crash() -> None:
    world = World()
    svc = service(world)
    await svc.tick()
    world.collect_error = ValueError("bad inputs")
    world.clock.advance(QUARTER_S)
    assert await svc.tick()
    assert svc.status().last_outcome == "failed"
    assert svc.plan is not None


async def test_the_plan_is_stale_after_two_hours_and_the_controller_gets_the_default() -> None:
    world = World()
    svc = service(world)
    await svc.tick()
    plan = svc.plan
    assert plan is not None
    world.clock.advance(7200 - 1)
    assert svc.active_plan() is plan
    assert not svc.view().stale
    world.clock.advance(2)
    assert svc.active_plan().is_default  # Phase 3 behaviour
    view = svc.view()
    assert view.stale
    assert not view.in_use
    assert view.fallback is not None
    assert view.fallback.key == rk.STATUS_STALE
    assert view.plan.intervals  # still shown, flagged
    assert svc.summary().stale


async def test_an_expired_plan_gives_the_default() -> None:
    world = World()
    svc = service(world, settings=PlanningSettings(stale_after_s=10 * 86400))
    await svc.tick()
    assert svc.plan is not None
    world.clock.advance(49 * 3600)
    fallback = svc.fallback_reason()
    assert fallback is not None
    assert fallback.key == rk.STATUS_EXPIRED
    assert svc.active_plan().is_default
    assert svc.view().expired


async def test_no_plan_yet_is_the_default_with_a_reason() -> None:
    world = World(ready=False)
    svc = service(world)
    assert svc.active_plan().is_default
    assert await svc.tick()
    assert svc.status().last_outcome == "skipped"
    view = svc.view()
    assert view.plan.source == "default"
    assert view.fallback is not None
    assert view.fallback.key == rk.STATUS_NO_PLAN


async def test_a_run_that_is_not_ready_is_retried_soon_not_every_tick() -> None:
    world = World(ready=False)
    svc = service(world)
    await svc.tick()
    calls = world.collect_calls
    await poll(svc, world, 10)
    assert world.collect_calls == calls  # retry_skipped_s = 15
    world.ready = True
    await poll(svc, world, 20)
    assert svc.plan is not None


async def test_the_first_plan_waits_for_prices_and_forecasts_within_the_grace_period() -> None:
    world = World(complete=False)
    svc = service(world, settings=PlanningSettings(first_run_grace_s=120.0))
    await svc.tick()
    assert svc.plan is None
    reason = svc.status().reason
    assert reason is not None
    assert reason.key == rk.STATUS_WAITING
    world.clock.advance(60)
    await svc.tick()
    assert svc.plan is None
    world.clock.advance(70)  # the grace is over: plan with what there is
    await svc.tick()
    assert svc.plan is not None


async def test_a_hanging_planner_is_abandoned_and_stacks_no_threads() -> None:
    world = World()
    release = threading.Event()

    # A canned result, computed before the clock-critical part: the run timeout below is
    # 0.1 s, which a real solve under coverage on a busy runner can exceed (review B2).
    canned = real_solver(
        situation_inputs(replace(situation("sunny_day"), now=START)), 8, 0.01, None
    )

    def hang(inputs: Any, budget: float, gap: float, previous: Plan | None) -> PlannerRun:
        release.wait(10)
        return canned

    def fast(*_a: object) -> PlannerRun:
        return canned

    settings = PlanningSettings(budget_s=0.05, run_timeout_margin_s=0.05)
    svc = service(world, settings=settings, solve=hang)
    assert await svc.tick()
    assert svc.status().last_outcome == "failed"
    reason = svc.status().reason
    assert reason is not None
    assert reason.key == rk.STATUS_RUN_TIMEOUT
    world.clock.advance(QUARTER_S)  # the thread still lives: no second one is started on top of it
    assert await svc.tick()
    assert svc.status().last_outcome == "skipped"
    reason = svc.status().reason
    assert reason is not None
    assert reason.key == rk.STATUS_RUN_STUCK
    release.set()
    await asyncio.sleep(0.3)
    svc._solve = fast  # type: ignore[assignment]
    world.clock.advance(QUARTER_S)
    assert await svc.tick()
    assert svc.status().last_outcome == "ok"


async def test_cancelling_the_service_does_not_wait_for_a_hanging_planner() -> None:
    world = World()
    release = threading.Event()
    started = threading.Event()

    def hang(inputs: Any, budget: float, gap: float, previous: Plan | None) -> PlannerRun:
        started.set()
        release.wait(10)
        return real_solver(inputs, budget, gap, previous)

    async def sleep(_s: float) -> None:
        await asyncio.sleep(0.001)

    svc = service(world, solve=hang, sleep=sleep)
    task = asyncio.create_task(svc.run())
    while not started.is_set():  # noqa: ASYNC110
        await asyncio.sleep(0.005)
    loop = asyncio.get_running_loop()
    began = loop.time()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert loop.time() - began < 0.5
    release.set()


async def test_the_run_loop_polls_and_survives_errors() -> None:
    world = World()
    ticks = 0

    async def sleep(seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        world.clock.advance(seconds)
        await asyncio.sleep(0)

    svc = service(world, sleep=sleep)
    world.collect_error = RuntimeError("down")
    task = asyncio.create_task(svc.run())
    while ticks < 5:  # noqa: ASYNC110
        await asyncio.sleep(0)
    world.collect_error = None
    while svc.plan is None:  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- hand-over, listeners ----------------------------------------------------------------


async def test_listeners_hear_about_every_new_plan_and_the_plan_has_the_start_soc() -> None:
    world = World(soc_pct=42.5)
    svc = service(world)
    heard: list[int] = []
    remove = svc.add_listener(lambda: heard.append(1))
    await svc.tick()
    assert heard == [1]
    assert svc.plan is not None
    assert svc.plan.assumptions["soc_start_pct"] == 42.5
    remove()
    world.clock.advance(QUARTER_S)
    await svc.tick()
    assert heard == [1]


async def test_a_failing_listener_does_not_break_the_hand_over() -> None:
    world = World()
    svc = service(world)

    def bad() -> None:
        raise RuntimeError

    svc.add_listener(bad)
    await svc.tick()
    assert svc.plan is not None


async def test_the_hand_over_is_one_reference_a_reader_thread_gets_whole() -> None:
    world = World()
    svc = service(world)
    await svc.tick()
    seen: list[Plan] = []
    stop = threading.Event()
    now = world.clock()

    def reader() -> None:
        while not stop.is_set():
            seen.append(svc.active_plan(now))

    thread = threading.Thread(target=reader)
    thread.start()
    for _ in range(3):
        world.clock.advance(QUARTER_S)
        await svc.tick()
    stop.set()
    thread.join()
    stamps = {p.created_at for p in seen}
    assert stamps <= {START + timedelta(seconds=QUARTER_S * k) for k in range(4)}
    assert all(p.intervals for p in seen)


async def test_the_view_cuts_the_horizon_and_the_past() -> None:
    world = World()
    svc = service(world)
    await svc.tick()
    world.clock.advance(3600)
    view = svc.view(hours=6)
    assert view.in_use
    first = view.plan.intervals[0]
    assert first.start <= world.clock() < first.end
    assert view.plan.intervals[-1].start < world.clock() + timedelta(hours=6)
    assert len(view.plan.intervals) <= 6 * 4 + 1


async def test_daemon_thread_helper_returns_results_and_errors() -> None:
    assert await run_in_daemon_thread(lambda: 7) == 7

    def boom() -> int:
        raise KeyError("x")

    with pytest.raises(KeyError):
        await run_in_daemon_thread(boom)


def test_floor_quarter() -> None:
    assert floor_quarter(START.replace(minute=44, second=59)).minute == 30
    assert floor_quarter(START.replace(minute=0, second=0)).minute == 0


async def test_planned_soc_interpolates_inside_the_first_interval() -> None:
    world = World(soc_pct=30.0)
    svc = service(world)
    await svc.tick()
    plan = svc.plan
    assert plan is not None
    first = plan.intervals[0]
    assert first.expected is not None
    assert first.expected.soc_pct is not None
    assert planned_soc_at(plan, first.start) == pytest.approx(30.0)
    mid = first.start + (first.end - first.start) / 2
    assert planned_soc_at(plan, mid) == pytest.approx((30.0 + first.expected.soc_pct) / 2, abs=0.01)
    assert planned_soc_at(plan, plan.intervals[-1].end + timedelta(seconds=1)) is None
    assert planned_soc_at(Plan.default(), START) is None
