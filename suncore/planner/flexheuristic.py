# SPDX-License-Identifier: Apache-2.0
"""Rules of the fallback planner for flexible loads (Phase 6 WP8; :mod:`flex` doc).

Used by :mod:`suncore.planner.heuristic` slot by slot, on the same flows as the LP, so the
one cost function (``model.objective``) judges both. Before the slot loop
(:func:`preplan`):

* **programs**: the start that costs least when the profile meets the surplus left by the
  other jobs (import price for the rest, the export price given up for the surplus), the
  earliest of equal starts; the previous start wins ties (no flip-flopping);
* **EV target**: the energy the P10 surplus is expected to give before the deadline, and grid
  power in the cheapest available slots for the gap (at least the 6 A floor per slot, within
  the import headroom).

In each slot (:class:`FlexSim`): user-demanded power first (programs, the heat pump's own
heating, EV floors, the EV target's planned grid power and its surplus) - surplus first, the
rest from the battery (programs and heating, when the battery may discharge) or the grid;
then, after the Phase 4 devices, the discretionary part: EV surplus charging and surplus
pre-heating up to the comfort maximum. The §14a budget caps the flagged loads. No grid
pre-heating in the fallback (the LP does it when ``grid_preheat`` allows it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from suncore.planner.flex import FlexFlows, PlannerEv, PlannerJob, PlannerThermal
from suncore.planner.flexmodel import JOB_MOVE_COST, FlexEconomics, FlexInputs

EPS: Final = 1e-6


@dataclass(frozen=True, slots=True)
class FlexPrePlan:
    job_start: tuple[int, ...]
    """Per load (``None`` for non-jobs is ``-1``)."""
    ev_grid_w: tuple[tuple[float, ...], ...]
    """Per load: the EV target's planned grid power per slot (0 for other loads)."""


def _headroom(inputs: FlexInputs, deficit_w: tuple[float, ...], t: int) -> float:
    limit = inputs.import_limit_at(t)
    if limit is None:
        return float("inf")
    return max(0.0, max(limit, deficit_w[t]) - deficit_w[t])


def preplan(  # noqa: PLR0912 - one block per load kind, then the EV targets
    inputs: FlexInputs,
    fe: FlexEconomics,
    surplus_w: tuple[float, ...],
    deficit_w: tuple[float, ...],
    battery_first_until: int = -1,
) -> FlexPrePlan:
    """Job starts and the EV targets' grid slots (module doc)."""
    n, hours = inputs.n, inputs.hours
    loads = inputs.flex_loads
    left = list(surplus_w)
    used = [0.0] * n
    """Power already planned per slot (jobs, floors, base heating): import headroom."""
    starts: list[int] = [-1] * len(loads)
    for i, load in enumerate(loads):
        if isinstance(load, PlannerJob):
            best: tuple[float, int] | None = None
            for k in load.candidates:
                cost = (
                    0.0
                    if k == load.previous_start or load.previous_start is None
                    else (JOB_MOVE_COST)
                )
                for t in range(k, min(n, k + load.length(k))):
                    p = load.power_at(k, t)
                    on_surplus = min(p, left[t])
                    cost += (
                        on_surplus * inputs.export_price[t]
                        + (p - on_surplus) * inputs.import_price[t]
                    ) * hours[t]
                if best is None or cost < best[0] - EPS:
                    best = (cost, k)
            start = load.candidates[0] if best is None else best[1]
            starts[i] = start
            for t in range(n):
                p = load.power_at(start, t)
                left[t] = max(0.0, left[t] - p)
                used[t] += p
        elif isinstance(load, PlannerThermal):
            for t in range(n):
                used[t] += load.base_w[t]
        elif isinstance(load, PlannerEv):
            for t in range(n):
                used[t] += load.floor_w[t]
    grids: list[tuple[float, ...]] = []
    for load in loads:
        if not isinstance(load, PlannerEv) or load.need_by_wh is None:
            grids.append((0.0,) * n)
            continue
        plan = [0.0] * n
        slots = [t for t in range(n) if load.target_slot(t)]
        # The battery-first window (morning buffer) takes the surplus before the EV.
        est = {
            t: 0.0
            if t < battery_first_until
            else min(load.max_w[t] - load.floor_w[t], fe.surplus_p10_w[t], left[t])
            for t in slots
        }
        gap = load.need_by_wh - sum((est[t] + load.floor_w[t]) * hours[t] for t in slots)
        for t in sorted(slots, key=lambda t: (inputs.import_price[t], t)):
            if gap <= EPS:
                break
            room = min(
                load.max_w[t] - load.floor_w[t] - est[t],
                load.grid_max_w[t] - load.floor_w[t],
                _headroom(inputs, deficit_w, t) - used[t],
            )
            want = min(room, gap / hours[t])
            if est[t] + load.floor_w[t] + want < load.min_w:
                want = load.min_w - est[t] - load.floor_w[t]
                if want > room + EPS:
                    continue
            if want <= EPS:
                continue
            plan[t] = want
            used[t] += want
            gap -= want * hours[t]
        grids.append(tuple(plan))
    return FlexPrePlan(job_start=tuple(starts), ev_grid_w=tuple(grids))


class FlexSim:
    """Per-slot state of the flexible loads in the heuristic's simulation (module doc)."""

    def __init__(
        self,
        inputs: FlexInputs,
        fe: FlexEconomics,
        pre: FlexPrePlan,
        deficit_w: tuple[float, ...],
    ) -> None:
        self.inputs = inputs
        self.fe = fe
        self.pre = pre
        self.deficit_w = deficit_w
        self.loads = inputs.flex_loads
        n = inputs.n
        self.s = [[0.0] * n for _ in self.loads]
        self.g = [[0.0] * n for _ in self.loads]
        self.b = [[0.0] * n for _ in self.loads]
        self.temp = [[0.0] * n for _ in self.loads]
        self.now_temp = [
            load.temp0_c if isinstance(load, PlannerThermal) else 0.0 for load in self.loads
        ]
        self.delivered = [0.0] * len(self.loads)
        self.need = [0.0] * len(self.loads)
        """Per load: power still to settle from battery or grid in the current slot."""
        hours = inputs.hours
        self.reserved: list[list[float]] = []
        """Per load and slot: energy the EV's later floors and planned grid slots need (Wh);
        surplus charging keeps room for it under the vehicle's total."""
        for i, load in enumerate(self.loads):
            later = [0.0] * (n + 1)
            if isinstance(load, PlannerEv):
                for t in range(n - 1, -1, -1):
                    later[t] = later[t + 1] + (load.floor_w[t] + pre.ev_grid_w[i][t]) * hours[t]
            self.reserved.append(later)

    # --- helpers ---------------------------------------------------------------------------

    def power(self, i: int, t: int) -> float:
        return self.s[i][t] + self.g[i][t] + self.b[i][t] + self.need[i]

    def _ev_room(self, i: int, ev: PlannerEv, t: int, *, keep: bool = True) -> float:
        room = ev.max_w[t] - self.power(i, t)
        if ev.need_total_wh is not None:
            dt = self.inputs.hours[t]
            left = ev.need_total_wh - self.delivered[i] - self.power(i, t) * dt
            if keep:
                left -= self.reserved[i][t + 1]
            room = min(room, max(0.0, left) / dt)
        return max(0.0, room)

    def _thermal_to(self, th: PlannerThermal, t: int, target_c: float, temp: float) -> float:
        """Electric power that ends slot ``t`` at ``target_c`` from ``temp`` (unclipped)."""
        k = th.heat_k[t]
        if k <= 0:
            return 0.0
        return (target_c - th.alpha[t] * temp - th.drift[t]) * 1000.0 / k

    def grid_reserved(self, t: int) -> float:
        """Grid and still unsettled power of slot ``t`` (worst case for the import limit)."""
        return sum(self.g[i][t] + self.need[i] for i in range(len(self.loads)))

    def _para14a_cap(self, t: int) -> None:
        """Cut the flagged loads to the §14a budget: EV power above its floor first (grid,
        then surplus), then the heat pump's unsettled and surplus power. The builder scaled
        the floors into the budget, so the cut always suffices."""
        budget = self.inputs.para14a_at(t)
        if budget is None:
            return
        flagged = [i for i, load in enumerate(self.loads) if load.para14a]
        over = sum(self.power(i, t) for i in flagged) - budget
        for i in sorted(flagged, key=lambda i: not isinstance(self.loads[i], PlannerEv)):
            if over <= EPS:
                return
            load = self.loads[i]
            if isinstance(load, PlannerEv):
                extra = max(0.0, self.power(i, t) - load.floor_w[t])
                cut = min(over, extra)
                from_grid = min(
                    cut, max(0.0, self.g[i][t] - max(0.0, load.floor_w[t] - self.s[i][t]))
                )
                self.g[i][t] -= from_grid
                self.s[i][t] -= cut - from_grid
                over -= cut
                p = self.power(i, t)
                if EPS < p < load.min_w - EPS:
                    self.s[i][t] = self.g[i][t] = 0.0
                    over -= p
            else:
                cut = min(over, self.need[i])
                self.need[i] -= cut
                over -= cut
                cut = min(over, self.s[i][t])
                self.s[i][t] -= cut
                over -= cut

    # --- the rules ---------------------------------------------------------------------------

    def forced(self, t: int, surplus: float) -> float:
        """User-demanded power of slot ``t``; returns the surplus it takes."""
        rest = surplus
        headroom = _headroom(self.inputs, self.deficit_w, t)
        for i, load in enumerate(self.loads):
            self.need[i] = 0.0
            if isinstance(load, PlannerJob):
                p = load.power_at(self.pre.job_start[i], t)
                take = min(rest, p)
                self.s[i][t] = take
                rest -= take
                self.need[i] = p - take
            elif isinstance(load, PlannerThermal):
                hold = self._thermal_to(load, t, load.min_c, self.now_temp[i])
                p = min(load.max_w[t], max(0.0, hold))
                take = min(rest, p)
                self.s[i][t] = take
                rest -= take
                self.need[i] = min(p - take, load.base_w[t])
        for i, load in enumerate(self.loads):
            if not isinstance(load, PlannerEv) or load.max_w[t] <= 0:
                continue
            floor = load.floor_w[t]
            take = min(rest, floor)
            self.s[i][t] = take
            rest -= take
            self.g[i][t] = floor - take
            if load.target_slot(t) and self._ev_room(i, load, t) > EPS:
                planned = min(self.pre.ev_grid_w[i][t], self._ev_room(i, load, t))
                planned = max(0.0, min(planned, headroom - self.grid_reserved(t)))
                self.g[i][t] += planned
                more = min(rest, self._ev_room(i, load, t))
                self.s[i][t] += more
                rest -= more
                p = self.power(i, t)
                if EPS < p < load.min_w - EPS and load.binary(t):
                    # Only without a floor (floors are >= the minimum): top up or give back.
                    top = load.min_w - p
                    grid_room = min(
                        load.grid_max_w[t] - self.g[i][t],
                        headroom - self.grid_reserved(t),
                    )
                    if top <= grid_room + EPS and top <= self._ev_room(i, load, t) + EPS:
                        self.g[i][t] += top
                    else:
                        rest += more
                        self.s[i][t] -= more
                        self.g[i][t] -= planned
        before = sum(self.s[i][t] for i in range(len(self.loads)))
        self._para14a_cap(t)
        rest += before - sum(self.s[i][t] for i in range(len(self.loads)))
        return surplus - rest

    def discretionary(self, t: int, surplus: float, budget_w: float) -> float:
        """Surplus charging and surplus pre-heating; returns the surplus it takes."""
        rest = min(surplus, max(0.0, budget_w))
        start = rest
        for i, load in enumerate(self.loads):
            if rest <= EPS:
                break
            if isinstance(load, PlannerEv) and load.max_w[t] > 0:
                room = self._ev_room(i, load, t)
                take = min(rest, room)
                if self.power(i, t) <= EPS and take < load.min_w - EPS:
                    continue
                self.s[i][t] += take
                rest -= take
            elif isinstance(load, PlannerThermal):
                cap = self._thermal_to(load, t, load.max_c, self.now_temp[i]) - self.power(i, t)
                take = min(rest, max(0.0, min(cap, load.max_w[t] - self.power(i, t))))
                if take <= EPS:
                    continue
                self.s[i][t] += take
                rest -= take
        before = sum(self.s[i][t] for i in range(len(self.loads)))
        self._para14a_cap(t)
        rest += before - sum(self.s[i][t] for i in range(len(self.loads)))
        return start - rest

    def battery_wanted(self) -> float:
        return sum(self.need[i] for i, load in enumerate(self.loads) if load.battery)

    def settle(self, t: int, battery_w: float) -> float:
        """Unsettled power from the battery (up to ``battery_w``) or the grid; the battery
        power used is returned. Updates temperatures and delivered energy."""
        used = 0.0
        dt = self.inputs.hours[t]
        for i, load in enumerate(self.loads):
            rest = self.need[i]
            if load.battery and rest > EPS:
                take = min(rest, max(0.0, battery_w - used))
                self.b[i][t] = take
                used += take
                rest -= take
            self.g[i][t] += max(0.0, rest)
            self.need[i] = 0.0
            p = self.s[i][t] + self.g[i][t] + self.b[i][t]
            self.delivered[i] += p * dt
            if isinstance(load, PlannerThermal):
                self.now_temp[i] = load.next_temp(t, self.now_temp[i], p)
                self.temp[i][t] = self.now_temp[i]
        return used

    def flows(self) -> tuple[FlexFlows, ...]:
        out: list[FlexFlows] = []
        for i, load in enumerate(self.loads):
            out.append(
                FlexFlows(
                    tuple(self.s[i]),
                    tuple(self.g[i]),
                    tuple(self.b[i]),
                    tuple(self.temp[i]) if isinstance(load, PlannerThermal) else (),
                    self.pre.job_start[i] if isinstance(load, PlannerJob) else None,
                )
            )
        return tuple(out)


__all__ = ["FlexPrePlan", "FlexSim", "preplan"]
