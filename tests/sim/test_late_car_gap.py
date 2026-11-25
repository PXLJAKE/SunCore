# SPDX-License-Identifier: Apache-2.0
"""Root-cause probe of the late-car gap (phase 7, WP9 step 1; findings in
``docs/research/late-car-gap.md``).

``ev_arrives_late`` ends at about 77 % of an 80 % target while no plan reports a shortfall. The
probe compares, for every plan, the energy the plan puts into its first slot with what the
charger can still deliver in it, and re-runs the scenario with the planner ticked every step
(the service fires once per quarter, 20 s after the boundary) instead of the harness' 15-minute
cadence, which lands 30 s before each quarter boundary.
"""

from __future__ import annotations

import asyncio
from unittest import mock

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

Run = tuple[RunResult, ScenarioRunner, list[Plan]]


@pytest.fixture(scope="module")
def harness_cadence() -> Run:
    """The scenario as the closed-loop tests run it: the planner ticks every 30 steps."""
    return asyncio.run(ev.arrives_late_run())


@pytest.fixture(scope="module")
def service_cadence() -> Run:
    """The planner ticked every step: its own scheduler fires at quarter + offset."""
    with mock.patch.object(ev, "PLAN_EVERY_STEPS", 1):
        return asyncio.run(ev.arrives_late_run())


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


def test_every_plan_books_the_elapsed_part_of_its_first_slot(harness_cadence: Run) -> None:
    """Cause: the plan counts the full first quarter although 14.5 of its 15 minutes have
    passed when it is made. In the last plans the whole remaining need sits in
    that first slot and the energy cannot be delivered any more."""
    result, _, plans = harness_cadence
    phantom_wh = 0.0
    last_with_grid = None
    for plan in plans:
        first = plan.intervals[0]
        band = first.devices["wallbox"]
        grid_w = band.grid_power_max_w or 0.0
        if grid_w <= 0:
            continue
        assert plan.created_at is not None
        left_s = QUARTER_S - (plan.created_at - first.start).total_seconds()
        assert 0 <= left_s <= QUARTER_S
        booked_wh = grid_w * QUARTER_S / 3600.0
        possible_wh = grid_w * left_s / 3600.0
        phantom_wh += booked_wh - possible_wh
        last_with_grid = plan, booked_wh, possible_wh
    assert last_with_grid is not None
    plan, booked_wh, possible_wh = last_with_grid
    need_wh = (
        (TARGET_SOC_PCT - _soc_at(result, _step_index(result, plan)))
        / 100.0
        * CAPACITY_WH
        / EFFICIENCY
    )
    assert plan.created_at is not None
    assert (plan.created_at - plan.intervals[0].start).total_seconds() == pytest.approx(870.0)
    assert booked_wh >= need_wh - 1.0  # the plan reports "covered"
    assert possible_wh < 0.05 * need_wh  # while 30 s of the slot remain
    assert phantom_wh > 0


@pytest.mark.xfail(
    reason="late-car gap: plans are made 14.5 min into their first slot and book its elapsed "
    "energy, see docs/research/late-car-gap.md",
    strict=True,
)
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
