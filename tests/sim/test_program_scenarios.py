# SPDX-License-Identifier: Apache-2.0
"""The shiftable-program scenarios of PR 6-L, closed-loop (plan WP12).

``program_deadline_plug`` (without a plan: the deadline guarantee; with the real 6-H planner:
the planned window), ``program_notify_start`` and ``program_crash_recovery``. Judged by the
physical appliance (started, interrupted, finished), the plug's switch count under cloud flicker,
the gate's decisions and the job states.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest

from suncore.controller.programs import ProgramEvent
from suncore.core.control import Plan
from suncore.core.decisions import DecisionKind, ReleaseAction, SwitchAction
from suncore.core.flex import JobState, ProgramJob
from suncore.planner.flex import FlexRequest
from suncore.sim.devices import SimProgram
from suncore.sim.runner import RunResult, ScenarioRunner
from suncore.sim.scenario import Fault, load_scenario
from tests.sim.helpers import catalog, run_named, run_scenario

pytestmark = pytest.mark.simulator

DEADLINE_S = 39_600  # 18:00
LATEST_S = 32_400  # 16:00 = deadline - 120 min


def offset(result: RunResult, at: datetime) -> float:
    return (at - result.scenario.start).total_seconds()


def appliance(runner: ScenarioRunner, device_id: str = "washer") -> SimProgram:
    sim = runner.sim_devices[device_id]
    assert isinstance(sim, SimProgram)
    return sim


def switches(result: RunResult, device_id: str = "washer") -> list[tuple[float, bool]]:
    return [
        (offset(result, d.ts), d.intent.action.on)
        for d in result.accepted(device_id)
        if isinstance(d.intent.action, SwitchAction)
    ]


def jobs(runner: ScenarioRunner) -> list[ProgramJob]:
    return list(runner.programs._jobs.values())


def events(runner: ScenarioRunner) -> list[ProgramEvent]:
    return [e for e in runner.program_events if isinstance(e, ProgramEvent)]


# --- program_deadline_plug without a plan: the deadline guarantee ----------------------------


def test_plug_program_is_powered_down_at_arming_and_started_once_at_the_latest_start() -> None:
    result, runner = run_named("program_deadline_plug")
    assert result.of_kind(DecisionKind.REJECTED) == []
    assert result.of_kind(DecisionKind.FAILED) == []
    moves = switches(result)
    assert [on for _, on in moves] == [False, True]
    assert moves[0][0] < 120  # P4: right after arming, while the machine only locks its door
    assert LATEST_S <= moves[1][0] < LATEST_S + 60  # no plan: the latest start that still fits
    sim = appliance(runner)
    assert sim.runs == 1
    assert sim.interruptions == 0  # P2: never interrupted (the cut happened in the lag phase)
    assert offset(result, sim.finished_at[0]) <= DEADLINE_S
    (job,) = jobs(runner)
    assert job.state is JobState.DONE
    assert [e.kind for e in events(runner)] == ["started", "done"]


def test_plug_program_is_released_to_on_after_it_finished() -> None:
    result, runner = run_named("program_deadline_plug")
    releases = [d for d in result.accepted("washer") if isinstance(d.intent.action, ReleaseAction)]
    assert len(releases) == 1
    assert offset(result, releases[0].ts) > offset(result, appliance(runner).finished_at[0])
    assert appliance(runner).plug_on  # P3: the baseline is on
    rt = runner.stack.gate.runtime_of("washer") if runner.stack is not None else None
    assert rt is not None
    assert not rt.under_control


def test_cloud_flicker_does_not_switch_the_program() -> None:
    result, runner = run_named("program_deadline_plug")
    # the machine runs twice in physical terms (the door lock before the cut, the programme):
    # loaded, cut at arming, started, finished - the flicker never moves it again
    assert len(result.transitions("washer")) == 4
    assert len(switches(result)) == 2  # one cut at arming, one start
    assert appliance(runner).interruptions == 0


def test_shadow_writes_nothing_and_the_machine_runs_as_the_user_left_it() -> None:
    result, runner = run_named("program_deadline_plug", shadow=True)
    assert result.accepted() == []
    sim = appliance(runner)
    assert sim.runs == 1
    assert offset(result, sim.started_at[0]) < 120  # it was never powered down
    (job,) = jobs(runner)
    assert job.state is JobState.DONE  # seen running by its power, then done


def _with_fault(fault: Fault) -> Any:
    scenario = load_scenario("program_deadline_plug")
    return scenario.model_copy(update={"faults": (fault,)})


def test_a_stale_power_sensor_never_starts_the_program_but_its_safe_state_is_on() -> None:
    """Fail-safe: SunCore issues no start while the appliance's power is unknown. The watchdog
    trips the device and releases it; the release restores the plug's baseline (on, P3), so the
    machine resumes - the safe state of a program is "running rather than stranded"."""
    fault = Fault(kind="unavailable", target="sensor.sim_washer_power", from_s=31_800, to_s=33_600)
    result, runner = run_scenario(_with_fault(fault))
    assert [t for t, on in switches(result) if on] == []  # no start by SunCore at all
    releases = [d for d in result.accepted("washer") if isinstance(d.intent.action, ReleaseAction)]
    assert releases
    assert "safety.watchdog_unavailable" in [r.key for r in releases[0].reasons]
    assert offset(result, releases[0].ts) >= 31_800
    sim = appliance(runner)
    assert sim.runs == 1
    assert sim.interruptions == 0
    (job,) = jobs(runner)
    assert job.state is JobState.DONE  # seen running once the sensor was back


# --- program_deadline_plug with the real 6-H planner ------------------------------------------

PLAN_EVERY_STEPS = 30  # 15 minutes at 30 s per step


async def planned_run() -> tuple[RunResult, ScenarioRunner, list[Plan]]:
    from suncore.app.planning import (  # noqa: PLC0415
        LiveReading,
        PlanningService,
        PlanningSettings,
        PlanningSnapshot,
    )
    from suncore.sim.planner_flex import flex_situation  # noqa: PLC0415
    from suncore.sim.planner_scenarios import situation_inputs  # noqa: PLC0415

    scenario = load_scenario("program_deadline_plug")
    base = flex_situation("program_deadline")
    runner = ScenarioRunner(scenario, catalog(), import_price=base.import_price)
    plans: list[Plan] = []

    def live() -> LiveReading:
        last = runner._steps[-1] if runner._steps else None
        return LiveReading(soc_pct=None if last is None else last.soc_pct)

    async def collect(now: datetime, previous: Plan | None) -> PlanningSnapshot:
        flex = FlexRequest(jobs=tuple(runner.programs.all_open()))
        soc = live().soc_pct or 50.0
        inputs = situation_inputs(replace(base, now=now, soc_pct=soc, flex=flex))
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
    await runner.close()
    return result, runner, plans


@pytest.fixture(scope="module")
def planned() -> tuple[RunResult, ScenarioRunner, list[Plan]]:
    return asyncio.run(planned_run())


def test_the_planner_picks_the_window_and_the_program_starts_in_it(
    planned: tuple[RunResult, ScenarioRunner, list[Plan]],
) -> None:
    result, runner, plans = planned
    assert result.of_kind(DecisionKind.FAILED) == []
    (job,) = jobs(runner)
    assert job.state is JobState.DONE
    starts = [t for t, on in switches(result) if on]
    assert len(starts) == 1
    started = result.scenario.start + timedelta(seconds=starts[0])
    planned_starts = {
        i.start
        for p in plans
        for i in p.intervals
        if (band := i.devices.get("washer")) is not None and band.start
    }
    assert planned_starts, "the planner must plan a start"
    assert any(s <= started < s + timedelta(minutes=15) for s in planned_starts)
    assert starts[0] < LATEST_S  # earlier than the deadline fallback: the planned window
    sim = appliance(runner)
    assert sim.runs == 1
    assert sim.interruptions == 0
    assert offset(result, sim.finished_at[0]) <= DEADLINE_S


def test_the_planned_start_imports_nothing_beyond_the_import_limit(
    planned: tuple[RunResult, ScenarioRunner, list[Plan]],
) -> None:
    result, _, _ = planned
    limit = result.scenario.control.import_limit_w
    assert limit is not None
    assert max(s.grid_w for s in result.steps) <= limit


# --- program_notify_start ---------------------------------------------------------------------


def test_notify_program_is_never_written_and_asks_once() -> None:
    result, runner = run_named("program_notify_start")
    assert [d for d in result.decisions if d.intent.asset_id == "dishwasher"] == []
    assert [c for c in runner.conn.call_log if any("dishwasher" in e for e in c.entity_ids)] == []
    kinds = [(e.kind, offset(result, e.ts)) for e in events(runner)]
    assert kinds[0] == ("start_now", 30_600.0)  # 16:30, the latest start for 18:30
    assert [k for k, _ in kinds] == ["start_now", "started", "done"]
    (job,) = jobs(runner)
    assert job.state is JobState.DONE
    assert job.started_at is not None
    assert offset(result, job.started_at) >= 30_720


def test_notify_shadow_still_asks_and_writes_nothing() -> None:
    result, runner = run_named("program_notify_start", shadow=True)
    assert result.accepted() == []
    assert next(e.kind for e in events(runner)) == "start_now"


# --- program_crash_recovery --------------------------------------------------------------------

CRASH_S = 7_200  # 09:00
RESTART_S = 9_000  # 09:30


async def crash_run() -> tuple[RunResult, ScenarioRunner, dict[str, Any]]:
    runner = ScenarioRunner(load_scenario("program_crash_recovery"), catalog())
    seen: dict[str, Any] = {}

    async def crash(r: ScenarioRunner) -> None:
        assert r.stack is not None
        await r.stack.crash()
        seen["plug_at_crash"] = "on" if appliance(r).plug_on else "off"

    async def restart(r: ScenarioRunner) -> None:
        await r.restart()

    runner.at(CRASH_S, crash)
    runner.at(RESTART_S, restart)
    try:
        result = await runner.run()
    finally:
        await runner.close()
    return result, runner, seen


@pytest.fixture(scope="module")
def crashed() -> tuple[RunResult, ScenarioRunner, dict[str, Any]]:
    return asyncio.run(crash_run())


def test_the_crash_recovery_switches_the_plug_on_and_the_program_runs(
    crashed: tuple[RunResult, ScenarioRunner, dict[str, Any]],
) -> None:
    result, runner, seen = crashed
    assert seen["plug_at_crash"] == "off"  # powered down at arming, the kill left it so
    recovery = [
        d
        for d in result.accepted("washer")
        if isinstance(d.intent.action, ReleaseAction)
        and "safety.crash_recovery" in [r.key for r in d.reasons]
    ]
    assert len(recovery) == 1
    assert RESTART_S <= offset(result, recovery[0].ts) < RESTART_S + 60
    sim = appliance(runner)
    assert sim.runs == 1
    assert sim.interruptions == 0
    assert offset(result, sim.finished_at[0]) < RESTART_S + 2 * 3600 + 120
    # the restarted controller never powers it down again
    assert [t for t, on in switches(result) if t > RESTART_S and not on] == []
    (job,) = jobs(runner)
    assert job.state is JobState.DONE
