# SPDX-License-Identifier: Apache-2.0
"""Root-cause probe of the late-car gap (phase 7, WP9; findings and fix in
``docs/research/late-car-gap.md``).

Before 7-K, ``ev_arrives_late`` ended at about 77 % of an 80 % target while no plan reported a
shortfall: plans made 14.5 minutes into their first slot booked the whole slot. The probe
compares, for every plan, the energy the plan puts into its first slot with what the charger
can still deliver in it, at the deliberately late cadence (every 30 steps, 30 s before each
quarter boundary) and at the service's own cadence (ticked every step, the service fires once
per quarter 20 s after the boundary).
"""

from __future__ import annotations

import asyncio

import pytest

import tests.sim.test_ev_scenarios as ev
from suncore.core.control import Plan
from suncore.sim.runner import RunResult, ScenarioRunner

pytestmark = pytest.mark.simulator

CAPACITY_WH = 60_000.0
EFFICIENCY = 0.9  # planner spec default and SimWallbox default: no mismatch
START_SOC_PCT = 30.0
TARGET_SOC_PCT = 80.0
QUARTER_S = 900.0
MAX_W = 11_040.0  # 16 A x 3 x 230 V

Run = tuple[RunResult, ScenarioRunner, list[Plan]]


@pytest.fixture(scope="module")
def harness_cadence() -> Run:
    """The step-1 harness cadence: the planner ticks every 30 steps, 14.5 min late."""
    return asyncio.run(ev.arrives_late_run(every_steps=ev.LATE_PLAN_EVERY_STEPS))


@pytest.fixture(scope="module")
def service_cadence() -> Run:
    """The planner ticked every step: its own scheduler fires at quarter + offset."""
    return asyncio.run(ev.arrives_late_run(every_steps=1))


def _soc_at(result: RunResult, index: int) -> float:
    """The car's SoC before step ``index``, rebuilt from the charger's power trace."""
    drawn_wh = sum(w * result.step_s / 3600.0 for w in result.devices["wallbox"].power_w[:index])
    return START_SOC_PCT + drawn_wh * EFFICIENCY / CAPACITY_WH * 100.0


def _step_index(result: RunResult, plan: Plan) -> int:
    assert plan.created_at is not None
    return max(0, round((plan.created_at - result.steps[0].t).total_seconds() / result.step_s))


def test_the_planner_and_the_sim_agree_on_the_charging_losses(harness_cadence: Run) -> None:
    """Ruled out: efficiency. Planner need and what the sim stores per drawn Wh match."""
    result, runner, _ = harness_cadence
    sim = ev.wallbox(runner)
    assert sim.p.efficiency == EFFICIENCY
    assert sim.p.capacity_wh == CAPACITY_WH
    stored_wh = (sim.soc_pct - START_SOC_PCT) / 100.0 * CAPACITY_WH
    assert stored_wh == pytest.approx(sim.charged_wh * EFFICIENCY, rel=1e-6)
    # no hidden consumer: the wallbox trace integrates to the sim's own counter
    traced_wh = sum(result.devices["wallbox"].power_w) * result.step_s / 3600.0
    assert traced_wh == pytest.approx(sim.charged_wh, rel=1e-3)


def test_no_plan_books_the_elapsed_part_of_its_first_slot(harness_cadence: Run) -> None:
    """The fix (7-K): the plan counts only what is left of its first quarter (30 s here). The
    car's grid band of that slot is a power, so the energy it stands for is at most the band
    times the remaining time; the plan's EV energy still adds up to the need."""
    result, _, plans = harness_cadence
    late = [p for p in plans[1:] if p.created_at is not None]
    assert late
    for plan in late:
        assert plan.created_at is not None
        first = plan.intervals[0]
        left_s = QUARTER_S - (plan.created_at - first.start).total_seconds()
        assert left_s == pytest.approx(QUARTER_S - ev.LATE_S)
    # The energy slot 0 stands for is what the charger can deliver in the 30 s left; while a
    # large need is open, the plan puts it into real slots (one step of charging as slack: the
    # SoC is rebuilt from the trace at the step, the planner read it just before).
    for plan in late:
        band = plan.intervals[0].devices.get("wallbox")
        grid_w = 0.0 if band is None else band.grid_power_max_w or 0.0
        slot0_wh = grid_w * (QUARTER_S - ev.LATE_S) / 3600.0
        assert slot0_wh <= MAX_W * (QUARTER_S - ev.LATE_S) / 3600.0 + 1.0
        need_wh = (
            (TARGET_SOC_PCT - _soc_at(result, _step_index(result, plan)))
            / 100.0
            * CAPACITY_WH
            / EFFICIENCY
        )
        later_wh = sum(
            (i.devices["wallbox"].grid_power_max_w or 0.0) * QUARTER_S / 3600.0
            for i in plan.intervals[1:]
            if "wallbox" in i.devices
        )
        if need_wh > 2 * MAX_W * QUARTER_S / 3600.0:
            slack_wh = MAX_W * result.step_s / 3600.0 + 1.0
            assert later_wh + slot0_wh >= need_wh - slack_wh, (plan.created_at, need_wh)


def test_the_late_car_reaches_80_percent_at_the_harness_cadence(harness_cadence: Run) -> None:
    _, runner, _ = harness_cadence
    assert ev.wallbox(runner).soc_pct >= TARGET_SOC_PCT - 1.0


def test_the_late_car_reaches_80_percent_when_plans_are_fresh(service_cadence: Run) -> None:
    """With the plan made at quarter + offset the same controller meets the target."""
    result, runner, plans = service_cadence
    assert ev.wallbox(runner).soc_pct >= TARGET_SOC_PCT - 1.0
    for plan in plans[1:]:
        assert plan.created_at is not None
        late_s = (plan.created_at - plan.intervals[0].start).total_seconds()
        assert late_s < 60.0, late_s  # fresh: no elapsed slot to book
    assert result.of_kind(ev.DecisionKind.REJECTED) == []
