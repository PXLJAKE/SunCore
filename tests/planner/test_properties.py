# SPDX-License-Identifier: Apache-2.0
"""Property tests of the planner (Phase 4 WP8, ADR-0003): random situations, solver and heuristic.

Every generated situation is planned by the HiGHS LP and by the heuristic; both schedules must
keep the energy balance, the SoC corridor and every bound, never charge from the grid outside
the effective policy, never feed devices from grid or battery, and the LP is never costlier
than the heuristic under the one shared cost function. A missed evening/user target is only
allowed when even an LP that maximises the SoC at that time cannot reach it.
``SUNCORE_PLANNER_EXAMPLES`` raises the number of examples for a local stress run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from hypothesis import HealthCheck, given, settings

from suncore.planner.heuristic import heuristic_schedule
from suncore.planner.inputs import PlannerInputs, PriceMode
from suncore.planner.model import (
    SOC,
    HighsBackend,
    Schedule,
    build_problem,
    economics,
    goal_shortfall_wh,
    objective,
    violations,
)
from suncore.planner.run import run
from suncore.sim.planner_scenarios import situation_inputs
from tests.planner.helpers import situations

LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "i18n" / "locales"
CATALOGUE = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
PROPS = settings(
    max_examples=int(os.environ.get("SUNCORE_PLANNER_EXAMPLES", "60")),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
TOL_W = 1.0
TOL_WH = 50.0


def _common(inputs: PlannerInputs, schedule: Schedule) -> None:
    assert violations(inputs, schedule) == []
    econ = economics(inputs)
    battery = inputs.battery
    policy = inputs.grid_charge_policy
    for t in range(inputs.n):
        # No import for devices: they only ever draw PV surplus over the base load.
        surplus = max(0.0, inputs.pv_p50[t] - inputs.load_p50[t])
        assert schedule.device_total_w(t) <= surplus + TOL_W
        # Grid charging only inside the effective policy (price level, schedule) and limits.
        if schedule.grid_charge_w[t] > TOL_W:
            assert inputs.price_mode is PriceMode.MARKET
            assert policy.allows(inputs.starts[t], inputs.import_known[t], inputs.tz)
            if inputs.import_limit_w is not None:
                cap = max(inputs.import_limit_w, inputs.load_p50[t])
                assert schedule.import_w(t) <= cap + TOL_W
        # A negative export price is never paid when curtailing is possible.
        if inputs.export_price[t] < 0:
            assert schedule.export_w[t] <= TOL_W
        if battery is not None:
            low = min(battery.reserve_wh, battery.soc_wh) - TOL_WH
            high = max(battery.max_wh, battery.soc_wh) + TOL_WH
            assert low <= schedule.soc_wh[t] <= high
    assert len(econ.goal_slots) == len(inputs.goals)


@PROPS
@given(situations())
def test_solver_plan_keeps_every_rule(situation) -> None:  # type: ignore[no-untyped-def]
    inputs = situation_inputs(situation)
    result = run(inputs)
    assert result.source == "solver"
    _common(inputs, result.schedule)
    assert result.objective <= result.heuristic_objective + 1e-6 * max(
        1.0, abs(result.heuristic_objective)
    )
    plan = result.plan
    assert len(plan.intervals) == inputs.n
    for t, interval in enumerate(plan.intervals):
        assert interval.reasons, "every interval explains itself"
        assert all(r.key in CATALOGUE for r in interval.reasons)
        band = interval.battery
        if band.grid_charge_max_w is not None:
            assert inputs.grid_charge_at(t) > 0
        if interval.expected is not None:
            e = interval.expected
            left = e.pv_p50_w + e.import_w + max(-e.battery_w, 0.0)
            right = e.load_w + e.devices_w + max(e.battery_w, 0.0) + e.export_w + e.curtail_w
            assert abs(left - right) <= 2.0
    assert all(r.key in CATALOGUE for r in plan.reasons)


@PROPS
@given(situations())
def test_heuristic_keeps_every_rule(situation) -> None:  # type: ignore[no-untyped-def]
    inputs = situation_inputs(situation)
    _common(inputs, heuristic_schedule(inputs))


def _max_soc_wh(inputs: PlannerInputs, slot: int) -> float:
    econ = economics(inputs)
    problem, layout = build_problem(inputs, econ)
    cost = np.zeros_like(problem.cost)
    cost[layout.var(slot, SOC)] = -1.0
    probe = type(problem)(
        cost=cost,
        col_lower=problem.col_lower,
        col_upper=problem.col_upper,
        row_lower=problem.row_lower,
        row_upper=problem.row_upper,
        a_start=problem.a_start,
        a_index=problem.a_index,
        a_value=problem.a_value,
    )
    result = HighsBackend().solve(probe, time_limit_s=5, mip_gap=0.0)
    assert result.x is not None
    return float(result.x[layout.var(slot, SOC)]) * 1000


@PROPS
@given(situations())
def test_targets_are_met_whenever_feasible(situation) -> None:  # type: ignore[no-untyped-def]
    inputs = situation_inputs(situation)
    result = run(inputs)
    econ = economics(inputs)
    battery = inputs.battery
    assert battery is not None
    shortfalls = goal_shortfall_wh(inputs, econ, result.schedule)
    for goal, slot, short in zip(inputs.goals, econ.goal_slots, shortfalls, strict=True):
        if slot is None or short <= TOL_WH or goal.kind not in ("evening_target", "user_target"):
            continue
        reachable = _max_soc_wh(inputs, slot)
        assert reachable < battery.model.soc_to_wh(goal.soc_pct) + TOL_WH


@PROPS
@given(situations())
def test_objective_is_the_lp_value(situation) -> None:  # type: ignore[no-untyped-def]
    inputs = situation_inputs(situation)
    result = run(inputs)
    assert result.solver is not None
    assert result.solver.objective is not None
    assert abs(objective(inputs, result.schedule) - result.solver.objective) <= 1e-4 * max(
        1.0, abs(result.solver.objective)
    )
