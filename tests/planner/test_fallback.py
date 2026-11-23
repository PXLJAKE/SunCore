# SPDX-License-Identifier: Apache-2.0
"""The planner's fallback chain (ADR-0003): every solver outcome ends in a usable plan."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from suncore.planner import reasons as rk
from suncore.planner.inputs import PlannerInputs
from suncore.planner.model import (
    FloatArray,
    HighsBackend,
    LinearProblem,
    SolverResult,
    build_problem,
    economics,
    objective,
    violations,
)
from suncore.planner.run import plan, run
from suncore.sim.planner_scenarios import scenario_inputs


@dataclass
class FakeBackend:
    """Returns a fixed outcome; records what it was asked."""

    status: str
    x: str = "none"
    raise_exc: BaseException | None = None
    name: str = "fake"
    calls: int = 0
    time_limit_s: float = 0.0
    warm: FloatArray | None = None

    def solve(
        self,
        problem: LinearProblem,
        *,
        time_limit_s: float,
        mip_gap: float,  # noqa: ARG002
        warm_start: FloatArray | None = None,
    ) -> SolverResult:
        self.calls += 1
        self.time_limit_s = time_limit_s
        self.warm = warm_start
        if self.raise_exc is not None:
            raise self.raise_exc
        x = None
        if self.x == "zeros":
            x = np.zeros(problem.n_cols)
        elif self.x == "warm":
            x = warm_start
        elif self.x == "optimal":
            x = HighsBackend().solve(problem, time_limit_s=5, mip_gap=0.0).x
        return SolverResult(self.status, x, None, 0.01)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def inputs() -> PlannerInputs:
    return scenario_inputs("two_poor_days")


def _first(result_plan_reasons: tuple[object, ...]) -> str:
    return result_plan_reasons[0].key  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("backend", "key"),
    [
        (FakeBackend("time_limit"), rk.FALLBACK_TIMEOUT),
        (FakeBackend("infeasible"), rk.FALLBACK_INFEASIBLE),
        (FakeBackend("error"), rk.FALLBACK_ERROR),
        (FakeBackend("optimal", raise_exc=RuntimeError("boom")), rk.FALLBACK_ERROR),
        (FakeBackend("optimal", raise_exc=ImportError("highspy")), rk.FALLBACK_SOLVER_UNAVAILABLE),
        (FakeBackend("optimal", x="zeros"), rk.FALLBACK_INVALID_SOLUTION),
    ],
)
def test_every_failure_falls_back_to_the_heuristic(
    inputs: PlannerInputs, backend: FakeBackend, key: str
) -> None:
    result = run(inputs, backend=backend)
    assert result.source == "heuristic"
    assert result.plan.source == "heuristic"
    assert _first(result.plan.reasons) == key
    assert result.plan.reasons[1].key == rk.HEURISTIC_RULES
    assert result.plan.stats is not None
    assert result.plan.stats.status == "heuristic"
    assert violations(inputs, result.schedule) == []
    assert len(result.plan.intervals) == inputs.n
    assert all(i.reasons for i in result.plan.intervals)


def test_a_feasible_incumbent_on_timeout_is_used(inputs: PlannerInputs) -> None:
    result = run(inputs, backend=FakeBackend("time_limit", x="optimal"))
    assert result.source == "solver"
    assert _first(result.plan.reasons) == rk.SOLVER_INCUMBENT


def test_an_incumbent_costlier_than_the_heuristic_is_replaced(inputs: PlannerInputs) -> None:
    # The warm start (previous plan or heuristic, repaired) is feasible but not optimal; when
    # it is costlier than the heuristic itself the heuristic wins.
    econ = economics(inputs)
    result = run(inputs, backend=FakeBackend("time_limit", x="warm"))
    assert result.objective <= result.heuristic_objective + 1e-9
    assert result.source in ("solver", "heuristic")
    if result.source == "heuristic":
        assert _first(result.plan.reasons) == rk.FALLBACK_HEURISTIC_BETTER
    assert objective(inputs, result.schedule, econ) == pytest.approx(result.objective)


def test_the_time_budget_reaches_the_solver(inputs: PlannerInputs) -> None:
    backend = FakeBackend("optimal", x="optimal")
    run(inputs, budget_s=2.5, backend=backend)
    assert 0 < backend.time_limit_s <= 2.5


def test_a_real_timeout_without_a_solution_falls_back() -> None:
    inputs = scenario_inputs("reference_house")
    result = run(inputs, budget_s=0.0)
    # 0 s budget = the minimum solver time; whatever HiGHS achieves, the plan is valid.
    assert result.source in ("solver", "heuristic")
    assert violations(inputs, result.schedule) == []


def test_plan_returns_the_plan(inputs: PlannerInputs) -> None:
    built = plan(inputs, backend=FakeBackend("infeasible"))
    assert built.source == "heuristic"


def test_highs_reports_an_infeasible_model() -> None:
    problem = LinearProblem(
        cost=np.array([1.0]),
        col_lower=np.array([0.0]),
        col_upper=np.array([1.0]),
        row_lower=np.array([2.0]),
        row_upper=np.array([np.inf]),
        a_start=np.array([0, 1], dtype=np.int64),
        a_index=np.array([0], dtype=np.int64),
        a_value=np.array([1.0]),
    )
    result = HighsBackend().solve(problem, time_limit_s=1, mip_gap=0.01)
    assert result.status == "infeasible"
    assert result.x is None


def test_highs_solves_integer_columns() -> None:
    problem = LinearProblem(
        cost=np.array([-1.0, -1.0]),
        col_lower=np.array([0.0, 0.0]),
        col_upper=np.array([10.0, 10.0]),
        row_lower=np.array([-np.inf]),
        row_upper=np.array([3.5]),
        a_start=np.array([0, 2], dtype=np.int64),
        a_index=np.array([0, 1], dtype=np.int64),
        a_value=np.array([1.0, 1.0]),
        integrality=(0, 1),
    )
    result = HighsBackend().solve(problem, time_limit_s=1, mip_gap=0.0)
    assert result.status == "optimal"
    assert result.x is not None
    assert sum(result.x) == pytest.approx(3.0)
    assert result.mip_gap is not None


def test_the_highs_backend_is_available() -> None:
    assert HighsBackend.available()


def test_the_warm_start_is_feasible_and_reaches_the_solver(inputs: PlannerInputs) -> None:
    backend = FakeBackend("optimal", x="optimal")
    first = run(inputs)
    run(inputs, backend=backend, warm_start=first.plan)
    assert backend.warm is not None
    problem, _ = build_problem(inputs, economics(inputs))
    assert len(backend.warm) == problem.n_cols
    assert np.all(backend.warm >= problem.col_lower - 1e-9)
    assert np.all(backend.warm <= problem.col_upper + 1e-9)


def test_a_warm_start_from_the_previous_plan_keeps_the_optimum(inputs: PlannerInputs) -> None:
    cold = run(inputs)
    warm = run(inputs, warm_start=cold.plan)
    assert warm.objective == pytest.approx(cold.objective, rel=1e-6, abs=1e-6)
    assert cold.solver is not None
    assert warm.solver is not None
    assert warm.solver.iterations <= cold.solver.iterations
