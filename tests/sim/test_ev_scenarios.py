# SPDX-License-Identifier: Apache-2.0
"""The EV scenarios of PR 6-I, closed-loop (plan WP9): surplus-only charging under cloud
flicker, min+PV, 1/3-phase switching, the late car with a target (real planner, E6 lock) and
§14a dimming of a charger in mode ``now``.

Judged by physical evidence (charger power, house balance, the car's SoC) and the gate's
decisions: nothing is rejected (the controller mirrors E1-E9), switching stays within the
limits, nothing is imported for the car beyond what the mode permits, the house battery never
discharges into the car and the phase is never changed while charging.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

import pytest

from suncore.controller.ev import PHASE_INTENT_PREFIX
from suncore.core.control import ControlSettings, Plan
from suncore.core.decisions import ChargeAction, Decision, DecisionKind, OperatingMode
from suncore.planner.flex import EvLive, FlexRequest
from suncore.sim.devices import SimWallbox
from suncore.sim.runner import RunResult, ScenarioRunner
from suncore.sim.scenario import load_scenario
from tests.sim.helpers import catalog, caused_import_wh, run_named

pytestmark = pytest.mark.simulator

SETTINGS = ControlSettings()
EV_SCENARIOS = ["ev_pv_only_flicker", "ev_min_pv", "ev_phase_switching", "ev_para14a"]
MIN_3PH_W = 6 * 230 * 3  # 4140 W
MIN_1PH_W = 6 * 230  # 1380 W


def at(result: RunResult, offset_s: float) -> int:
    return int(offset_s // result.step_s)


def charges(result: RunResult) -> list[Decision]:
    return [d for d in result.accepted("wallbox") if isinstance(d.intent.action, ChargeAction)]


def wallbox(runner: ScenarioRunner) -> SimWallbox:
    sim = runner.sim_devices["wallbox"]
    assert isinstance(sim, SimWallbox)
    return sim


@pytest.mark.parametrize("name", EV_SCENARIOS)
def test_nothing_is_rejected_and_switching_stays_within_the_limits(name: str) -> None:
    result, _ = run_named(name)
    assert result.of_kind(DecisionKind.REJECTED) == []
    assert result.of_kind(DecisionKind.FAILED) == []
    config = result.configs["wallbox"]
    limit = config.max_switches_per_hour or SETTINGS.default_max_switches_per_hour
    assert result.switches_in_window("wallbox") <= limit
    assert charges(result), "the scenario must charge"


@pytest.mark.parametrize("name", EV_SCENARIOS)
def test_shadow_writes_nothing(name: str) -> None:
    result, runner = run_named(name, shadow=True)
    assert result.accepted() == []
    assert all(w == 0 for w in result.devices["wallbox"].power_w)
    assert wallbox(runner).charged_wh == 0


# --- ev_pv_only_flicker ----------------------------------------------------------------------


def test_pv_only_follows_the_surplus_and_never_imports_for_the_car() -> None:
    result, runner = run_named("ev_pv_only_flicker")
    caused, moved = caused_import_wh("ev_pv_only_flicker")
    assert moved > 10_000  # the car took > 10 kWh of PV
    # Cloud flicker: a deficit is tolerated only briefly (the deficit budget) before backing off.
    assert caused <= 0.02 * moved
    assert wallbox(runner).soc_pct > 50
    powers = {round(w) for w in result.devices["wallbox"].power_w}
    assert all(w == 0 or w >= MIN_3PH_W for w in powers)  # never below the 6 A minimum


def test_pv_only_flicker_does_not_oscillate() -> None:
    result, _ = run_named("ev_pv_only_flicker")
    flicker = [i for i, _ in result.transitions("wallbox") if 1800 <= i * result.step_s < 2400]
    assert len(flicker) <= 2  # one pause and at most one resume per flicker episode
    assert len(charges(result)) <= 10  # a few current steps in three hours, not every tick


# --- ev_min_pv -------------------------------------------------------------------------------


def test_min_pv_holds_the_minimum_and_adds_surplus() -> None:
    result, runner = run_named("ev_min_pv")
    power = result.devices["wallbox"].power_w
    depart = at(result, 12_600)
    settle = at(result, 60)
    assert all(w >= MIN_3PH_W - 1 for w in power[settle:depart])
    assert all(w == 0 for w in power[depart + at(result, 60) :])
    assert max(power[:depart]) > MIN_3PH_W  # surplus on top of the floor
    first = charges(result)[0]
    assert [r.key for r in first.intent.reasons] == ["controller.ev.min_pv"]
    # The import beyond the floor: none (the floor is imported by design, ADR-0017 d. 4).
    caused, _ = caused_import_wh("ev_min_pv")
    floor_wh = MIN_3PH_W * (12_600 - 60) / 3600
    assert caused <= floor_wh + 200
    assert wallbox(runner).soc_pct > 35
    limit = result.scenario.control.import_limit_w
    assert limit is not None
    assert all(s.grid_w <= limit for s in result.steps)


# --- ev_phase_switching ----------------------------------------------------------------------


def test_phases_switch_only_paused_and_within_the_limit() -> None:
    result, runner = run_named("ev_phase_switching")
    sim = wallbox(runner)
    assert sim.phase_changes >= 2  # down in the morning, up at noon
    switches = [d for d in charges(result) if d.intent.id.startswith(PHASE_INTENT_PREFIX)]
    assert switches
    for d in switches:
        i = at(result, (d.intent.ts - result.scenario.start).total_seconds())
        assert result.devices["wallbox"].power_w[max(0, i - 1)] == 0  # paused before the switch
    times = sorted(d.intent.ts for d in switches)
    for index, t in enumerate(times):
        within = [u for u in times[index:] if (u - t).total_seconds() < 3600]
        assert len(within) <= 4 + 1  # max 4 phase switches (plus their pauses) per hour
    one_phase = [w for w in result.devices["wallbox"].power_w if 0 < w < MIN_3PH_W]
    assert one_phase
    assert min(one_phase) >= MIN_1PH_W - 1


# --- ev_para14a ------------------------------------------------------------------------------


def test_para14a_dims_the_now_floor_and_cooldown_holds() -> None:
    result, _ = run_named("ev_para14a")
    budget = 4200
    power = result.devices["wallbox"].power_w
    for i in range(at(result, 1800 + 30), at(result, 5400)):
        assert power[i] <= budget + 1, (i * result.step_s, power[i])
    assert max(power[: at(result, 1800)]) > 11_000 - 1  # full power before the signal
    dims = [
        d
        for d in charges(result)
        if any(r.key.startswith("controller.grid.para14a") for r in d.intent.reasons)
    ]
    assert dims
    cooldown = SETTINGS.safe_state_cooldown_s
    rises = [
        (d.intent.ts - result.scenario.start).total_seconds()
        for d in charges(result)
        if isinstance(d.intent.action, ChargeAction) and d.intent.action.current_a > 6
    ]
    for t in rises:
        assert not 1800 <= t < 5400 + cooldown, t
        assert not 7200 <= t < 7800 + cooldown, t


# --- ev_arrives_late (planner + controller + gate + battery lock, automatic) -----------------

PLAN_EVERY_STEPS = 30  # 15 minutes at 30 s per step


async def arrives_late_run(
    lose_lock_at_s: float | None = None,
) -> tuple[RunResult, ScenarioRunner, list[Plan]]:
    from suncore.app.planning import (  # noqa: PLC0415
        LiveReading,
        PlanningService,
        PlanningSettings,
        PlanningSnapshot,
    )
    from suncore.sim.planner_flex import flex_situation  # noqa: PLC0415
    from suncore.sim.planner_scenarios import situation_inputs  # noqa: PLC0415

    scenario = load_scenario("ev_arrives_late")
    base = flex_situation("ev_arrives_late")
    runner = ScenarioRunner(scenario, catalog(), import_price=base.import_price)
    plans: list[Plan] = []
    assert base.flex is not None

    def live() -> LiveReading:
        last = runner._steps[-1] if runner._steps else None
        return LiveReading(soc_pct=None if last is None else last.soc_pct)

    async def collect(now: datetime, previous: Plan | None) -> PlanningSnapshot:
        sim = wallbox(runner)
        arrival = base.flex.ev_live["wallbox"].arrival if base.flex is not None else None
        connected = sim.connected_at(now)
        flex = FlexRequest(
            ev_states=dict(runner.ev_states),
            ev_live={
                "wallbox": EvLive(
                    soc_pct=sim.soc_pct,
                    connected=connected,
                    arrival=None if connected else arrival,
                )
            },
        )
        soc = live().soc_pct or 60.0
        inputs = situation_inputs(replace(base, now=now, soc_pct=soc, flex=flex))
        return PlanningSnapshot(replace(inputs, previous=previous))

    planning = PlanningService(collect, live, clock=runner.vt.clock, settings=PlanningSettings())
    planning.add_listener(lambda: plans.append(planning.plan))  # type: ignore[arg-type]
    runner.plan_provider = planning.active_plan
    if lose_lock_at_s is not None:

        async def lose_lock(r: ScenarioRunner) -> None:
            # the battery goes to shadow: the gate releases it, the discharge lock is gone
            r.modes.set_battery(OperatingMode.SHADOW)

        runner.at(lose_lock_at_s, lose_lock)
    await runner.start()
    for index in range(scenario.steps):
        if index % PLAN_EVERY_STEPS == 0:
            await planning.tick()
        await runner.step(index)
    result = await runner.finish()
    await runner.close()
    return result, runner, plans


@pytest.fixture(scope="module")
def late() -> tuple[RunResult, ScenarioRunner, list[Plan]]:
    return asyncio.run(arrives_late_run())


LOSE_LOCK_AT_S = 12_600.0  # 02:00, the car charges from the grid on the plan's floor


@pytest.fixture(scope="module")
def late_lock_lost() -> tuple[RunResult, ScenarioRunner, list[Plan]]:
    return asyncio.run(arrives_late_run(LOSE_LOCK_AT_S))


def test_a_lock_lost_while_charging_sheds_the_car_within_a_tick_and_settle(
    late_lock_lost: tuple[RunResult, ScenarioRunner, list[Plan]],
) -> None:
    """Review 6-I A2: the battery leaves automatic mid-charge (its lock is released); the car
    is paused within one tick plus the settle time, min run waived, and never charges from
    the house battery afterwards."""
    result, _, _ = late_lock_lost
    assert result.of_kind(DecisionKind.FAILED) == []
    power = result.devices["wallbox"].power_w
    lost = at(result, LOSE_LOCK_AT_S)
    assert any(w > 0 for w in power[lost - 10 : lost])  # it was charging when the lock went
    grace = math.ceil((30 + 2 * result.step_s) / result.step_s)  # settle + tick (+ read-back)
    for i in range(lost + grace, len(power)):
        if power[i] > 0:
            assert result.steps[i].battery_w >= -50, (result.steps[i].t, result.steps[i].battery_w)
    assert all(w == 0 for w in power[lost + grace :])  # no lock, no surplus at night: paused
    sheds = [
        d
        for d in result.accepted("wallbox")
        if any(r.key == "controller.ev.battery_wait" for r in d.intent.reasons)
    ]
    assert sheds


def _grid_hours(plans: Sequence[Plan]) -> set[int]:
    return {
        i.start.hour
        for p in plans
        for i in p.intervals
        if "wallbox" in i.devices and (i.devices["wallbox"].grid_power_max_w or 0) > 0
    }


def test_the_late_car_reaches_its_target_in_the_cheap_hours(
    late: tuple[RunResult, ScenarioRunner, list[Plan]],
) -> None:
    result, runner, plans = late
    assert result.of_kind(DecisionKind.REJECTED) == []
    assert result.of_kind(DecisionKind.FAILED) == []
    assert _grid_hours(plans)  # the planner permitted grid power for the car
    sim = wallbox(runner)
    # The 6-H planner stops permitting grid power at ~77 % (target 80 %): see plan §8 (6-I).
    assert sim.soc_pct >= 75.0
    power = result.devices["wallbox"].power_w
    assert all(w == 0 for w in power[: at(result, 1800)])  # not before it arrives
    charging = [result.steps[i].t for i, w in enumerate(power) if w > 0]
    assert charging
    assert all(1 <= t.hour < 5 for t in charging)  # 01:00-05:00 cheap window only
    limit = result.scenario.control.import_limit_w
    assert limit is not None
    assert all(s.grid_w <= limit + 1 for s in result.steps)


def test_the_house_battery_never_discharges_into_the_car(
    late: tuple[RunResult, ScenarioRunner, list[Plan]],
) -> None:
    result, runner, _ = late
    power = result.devices["wallbox"].power_w
    for i, w in enumerate(power):
        if w > 0:
            # battery + = charging; a discharge while the car draws would feed the car (E6).
            assert result.steps[i].battery_w >= -50, (result.steps[i].t, result.steps[i].battery_w)
    locks = [
        d
        for d in runner.battery_decisions()
        if d.kind is DecisionKind.ACCEPTED
        and any(
            r.key in ("controller.battery.ev_lock", "controller.battery.discharge_lock")
            for r in d.intent.reasons
        )
    ]
    assert locks
    first_charge = next(i for i, w in enumerate(power) if w > 0)
    assert locks[0].ts <= result.steps[first_charge].t
