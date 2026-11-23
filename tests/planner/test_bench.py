# SPDX-License-Identifier: Apache-2.0
"""Solve-time budget of the reference house (risk R3; ``scripts/bench_planner.py``).

ADR-0003 budget: 8 s on the target hardware, < 10 s on a Raspberry Pi 4 (BRIEF §2). CI's x86
runner must stay far below that: < 3 s for the whole run (inputs, heuristic, LP, plan).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bench_planner.py"
CI_BUDGET_S = 3.0


@pytest.mark.slow
def test_reference_house_plans_within_the_ci_budget() -> None:
    spec = importlib.util.spec_from_file_location("bench_planner", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.bench(3)
    assert result["source"] == "solver"
    assert result["status"] == "optimal"
    assert result["binaries"] == 0
    assert result["plan_total_s"] < CI_BUDGET_S
    assert result["build_inputs_s"] + result["plan_total_s"] < CI_BUDGET_S
