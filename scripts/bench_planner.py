#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Benchmark of the planner on the maintainer's reference house (Phase 4 WP8, risk R3).

17 kWp east-west, 29.6 kWh battery (10 kW), Tibber-like 15-minute prices for 48 h, export limit
13.5 kW, tiered EEG export price blended by kWp share, two climate devices with a value
(``suncore.sim.planner_scenarios.reference_situation``). Prints the median of ``--runs`` runs:
building the inputs (incl. the risk model's targets), the HiGHS solve cold and warm-started from
the previous plan, the heuristic, and the model size. The budget (ADR-0003, BRIEF §2) is 10 s on
a Raspberry Pi 4; CI checks < 3 s on x86 (``tests/planner/test_bench.py``).

Usage: ``uv run python scripts/bench_planner.py [--runs 5] [--json]``.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time

from suncore.planner.heuristic import heuristic_schedule
from suncore.planner.run import run
from suncore.sim.planner_scenarios import reference_situation, situation_inputs


def bench(runs: int) -> dict[str, float | int | str]:
    situation = reference_situation()
    build, cold, warm, heuristic, total = [], [], [], [], []
    stats: dict[str, float | int | str] = {}
    for _ in range(runs):
        started = time.perf_counter()
        inputs = situation_inputs(situation)
        build.append(time.perf_counter() - started)
        started = time.perf_counter()
        first = run(inputs)
        total.append(time.perf_counter() - started)
        second = run(inputs, warm_start=first.plan)
        assert first.solver is not None
        assert second.solver is not None
        cold.append(first.solver.solve_time_s)
        warm.append(second.solver.solve_time_s)
        started = time.perf_counter()
        heuristic_schedule(inputs)
        heuristic.append(time.perf_counter() - started)
        plan_stats = first.plan.stats
        assert plan_stats is not None
        stats = {
            "source": first.source,
            "status": plan_stats.status,
            "slots": inputs.n,
            "vars": first.n_vars,
            "rows": first.n_rows,
            "binaries": plan_stats.n_binaries,
            "iterations_cold": first.solver.iterations,
            "iterations_warm": second.solver.iterations,
            "objective": round(first.objective, 4),
            "heuristic_objective": round(first.heuristic_objective, 4),
        }
    return {
        "machine": f"{platform.machine()} {platform.python_implementation()} "
        f"{platform.python_version()}",
        "runs": runs,
        "build_inputs_s": round(statistics.median(build), 4),
        "solve_cold_s": round(statistics.median(cold), 4),
        "solve_warm_s": round(statistics.median(warm), 4),
        "heuristic_s": round(statistics.median(heuristic), 4),
        "plan_total_s": round(statistics.median(total), 4),
        **stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = bench(max(1, args.runs))
    if args.json:
        print(json.dumps(result))
    else:
        for key, value in result.items():
            print(f"{key:20s} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
