# SPDX-License-Identifier: Apache-2.0
"""LP columns and rows of the flexible loads and the grid (Phase 6 WP8; :mod:`flex` doc).

Appended to the Phase 4 model (:mod:`suncore.planner.model`) after its columns; without
flexible loads and without a peak variable nothing is added. Per load ``i`` and slot ``t``
(kW in the LP, like the rest of the model):

==============  ==================================================================
column          meaning
==============  ==================================================================
``S[i,t]``      PV surplus -> load (in the surplus balance of slot ``t``)
``G[i,t]``      grid -> load (``<= grid_max_w``; import price + self-sufficiency weight)
``B[i,t]``      battery -> load (programs and heating only; in the SoC dynamics)
``on[i,t]``     EV: charging indicator, binary in the first 24 h, ``[0, 1]`` beyond
``S10[i,t]``    EV target: the surplus that counts toward the target (``<= S``, ``<= P10``)
``short[i]``    EV target: missing energy (target penalty per kWh)
``y[i,k]``      program: start in candidate slot ``k`` (binary, ``sum = 1``)
``T, LO, HI``   thermal: temperature at the end of ``t`` and the comfort slacks (K)
``OV[t]``       import above the limit, only up to the user-demanded power (huge penalty)
``PK[m]``       peak of month ``m`` (capacity price per kW)
==============  ==================================================================

Rows: EV ``min * on <= S + G <= max * on``, ``S + G >= floor``, ``sum dt (G + S10) + short >=
need``, ``sum dt (S + G) <= total``; program ``S + G + B = sum_k profile[t - k] y_k``; thermal
``T_t = alpha T_{t-1} + k_t (S + G + B) + drift_t``, ``min - LO <= T <= max + HI``, ``G + B <=
base`` without ``grid_preheat``; grid ``gL + gC + sum G - OV <= max(limit, deficit)``, ``gL + gC +
sum G <= PK[m]``, ``sum_{flagged} (S + G + B) <= §14a budget``; battery ``bL + sum B <= max
discharge``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import tzinfo
from typing import Final, Protocol

import numpy as np
import numpy.typing as npt

from suncore.core.control import BandPreference, DeviceBand
from suncore.core.decisions import Reason
from suncore.planner import reasons as rk
from suncore.planner.flex import FlexFlows, FlexLoad, PlannerEv, PlannerJob, PlannerThermal

FloatArray = npt.NDArray[np.float64]

KW: Final = 1000.0
INF: Final = math.inf
TOL_W: Final = 1.0
TOL_K: Final = 0.05
EPS_W: Final = 1.0
JOB_MOVE_COST: Final = 1e-3
"""Tie-breaker (currency): a scheduled job keeps its previous start unless another start
saves more than this, so consecutive plans do not move the start back and forth."""
TEMP_BOUND_C: Final = 200.0


class RowSink(Protocol):
    def add(self, terms: Sequence[tuple[int, float]], lower: float, upper: float) -> None: ...


class FlexInputs(Protocol):
    """What the flexible part reads from :class:`~suncore.planner.inputs.PlannerInputs`."""

    @property
    def n(self) -> int: ...

    @property
    def dt_h(self) -> float: ...

    @property
    def tz(self) -> tzinfo: ...

    @property
    def import_price(self) -> tuple[float, ...]: ...

    @property
    def export_price(self) -> tuple[float, ...]: ...

    @property
    def pv_p10(self) -> tuple[float, ...]: ...

    @property
    def load_p50(self) -> tuple[float, ...]: ...

    @property
    def evs(self) -> tuple[PlannerEv, ...]: ...

    @property
    def jobs(self) -> tuple[PlannerJob, ...]: ...

    @property
    def thermals(self) -> tuple[PlannerThermal, ...]: ...

    @property
    def flex_loads(self) -> tuple[FlexLoad, ...]: ...

    def import_limit_at(self, t: int) -> float | None: ...

    def para14a_at(self, t: int) -> float | None: ...

    def peak_price(self) -> float: ...

    def month_of(self, t: int) -> int: ...

    def month_floors_w(self) -> tuple[float, ...]: ...


# --- economics --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlexEconomics:
    """The flexible part's numbers (derived once, like ``model.Economics``)."""

    surplus_p10_w: tuple[float, ...]
    value: tuple[tuple[float, ...], ...]
    """Per load and slot: the utility of surplus energy (EV only; 0 otherwise)."""
    comfort_penalty: tuple[float, ...]
    """Per load: currency per K and hour outside the comfort band (thermal only)."""
    target_penalty: float
    over_penalty: float


def flex_economics(
    inputs: FlexInputs,
    *,
    target_penalty: float,
    reserve_penalty: float,
    biggest: float,
    margin: float,
    comfort_weight: float | None,
) -> FlexEconomics:
    """Values and penalties (module doc of :mod:`model`): an EV's surplus value is the device
    rule (configured or export + margin, never above the import price); the comfort penalty
    per K·h is ``(10 m + 1) x max(1, C / 1 kWh/K)`` unless the expert weight is set."""
    loads = inputs.flex_loads
    values: list[tuple[float, ...]] = []
    comfort: list[float] = []
    for load in loads:
        row = [0.0] * inputs.n
        if isinstance(load, PlannerEv):
            for t, (pi, pe) in enumerate(
                zip(inputs.import_price, inputs.export_price, strict=True)
            ):
                floor = pe + margin
                value = floor if load.value_per_kwh is None else load.value_per_kwh
                row[t] = min(value, max(pi, floor))
        values.append(tuple(row))
        if isinstance(load, PlannerThermal):
            weight = (
                comfort_weight
                if comfort_weight is not None
                else (10 * biggest + 1) * max(1.0, load.capacity_wh_per_k / 1000.0)
            )
            comfort.append(weight)
        else:
            comfort.append(0.0)
    p10 = tuple(max(0.0, pv - lo) for pv, lo in zip(inputs.pv_p10, inputs.load_p50, strict=True))
    return FlexEconomics(
        surplus_p10_w=p10,
        value=tuple(values),
        comfort_penalty=tuple(comfort),
        target_penalty=target_penalty,
        over_penalty=reserve_penalty,
    )


# --- layout -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlexLayout:
    n: int
    offset: int
    loads: tuple[FlexLoad, ...]
    block: tuple[int, ...]
    grid_start: int
    n_months: int

    @property
    def size(self) -> int:
        return self.grid_start + self.n + self.n_months - self.offset

    def s(self, i: int, t: int) -> int:
        return self.block[i] + t

    def g(self, i: int, t: int) -> int:
        return self.block[i] + self.n + t

    def b(self, i: int, t: int) -> int:
        return self.block[i] + 2 * self.n + t

    def ev_on(self, i: int, t: int) -> int:
        return self.block[i] + 3 * self.n + t

    def ev_s10(self, i: int, t: int) -> int:
        return self.block[i] + 4 * self.n + t

    def ev_short(self, i: int) -> int:
        return self.block[i] + 5 * self.n

    def job_y(self, i: int, k: int) -> int:
        return self.block[i] + 3 * self.n + k

    def temp(self, i: int, t: int) -> int:
        return self.block[i] + 3 * self.n + t

    def low(self, i: int, t: int) -> int:
        return self.block[i] + 4 * self.n + t

    def high(self, i: int, t: int) -> int:
        return self.block[i] + 5 * self.n + t

    def ov(self, t: int) -> int:
        return self.grid_start + t

    def pk(self, m: int) -> int:
        return self.grid_start + self.n + m


def _block_size(load: FlexLoad, n: int) -> int:
    if isinstance(load, PlannerEv):
        return 5 * n + 1
    if isinstance(load, PlannerJob):
        return 3 * n + len(load.candidates)
    return 6 * n


def flex_layout(inputs: FlexInputs, offset: int) -> FlexLayout | None:
    """The column layout, ``None`` when there is nothing flexible (Phase 4 model)."""
    loads = inputs.flex_loads
    n_months = len(inputs.month_floors_w()) if inputs.peak_price() > 0 else 0
    if not loads and n_months == 0:
        return None
    n = inputs.n
    blocks: list[int] = []
    at = offset
    for load in loads:
        blocks.append(at)
        at += _block_size(load, n)
    return FlexLayout(
        n=n, offset=offset, loads=loads, block=tuple(blocks), grid_start=at, n_months=n_months
    )


# --- the rows ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BaseColumns:
    """Columns and data of the Phase 4 part the flexible rows refer to."""

    grid_load: Callable[[int], int]
    grid_charge: Callable[[int], int]
    battery_load: Callable[[int], int]
    surplus_w: tuple[float, ...]
    deficit_w: tuple[float, ...]
    battery: bool
    max_discharge_kw: float
    degradation: float
    autarky: float


def surplus_terms(fl: FlexLayout, upper: FloatArray, t: int) -> list[tuple[int, float]]:
    """``S`` terms of slot ``t`` for the surplus balance (only columns that can be > 0)."""
    return [(fl.s(i, t), 1.0) for i in range(len(fl.loads)) if upper[fl.s(i, t)] > 0]


def battery_terms(fl: FlexLayout, upper: FloatArray, t: int) -> list[int]:
    return [fl.b(i, t) for i in range(len(fl.loads)) if upper[fl.b(i, t)] > 0]


def set_flow_bounds(  # noqa: PLR0917 - the LP arrays
    inputs: FlexInputs,
    fe: FlexEconomics,
    fl: FlexLayout,
    base: BaseColumns,
    cost: FloatArray,
    upper: FloatArray,
) -> None:
    """Bounds and costs of ``S``, ``G``, ``B`` (before the balance rows are written)."""
    dt = inputs.dt_h
    for i, load in enumerate(fl.loads):
        for t in range(fl.n):
            cap = load.max_w[t] / KW
            if cap <= 0:
                continue
            upper[fl.s(i, t)] = min(cap, base.surplus_w[t] / KW)
            upper[fl.g(i, t)] = min(cap, load.grid_max_w[t] / KW)
            if load.battery and base.battery:
                limit = min(cap, base.max_discharge_kw)
                if isinstance(load, PlannerThermal) and not load.grid_preheat:
                    limit = min(limit, load.base_w[t] / KW)
                upper[fl.b(i, t)] = limit
            cost[fl.s(i, t)] = -dt * fe.value[i][t]
            cost[fl.g(i, t)] = dt * (inputs.import_price[t] + base.autarky)
            cost[fl.b(i, t)] = dt * base.degradation / 2


def add_rows(  # noqa: PLR0917 - one block per load kind and grid rule
    inputs: FlexInputs,
    fe: FlexEconomics,
    fl: FlexLayout,
    base: BaseColumns,
    cost: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
    rows: RowSink,
    integrality: list[int],
) -> None:
    """Every row of the module doc except the surplus balance and the SoC dynamics."""
    n, dt = fl.n, inputs.dt_h
    for i, load in enumerate(fl.loads):
        if isinstance(load, PlannerEv):
            _ev_rows(fe, fl, i, load, cost, lower, upper, rows, integrality, dt)
        elif isinstance(load, PlannerJob):
            _job_rows(fl, i, load, cost, lower, upper, rows, integrality)
        elif isinstance(load, PlannerThermal):
            _thermal_rows(fe, fl, i, load, cost, lower, upper, rows, dt)
    grid_cols = [
        [fl.g(i, t) for i in range(len(fl.loads)) if upper[fl.g(i, t)] > 0] for t in range(n)
    ]
    for t in range(n):
        imports = [(base.grid_load(t), 1.0), (base.grid_charge(t), 1.0)]
        imports += [(c, 1.0) for c in grid_cols[t]]
        limit = inputs.import_limit_at(t)
        if limit is not None and grid_cols[t]:
            forced = sum(load.forced_max_w(t) for load in fl.loads) / KW
            upper[fl.ov(t)] = forced
            cost[fl.ov(t)] = dt * fe.over_penalty
            cap = max(limit, base.deficit_w[t]) / KW
            rows.add([*imports, (fl.ov(t), -1.0)], -INF, cap)
        if fl.n_months:
            rows.add([*imports, (fl.pk(inputs.month_of(t)), -1.0)], -INF, 0.0)
        budget = inputs.para14a_at(t)
        if budget is not None:
            flagged = [
                (col, 1.0)
                for i, load in enumerate(fl.loads)
                if load.para14a
                for col in (fl.s(i, t), fl.g(i, t), fl.b(i, t))
                if upper[col] > 0
            ]
            if flagged:
                rows.add(flagged, -INF, budget / KW)
        if base.battery:
            extra = battery_terms(fl, upper, t)
            if extra:
                rows.add(
                    [(base.battery_load(t), 1.0), *((c, 1.0) for c in extra)],
                    -INF,
                    base.max_discharge_kw,
                )
    for m, floor in enumerate(inputs.month_floors_w() if fl.n_months else ()):
        lower[fl.pk(m)] = floor / KW
        upper[fl.pk(m)] = INF
        cost[fl.pk(m)] = inputs.peak_price()


def _power(fl: FlexLayout, i: int, t: int) -> list[tuple[int, float]]:
    return [(fl.s(i, t), 1.0), (fl.g(i, t), 1.0), (fl.b(i, t), 1.0)]


def _ev_rows(  # noqa: PLR0917 - the LP arrays
    fe: FlexEconomics,
    fl: FlexLayout,
    i: int,
    ev: PlannerEv,
    cost: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
    rows: RowSink,
    integrality: list[int],
    dt: float,
) -> None:
    target: list[tuple[int, float]] = []
    total: list[tuple[int, float]] = []
    for t in range(fl.n):
        cap = ev.max_w[t] / KW
        if cap <= 0:
            continue
        on = fl.ev_on(i, t)
        upper[on] = 1.0
        if ev.floor_w[t] > 0:
            lower[on] = 1.0
        if ev.binary(t):
            integrality.append(on)
        power = [(fl.s(i, t), 1.0), (fl.g(i, t), 1.0)]
        rows.add([*power, (on, -cap)], -INF, 0.0)
        if ev.min_w > 0:
            rows.add([*power, (on, -ev.min_w / KW)], 0.0, INF)
        if ev.floor_w[t] > 0:
            rows.add(power, ev.floor_w[t] / KW, INF)
        total += [(fl.s(i, t), dt), (fl.g(i, t), dt)]
        if ev.target_slot(t):
            s10 = fl.ev_s10(i, t)
            upper[s10] = min(cap, fe.surplus_p10_w[t] / KW, upper[fl.s(i, t)])
            rows.add([(s10, 1.0), (fl.s(i, t), -1.0)], -INF, 0.0)
            target += [(fl.g(i, t), dt), (s10, dt)]
    if ev.need_by_wh is not None and ev.deadline_slot is not None:
        short = fl.ev_short(i)
        need = ev.need_by_wh / KW
        upper[short] = need
        cost[short] = fe.target_penalty
        rows.add([*target, (short, 1.0)], need, INF)
    if ev.need_total_wh is not None and total:
        rows.add(total, -INF, ev.need_total_wh / KW)


def _job_rows(  # noqa: PLR0917 - the LP arrays
    fl: FlexLayout,
    i: int,
    job: PlannerJob,
    cost: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
    rows: RowSink,
    integrality: list[int],
) -> None:
    ys = [fl.job_y(i, k) for k in range(len(job.candidates))]
    for col, start in zip(ys, job.candidates, strict=True):
        upper[col] = 1.0
        if len(ys) == 1:
            lower[col] = 1.0
        else:
            integrality.append(col)
        if job.previous_start is not None and start != job.previous_start:
            cost[col] = JOB_MOVE_COST
    if len(ys) > 1:
        rows.add([(c, 1.0) for c in ys], 1.0, 1.0)
    for t in range(fl.n):
        if job.max_w[t] <= 0:
            continue
        terms = _power(fl, i, t)
        for col, start in zip(ys, job.candidates, strict=True):
            p = job.power_at(start, t)
            if p > 0:
                terms.append((col, -p / KW))
        rows.add(terms, 0.0, 0.0)


def _thermal_rows(  # noqa: PLR0917 - the LP arrays
    fe: FlexEconomics,
    fl: FlexLayout,
    i: int,
    th: PlannerThermal,
    cost: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
    rows: RowSink,
    dt: float,
) -> None:
    penalty = dt * fe.comfort_penalty[i]
    for t in range(fl.n):
        temp, low, high = fl.temp(i, t), fl.low(i, t), fl.high(i, t)
        lower[temp], upper[temp] = -TEMP_BOUND_C, TEMP_BOUND_C
        upper[low] = upper[high] = INF
        cost[low] = cost[high] = penalty
        power = [(c, -th.heat_k[t]) for c, _ in _power(fl, i, t)]
        if t == 0:
            rhs = th.drift[0] + th.alpha * th.temp0_c
            rows.add([(temp, 1.0), *power], rhs, rhs)
        else:
            rows.add(
                [(temp, 1.0), (fl.temp(i, t - 1), -th.alpha), *power], th.drift[t], th.drift[t]
            )
        rows.add([(temp, 1.0), (low, 1.0)], th.min_c, INF)
        rows.add([(temp, 1.0), (high, -1.0)], -INF, th.max_c)
        rows.add(_power(fl, i, t), -INF, th.max_w[t] / KW)
        if not th.grid_preheat:
            rows.add([(fl.g(i, t), 1.0), (fl.b(i, t), 1.0)], -INF, th.base_w[t] / KW)


# --- schedule conversions -------------------------------------------------------------------


def thermal_temps(th: PlannerThermal, power_w: Sequence[float]) -> list[float]:
    temps: list[float] = []
    temp = th.temp0_c
    for t, p in enumerate(power_w):
        temp = th.next_temp(t, temp, p)
        temps.append(temp)
    return temps


def flows_from_vector(fl: FlexLayout, x: Sequence[float] | FloatArray) -> tuple[FlexFlows, ...]:
    """The flexible loads' flows of an LP solution (W; tiny negatives clipped)."""
    out: list[FlexFlows] = []
    n = fl.n

    def series(col: Callable[[int, int], int], i: int) -> tuple[float, ...]:
        return tuple(max(0.0, float(x[col(i, t)])) * KW for t in range(n))

    for i, load in enumerate(fl.loads):
        s, g, b = series(fl.s, i), series(fl.g, i), series(fl.b, i)
        temps: tuple[float, ...] = ()
        start: int | None = None
        if isinstance(load, PlannerThermal):
            temps = tuple(thermal_temps(load, [s[t] + g[t] + b[t] for t in range(n)]))
        if isinstance(load, PlannerJob):
            weights = [float(x[fl.job_y(i, k)]) for k in range(len(load.candidates))]
            start = load.candidates[int(np.argmax(weights))]
        out.append(FlexFlows(s, g, b, temps, start))
    return tuple(out)


def fill_vector(  # noqa: PLR0912, PLR0917 - one block per load kind
    inputs: FlexInputs,
    fe: FlexEconomics,
    fl: FlexLayout,
    x: FloatArray,
    flows: Sequence[FlexFlows],
    import_w: Sequence[float],
    deficit_w: Sequence[float],
) -> None:
    """The flexible columns of a schedule (slacks recomputed, for costing and warm starts)."""
    n, dt = fl.n, inputs.dt_h
    for i, (load, f) in enumerate(zip(fl.loads, flows, strict=True)):
        for t in range(n):
            x[fl.s(i, t)] = f.surplus_w[t] / KW
            x[fl.g(i, t)] = f.grid_w[t] / KW
            x[fl.b(i, t)] = f.battery_w[t] / KW
        if isinstance(load, PlannerEv):
            got = 0.0
            for t in range(n):
                p = f.power_w(t)
                if load.max_w[t] > 0:
                    x[fl.ev_on(i, t)] = (
                        (1.0 if p > TOL_W or load.floor_w[t] > 0 else 0.0)
                        if load.binary(t)
                        else max(min(1.0, p / load.max_w[t]), 1.0 if load.floor_w[t] > 0 else 0.0)
                    )
                if load.target_slot(t):
                    s10 = min(f.surplus_w[t], fe.surplus_p10_w[t], load.max_w[t])
                    x[fl.ev_s10(i, t)] = s10 / KW
                    got += (f.grid_w[t] + s10) * dt
            if load.need_by_wh is not None and load.deadline_slot is not None:
                x[fl.ev_short(i)] = max(0.0, load.need_by_wh - got) / KW
        elif isinstance(load, PlannerJob):
            start = f.start if f.start is not None else load.candidates[0]
            for k, cand in enumerate(load.candidates):
                x[fl.job_y(i, k)] = 1.0 if cand == start else 0.0
        elif isinstance(load, PlannerThermal):
            temps = thermal_temps(load, [f.power_w(t) for t in range(n)])
            for t, temp in enumerate(temps):
                x[fl.temp(i, t)] = temp
                x[fl.low(i, t)] = max(0.0, load.min_c - temp)
                x[fl.high(i, t)] = max(0.0, temp - load.max_c)
    for t in range(n):
        limit = inputs.import_limit_at(t)
        if limit is not None:
            x[fl.ov(t)] = max(0.0, import_w[t] - max(limit, deficit_w[t])) / KW
    if fl.n_months:
        peaks = list(inputs.month_floors_w())
        for t in range(n):
            m = inputs.month_of(t)
            peaks[m] = max(peaks[m], import_w[t])
        for m, peak in enumerate(peaks):
            x[fl.pk(m)] = peak / KW


def forced_power_w(load: FlexLoad, flows: FlexFlows, t: int) -> float:
    """The user-demanded part of the planned power (may exceed the import limit)."""
    power = flows.power_w(t)
    if isinstance(load, PlannerJob):
        return power
    if isinstance(load, PlannerThermal):
        return min(power, load.base_w[t])
    if isinstance(load, PlannerEv):
        return min(power, load.floor_w[t])
    return 0.0


def ev_delivered_wh(
    ev: PlannerEv, flows: FlexFlows, surplus_p10_w: Sequence[float], dt: float
) -> float:
    """Energy that counts toward the target (grid plus surplus up to the P10 surplus)."""
    return sum(
        (flows.grid_w[t] + min(flows.surplus_w[t], surplus_p10_w[t], ev.max_w[t])) * dt
        for t in range(len(flows.grid_w))
        if ev.target_slot(t)
    )


def flex_violations(  # noqa: PLR0912 - one check per rule of the module doc
    inputs: FlexInputs, flows: Sequence[FlexFlows], *, battery: bool
) -> list[str]:
    """Constraint violations of the flexible loads beyond rounding (empty = feasible)."""
    found: list[str] = []
    n, dt = inputs.n, inputs.dt_h
    loads = inputs.flex_loads
    if len(flows) != len(loads):
        return [f"flex flows: {len(flows)} for {len(loads)} loads"]
    for load, f in zip(loads, flows, strict=True):
        name = load.device_id
        total = 0.0
        for t in range(n):
            s, g, b = f.surplus_w[t], f.grid_w[t], f.battery_w[t]
            p = s + g + b
            if min(s, g, b) < -TOL_W:
                found.append(f"{name}: negative flow at {t}")
            if p > load.max_w[t] + TOL_W:
                found.append(f"{name}: above its power at {t}")
            if g > load.grid_max_w[t] + TOL_W:
                found.append(f"{name}: grid power not allowed at {t}")
            if b > TOL_W and (not load.battery or not battery):
                found.append(f"{name}: battery power not allowed at {t}")
            total += p * dt
            if isinstance(load, PlannerEv):
                if p > TOL_W and p < load.min_w - TOL_W and load.binary(t):
                    found.append(f"{name}: below the minimum power at {t}")
                if p < load.floor_w[t] - TOL_W:
                    found.append(f"{name}: below the demanded floor at {t}")
            if (
                isinstance(load, PlannerThermal)
                and not load.grid_preheat
                and g + b > load.base_w[t] + TOL_W
            ):
                found.append(f"{name}: pre-heating from the grid at {t}")
        if (
            isinstance(load, PlannerEv)
            and load.need_total_wh is not None
            and total > load.need_total_wh + TOL_W * dt
        ):
            found.append(f"{name}: more energy than the vehicle takes")
        if isinstance(load, PlannerJob):
            start = f.start
            if start is None or start not in load.candidates:
                found.append(f"{name}: no valid start")
            else:
                for t in range(n):
                    if abs(f.power_w(t) - load.power_at(start, t)) > TOL_W:
                        found.append(f"{name}: profile not followed at {t}")
                        break
        if isinstance(load, PlannerThermal):
            temps = thermal_temps(load, [f.power_w(t) for t in range(n)])
            if f.temp_c and any(abs(a - b) > TOL_K for a, b in zip(temps, f.temp_c, strict=True)):
                found.append(f"{name}: temperature dynamics")
    for t in range(n):
        budget = inputs.para14a_at(t)
        if budget is not None:
            flagged = sum(
                f.power_w(t) for load, f in zip(loads, flows, strict=True) if load.para14a
            )
            if flagged > budget + TOL_W:
                found.append(f"§14a budget at {t}")
    return found


# --- bands and reasons ------------------------------------------------------------------------


def _w(value: float) -> float:
    return round(max(0.0, value), 1)


def flex_band(load: FlexLoad, flows: FlexFlows, t: int) -> DeviceBand:  # noqa: PLR0911
    """The device band of slot ``t`` (ADR-0017 decisions 4 and 7)."""
    power = flows.power_w(t)
    if isinstance(load, PlannerJob):
        start = flows.start if flows.start is not None else load.candidates[0]
        end = start + len(load.profile_w)
        if load.running:
            return DeviceBand(preference=BandPreference.PREFER_ON)
        if t < start:
            return DeviceBand(allowed=False, preference=BandPreference.PREFER_OFF)
        if t == start:
            return DeviceBand(preference=BandPreference.PREFER_ON, start=True)
        if t < end:
            return DeviceBand(preference=BandPreference.PREFER_ON)
        return DeviceBand()
    if isinstance(load, PlannerEv):
        if load.max_w[t] <= 0:
            return DeviceBand(allowed=False, preference=BandPreference.PREFER_OFF, max_power_w=0.0)
        grid = flows.grid_w[t]
        permitted = _w(grid) if grid > EPS_W and load.mode.value == "target" else None
        return DeviceBand(
            preference=BandPreference.PREFER_ON if power > EPS_W else BandPreference.NEUTRAL,
            max_power_w=_w(load.max_w[t]),
            grid_power_max_w=permitted,
        )
    if isinstance(load, PlannerThermal):
        lift_grid = max(0.0, flows.grid_w[t] + flows.battery_w[t] - load.base_w[t])
        lift_grid = min(lift_grid, flows.grid_w[t])
        permitted = _w(lift_grid) if load.grid_preheat and lift_grid > EPS_W else None
        lifting = power > load.base_w[t] + EPS_W
        return DeviceBand(
            preference=BandPreference.PREFER_ON if lifting else BandPreference.NEUTRAL,
            max_power_w=_w(load.max_w[t]),
            grid_power_max_w=permitted,
        )
    return DeviceBand()


def flex_reasons(  # noqa: PLR0912 - one branch per load kind
    inputs: FlexInputs,
    fe: FlexEconomics,
    flows: Sequence[FlexFlows],
    t: int,
    hhmm: Callable[[int], str],
) -> list[Reason]:
    """Reasons of slot ``t`` for the flexible loads (keys in :mod:`reasons`)."""
    out: list[Reason] = []
    dt = inputs.dt_h
    for load, f in zip(inputs.flex_loads, flows, strict=True):
        device = load.device_id
        power = f.power_w(t)
        if isinstance(load, PlannerEv):
            if load.floor_w[t] > EPS_W and power > EPS_W:
                out.append(
                    Reason(
                        key=rk.EV_FLOOR,
                        args={"device": device, "mode": load.mode.value, "power_w": round(power)},
                    )
                )
            elif f.grid_w[t] > EPS_W:
                out.append(
                    Reason(
                        key=rk.EV_GRID_FOR_TARGET,
                        args={
                            "device": device,
                            "power_w": round(f.grid_w[t]),
                            "price": round(inputs.import_price[t], 4),
                            "time": hhmm(load.deadline_slot if load.deadline_slot else t),
                        },
                    )
                )
            elif f.surplus_w[t] > EPS_W:
                out.append(
                    Reason(key=rk.EV_SURPLUS, args={"device": device, "power_w": round(power)})
                )
            if (
                load.need_by_wh is not None
                and load.deadline_slot == t
                and ev_delivered_wh(load, f, fe.surplus_p10_w, dt) < load.need_by_wh - 1.0
            ):
                out.append(
                    Reason(
                        key=rk.EV_TARGET_SHORTFALL,
                        args={
                            "device": device,
                            "need_kwh": round(load.need_by_wh / KW, 1),
                            "planned_kwh": round(
                                ev_delivered_wh(load, f, fe.surplus_p10_w, dt) / KW, 1
                            ),
                            "time": hhmm(t),
                        },
                    )
                )
        elif isinstance(load, PlannerJob):
            if load.running and power > EPS_W:
                out.append(
                    Reason(key=rk.JOB_RUNNING, args={"device": device, "power_w": round(power)})
                )
            elif f.start == t:
                out.append(
                    Reason(
                        key=rk.JOB_START,
                        args={
                            "device": device,
                            "deadline": load.deadline.astimezone(inputs.tz).strftime("%H:%M"),
                        },
                    )
                )
        elif isinstance(load, PlannerThermal):
            temp = f.temp_c[t] if f.temp_c else load.temp0_c
            lift = power - load.base_w[t]
            if lift > EPS_W:
                grid_lift = min(
                    f.grid_w[t], max(0.0, f.grid_w[t] + f.battery_w[t] - load.base_w[t])
                )
                if grid_lift > EPS_W:
                    out.append(
                        Reason(
                            key=rk.THERMAL_PREHEAT_GRID,
                            args={
                                "device": device,
                                "power_w": round(grid_lift),
                                "price": round(inputs.import_price[t], 4),
                            },
                        )
                    )
                else:
                    out.append(
                        Reason(
                            key=rk.THERMAL_PREHEAT_SURPLUS,
                            args={
                                "device": device,
                                "power_w": round(lift),
                                "temp_c": round(temp, 1),
                            },
                        )
                    )
            if temp < load.min_c - TOL_K:
                out.append(
                    Reason(
                        key=rk.THERMAL_COMFORT_SHORTFALL,
                        args={"device": device, "temp_c": round(temp, 1), "min_c": load.min_c},
                    )
                )
    return out


__all__ = [
    "BaseColumns",
    "FlexEconomics",
    "FlexInputs",
    "FlexLayout",
    "add_rows",
    "battery_terms",
    "ev_delivered_wh",
    "fill_vector",
    "flex_band",
    "flex_economics",
    "flex_layout",
    "flex_reasons",
    "flex_violations",
    "flows_from_vector",
    "forced_power_w",
    "set_flow_bounds",
    "surplus_terms",
    "thermal_temps",
]
