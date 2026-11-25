# SPDX-License-Identifier: Apache-2.0
"""Solve-time budget of the reference house (risk R3; ``scripts/bench_planner.py``).

ADR-0003 budget: 8 s on the target hardware, < 10 s on a Raspberry Pi 4 (BRIEF §2). CI's x86
runner must stay far below that: < 3 s for the whole run (inputs, heuristic, LP, plan). The
Phase 6 "full house" (reference house plus one EV, one heat pump and two programs, plan §1.7) is
a MILP; its budget is 10 s on the CI arm runner, and CI's x86 runner checks < 5 s.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bench_planner.py"
CI_BUDGET_S = 3.0
FULL_HOUSE_CI_BUDGET_S = 5.0


def _bench_module() -> Any:
    spec = importlib.util.spec_from_file_location("bench_planner", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.slow
def test_reference_house_plans_within_the_ci_budget() -> None:
    result = _bench_module().bench(3)
    assert result["source"] == "solver"
    assert result["status"] == "optimal"
    assert result["binaries"] == 0
    assert result["plan_total_s"] < CI_BUDGET_S
    assert result["build_inputs_s"] + result["plan_total_s"] < CI_BUDGET_S


@pytest.mark.slow
def test_full_house_plans_within_the_ci_budget() -> None:
    result = _bench_module().bench(3, "full_house")
    assert result["source"] == "solver"
    assert result["status"] == "optimal"
    assert result["flex_loads"] == 4
    assert result["binaries"] > 0
    assert result["objective"] <= result["heuristic_objective"] + 1e-6
    assert result["plan_total_s"] < FULL_HOUSE_CI_BUDGET_S
    assert result["build_inputs_s"] + result["plan_total_s"] < FULL_HOUSE_CI_BUDGET_S
