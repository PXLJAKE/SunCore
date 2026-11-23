# SPDX-License-Identifier: Apache-2.0
"""Battery control in the closed loop (WP10): the simulated inverter, the real adapters, the
real controller and gate in simulated time. Evidence comes from the simulated inverter (what it
really did), not from the gate's own opinion."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest

from suncore.core.battery import BatteryAction
from suncore.core.control import BatteryBand, Plan, PlanInterval
from suncore.core.decisions import Decision, DecisionKind, ReleaseAction
from suncore.sim.inverter import SimInverter
from suncore.sim.runner import RunResult, ScenarioRunner
from suncore.sim.scenario import load_scenario
from tests.sim.helpers import catalog

pytestmark = pytest.mark.simulator

QUARTER = timedelta(minutes=15)


def rolling_plan(band: Callable[[datetime], BatteryBand | None]) -> Callable[[datetime], Plan]:
    """A fresh plan every quarter hour whose battery band comes from ``band(now)``."""

    def provider(now: datetime) -> Plan:
        start = now - timedelta(
            minutes=now.minute % 15, seconds=now.second, microseconds=now.microsecond
        )
        return Plan(
            created_at=start,
            source="heuristic",
            intervals=(PlanInterval(start=start, end=start + QUARTER, battery=band(now)),),
        )

    return provider


def keys(decision: Decision) -> list[str]:
    return [r.key for r in decision.reasons]


def battery_actions(runner: ScenarioRunner, kind: DecisionKind) -> list[Decision]:
    return [d for d in runner.battery_decisions() if d.kind is kind]


async def play(
    name: str,
    plan: Callable[[datetime], Plan],
    *,
    actions: dict[float, Callable[[ScenarioRunner], Any]] | None = None,
    **kw: Any,
) -> tuple[RunResult, ScenarioRunner, SimInverter]:
    runner = ScenarioRunner(load_scenario(name), catalog(), plan_provider=plan, **kw)
    for at_s, action in (actions or {}).items():
        runner.at(at_s, action)
    await runner.start()
    inverter = runner.inverter
    assert inverter is not None
    result = await runner.run()
    await runner.close()
    return result, runner, inverter


# --- modbus_heartbeat_lost -------------------------------------------------------------------


async def _crash(runner: ScenarioRunner) -> None:
    assert runner.stack is not None
    await runner.stack.crash()


@pytest.fixture(scope="module")
def heartbeat() -> tuple[RunResult, ScenarioRunner, SimInverter]:
    lock = rolling_plan(lambda _now: BatteryBand(discharge_lock=True))
    return asyncio.run(play("modbus_heartbeat_lost", lock, actions={3600: _crash}))


def test_modbus_arms_the_fallback_then_locks_with_a_heartbeat(
    heartbeat: tuple[RunResult, ScenarioRunner, SimInverter],
) -> None:
    result, runner, inverter = heartbeat
    accepted = battery_actions(runner, DecisionKind.ACCEPTED)
    assert accepted, [keys(d) for d in runner.battery_decisions()][:5]
    assert accepted[0].intent.action == BatteryAction(max_discharge_w=0)
    writes = [w for w in inverter.log.writes if w[1] == "modbus"]
    addresses = [w[2]["address"] for w in writes]
    assert addresses[:3] == [1284, 1286, 1288]  # the fallback first
    start = runner.scenario.start
    assert inverter.registers[1284] == inverter.own_charge_w
    assert inverter.registers[1286] == inverter.own_discharge_w
    assert inverter.registers[1288] == 60
    # The stale lock in 1282 (an earlier controller) never became the baseline (review M1).
    takeover = next(d for d in runner.battery_decisions() if d.kind is DecisionKind.ACCEPTED)
    assert takeover.intent.action == BatteryAction(max_discharge_w=0)
    stored = asyncio.run(runner.store.load_runtime("battery"))
    assert stored is not None
    assert stored.baseline == BatteryAction(max_charge_w=5000, max_discharge_w=5000)
    # The heartbeat re-writes 1280/1282 every 20 s while the lease holds (first hour).
    start = runner.scenario.start
    limit_writes = [
        w[0] for w in writes if w[2]["address"] == 1282 and w[0] < start + timedelta(hours=1)
    ]
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(limit_writes)]
    assert gaps
    assert max(gaps) <= 30
    # While SunCore held the lock the inverter never fell back and never discharged.
    hour = [
        s for s in result.steps if start + timedelta(minutes=10) <= s.t < start + timedelta(hours=1)
    ]
    assert all(s.battery_w > -50 for s in hour)
    assert not any(
        active for t, active in inverter.log.fallback_active if t < start + timedelta(hours=1)
    )


def test_a_dead_suncore_falls_back_within_the_timer(
    heartbeat: tuple[RunResult, ScenarioRunner, SimInverter],
) -> None:
    result, runner, inverter = heartbeat
    start = runner.scenario.start
    crash = start + timedelta(hours=1)
    last_write = max(w[0] for w in inverter.log.writes if w[0] <= crash + timedelta(seconds=10))
    fell_back = [t for t, active in inverter.log.fallback_active if active]
    assert fell_back
    assert fell_back[0] > crash - timedelta(seconds=10)
    assert (fell_back[0] - last_write).total_seconds() <= 60 + runner.scenario.step_s
    # The inverter covers the house again with its own limits (the armed fallback).
    after = [s for s in result.steps if s.t > fell_back[0] + timedelta(minutes=1)]
    assert after
    assert all(s.battery_w < -100 for s in after if s.soc_pct > 15)
    assert not [w for w in inverter.log.writes if w[0] > crash + timedelta(seconds=30)]


# --- battery_reserve_drift -----------------------------------------------------------------------


def _night_lock(now: datetime) -> BatteryBand:
    local = now.astimezone(now.tzinfo)
    hold = local.hour >= 22 or local.hour < 2
    return BatteryBand(discharge_lock=hold)


@pytest.fixture(scope="module")
def drift() -> tuple[RunResult, ScenarioRunner, SimInverter]:
    return asyncio.run(play("battery_reserve_drift", rolling_plan(_night_lock)))


def test_the_floor_is_never_below_the_reserve_and_never_follows_the_sagging_reading(
    drift: tuple[RunResult, ScenarioRunner, SimInverter],
) -> None:
    result, runner, inverter = drift
    accepted = [
        d
        for d in battery_actions(runner, DecisionKind.ACCEPTED)
        if isinstance(d.intent.action, BatteryAction)
    ]
    assert accepted, [keys(d) for d in runner.battery_decisions()][:6]
    for d in accepted:
        floor = d.intent.action.min_soc_pct  # type: ignore[union-attr]
        assert floor is not None
        assert floor >= 15  # effective reserve: reserve 10 + drift margin 5
    floors = [w for w in inverter.log.writes if w[1] == "number.sim_battery_min_soc"]
    baseline = runner.scenario.house.inverter.min_soc_pct  # type: ignore[union-attr]
    assert all(v >= 15 or v == baseline for _, _, v in floors)
    lock_writes = [v for _, _, v in floors if v >= 15]
    assert 1 <= len(lock_writes) <= 3  # hysteresis: the floor is not re-written for the drift
    # The reported SoC sagged below the floor without SunCore lowering it.
    start = runner.scenario.start
    night = [
        s
        for s in result.steps
        if start + timedelta(minutes=40) <= s.t < start + timedelta(hours=4, minutes=20)
    ]
    assert any(s.true_soc_pct is not None and s.soc_pct < s.true_soc_pct - 2 for s in night)
    # No SunCore-caused discharge while the floor is held: the true SoC stays.
    held = [s.true_soc_pct for s in night if s.true_soc_pct is not None]
    assert max(held) - min(held) < 1.0


def test_the_floor_is_given_back_when_the_plan_stops_asking(
    drift: tuple[RunResult, ScenarioRunner, SimInverter],
) -> None:
    result, runner, inverter = drift
    releases = [
        d
        for d in battery_actions(runner, DecisionKind.ACCEPTED)
        if isinstance(d.intent.action, ReleaseAction)
    ]
    assert releases
    two = runner.scenario.start.replace(hour=2, minute=0) + timedelta(days=1)
    assert any(d.ts >= two for d in releases)
    assert inverter.min_soc_pct == runner.scenario.house.inverter.min_soc_pct  # type: ignore[union-attr]
    late = [s for s in result.steps if s.t > two + timedelta(minutes=5)]
    assert any(s.battery_w < -100 for s in late)  # the inverter covers the house again


# --- kostal_rest_reserve ----------------------------------------


def _rest_plan(now: datetime) -> BatteryBand:
    local = now.astimezone(now.tzinfo)
    if local.hour == 0:
        return BatteryBand(grid_charge_max_w=3000)
    return BatteryBand(discharge_lock=True)


@pytest.fixture(scope="module")
def rest() -> tuple[RunResult, ScenarioRunner, SimInverter, list[list[str]]]:
    seen: list[list[str]] = []

    async def look(runner: ScenarioRunner) -> None:
        stack = runner.stack
        assert stack is not None
        view = None if stack.last is None else stack.last.battery
        seen.append([] if view is None else [r.key for r in view.reasons])

    result, runner, inverter = asyncio.run(
        play("kostal_rest_reserve", rolling_plan(_rest_plan), actions={600: look, 1200: look})
    )
    return result, runner, inverter, seen


def test_grid_charging_over_rest_is_shown_never_written(
    rest: tuple[RunResult, ScenarioRunner, SimInverter, list[list[str]]],
) -> None:
    _, runner, inverter, seen = rest
    assert any("controller.battery.grid_charge_unsupported" in s for s in seen)
    for d in runner.battery_decisions():
        action = d.intent.action
        assert not (isinstance(action, BatteryAction) and action.grid_charge_w)
    assert all(w[1] == "number.sim_battery_min_soc" for w in inverter.log.writes)


def test_silent_rest_failures_are_caught_by_the_read_back_and_trip(
    rest: tuple[RunResult, ScenarioRunner, SimInverter, list[list[str]]],
) -> None:
    _, runner, inverter, _ = rest
    failed = battery_actions(runner, DecisionKind.FAILED)
    assert sum(keys(d) == ["safety.readback_mismatch"] for d in failed) >= 3
    assert not [
        d
        for d in battery_actions(runner, DecisionKind.ACCEPTED)
        if isinstance(d.intent.action, BatteryAction)
    ]
    tripped = [d for d in runner.battery_decisions() if "safety.repeated_failures" in keys(d)]
    assert tripped
    # The read-back waited for the refresh: never earlier than 35 s after the write.
    for d in failed:
        if keys(d) == ["safety.readback_mismatch"]:
            assert (d.ts - d.intent.ts).total_seconds() >= 35
    assert inverter.min_soc_pct == 10  # nothing ever took effect


# --- inverter_hang_battery ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def hang() -> tuple[RunResult, ScenarioRunner, SimInverter]:
    lock = rolling_plan(lambda _now: BatteryBand(discharge_lock=True))
    return asyncio.run(play("inverter_hang_battery", lock))


def test_a_hung_inverter_is_noticed_released_and_left_alone(
    hang: tuple[RunResult, ScenarioRunner, SimInverter],
) -> None:
    _, runner, inverter = hang
    start = runner.scenario.start
    hang_from = start + timedelta(seconds=5400)
    hang_to = start + timedelta(seconds=9000)
    watch = {"safety.battery_frozen", "safety.battery_soc_frozen"}  # B8, either reading
    frozen = [d for d in runner.battery_decisions() if watch & set(keys(d))]
    assert frozen, [keys(d) for d in runner.battery_decisions()]
    noticed = frozen[0].ts
    assert hang_from < noticed < hang_from + timedelta(minutes=25)
    # Stop writing: only the release of the original baseline is retried (with back-off) until
    # the inverter is back; never a new limit.
    during = [w for w in inverter.log.writes if noticed <= w[0] < hang_to]
    assert during
    assert all(w[1] == "number.sim_battery_max_discharge" and w[2] == 5000 for w in during)
    gaps = [(b[0] - a[0]).total_seconds() for a, b in itertools.pairwise(during)]
    assert gaps == sorted(gaps)  # back-off: the retries thin out ...
    assert max(gaps) <= 600  # ... up to one every 10 minutes
    assert not [
        d
        for d in battery_actions(runner, DecisionKind.ACCEPTED)
        if isinstance(d.intent.action, BatteryAction) and noticed <= d.ts < hang_to
    ]
    unconfirmed = [
        d for d in runner.battery_decisions() if "safety.battery_release_unconfirmed" in keys(d)
    ]
    assert unconfirmed
    # The latch holds until the inverter is back, then the read-back confirms the release.
    assert all(d.ts < hang_to + timedelta(minutes=11) for d in unconfirmed)
    confirmed = [
        d for d in runner.battery_decisions() if "safety.battery_release_confirmed" in keys(d)
    ]
    assert confirmed
    assert confirmed[0].ts >= hang_to


def test_cloud_flicker_does_not_make_the_battery_oscillate(
    hang: tuple[RunResult, ScenarioRunner, SimInverter],
) -> None:
    _, runner, inverter = hang
    hours = runner.scenario.duration_s / 3600
    accepted = battery_actions(runner, DecisionKind.ACCEPTED)
    assert len(accepted) <= 2 * hours  # a lock and its release per hour at most
    locks = [w for w in inverter.log.writes if w[2] == 0]
    assert len(locks) <= hours


# --- grid_charge_price_level (planner + controller + gate, automatic) ---


PLAN_EVERY_STEPS = 30  # 15 minutes at 30 s per step


async def _grid_charge_run(
    live_price: Callable[[datetime], float | None] | None = None,
) -> tuple[RunResult, ScenarioRunner, SimInverter, list[Plan]]:
    from dataclasses import replace  # noqa: PLC0415

    from suncore.app.planning import (  # noqa: PLC0415
        LiveReading,
        PlanningService,
        PlanningSettings,
        PlanningSnapshot,
    )
    from suncore.sim.planner_scenarios import situation, situation_inputs  # noqa: PLC0415

    scenario = load_scenario("grid_charge_price_level")
    base = replace(situation("grid_charge_price_level"), import_limit_w=8000.0)
    runner = ScenarioRunner(scenario, catalog(), import_price=live_price or base.import_price)
    plans: list[Plan] = []

    def live() -> LiveReading:
        last = runner._steps[-1] if runner._steps else None
        return LiveReading(soc_pct=None if last is None else last.soc_pct)

    async def collect(now, previous):  # type: ignore[no-untyped-def]
        soc = live().soc_pct or 20.0
        inputs = situation_inputs(replace(base, now=now, soc_pct=soc))
        return PlanningSnapshot(replace(inputs, previous=previous))

    planning = PlanningService(collect, live, clock=runner.vt.clock, settings=PlanningSettings())
    planning.add_listener(lambda: plans.append(planning.plan))  # type: ignore[arg-type]
    runner.plan_provider = planning.active_plan
    await runner.start()
    for index in range(scenario.steps):
        if index % PLAN_EVERY_STEPS == 0:
            await planning.tick()
        await runner.step(index)
    result = await runner.finish()
    inverter = runner.inverter
    assert inverter is not None
    await runner.close()
    return result, runner, inverter, plans


@pytest.fixture(scope="module")
def grid_charge() -> tuple[RunResult, ScenarioRunner, SimInverter, list[Plan]]:
    return asyncio.run(_grid_charge_run())


def _local_hour(t: datetime) -> float:
    return t.hour + t.minute / 60


def test_the_planner_plans_grid_charging_in_the_cheap_window(
    grid_charge: tuple[RunResult, ScenarioRunner, SimInverter, list[Plan]],
) -> None:
    _, _, _, plans = grid_charge
    planned = [
        i
        for p in plans
        for i in p.intervals
        if i.battery is not None and (i.battery.grid_charge_max_w or 0) > 0
    ]
    assert planned
    assert all(1 <= _local_hour(i.start) < 5 for i in planned)


def test_the_battery_is_charged_from_the_grid_only_inside_the_policy(
    grid_charge: tuple[RunResult, ScenarioRunner, SimInverter, list[Plan]],
) -> None:
    result, runner, inverter, _ = grid_charge
    accepted = [
        d
        for d in battery_actions(runner, DecisionKind.ACCEPTED)
        if isinstance(d.intent.action, BatteryAction) and d.intent.action.grid_charge_w
    ]
    assert accepted, [keys(d) for d in runner.battery_decisions()][:8]
    forced = [
        w for w in inverter.log.writes if w[1] == "number.sim_battery_grid_charge" and w[2] > 0
    ]
    assert forced
    assert all(1 <= _local_hour(t) < 5 for t, _, _ in forced)
    charging = [s for s in result.steps if s.battery_w > 50 and s.pv_w < 1]
    assert charging
    tick = runner.scenario.step_s / 3600 * 2  # one tick plus the lease check
    assert all(1 <= _local_hour(s.t) < 5 + tick for s in charging)
    # The true SoC rose, and the import limit always held.
    assert (
        max(s.true_soc_pct or 0 for s in charging)
        > runner.scenario.house.battery.initial_soc_pct + 10
    )
    assert max(s.grid_w for s in result.steps) <= 8000


def test_the_battery_gets_its_own_settings_back_after_the_window(
    grid_charge: tuple[RunResult, ScenarioRunner, SimInverter, list[Plan]],
) -> None:
    result, runner, inverter, _ = grid_charge
    assert inverter.grid_charge_w == 0
    late = [
        s for s in result.steps if _local_hour(s.t) >= 5.1 and s.t.day != runner.scenario.start.day
    ]
    assert late
    assert all(s.battery_w <= 50 for s in late)
    hours = runner.scenario.duration_s / 3600
    assert len(battery_actions(runner, DecisionKind.ACCEPTED)) <= 4 * hours  # no oscillation


def _price_jump(at: datetime) -> float | None:
    """The cheap night price, but at 03:30 the live price jumps above the policy's level."""
    hour = at.hour + at.minute / 60
    if 3.5 <= hour < 5:
        return 0.35
    return 0.15 if 1 <= hour < 5 else 0.35


def test_a_price_jump_inside_the_window_ends_grid_charging() -> None:
    """Review minor 6: the plan still says "charge" (it planned on older prices), the live price
    left the policy - the lease ends and nothing is forced any more."""
    result, runner, inverter, _ = asyncio.run(_grid_charge_run(_price_jump))
    ended = [
        d
        for d in runner.battery_decisions()
        if "safety.battery_grid_charge_policy" in keys(d) and _local_hour(d.ts) >= 3.5
    ]
    assert ended
    forced = [
        w for w in inverter.log.writes if w[1] == "number.sim_battery_grid_charge" and w[2] > 0
    ]
    assert forced
    assert all(_local_hour(t) < 3.5 for t, _, _ in forced)
    after = [s for s in result.steps if 3.55 <= _local_hour(s.t) < 5 and s.pv_w < 1]
    assert after
    assert all(s.battery_w <= 50 for s in after)
