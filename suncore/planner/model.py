# SPDX-License-Identifier: Apache-2.0
"""The planner's linear program and the solver backend (Phase 4 WP8, ADR-0003).

**Flows.** PV serves the base load first (what the inverter does physically). Per 15-minute
slot ``t`` the P50 forecast then leaves either a *surplus* ``s_t = max(0, pv - load)`` or a
*deficit* ``d_t = max(0, load - pv)``, known before solving. The variables (kW in the LP, W in
a :class:`Schedule`) split the flows by source and sink:

=========  ===================================  ==========================================
variable   flow                                 bounds
=========  ===================================  ==========================================
``gL``     grid -> base load                    ``[0, d]``
``bL``     battery -> base load (discharge)     ``[0, min(d, max discharge)]``
``pC``     PV surplus -> battery                ``[0, min(s, max charge)]``
``gC``     grid -> battery (grid charging)      ``[0, allowance_t]`` (policy + spread rule)
``E``      PV surplus -> export                 ``[0, min(s, export limit)]``
``K``      PV surplus curtailed (informational) ``[0, s]``
``D_j``    PV surplus -> device ``j``           ``[0, min(max_j, s)]``, 0 outside its window
``e``      stored energy at the end of ``t``    ``[reserve, max SoC]`` (kWh)
=========  ===================================  ==========================================

Constraints: ``pC + E + K + sum D = s`` and ``gL + bL = d`` (together the energy balance
``pv + import + discharge = load + devices + charge + export + curtail``); ``pC + gC <= max
charge``; ``e_t = e_{t-1} + dt * (eta_c * (pC + gC) - bL / eta_d)``; soft lower bounds
``e_k + short_k >= target_k`` for the morning buffer, the evening target and the user's timed
target (a high penalty per kWh of shortfall, so the model is always feasible and the
shortfall is explained); the import limit holds through the grid-charging allowance
(``allowance <= import limit - base load``).

**Rigour of "no import for a device"**: devices draw only from the PV surplus over the base
load (``D_j`` appears only in the surplus balance), so neither the grid nor the battery can
feed them - structurally, not through a price cap. The battery discharges only for the base
load (never for export; ADR-0013 limits cannot express a forced discharge).

**Simultaneous charge and discharge** needs no binary: in a deficit slot only ``gC`` and
``bL`` could coexist, and that loop only loses efficiency and degradation (``gL`` serves the
same load at the same price without the loss); dumping energy at negative prices is done by
curtailment, which is free of losses. Phase 4 therefore solves a pure LP (``n_binaries = 0``);
the backend accepts integer columns for later device models (Phase 6).

**Objective** (minimised, currency; energies ``dt * power``)::

    sum_t  (p_imp + w_aut) * (gL + gC)            import cost + self-sufficiency weight
         - p_exp * E                               export revenue (0 under the negative rule)
         + deg / 2 * (pC + gC + bL)                degradation per kWh of throughput
         + w_cur * K                               tie-breaker: export at price 0 beats curtail
         - sum_j v_jt * D_j                        device utility
    + sum_k P_k * short_k                          soft targets
    + P_res * (below-reserve / above-max slack)    only when the live SoC starts outside
    - V_term * e_{T}                               terminal value of the stored energy

``deg / 2`` on charge and on discharge equals the configured cost per charged kWh over a full
cycle. Device value ``v_jt`` = the configured ``planner_value_per_kwh`` or automatically the
export price + 0.01, capped at ``max(import price, export price + 0.01)`` (plan §0: never above
the import price; in a negative-price slot the floor "consumption beats export" wins, which
is safe because devices cannot import anyway). Penalties derive from the largest absolute
price ``m``: morning buffer ``2m + 0.5`` (beats every device value and export), evening and
user target ``10m + 1``, reserve ``100m + 10``. The terminal value ``median(import) * eta_d -
deg / 2`` is conservative (median, not the peak) so the plan neither empties the battery
because the horizon ends nor hoards energy against a price it may never see.
"""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

import numpy as np
import numpy.typing as npt

from suncore.planner.inputs import DEVICE_VALUE_MARGIN, PlannerInputs, SocGoal

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

GL, BL, PC, GC, EX, CU, SOC, LOW, HIGH, FIRST = range(10)
SLOT_VARS: Final = 10
KW: Final = 1000.0
INF: Final = math.inf
BALANCE_TOL_W: Final = 1.0
BUFFER_KINDS: Final = ("morning_buffer", "prefer_full")
SOC_TOL_WH: Final = 1.0


# --- layout and economics -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Layout:
    """Column index of every variable."""

    n: int
    n_dev: int
    n_goals: int

    @property
    def per_slot(self) -> int:
        return SLOT_VARS + self.n_dev

    @property
    def n_vars(self) -> int:
        return self.n * self.per_slot + self.n_goals

    def var(self, t: int, kind: int) -> int:
        return t * self.per_slot + kind

    def dev(self, t: int, j: int) -> int:
        return t * self.per_slot + SLOT_VARS + j

    def goal(self, k: int) -> int:
        return self.n * self.per_slot + k


@dataclass(frozen=True, slots=True)
class Economics:
    """The numbers the objective uses, derived once from the inputs (module doc)."""

    surplus_w: tuple[float, ...]
    deficit_w: tuple[float, ...]
    device_value: tuple[tuple[float, ...], ...]
    """``[device][slot]`` currency/kWh."""
    buffer_penalty: float
    target_penalty: float
    reserve_penalty: float
    terminal_value: float
    goal_slots: tuple[int | None, ...]
    goal_penalty: tuple[float, ...]
    first_until: int
    """Slots before this index are "battery first" (the morning buffer or ``prefer_full``
    goal is due at its end): surplus beyond what the battery can take
    (``s - max charge``) may go to devices, export or curtailment; more costs the buffer
    penalty (slack ``FIRST``). ``-1`` = no such rule."""


def economics(inputs: PlannerInputs) -> Economics:
    w = inputs.weights
    surplus = tuple(max(0.0, p - lo) for p, lo in zip(inputs.pv_p50, inputs.load_p50, strict=True))
    deficit = tuple(max(0.0, lo - p) for p, lo in zip(inputs.pv_p50, inputs.load_p50, strict=True))
    values: list[tuple[float, ...]] = []
    for device in inputs.devices:
        row: list[float] = []
        for pi, pe in zip(inputs.import_price, inputs.export_price, strict=True):
            floor = pe + DEVICE_VALUE_MARGIN
            value = floor if device.value_per_kwh is None else device.value_per_kwh
            row.append(min(value, max(pi, floor)))
        values.append(tuple(row))
    biggest = max(abs(v) for v in (*inputs.import_price, *inputs.export_price))
    buffer_p = (
        w.buffer_penalty_per_kwh if w.buffer_penalty_per_kwh is not None else 2 * biggest + 0.5
    )
    target_p = (
        w.target_penalty_per_kwh if w.target_penalty_per_kwh is not None else 10 * biggest + 1
    )
    reserve_p = (
        w.reserve_penalty_per_kwh if w.reserve_penalty_per_kwh is not None else 100 * biggest + 10
    )
    battery = inputs.battery
    if w.terminal_value_per_kwh is not None:
        terminal = w.terminal_value_per_kwh
    elif battery is None:
        terminal = 0.0
    else:
        median = statistics.median(inputs.import_price)
        model = battery.model
        terminal = max(
            0.0, median * model.discharge_efficiency - model.degradation_cost_per_kwh / 2
        )
    slots = tuple(inputs.slot_of(g.at) for g in inputs.goals)
    penalties = tuple(_goal_penalty(g, buffer_p, target_p) for g in inputs.goals)
    first_until = max(
        (
            slot
            for g, slot in zip(inputs.goals, slots, strict=True)
            if slot is not None and g.kind in BUFFER_KINDS
        ),
        default=-1,
    )
    return Economics(
        surplus_w=surplus,
        deficit_w=deficit,
        device_value=tuple(values),
        buffer_penalty=buffer_p,
        target_penalty=target_p,
        reserve_penalty=reserve_p,
        terminal_value=terminal,
        goal_slots=slots,
        goal_penalty=penalties,
        first_until=first_until,
    )


def _goal_penalty(goal: SocGoal, buffer_p: float, target_p: float) -> float:
    return buffer_p if goal.kind in BUFFER_KINDS else target_p


# --- schedule --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Schedule:
    """A planned operation (W per slot, Wh at the end of each slot); LP and heuristic alike."""

    grid_load_w: tuple[float, ...]
    battery_load_w: tuple[float, ...]
    pv_charge_w: tuple[float, ...]
    grid_charge_w: tuple[float, ...]
    export_w: tuple[float, ...]
    curtail_w: tuple[float, ...]
    soc_wh: tuple[float, ...]
    devices_w: tuple[tuple[float, ...], ...]
    """``[device][slot]``."""

    @property
    def n(self) -> int:
        return len(self.grid_load_w)

    def import_w(self, t: int) -> float:
        return self.grid_load_w[t] + self.grid_charge_w[t]

    def charge_w(self, t: int) -> float:
        return self.pv_charge_w[t] + self.grid_charge_w[t]

    def battery_w(self, t: int) -> float:
        """Battery power, + = charging."""
        return self.charge_w(t) - self.battery_load_w[t]

    def device_total_w(self, t: int) -> float:
        return sum(d[t] for d in self.devices_w)


# --- the LP ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinearProblem:
    """``min c.x`` s.t. ``row_lower <= A x <= row_upper``, ``col_lower <= x <= col_upper``.

    ``A`` is row-wise (CSR: ``a_start``, ``a_index``, ``a_value``). ``integrality`` lists the
    integer columns (none in Phase 4).
    """

    cost: FloatArray
    col_lower: FloatArray
    col_upper: FloatArray
    row_lower: FloatArray
    row_upper: FloatArray
    a_start: IntArray
    a_index: IntArray
    a_value: FloatArray
    integrality: tuple[int, ...] = ()

    @property
    def n_cols(self) -> int:
        return len(self.cost)

    @property
    def n_rows(self) -> int:
        return len(self.row_lower)


class _Rows:
    def __init__(self) -> None:
        self.start: list[int] = [0]
        self.index: list[int] = []
        self.value: list[float] = []
        self.lower: list[float] = []
        self.upper: list[float] = []

    def add(self, terms: Sequence[tuple[int, float]], lower: float, upper: float) -> None:
        for col, coef in terms:
            self.index.append(col)
            self.value.append(coef)
        self.start.append(len(self.index))
        self.lower.append(lower)
        self.upper.append(upper)


@dataclass(frozen=True, slots=True)
class _Battery:
    e0: float
    cap: float
    reserve: float
    max_: float
    max_charge: float
    max_discharge: float
    eta_c: float
    eta_d: float
    deg: float


def _battery_kwh(inputs: PlannerInputs) -> _Battery | None:
    b = inputs.battery
    if b is None:
        return None
    return _Battery(
        e0=b.soc_wh / KW,
        cap=b.capacity_wh / KW,
        reserve=b.reserve_wh / KW,
        max_=b.max_wh / KW,
        max_charge=b.max_charge_w / KW,
        max_discharge=b.max_discharge_w / KW,
        eta_c=b.model.charge_efficiency,
        eta_d=b.model.discharge_efficiency,
        deg=b.model.degradation_cost_per_kwh,
    )


def build_problem(  # noqa: PLR0912, PLR0915 - one block per constraint family
    inputs: PlannerInputs, econ: Economics
) -> tuple[LinearProblem, Layout]:
    """The sparse LP of the module doc."""
    n, dt = inputs.n, inputs.dt_h
    layout = Layout(n=n, n_dev=len(inputs.devices), n_goals=len(inputs.goals))
    nv = layout.n_vars
    cost = np.zeros(nv)
    lower = np.zeros(nv)
    upper = np.zeros(nv)
    rows = _Rows()
    bat = _battery_kwh(inputs)
    aut = inputs.weights.autarky_per_kwh
    cur = inputs.weights.curtail_per_kwh
    export_cap = INF if inputs.export_limit_w is None else inputs.export_limit_w / KW
    for t in range(n):
        s, d = econ.surplus_w[t] / KW, econ.deficit_w[t] / KW
        pi, pe = inputs.import_price[t], inputs.export_price[t]
        col = layout.var
        upper[col(t, GL)] = d
        upper[col(t, EX)] = min(s, export_cap)
        upper[col(t, CU)] = s
        cost[col(t, GL)] = dt * (pi + aut)
        cost[col(t, EX)] = -dt * pe
        cost[col(t, CU)] = dt * cur
        for j, device in enumerate(inputs.devices):
            upper[layout.dev(t, j)] = min(device.max_w / KW, s) if device.allowed[t] else 0.0
            cost[layout.dev(t, j)] = -dt * econ.device_value[j][t]
        if bat is not None:
            upper[col(t, BL)] = min(d, bat.max_discharge)
            upper[col(t, PC)] = min(s, bat.max_charge)
            upper[col(t, GC)] = min(inputs.grid_charge_at(t) / KW, bat.max_charge)
            half = dt * bat.deg / 2
            cost[col(t, BL)] = half
            cost[col(t, PC)] = half
            cost[col(t, GC)] = dt * (pi + aut) + half
            # SoC bounds: inside [reserve, max]; a live SoC outside gets a penalised slack.
            lower[col(t, SOC)] = bat.reserve if bat.e0 >= bat.reserve else 0.0
            upper[col(t, SOC)] = bat.max_ if bat.e0 <= bat.max_ else bat.cap
            upper[col(t, LOW)] = max(0.0, bat.reserve - bat.e0)
            upper[col(t, HIGH)] = max(0.0, bat.e0 - bat.max_)
            cost[col(t, LOW)] = econ.reserve_penalty
            cost[col(t, HIGH)] = econ.reserve_penalty
        if s > 0:
            terms = [(col(t, PC), 1.0), (col(t, EX), 1.0), (col(t, CU), 1.0)]
            terms += [(layout.dev(t, j), 1.0) for j in range(layout.n_dev)]
            rows.add(terms, s, s)
            if bat is not None and t < econ.first_until and s > 0:
                excess = max(0.0, s - bat.max_charge)
                upper[col(t, FIRST)] = s
                cost[col(t, FIRST)] = dt * econ.buffer_penalty
                others = [(col(t, EX), 1.0), (col(t, CU), 1.0), (col(t, FIRST), -1.0)]
                others += [(layout.dev(t, j), 1.0) for j in range(layout.n_dev)]
                rows.add(others, -INF, excess)
        if d > 0:
            rows.add([(col(t, GL), 1.0), (col(t, BL), 1.0)], d, d)
        if bat is None:
            continue
        if upper[col(t, GC)] > 0 and upper[col(t, PC)] > 0:
            rows.add([(col(t, PC), 1.0), (col(t, GC), 1.0)], -INF, bat.max_charge)
        dyn = [
            (col(t, SOC), 1.0),
            (col(t, PC), -dt * bat.eta_c),
            (col(t, GC), -dt * bat.eta_c),
            (col(t, BL), dt / bat.eta_d),
        ]
        if t == 0:
            rows.add(dyn, bat.e0, bat.e0)
        else:
            rows.add([*dyn, (col(t - 1, SOC), -1.0)], 0.0, 0.0)
        if bat.e0 < bat.reserve:
            rows.add([(col(t, SOC), 1.0), (col(t, LOW), 1.0)], bat.reserve, INF)
        if bat.e0 > bat.max_:
            rows.add([(col(t, SOC), 1.0), (col(t, HIGH), -1.0)], -INF, bat.max_)
    if bat is not None:
        cost[layout.var(n - 1, SOC)] -= econ.terminal_value
        for k, goal in enumerate(inputs.goals):
            slot = econ.goal_slots[k]
            target = inputs.battery.model.soc_to_wh(goal.soc_pct) / KW if inputs.battery else 0.0
            g = layout.goal(k)
            if slot is None:
                continue
            upper[g] = target
            cost[g] = econ.goal_penalty[k]
            rows.add([(layout.var(slot, SOC), 1.0), (g, 1.0)], target, INF)
    problem = LinearProblem(
        cost=cost,
        col_lower=lower,
        col_upper=upper,
        row_lower=np.array(rows.lower, dtype=np.float64),
        row_upper=np.array(rows.upper, dtype=np.float64),
        a_start=np.array(rows.start, dtype=np.int64),
        a_index=np.array(rows.index, dtype=np.int64),
        a_value=np.array(rows.value, dtype=np.float64),
    )
    return problem, layout


def schedule_from_vector(
    inputs: PlannerInputs, layout: Layout, x: Sequence[float] | FloatArray
) -> Schedule:
    """The LP solution as a :class:`Schedule` (W, Wh), tiny negatives clipped to 0."""
    n = layout.n

    def series(kind: int, scale: float = KW) -> tuple[float, ...]:
        return tuple(max(0.0, float(x[layout.var(t, kind)])) * scale for t in range(n))

    soc = series(SOC) if inputs.battery is not None else (0.0,) * n
    devices = tuple(
        tuple(max(0.0, float(x[layout.dev(t, j)])) * KW for t in range(n))
        for j in range(layout.n_dev)
    )
    return Schedule(
        grid_load_w=series(GL),
        battery_load_w=series(BL),
        pv_charge_w=series(PC),
        grid_charge_w=series(GC),
        export_w=series(EX),
        curtail_w=series(CU),
        soc_wh=soc,
        devices_w=devices,
    )


def goal_shortfall_wh(inputs: PlannerInputs, econ: Economics, schedule: Schedule) -> list[float]:
    """Per goal: Wh the planned SoC misses the target by (0 = met; outside the grid: 0)."""
    out: list[float] = []
    battery = inputs.battery
    for goal, slot in zip(inputs.goals, econ.goal_slots, strict=True):
        if battery is None or slot is None:
            out.append(0.0)
            continue
        target = battery.model.soc_to_wh(goal.soc_pct)
        out.append(max(0.0, target - schedule.soc_wh[slot]))
    return out


def vector_from_schedule(
    inputs: PlannerInputs, layout: Layout, econ: Economics, schedule: Schedule
) -> FloatArray:
    """The LP column vector of a schedule, slacks included (for costing and warm starts)."""
    x = np.zeros(layout.n_vars)
    bat = _battery_kwh(inputs)
    for t in range(layout.n):
        x[layout.var(t, GL)] = schedule.grid_load_w[t] / KW
        x[layout.var(t, BL)] = schedule.battery_load_w[t] / KW
        x[layout.var(t, PC)] = schedule.pv_charge_w[t] / KW
        x[layout.var(t, GC)] = schedule.grid_charge_w[t] / KW
        x[layout.var(t, EX)] = schedule.export_w[t] / KW
        x[layout.var(t, CU)] = schedule.curtail_w[t] / KW
        e = schedule.soc_wh[t] / KW
        x[layout.var(t, SOC)] = e
        if bat is not None:
            if t < econ.first_until:
                others = (
                    schedule.export_w[t] + schedule.curtail_w[t] + schedule.device_total_w(t)
                ) / KW
                excess = max(0.0, econ.surplus_w[t] / KW - bat.max_charge)
                x[layout.var(t, FIRST)] = max(0.0, others - excess)
            x[layout.var(t, LOW)] = max(0.0, bat.reserve - e) if bat.e0 < bat.reserve else 0.0
            x[layout.var(t, HIGH)] = max(0.0, e - bat.max_) if bat.e0 > bat.max_ else 0.0
        for j in range(layout.n_dev):
            x[layout.dev(t, j)] = schedule.devices_w[j][t] / KW
    for k, short in enumerate(goal_shortfall_wh(inputs, econ, schedule)):
        x[layout.goal(k)] = short / KW
    return x


def objective(inputs: PlannerInputs, schedule: Schedule, econ: Economics | None = None) -> float:
    """The LP's objective of any schedule: the one cost function for LP and heuristic."""
    econ = econ or economics(inputs)
    problem, layout = build_problem(inputs, econ)
    return float(problem.cost @ vector_from_schedule(inputs, layout, econ, schedule))


def violations(  # noqa: PLR0912 - one check per constraint family
    inputs: PlannerInputs, schedule: Schedule, econ: Economics | None = None
) -> list[str]:
    """Constraint violations of a schedule beyond rounding (empty = feasible)."""
    econ = econ or economics(inputs)
    found: list[str] = []
    bat = inputs.battery
    prev = None if bat is None else bat.soc_wh
    dt = inputs.dt_h
    for t in range(schedule.n):
        flows = (
            schedule.grid_load_w[t],
            schedule.battery_load_w[t],
            schedule.pv_charge_w[t],
            schedule.grid_charge_w[t],
            schedule.export_w[t],
            schedule.curtail_w[t],
            *(d[t] for d in schedule.devices_w),
        )
        if any(v < -BALANCE_TOL_W for v in flows):
            found.append(f"negative flow at {t}")
        left = inputs.pv_p50[t] + schedule.import_w(t) + schedule.battery_load_w[t]
        right = (
            inputs.load_p50[t]
            + schedule.device_total_w(t)
            + schedule.charge_w(t)
            + schedule.export_w[t]
            + schedule.curtail_w[t]
        )
        if abs(left - right) > BALANCE_TOL_W:
            found.append(f"balance at {t}: {left:.1f} != {right:.1f}")
        if schedule.device_total_w(t) > econ.surplus_w[t] + BALANCE_TOL_W:
            found.append(f"devices above the PV surplus at {t}")
        if schedule.grid_charge_w[t] > inputs.grid_charge_at(t) + BALANCE_TOL_W:
            found.append(f"grid charging not allowed at {t}")
        if (
            inputs.export_limit_w is not None
            and schedule.export_w[t] > inputs.export_limit_w + BALANCE_TOL_W
        ):
            found.append(f"export limit at {t}")
        for j, device in enumerate(inputs.devices):
            limit = device.max_w if device.allowed[t] else 0.0
            if schedule.devices_w[j][t] > limit + BALANCE_TOL_W:
                found.append(f"device {device.device_id} above its limit at {t}")
        if bat is None or prev is None:
            if schedule.charge_w(t) > BALANCE_TOL_W or schedule.battery_load_w[t] > BALANCE_TOL_W:
                found.append(f"battery flow without a battery at {t}")
            continue
        if schedule.charge_w(t) > bat.max_charge_w + BALANCE_TOL_W:
            found.append(f"charge limit at {t}")
        if schedule.battery_load_w[t] > bat.max_discharge_w + BALANCE_TOL_W:
            found.append(f"discharge limit at {t}")
        model = bat.model
        expected = prev + dt * (
            model.charge_efficiency * schedule.charge_w(t)
            - schedule.battery_load_w[t] / model.discharge_efficiency
        )
        e = schedule.soc_wh[t]
        if abs(expected - e) > SOC_TOL_WH:
            found.append(f"soc dynamics at {t}")
        floor = min(bat.reserve_wh, bat.soc_wh)
        ceiling = max(bat.max_wh, bat.soc_wh)
        if e < floor - SOC_TOL_WH or e > ceiling + SOC_TOL_WH:
            found.append(f"soc bounds at {t}: {e:.0f} Wh")
        prev = e
    return found


# --- solver backend ----------------------------------------------------------------------------

SolveStatus = Literal["optimal", "time_limit", "infeasible", "error"]


@dataclass(frozen=True, slots=True)
class SolverResult:
    status: SolveStatus
    x: FloatArray | None
    """Primal solution when one is feasible (``None`` otherwise)."""
    objective: float | None
    solve_time_s: float
    iterations: int = 0
    mip_gap: float | None = None
    detail: str = ""


class SolverBackend(Protocol):
    """What the planner needs from a solver (ADR-0003: swappable in tests)."""

    @property
    def name(self) -> str: ...

    def solve(
        self,
        problem: LinearProblem,
        *,
        time_limit_s: float,
        mip_gap: float,
        warm_start: FloatArray | None = None,
    ) -> SolverResult: ...


class HighsBackend:
    """HiGHS through ``highspy`` (imported lazily: the heuristic works without it)."""

    name = "highs"

    def __init__(self, threads: int = 1) -> None:
        self._threads = threads

    @staticmethod
    def available() -> bool:
        try:
            import highspy  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def solve(
        self,
        problem: LinearProblem,
        *,
        time_limit_s: float,
        mip_gap: float,
        warm_start: FloatArray | None = None,
    ) -> SolverResult:
        import highspy  # noqa: PLC0415 - optional at import time

        started = time.perf_counter()
        h = highspy.Highs()  # type: ignore[no-untyped-call]
        h.setOptionValue("output_flag", False)
        h.setOptionValue("time_limit", max(0.01, float(time_limit_s)))
        h.setOptionValue("mip_rel_gap", float(mip_gap))
        h.setOptionValue("threads", self._threads)
        lp = highspy.HighsLp()
        lp.num_col_ = problem.n_cols
        lp.num_row_ = problem.n_rows
        lp.col_cost_ = problem.cost
        lp.col_lower_ = problem.col_lower
        lp.col_upper_ = problem.col_upper
        lp.row_lower_ = problem.row_lower
        lp.row_upper_ = problem.row_upper
        lp.a_matrix_.format_ = highspy.MatrixFormat.kRowwise
        lp.a_matrix_.num_col_ = problem.n_cols
        lp.a_matrix_.num_row_ = problem.n_rows
        lp.a_matrix_.start_ = problem.a_start
        lp.a_matrix_.index_ = problem.a_index
        lp.a_matrix_.value_ = problem.a_value
        if problem.integrality:
            kinds = [highspy.HighsVarType.kContinuous] * problem.n_cols
            for col in problem.integrality:
                kinds[col] = highspy.HighsVarType.kInteger
            lp.integrality_ = kinds
        if h.passModel(lp) == highspy.HighsStatus.kError:
            return SolverResult("error", None, None, time.perf_counter() - started, detail="model")
        if warm_start is not None and len(warm_start) == problem.n_cols:
            start = highspy.HighsSolution()
            start.col_value = [float(v) for v in warm_start]
            start.value_valid = True
            h.setSolution(start)
        h.run()
        elapsed = time.perf_counter() - started
        status = h.getModelStatus()
        info = h.getInfo()
        feasible = info.primal_solution_status == 2  # kSolutionStatusFeasible
        x = np.array(h.getSolution().col_value, dtype=np.float64) if feasible else None
        value = float(info.objective_function_value) if feasible else None
        gap = float(info.mip_gap) if problem.integrality and feasible else None
        iterations = int(info.simplex_iteration_count)
        ms = highspy.HighsModelStatus
        kind: SolveStatus
        if status == ms.kOptimal:
            kind = "optimal"
        elif status in (ms.kTimeLimit, ms.kIterationLimit, ms.kInterrupt):
            kind = "time_limit"
        elif status in (ms.kInfeasible, ms.kUnboundedOrInfeasible, ms.kUnbounded):
            kind = "infeasible"
        else:
            kind = "error"
        return SolverResult(
            kind,
            x,
            value,
            elapsed,
            iterations,
            gap,
            detail=h.modelStatusToString(status),
        )


__all__ = [
    "BL",
    "BUFFER_KINDS",
    "CU",
    "EX",
    "FIRST",
    "GC",
    "GL",
    "HIGH",
    "LOW",
    "PC",
    "SOC",
    "Economics",
    "HighsBackend",
    "Layout",
    "LinearProblem",
    "Schedule",
    "SolveStatus",
    "SolverBackend",
    "SolverResult",
    "build_problem",
    "economics",
    "goal_shortfall_wh",
    "objective",
    "schedule_from_vector",
    "vector_from_schedule",
    "violations",
]
