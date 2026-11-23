# SPDX-License-Identifier: Apache-2.0
"""Entry point of the planner (Phase 4 WP8): inputs -> Plan v2, with the fallback chain.

:func:`plan` is what the runtime (WP9) calls, in a worker thread, every 15 minutes and on
events. It never raises for a model or solver problem; the chain (ADR-0003) is:

1. the heuristic schedule is always computed first (milliseconds) - it is the fallback and
   the yardstick ("never worse than the heuristic");
2. the LP is built and solved with HiGHS within the remaining ``budget_s`` and ``mip_gap``,
   warm-started from the previous plan shifted onto the new grid (slots the previous plan
   does not cover take the heuristic's flows; the start is repaired to be feasible);
3. ``optimal`` -> the solver's plan; ``time_limit`` with a feasible incumbent -> that
   incumbent; anything else (time limit without a solution, infeasible, error, a solution
   that fails the independent feasibility check, ``highspy`` missing) -> the heuristic with
   a ``planner.fallback.*`` reason. An incumbent costlier than the heuristic is replaced by
   it (``planner.fallback.heuristic_better``). "Relaxed targets" are built in: every target
   is soft, so infeasibility can only come from a broken model.

The plan's bands follow ADR-0013: limits, not set-points. ``BatteryBand`` carries the SoC
corridor ``[effective reserve, max SoC]``, the power corridor (``min_power_w = 0`` where the
plan holds the battery: ``discharge_lock``), ``grid_charge_max_w`` only where grid charging is
planned (and therefore allowed by the policy and the spread rule), ``priority_until_soc_pct``
= the morning buffer until the plan reaches it, and ``charge_from_surplus = False`` only where
the plan gives devices the surplus while no battery target binds. ``DeviceBand``: devices
planned on surplus are ``prefer_on``; while a battery target binds they are throttled to the
planned power or switched off (``allowed = False``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import numpy as np

from suncore.core.control import (
    BandPreference,
    BatteryBand,
    DeviceBand,
    IntervalFlows,
    IntervalPrices,
    Plan,
    PlanInterval,
    PlanSource,
    SolverStats,
)
from suncore.core.decisions import Reason
from suncore.planner import reasons as rk
from suncore.planner.explain import EPS_W, SlotFacts, interval_reasons, slot_facts
from suncore.planner.heuristic import heuristic_schedule
from suncore.planner.inputs import DEFAULT_BUDGET_S, DEFAULT_MIP_GAP, PlannerInputs
from suncore.planner.model import (
    Economics,
    FloatArray,
    HighsBackend,
    Layout,
    Schedule,
    SolverBackend,
    SolverResult,
    build_problem,
    economics,
    objective,
    schedule_from_vector,
    vector_from_schedule,
    violations,
)

OBJECTIVE_TOL: Final = 1e-6
MIN_SOLVER_TIME_S: Final = 0.05


@dataclass(frozen=True, slots=True)
class PlannerRun:
    """A finished run: the plan and what produced it (tests and the benchmark read it)."""

    plan: Plan
    schedule: Schedule
    source: PlanSource
    objective: float
    heuristic_objective: float
    solver: SolverResult | None
    n_vars: int
    n_rows: int


def plan(
    inputs: PlannerInputs,
    budget_s: float = DEFAULT_BUDGET_S,
    mip_gap: float = DEFAULT_MIP_GAP,
    warm_start: Plan | None = None,
    backend: SolverBackend | None = None,
) -> Plan:
    """The Plan v2 for ``inputs`` (module doc); ``warm_start`` defaults to ``inputs.previous``."""
    return run(
        inputs, budget_s=budget_s, mip_gap=mip_gap, warm_start=warm_start, backend=backend
    ).plan


def run(  # noqa: PLR0912, PLR0915 - the fallback chain, one branch per outcome
    inputs: PlannerInputs,
    *,
    budget_s: float = DEFAULT_BUDGET_S,
    mip_gap: float = DEFAULT_MIP_GAP,
    warm_start: Plan | None = None,
    backend: SolverBackend | None = None,
) -> PlannerRun:
    """Plan with the solver, fall back to the heuristic (module doc)."""
    started = time.perf_counter()
    econ = economics(inputs)
    heuristic = heuristic_schedule(inputs, econ)
    heuristic_cost = objective(inputs, heuristic, econ)
    solver = backend or HighsBackend()
    previous = warm_start if warm_start is not None else inputs.previous
    result: SolverResult | None = None
    fallback: Reason | None = None
    chosen: Schedule | None = None
    chosen_cost = heuristic_cost
    n_vars = n_rows = 0
    n_binaries = 0
    try:
        problem, layout = build_problem(inputs, econ)
        n_vars, n_rows, n_binaries = problem.n_cols, problem.n_rows, len(problem.integrality)
        warm = warm_vector(inputs, econ, layout, heuristic, previous)
        remaining = max(MIN_SOLVER_TIME_S, budget_s - (time.perf_counter() - started))
        result = solver.solve(problem, time_limit_s=remaining, mip_gap=mip_gap, warm_start=warm)
    except ImportError:
        fallback = Reason(key=rk.FALLBACK_SOLVER_UNAVAILABLE)
    except Exception as exc:
        fallback = Reason(key=rk.FALLBACK_ERROR, args={"error": type(exc).__name__})
    if result is not None and fallback is None:
        if result.x is not None and result.status in ("optimal", "time_limit"):
            candidate = schedule_from_vector(inputs, layout, result.x)
            broken = violations(inputs, candidate, econ)
            if broken:
                fallback = Reason(
                    key=rk.FALLBACK_INVALID_SOLUTION, args={"violations": len(broken)}
                )
            else:
                cost = objective(inputs, candidate, econ)
                tolerance = OBJECTIVE_TOL * max(1.0, abs(heuristic_cost))
                if cost > heuristic_cost + tolerance:
                    fallback = Reason(
                        key=rk.FALLBACK_HEURISTIC_BETTER,
                        args={"status": result.status},
                    )
                else:
                    chosen, chosen_cost = candidate, cost
        elif result.status == "time_limit":
            fallback = Reason(key=rk.FALLBACK_TIMEOUT, args={"budget_s": round(budget_s, 2)})
        elif result.status == "infeasible":
            fallback = Reason(key=rk.FALLBACK_INFEASIBLE)
        else:
            fallback = Reason(key=rk.FALLBACK_ERROR, args={"error": result.detail or "solver"})
    elapsed = time.perf_counter() - started
    plan_reasons = list(inputs.reasons)
    source: PlanSource
    if chosen is not None and result is not None:
        source = "solver"
        key = rk.SOLVER_OPTIMAL if result.status == "optimal" else rk.SOLVER_INCUMBENT
        plan_reasons.insert(0, Reason(key=key, args={"solve_time_s": round(elapsed, 3)}))
        status: str = result.status
        schedule = chosen
    else:
        source = "heuristic"
        plan_reasons.insert(0, Reason(key=rk.HEURISTIC_RULES))
        if fallback is not None:
            plan_reasons.insert(0, fallback)
        status = "heuristic"
        schedule = heuristic
        chosen_cost = heuristic_cost
    stats = SolverStats(
        solve_time_s=round(elapsed, 4),
        status=status,
        objective=round(chosen_cost, 6),
        n_vars=n_vars,
        n_binaries=n_binaries,
        mip_gap=None if result is None else result.mip_gap,
    )
    assumptions = dict(inputs.assumptions)
    assumptions["solver"] = getattr(solver, "name", "unknown")
    if result is not None:
        assumptions["solver_status"] = result.status
    built = to_plan(inputs, econ, schedule, source=source, stats=stats, reasons=plan_reasons)
    built = built.model_copy(update={"assumptions": assumptions})
    return PlannerRun(
        plan=built,
        schedule=schedule,
        source=source,
        objective=chosen_cost,
        heuristic_objective=heuristic_cost,
        solver=result,
        n_vars=n_vars,
        n_rows=n_rows,
    )


# --- warm start -------------------------------------------------------------------------------


def _previous_battery_w(inputs: PlannerInputs, previous: Plan | None) -> dict[datetime, float]:
    if previous is None:
        return {}
    wanted = set(inputs.starts)
    out: dict[datetime, float] = {}
    for interval in previous.intervals:
        if interval.start in wanted and interval.expected is not None:
            out[interval.start] = interval.expected.battery_w
    return out


def follow(
    inputs: PlannerInputs,
    econ: Economics,
    battery_w: list[float],
    devices_w: list[list[float]],
) -> Schedule:
    """A feasible schedule that follows the wanted battery and device powers as far as the
    bounds allow (the warm start's repair: SoC re-simulated from the live SoC)."""
    n, dt = inputs.n, inputs.dt_h
    battery = inputs.battery
    gl, bl, pc, gc, ex, cu, soc = ([0.0] * n for _ in range(7))
    dev = [[0.0] * n for _ in inputs.devices]
    e = battery.soc_wh if battery else 0.0
    floor = min(battery.reserve_wh, e) if battery else 0.0
    ceiling = max(battery.max_wh, e) if battery else 0.0
    for t in range(n):
        s, d = econ.surplus_w[t], econ.deficit_w[t]
        rest = s
        for j, device in enumerate(inputs.devices):
            cap = device.max_w if device.allowed[t] else 0.0
            take = max(0.0, min(devices_w[j][t], cap, rest))
            dev[j][t] = take
            rest -= take
        if battery is not None:
            eta_c, eta_d = battery.model.charge_efficiency, battery.model.discharge_efficiency
            want = battery_w[t]
            room = max(0.0, ceiling - e) / (dt * eta_c)
            if want > 0:
                pc[t] = max(0.0, min(want, rest, battery.max_charge_w, room))
                rest -= pc[t]
                gc[t] = max(
                    0.0,
                    min(
                        want - pc[t],
                        inputs.grid_charge_at(t),
                        battery.max_charge_w - pc[t],
                        room - pc[t],
                    ),
                )
            elif want < 0:
                avail = max(0.0, e - floor) * eta_d / dt
                bl[t] = max(0.0, min(-want, d, battery.max_discharge_w, avail))
            e = e + dt * (eta_c * (pc[t] + gc[t]) - bl[t] / eta_d)
            soc[t] = e
        if rest > 0:
            cap_e = rest if inputs.export_limit_w is None else min(rest, inputs.export_limit_w)
            if inputs.export_price[t] < 0 or inputs.discouraged(t):
                cap_e = 0.0
            ex[t] = cap_e
            cu[t] = rest - cap_e
        gl[t] = max(0.0, d - bl[t])
    return Schedule(
        grid_load_w=tuple(gl),
        battery_load_w=tuple(bl),
        pv_charge_w=tuple(pc),
        grid_charge_w=tuple(gc),
        export_w=tuple(ex),
        curtail_w=tuple(cu),
        soc_wh=tuple(soc),
        devices_w=tuple(tuple(x) for x in dev),
    )


def warm_vector(
    inputs: PlannerInputs,
    econ: Economics,
    layout: Layout,
    heuristic: Schedule,
    previous: Plan | None,
) -> FloatArray:
    """The previous plan shifted onto this grid (heuristic where it has no slot), repaired."""
    shifted = _previous_battery_w(inputs, previous)
    battery_w = [
        shifted.get(start, heuristic.battery_w(t)) for t, start in enumerate(inputs.starts)
    ]
    devices = [list(d) for d in heuristic.devices_w]
    start = follow(inputs, econ, battery_w, devices)
    x = vector_from_schedule(inputs, layout, econ, start)
    return np.asarray(x, dtype=np.float64)


# --- plan -------------------------------------------------------------------------------------


def _w(value: float) -> float:
    return round(max(0.0, value), 1)


def _device_band(power_w: float, cap_w: float, fact: SlotFacts) -> DeviceBand:
    if power_w > EPS_W:
        throttled = fact.tight and power_w < cap_w - EPS_W
        return DeviceBand(
            allowed=True,
            preference=BandPreference.PREFER_ON,
            max_power_w=_w(power_w) if throttled else None,
        )
    if fact.tight:
        return DeviceBand(allowed=False, preference=BandPreference.PREFER_OFF, max_power_w=0.0)
    return DeviceBand()


def _battery_band(
    inputs: PlannerInputs, schedule: Schedule, fact: SlotFacts, t: int
) -> BatteryBand | None:
    battery = inputs.battery
    if battery is None:
        # No planner battery: no band, the controller keeps the goal rules (cs review m6).
        return None
    grid = schedule.grid_charge_w[t]
    devices = schedule.device_total_w(t)
    return BatteryBand(
        min_power_w=0.0 if fact.discharge_lock else -battery.max_discharge_w,
        max_power_w=battery.max_charge_w,
        min_soc_pct=round(battery.reserve_pct, 2),
        max_soc_pct=round(max(battery.reserve_pct, battery.max_soc_pct), 2),
        charge_from_surplus=not (devices > EPS_W and not fact.tight),
        priority_until_soc_pct=None
        if fact.priority_until_pct is None
        else round(min(100.0, fact.priority_until_pct), 1),
        grid_charge_max_w=_w(grid) if grid > EPS_W else None,
        discharge_lock=fact.discharge_lock,
    )


def to_plan(
    inputs: PlannerInputs,
    econ: Economics,
    schedule: Schedule,
    *,
    source: PlanSource,
    stats: SolverStats,
    reasons: list[Reason],
) -> Plan:
    """The Plan v2 of a schedule: bands, expected flows, prices and reasons per interval."""
    facts = slot_facts(inputs, econ, schedule)
    battery = inputs.battery
    intervals: list[PlanInterval] = []
    for t, start in enumerate(inputs.starts):
        fact = facts[t]
        p50 = _w(inputs.pv_p50[t])
        expected = IntervalFlows(
            pv_p10_w=min(p50, _w(inputs.pv_p10[t])),
            pv_p50_w=p50,
            pv_p90_w=max(p50, _w(inputs.pv_p90[t])),
            load_w=_w(inputs.load_p50[t]),
            import_w=_w(schedule.import_w(t)),
            export_w=_w(schedule.export_w[t]),
            battery_w=round(schedule.battery_w(t), 1),
            soc_pct=None
            if battery is None
            else round(min(100.0, max(0.0, battery.model.wh_to_soc(schedule.soc_wh[t]))), 2),
            devices_w=_w(schedule.device_total_w(t)),
            curtail_w=_w(schedule.curtail_w[t]),
        )
        devices = {
            device.device_id: _device_band(schedule.devices_w[j][t], device.max_w, fact)
            for j, device in enumerate(inputs.devices)
        }
        intervals.append(
            PlanInterval(
                start=start,
                end=start + inputs.step,
                devices=devices,
                battery=_battery_band(inputs, schedule, fact, t),
                grid_import_limit_w=inputs.import_limit_w,
                expected=expected,
                prices=IntervalPrices(
                    import_price=round(inputs.import_price[t], 6),
                    export_price=round(inputs.export_price[t], 6),
                    estimated=inputs.price_estimated(t),
                ),
                reasons=interval_reasons(inputs, econ, schedule, facts, t),
            )
        )
    return Plan(
        created_at=inputs.now,
        intervals=tuple(intervals),
        reasons=tuple(reasons),
        assumptions=dict(inputs.assumptions),
        source=source,
        stats=stats,
    )


__all__ = ["PlannerRun", "follow", "plan", "run", "to_plan", "warm_vector"]
