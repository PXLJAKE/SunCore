# SPDX-License-Identifier: Apache-2.0
"""Rule-based fallback planner (Phase 4 WP8, ADR-0003): always available, no solver.

It produces the same :class:`~suncore.planner.model.Schedule` as the LP, on the same flows
(PV serves the base load first; devices only from the PV surplus; the battery discharges only
for the base load), so :func:`suncore.planner.model.objective` costs both alike. Rules, slot
by slot on the P50 forecast:

1. **Battery first** while the SoC is below the morning buffer (``priority_until``).
2. Then **devices by value** (highest first), as far as every pending evening/user target is
   still reachable: a projection of the SoC at each target (all later surplus to the battery,
   later deficits drawn from it) must stay above the target after the device's energy.
3. The battery takes the rest up to its charge limit and max SoC; the remainder is exported
   up to the export limit (curtailed when the export price is negative or above the limit).
4. A deficit is covered by the battery down to the reserve - unless a pending evening/user
   target is projected to be missed, then the battery holds (discharge lock) and the house
   imports.
5. **Grid charging** only when a target is still missed after rules 1-4: in the cheapest
   slots before the target whose allowance (policy, schedule and spread rule) is positive,
   just enough to close the gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from suncore.planner.inputs import PlannerInputs
from suncore.planner.model import (
    Economics,
    Schedule,
    economics,
    goal_shortfall_wh,
)

EPS_W: Final = 1e-6
HOLD_KINDS: Final = ("evening_target", "user_target")


@dataclass(frozen=True, slots=True)
class _Goal:
    slot: int
    target_wh: float
    holds: bool
    """An evening or user target (a deficit may be held for it)."""


def _goals(inputs: PlannerInputs, econ: Economics) -> list[_Goal]:
    battery = inputs.battery
    if battery is None:
        return []
    out = []
    for goal, slot in zip(inputs.goals, econ.goal_slots, strict=True):
        if slot is not None:
            out.append(_Goal(slot, battery.model.soc_to_wh(goal.soc_pct), goal.kind in HOLD_KINDS))
    return out


def _simulate(  # noqa: PLR0912, PLR0915 - one pass over the rules of the module doc
    inputs: PlannerInputs, econ: Economics, extra_grid_w: list[float]
) -> Schedule:
    n, dt = inputs.n, inputs.dt_h
    battery = inputs.battery
    goals = _goals(inputs, econ)
    order = sorted(
        range(len(inputs.devices)),
        key=lambda j: (-max(econ.device_value[j]), inputs.devices[j].priority),
    )
    gl = [0.0] * n
    bl = [0.0] * n
    pc = [0.0] * n
    gc = [0.0] * n
    ex = [0.0] * n
    cu = [0.0] * n
    soc = [0.0] * n
    dev = [[0.0] * n for _ in inputs.devices]
    eta_c = battery.model.charge_efficiency if battery else 1.0
    eta_d = battery.model.discharge_efficiency if battery else 1.0
    max_ch = battery.max_charge_w if battery else 0.0
    max_dis = battery.max_discharge_w if battery else 0.0
    e = battery.soc_wh if battery else 0.0
    floor = min(battery.reserve_wh, e) if battery else 0.0
    ceiling = max(battery.max_wh, e) if battery else 0.0
    buffer_wh = (
        battery.model.soc_to_wh(inputs.morning_buffer_pct)
        if battery is not None and inputs.morning_buffer_pct is not None
        else None
    )
    # Net battery energy each slot would add if all surplus went to it and deficits drew from it.
    gain = [
        min(econ.surplus_w[t], max_ch) * dt * eta_c - min(econ.deficit_w[t], max_dis) * dt / eta_d
        for t in range(n)
    ]
    budget_left = dict(econ.device_budget)
    for t in range(n):
        s, d = econ.surplus_w[t], econ.deficit_w[t]
        pending = [g for g in goals if g.slot >= t]
        # Projected margin over every pending target if this slot's surplus went to the battery.
        margin = min(
            (
                min(
                    ceiling,
                    e + sum(gain[t : g.slot + 1]) + _extra(extra_grid_w, t, g.slot, dt, eta_c),
                )
                - g.target_wh
                for g in pending
                if g.holds
            ),
            default=float("inf"),
        )
        rest = s
        # Same rule as the LP's FIRST slack (review n2): strictly before the buffer's slot.
        battery_first = buffer_wh is not None and e < buffer_wh - 1e-6 and t < econ.first_until
        if battery is not None and battery_first:
            first = min(rest, max_ch, max(0.0, ceiling - e) / (dt * eta_c))
            pc[t] = first
            rest -= first
        budget_w = float("inf") if margin == float("inf") else max(0.0, margin) / (dt * eta_c)
        for slot, left in budget_left.items():
            if slot >= t:
                budget_w = min(budget_w, max(0.0, left) / dt)
        for j in order:
            device = inputs.devices[j]
            if not device.allowed[t] or rest <= EPS_W:
                continue
            take = min(rest, device.max_w, budget_w)
            if take <= EPS_W:
                continue
            dev[j][t] = take
            rest -= take
            budget_w -= take
            for slot in budget_left:
                if slot >= t:
                    budget_left[slot] -= take * dt
        if battery is not None:
            room = max(0.0, ceiling - e) / (dt * eta_c) - pc[t]
            more = max(0.0, min(rest, max_ch - pc[t], room))
            pc[t] += more
            rest -= more
            room -= more
            gc[t] = max(0.0, min(extra_grid_w[t], inputs.grid_charge_at(t), max_ch - pc[t], room))
        if rest > EPS_W:
            if inputs.export_price[t] < 0 or inputs.discouraged(t):
                cu[t] = rest
            else:
                cap = rest if inputs.export_limit_w is None else min(rest, inputs.export_limit_w)
                ex[t] = cap
                cu[t] = rest - cap
        if d > 0:
            hold = margin < 0 and any(g.holds for g in pending)
            if battery is not None and not hold:
                bl[t] = max(0.0, min(d, max_dis, (e - floor) * eta_d / dt))
            gl[t] = d - bl[t]
        if battery is not None:
            e = e + dt * (eta_c * (pc[t] + gc[t]) - bl[t] / eta_d)
            soc[t] = e
    return Schedule(
        grid_load_w=tuple(gl),
        battery_load_w=tuple(bl),
        pv_charge_w=tuple(pc),
        grid_charge_w=tuple(gc),
        export_w=tuple(ex),
        curtail_w=tuple(cu),
        soc_wh=tuple(soc),
        devices_w=tuple(tuple(d) for d in dev),
    )


def _extra(extra_grid_w: list[float], start: int, end: int, dt: float, eta_c: float) -> float:
    return sum(extra_grid_w[start : end + 1]) * dt * eta_c


def heuristic_schedule(inputs: PlannerInputs, econ: Economics | None = None) -> Schedule:
    """The rule-based schedule of the module doc."""
    econ = econ or economics(inputs)
    extra = [0.0] * inputs.n
    schedule = _simulate(inputs, econ, extra)
    battery = inputs.battery
    if battery is None or not any(w > 0 for w in inputs.grid_charge_w):
        return schedule
    dt, eta_c = inputs.dt_h, battery.model.charge_efficiency
    for _ in range(len(inputs.goals) + 1):
        shortfalls = goal_shortfall_wh(inputs, econ, schedule)
        missing = [
            (slot, short)
            for goal, slot, short in zip(inputs.goals, econ.goal_slots, shortfalls, strict=True)
            if slot is not None and short > 1.0 and goal.kind in HOLD_KINDS
        ]
        if not missing:
            break
        slot, short = missing[0]
        candidates = sorted(
            (t for t in range(slot + 1) if inputs.grid_charge_at(t) > extra[t] + EPS_W),
            key=lambda t: (inputs.import_price[t], t),
        )
        if not candidates:
            break
        need = short
        for t in candidates:
            if need <= 0:
                break
            free = min(inputs.grid_charge_at(t), battery.max_charge_w) - extra[t]
            add = min(free, need / (dt * eta_c))
            extra[t] += add
            need -= add * dt * eta_c
        schedule = _simulate(inputs, econ, extra)
    return schedule


__all__ = ["heuristic_schedule"]
