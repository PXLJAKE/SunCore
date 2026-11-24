# SPDX-License-Identifier: Apache-2.0
"""Backtest engine (Phase 5 WP6, ADR-0014 decision 5): replay stored history through the planner.

For each local day of the window and each hour of it, the engine builds
:class:`~suncore.planner.inputs.PlannerInputs` from what was **known then** - the newest stored
PV and load forecast issued at or before that hour (``latest_forecast_before``), the stored
prices up to their day-ahead publication (13:00 local publishes the next day), and the battery's
state of charge as the replay has simulated it - runs the **real planner** (HiGHS with the
heuristic fallback, the parameter overlay applied) and then **executes the first hour against
the actual PV and house load** with a simple battery model (:func:`execute_slot`): PV surplus
charges the battery, a deficit discharges it, both inside the plan's battery band (SoC corridor,
power limits, discharge lock, grid charge only where the plan has it), with the efficiencies of
the battery model. The controller and the safety gate are not in the loop and the devices' loads
stay as measured: the result is the *plan with perfect execution*, labelled that way.

Three runs are compared on exactly the same priced quarter hours: *actual* (the measured grid
exchange), *what-if* (the replay) and, on request, *perfect foresight* (the replay with the
actual PV and load as forecast - the bound for better forecasts). Both baselines come from
:mod:`suncore.analysis` on the actual house load and PV, so ``savings = baseline - cost`` holds
for every run. A day is skipped, with a :class:`~suncore.core.decisions.Reason`, when less than
80 % of it was measured, when no price is stored for it (unless a flat tariff is tried) or when
an hour has no stored forecast.

**No device writes.** The engine sees history only through :class:`HistoryReader` (four read
methods) and configuration only as the plain :class:`BacktestSite` value; it imports nothing
from ``safety``, ``ha``, ``devices``, ``controller`` or the composition root (boundary test).

Determinism: the same history, site and request give the same result; only
:class:`~suncore.sim.backtest_models.SolveStats` (wall-clock solve times) varies.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from datetime import time as dtime
from functools import partial
from typing import Final, Literal, Protocol

from suncore.analysis.baselines import compute_savings
from suncore.analysis.energy import MIN_COVERAGE, SlotEnergy
from suncore.analysis.flows import total_flows
from suncore.analysis.kpi import kpis
from suncore.analysis.period import Granularity, bucket_for
from suncore.core.battery import BatteryModel, BatterySettings
from suncore.core.config import GoalsConfig
from suncore.core.control import BatteryBand, Plan
from suncore.core.decisions import Reason
from suncore.core.measurements import (
    SITE_BATTERY_W,
    SITE_GRID_W,
    SITE_HOUSE_W,
    SITE_PV_W,
    SITE_SOC_PCT,
)
from suncore.core.params import whatif_targets
from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    PriceSeries,
    QuantileSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
    resample_prices,
)
from suncore.core.site import BatteryConfig
from suncore.planner.inputs import (
    DEFAULT_MIP_GAP,
    PLANNER_HORIZON,
    PlannerBattery,
    PlannerInputs,
    build_inputs,
)
from suncore.planner.run import PlannerRun
from suncore.planner.run import run as run_planner
from suncore.sim.backtest_models import (
    BacktestRequest,
    BacktestResult,
    BacktestTotals,
    BacktestWindowError,
    DayResult,
    HourlySeries,
    RunFigures,
    SkippedDay,
    SolveStats,
)

BACKTEST_BUDGET_S: Final = 2.0
"""Default solver budget of one hourly replan (the registry's ``planner.budget_s`` overrides)."""
MAX_REPLAN_BUDGET_S: Final = 10.0
"""Upper bound of a replan's budget in a backtest (plan §0 item 7: ≤ 10 s on the Pi)."""
REPLAN_SLOTS: Final = 4
"""Quarter hours executed per replan (hourly replanning)."""
PRICE_PUBLICATION_HOUR: Final = 13
"""Local hour after which the next day's day-ahead prices count as known."""
PV_KIND: Final = "pv"
LOAD_KIND: Final = "load"

LIMITATIONS: Final = (
    Reason(key="backtest.limit.perfect_execution"),
    Reason(key="backtest.limit.devices_as_measured"),
    Reason(key="backtest.limit.no_risk_model"),
    Reason(key="backtest.limit.grid_cost_only"),
)
"""What the replay does not model (shown with every result)."""
LIMITATION_PRICES: Final = Reason(key="backtest.limit.prices_day_ahead")


class BacktestStoppedError(RuntimeError):
    """The run was asked to stop (cancel, runtime or memory limit)."""


# --- inputs ----------------------------------------------------------------------------------


class HistoryReader(Protocol):
    """The read-only view of ``timeseries.db`` a backtest gets (no write method exists here)."""

    async def read_series(
        self,
        key: str,
        window: TimeRange,
        resolution: Literal["15m"] = "15m",
        unit: SeriesUnit | None = None,
        *,
        include_recent: bool = True,
    ) -> TimeSeries: ...

    async def latest_forecast_before(
        self,
        source_id: str,
        kind: Literal["pv", "load"],
        before: datetime,
        window: TimeRange | None = None,
    ) -> QuantileSeries | None: ...

    async def read_prices(self, source_id: str, window: TimeRange) -> list[PriceSeries]: ...

    async def price_source_ids(self, window: TimeRange) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class BacktestSite:
    """The configuration a run plans with (a plain value; built by the app, overlaid here)."""

    tz: tzinfo
    tz_name: str
    battery: BatterySettings
    site_battery: BatteryConfig | None = None
    goals: GoalsConfig | None = None
    import_limit_w: float | None = None
    export_limit_w: float | None = None
    price_source_id: str | None = None
    """The configured price source; its stored prices are preferred."""
    export_fallback: float | None = None
    """Feed-in price where the stored prices have none (a fixed export tariff)."""
    currency: str | None = None
    pv_source_ids: tuple[str, ...] = ()
    """PV forecast sources in order of preference (the blend first)."""
    load_source_ids: tuple[str, ...] = ("load_profile",)
    budget_s: float = BACKTEST_BUDGET_S
    mip_gap: float = DEFAULT_MIP_GAP


def apply_overlay(site: BacktestSite, request: BacktestRequest) -> BacktestSite:
    """``site`` with the request's overlay (registry parameters and battery settings).

    Raises :class:`~suncore.core.params.ParamError` for a parameter a what-if may not change and
    ``pydantic.ValidationError`` for battery values that do not fit together. Never writes.
    """
    planner = whatif_targets(request.params, "planner")
    budget = float(planner.get("budget_s", site.budget_s))
    mip_gap = float(planner.get("mip_gap", site.mip_gap))
    battery = site.battery
    overlay = request.battery
    if overlay is not None:
        body = battery.model_dump()
        for name in ("strategy", "autarky_weight_per_kwh", "grid_charge"):
            value = getattr(overlay, name)
            if value is not None:
                body[name] = value.model_dump() if name == "grid_charge" else value
        model_changes = {
            name: getattr(overlay, name)
            for name in ("capacity_wh", "max_charge_w", "max_discharge_w", "reserve_pct")
            if getattr(overlay, name) is not None
        }
        if model_changes and body.get("model") is not None:
            body["model"] = {**body["model"], **model_changes}
        battery = BatterySettings.model_validate(body)
    return replace(
        site,
        battery=battery,
        budget_s=min(MAX_REPLAN_BUDGET_S, max(0.1, budget)),
        mip_gap=mip_gap,
    )


Solve = Callable[[PlannerInputs, float, float, Plan | None], PlannerRun]


def default_solve(
    inputs: PlannerInputs, budget_s: float, mip_gap: float, previous: Plan | None
) -> PlannerRun:
    return run_planner(inputs, budget_s=budget_s, mip_gap=mip_gap, warm_start=previous)


@dataclass(frozen=True, slots=True)
class HourInputs:
    """What one replan knew: its instant, the forecasts and the prices known then."""

    now: datetime
    pv: QuantileSeries | None
    load: QuantileSeries | None
    prices: PriceSeries | None


@dataclass(frozen=True, slots=True)
class DayData:
    """One local day: the measured quarter hours and the inputs of each replan."""

    day: date
    starts: tuple[datetime, ...]
    pv_w: tuple[float | None, ...]
    house_w: tuple[float | None, ...]
    grid_w: tuple[float | None, ...]
    battery_w: tuple[float | None, ...]
    soc_pct: tuple[float | None, ...]
    prices: tuple[tuple[float, float] | None, ...]
    """Import and export price per slot of the day under the run's tariff."""
    hours: tuple[HourInputs, ...]
    perfect_hours: tuple[HourInputs, ...] = ()

    def complete(self, i: int) -> bool:
        """Slot ``i`` has every actual reading (PV, house, grid, battery)."""
        return None not in (self.pv_w[i], self.house_w[i], self.grid_w[i], self.battery_w[i])

    @property
    def coverage(self) -> float:
        return sum(1 for i in range(len(self.starts)) if self.complete(i)) / len(self.starts)


# --- execution -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BatteryLimits:
    """The physical battery of the replay (from the planner's view of it)."""

    model: BatteryModel
    floor_wh: float
    ceiling_wh: float
    max_charge_w: float
    max_discharge_w: float

    @classmethod
    def of(cls, battery: PlannerBattery) -> BatteryLimits:
        return cls(
            model=battery.model,
            floor_wh=battery.reserve_wh,
            ceiling_wh=battery.max_wh,
            max_charge_w=battery.max_charge_w,
            max_discharge_w=battery.max_discharge_w,
        )


@dataclass(frozen=True, slots=True)
class Executed:
    """One executed quarter hour (mean W; battery + = charging, grid + = import)."""

    pv_w: float
    """PV used (generation minus curtailment)."""
    house_w: float
    battery_w: float
    grid_w: float
    curtail_w: float
    energy_wh: float
    """Battery content at the end of the slot."""


def execute_slot(
    pv_w: float,
    house_w: float,
    energy_wh: float,
    dt_h: float,
    *,
    limits: BatteryLimits | None,
    band: BatteryBand | None,
    planned_battery_w: float = 0.0,
    export_limit_w: float | None = None,
) -> Executed:
    """Execute one slot of a plan against the actual PV and house load.

    The battery behaves like a self-consumption inverter inside the plan's band (ADR-0013:
    limits, not set-points): PV surplus charges it (unless the band gives the surplus to
    devices), a deficit discharges it (unless the band locks discharging), never outside the
    band's SoC corridor or power limits; it charges from the grid only where the band allows it
    and the plan expects it, up to the planned power. ``pv_used + grid = house + battery`` holds
    exactly; the battery content changes by ``dt * (eta_c * charge - discharge / eta_d)``.
    """
    pv = max(pv_w, 0.0)
    house = max(house_w, 0.0)
    surplus = pv - house
    charge = grid_charge = discharge = 0.0
    energy = energy_wh
    if limits is not None and dt_h > 0:
        model = limits.model
        eta_c, eta_d = model.charge_efficiency, model.discharge_efficiency
        floor, ceiling = limits.floor_wh, limits.ceiling_wh
        max_c, max_d = limits.max_charge_w, limits.max_discharge_w
        from_surplus, grid_allowed = True, 0.0
        if band is not None:
            if band.min_soc_pct is not None:
                floor = max(floor, model.soc_to_wh(band.min_soc_pct))
            if band.max_soc_pct is not None:
                ceiling = min(ceiling, model.soc_to_wh(band.max_soc_pct))
            if band.max_power_w is not None:
                max_c = min(max_c, max(0.0, band.max_power_w))
            if band.min_power_w is not None:
                max_d = min(max_d, max(0.0, -band.min_power_w))
            if band.discharge_lock:
                max_d = 0.0
            from_surplus = band.charge_from_surplus
            grid_allowed = band.grid_charge_max_w or 0.0
        floor, ceiling = min(floor, energy), max(ceiling, energy)
        room_w = max(0.0, ceiling - energy) / (dt_h * eta_c)
        avail_w = max(0.0, energy - floor) * eta_d / dt_h
        grid_planned = grid_allowed > 0 and planned_battery_w > 0
        if surplus > 0 and from_surplus:
            charge = min(surplus, max_c, room_w)
        elif surplus < 0 and not grid_planned:
            discharge = min(-surplus, max_d, avail_w)
        if grid_planned:
            grid_charge = max(
                0.0, min(planned_battery_w - charge, grid_allowed, max_c - charge, room_w - charge)
            )
        energy = energy + dt_h * (eta_c * (charge + grid_charge) - discharge / eta_d)
    battery = charge + grid_charge - discharge
    grid = house + battery - pv
    curtail = 0.0
    if export_limit_w is not None and -grid > export_limit_w:
        curtail = -grid - export_limit_w
        grid = -export_limit_w
    return Executed(
        pv_w=pv - curtail,
        house_w=house,
        battery_w=battery,
        grid_w=grid,
        curtail_w=curtail,
        energy_wh=energy,
    )


# --- replay of one day -----------------------------------------------------------------------


@dataclass(slots=True)
class ReplayState:
    """What carries from one replayed day to the next (reset after a skipped day)."""

    energy_wh: float | None = None
    previous: Plan | None = None


@dataclass(slots=True)
class DayRun:
    """One replayed day: the executed slots (``None`` = no actuals) and solver statistics."""

    slots: list[Executed | None] = field(default_factory=list)
    soc_pct: list[float | None] = field(default_factory=list)
    replans: int = 0
    fallbacks: int = 0
    solve_s: list[float] = field(default_factory=list)


def _first_known(values: Sequence[float | None]) -> float | None:
    return next((v for v in values if v is not None), None)


def replay_day(
    data: DayData,
    site: BacktestSite,
    state: ReplayState,
    *,
    perfect: bool = False,
    solve: Solve = default_solve,
    should_stop: Callable[[], bool] | None = None,
) -> DayRun:
    """Replan hourly and execute each hour against the actuals (module doc). Pure apart from
    the solver's wall-clock time; mutates ``state`` (the battery content and the warm start)."""
    hours = data.perfect_hours if perfect else data.hours
    out = DayRun()
    dt_h = PLANNING_STEP / timedelta(hours=1)
    model = site.battery.model
    if state.energy_wh is None and model is not None:
        soc = _first_known(data.soc_pct)
        state.energy_wh = model.soc_to_wh(soc if soc is not None else model.reserve_pct)
    for k, hour in enumerate(hours):
        if should_stop is not None and should_stop():
            raise BacktestStoppedError
        soc_pct = None
        if model is not None and state.energy_wh is not None:
            soc_pct = min(100.0, max(0.0, model.wh_to_soc(state.energy_wh)))
        inputs = build_inputs(
            hour.now,
            tz=site.tz,
            load=hour.load,
            pv=hour.pv,
            prices=hour.prices,
            battery=site.battery,
            soc_pct=soc_pct,
            goals=site.goals,
            site_battery=site.site_battery,
            import_limit_w=site.import_limit_w,
            export_limit_w=site.export_limit_w,
            previous=state.previous,
        )
        started = time.perf_counter()
        result = solve(inputs, site.budget_s, site.mip_gap, state.previous)
        out.solve_s.append(time.perf_counter() - started)
        out.replans += 1
        if result.source == "heuristic":
            out.fallbacks += 1
        plan = result.plan
        state.previous = plan
        by_start = {i.start: i for i in plan.intervals}
        limits = None if inputs.battery is None else BatteryLimits.of(inputs.battery)
        first = k * REPLAN_SLOTS
        for i in range(first, min(first + REPLAN_SLOTS, len(data.starts))):
            pv, house = data.pv_w[i], data.house_w[i]
            if pv is None or house is None:
                out.slots.append(None)
                out.soc_pct.append(None)
                continue
            interval = by_start.get(data.starts[i])
            band = None if interval is None else interval.battery
            planned = 0.0
            if interval is not None and interval.expected is not None:
                planned = interval.expected.battery_w
            done = execute_slot(
                pv,
                house,
                state.energy_wh or 0.0,
                dt_h,
                limits=limits,
                band=band,
                planned_battery_w=planned,
                export_limit_w=site.export_limit_w,
            )
            if model is not None:
                state.energy_wh = done.energy_wh
            out.slots.append(done)
            out.soc_pct.append(None if model is None else round(model.wh_to_soc(done.energy_wh), 3))
    return out


# --- figures ---------------------------------------------------------------------------------


@dataclass(slots=True)
class _Runs:
    """Priced slots of every run, aligned (the same slots in each list)."""

    actual: list[SlotEnergy] = field(default_factory=list)
    whatif: list[SlotEnergy] = field(default_factory=list)
    perfect: list[SlotEnergy] = field(default_factory=list)
    whatif_curtailed: float = 0.0
    perfect_curtailed: float = 0.0
    prices: dict[datetime, tuple[float, float]] = field(default_factory=dict)

    def extend(self, other: _Runs) -> None:
        self.actual.extend(other.actual)
        self.whatif.extend(other.whatif)
        self.perfect.extend(other.perfect)
        self.whatif_curtailed += other.whatif_curtailed
        self.perfect_curtailed += other.perfect_curtailed
        self.prices.update(other.prices)


def _slot_energy(start: datetime, done: Executed, dt_h: float) -> SlotEnergy:
    return SlotEnergy(
        start=start,
        pv_wh=done.pv_w * dt_h,
        grid_wh=done.grid_w * dt_h,
        battery_wh=done.battery_w * dt_h,
        house_wh=done.house_w * dt_h,
    )


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def figures(
    slots: Sequence[SlotEnergy],
    prices: dict[datetime, tuple[float, float]],
    battery: BatteryModel | None,
    curtailed_wh: float = 0.0,
) -> RunFigures:
    """Energy, cost and KPIs of one run (``suncore.analysis``)."""
    flows = total_flows(slots)
    figures_ = kpis(flows, battery)
    cost = compute_savings(slots, prices).actual
    return RunFigures(
        pv_wh=round(flows.pv_wh, 1),
        house_wh=round(flows.house_wh, 1),
        import_wh=round(flows.import_wh, 1),
        export_wh=round(flows.export_wh, 1),
        charge_wh=round(flows.charge_wh, 1),
        discharge_wh=round(flows.discharge_wh, 1),
        curtailed_wh=round(curtailed_wh, 1),
        import_cost=round(cost.import_cost, 6),
        export_revenue=round(cost.export_revenue, 6),
        cost_net=round(cost.net, 6),
        autarky=_round(figures_.autarky, 6),
        self_consumption=_round(figures_.self_consumption, 6),
        equivalent_full_cycles=_round(figures_.equivalent_full_cycles, 6),
    )


def _priced_runs(data: DayData, whatif: DayRun, perfect: DayRun | None) -> _Runs:
    """The slots that have every actual reading and a price, for every run."""
    dt_h = PLANNING_STEP / timedelta(hours=1)
    runs = _Runs()
    for i, start in enumerate(data.starts):
        price = data.prices[i]
        done = whatif.slots[i]
        if price is None or done is None or not data.complete(i):
            continue
        other = None if perfect is None else perfect.slots[i]
        if perfect is not None and other is None:
            continue
        pv, grid, battery, house = (
            data.pv_w[i],
            data.grid_w[i],
            data.battery_w[i],
            data.house_w[i],
        )
        assert pv is not None
        assert grid is not None
        assert battery is not None
        assert house is not None
        runs.actual.append(
            SlotEnergy(
                start=start,
                pv_wh=pv * dt_h,
                grid_wh=grid * dt_h,
                battery_wh=battery * dt_h,
                house_wh=house * dt_h,
            )
        )
        runs.whatif.append(_slot_energy(start, done, dt_h))
        runs.whatif_curtailed += done.curtail_w * dt_h
        if other is not None:
            runs.perfect.append(_slot_energy(start, other, dt_h))
            runs.perfect_curtailed += other.curtail_w * dt_h
        runs.prices[start] = price
    return runs


def _baselines(runs: _Runs) -> tuple[float, float]:
    savings = compute_savings(runs.actual, runs.prices)
    return round(savings.baseline_no_pv.net, 6), round(savings.baseline_pv_only.net, 6)


def day_result(
    data: DayData, whatif: DayRun, perfect: DayRun | None, battery: BatteryModel | None
) -> tuple[DayResult, _Runs]:
    runs = _priced_runs(data, whatif, perfect)
    no_pv, pv_only = _baselines(runs)
    return (
        DayResult(
            day=data.day,
            slots=sum(1 for i in range(len(data.starts)) if data.complete(i)),
            priced_slots=len(runs.actual),
            actual=figures(runs.actual, runs.prices, battery),
            whatif=figures(runs.whatif, runs.prices, battery, runs.whatif_curtailed),
            perfect=None
            if perfect is None
            else figures(runs.perfect, runs.prices, battery, runs.perfect_curtailed),
            baseline_no_pv_cost=no_pv,
            baseline_pv_only_cost=pv_only,
        ),
        runs,
    )


@dataclass(slots=True)
class _Hourly:
    starts: list[datetime] = field(default_factory=list)
    actual_soc: list[float | None] = field(default_factory=list)
    whatif_soc: list[float | None] = field(default_factory=list)
    perfect_soc: list[float | None] = field(default_factory=list)
    actual_grid: list[float | None] = field(default_factory=list)
    whatif_grid: list[float | None] = field(default_factory=list)
    actual_battery: list[float | None] = field(default_factory=list)
    whatif_battery: list[float | None] = field(default_factory=list)

    def add_day(self, data: DayData, whatif: DayRun | None, perfect: DayRun | None) -> None:
        for first in range(0, len(data.starts), REPLAN_SLOTS):
            idx = range(first, min(first + REPLAN_SLOTS, len(data.starts)))
            self.starts.append(data.starts[first])
            self.actual_soc.append(_mean([data.soc_pct[i] for i in idx], 2))
            self.actual_grid.append(_mean([data.grid_w[i] for i in idx], 1))
            self.actual_battery.append(_mean([data.battery_w[i] for i in idx], 1))
            done = [None if whatif is None else whatif.slots[i] for i in idx]
            self.whatif_soc.append(
                _last([None if whatif is None else whatif.soc_pct[i] for i in idx])
            )
            self.whatif_grid.append(_mean([None if d is None else d.grid_w for d in done], 1))
            self.whatif_battery.append(_mean([None if d is None else d.battery_w for d in done], 1))
            self.perfect_soc.append(
                _last([None if perfect is None else perfect.soc_pct[i] for i in idx])
            )

    def series(self, with_perfect: bool) -> HourlySeries:
        return HourlySeries(
            starts=self.starts,
            actual_soc_pct=self.actual_soc,
            whatif_soc_pct=self.whatif_soc,
            perfect_soc_pct=self.perfect_soc if with_perfect else None,
            actual_grid_w=self.actual_grid,
            whatif_grid_w=self.whatif_grid,
            actual_battery_w=self.actual_battery,
            whatif_battery_w=self.whatif_battery,
        )


def _mean(values: Sequence[float | None], digits: int) -> float | None:
    known = [v for v in values if v is not None]
    return round(math.fsum(known) / len(known), digits) if known else None


def _last(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return known[-1] if known else None


# --- data assembly ---------------------------------------------------------------------------


def price_known_until(now: datetime, tz: tzinfo) -> datetime:
    """End of what day-ahead prices tell at ``now``: the end of today, or of tomorrow from
    :data:`PRICE_PUBLICATION_HOUR` local time on."""
    local = now.astimezone(tz)
    days = 1 if local.hour < PRICE_PUBLICATION_HOUR else 2
    return bucket_for(local + timedelta(days=days - 1), Granularity.DAY, tz).end


def _clip_prices(prices: PriceSeries, until: datetime) -> PriceSeries:
    def cut(series: TimeSeries) -> TimeSeries:
        values = tuple(
            v if series.slot_start(i) < until else None for i, v in enumerate(series.values)
        )
        return series.model_copy(update={"values": values})

    return prices.model_copy(
        update={
            "import_price": cut(prices.import_price),
            "export_price": None if prices.export_price is None else cut(prices.export_price),
        }
    )


def _flat_prices(grid: Grid, import_price: float, export_price: float) -> PriceSeries:
    def series(value: float) -> TimeSeries:
        return TimeSeries(
            start=grid.start,
            step=grid.step,
            values=tuple(value for _ in range(grid.count)),
            unit=SeriesUnit.PRICE,
        )

    return PriceSeries(
        source_id="backtest.fixed",
        price_includes="all_in",
        import_price=series(import_price),
        export_price=series(export_price),
    )


async def _stored_prices(
    history: HistoryReader, site: BacktestSite, grid: Grid
) -> PriceSeries | None:
    """The stored prices on ``grid`` from one source (the configured one if it has any)."""
    window = TimeRange(start=grid.start, end=grid.end)
    sources = await history.price_source_ids(window)
    if not sources:
        return None
    source = site.price_source_id if site.price_source_id in sources else sources[0]
    segments = await history.read_prices(source, window)
    if not segments:
        return None
    includes = segments[0].price_includes
    held = resample_prices([s for s in segments if s.price_includes == includes], grid)
    fallback = site.export_fallback
    exports = None if held.export_price is None else held.export_price.values
    if exports is None and fallback is None:
        return held
    values = tuple(
        (fallback if exports is None or exports[i] is None else exports[i])
        for i in range(grid.count)
    )
    return held.model_copy(
        update={"export_price": held.import_price.model_copy(update={"values": values})}
    )


def _values_on(series: TimeSeries, grid: Grid) -> tuple[float | None, ...]:
    """The values of a 15-minute ``series`` at the slots of ``grid`` (``None`` outside)."""
    by_start = {series.slot_start(i): v for i, v in enumerate(series.values)}
    return tuple(by_start.get(s) for s in grid.starts())


async def _forecast(
    history: HistoryReader,
    sources: Sequence[str],
    kind: Literal["pv", "load"],
    now: datetime,
) -> QuantileSeries | None:
    window = TimeRange(start=now, end=now + PLANNER_HORIZON)
    for source in sources:
        found = await history.latest_forecast_before(source, kind, now, window)
        if found is not None and any(v is not None for v in found.p50.values):
            return found
    return None


def _actual_forecast(
    starts: Sequence[datetime], values: Sequence[float | None], now: datetime
) -> QuantileSeries:
    """The actual values from ``now`` on as a forecast without spread (perfect foresight).

    A slot without a reading takes the reading one day earlier (``None`` if that is missing
    too); the planner's input builder fills what is still missing.
    """
    index = {s: i for i, s in enumerate(starts)}
    begin = index[now]
    per_day = round(timedelta(days=1) / PLANNING_STEP)
    end = min(len(starts), begin + round(PLANNER_HORIZON / PLANNING_STEP))
    out: list[float | None] = []
    for i in range(begin, end):
        value = values[i]
        if value is None and i - per_day >= 0:
            value = values[i - per_day]
        out.append(None if value is None else max(0.0, value))
    series = TimeSeries(start=now, step=PLANNING_STEP, values=tuple(out), unit=SeriesUnit.W)
    return QuantileSeries.deterministic(series)


def _derived_house(
    house: float | None, grid: float | None, pv: float | None, battery: float | None
) -> float | None:
    """The measured house load, else the balance ``grid + pv - battery`` (as ``analysis``)."""
    if house is not None:
        return house
    if grid is None or pv is None or battery is None:
        return None
    return grid + pv - battery


async def load_day(
    history: HistoryReader,
    site: BacktestSite,
    request: BacktestRequest,
    day: date,
) -> DayData | Reason:
    """Read one day and the inputs of its replans; a :class:`Reason` if it must be skipped."""
    bucket = bucket_for(datetime.combine(day, dtime(12), tzinfo=site.tz), Granularity.DAY, site.tz)
    count = round(bucket.duration / PLANNING_STEP)
    day_grid = Grid(start=bucket.start, step=PLANNING_STEP, count=count)
    span = Grid(
        start=bucket.start,
        step=PLANNING_STEP,
        count=count + round(PLANNER_HORIZON / PLANNING_STEP),
    )
    window = TimeRange(start=span.start, end=span.end)

    async def read(key: str, unit: SeriesUnit) -> tuple[float | None, ...]:
        return _values_on(await history.read_series(key, window, "15m", unit), span)

    pv = await read(SITE_PV_W, SeriesUnit.W)
    grid = await read(SITE_GRID_W, SeriesUnit.W)
    battery = await read(SITE_BATTERY_W, SeriesUnit.W)
    house = await read(SITE_HOUSE_W, SeriesUnit.W)
    soc = await read(SITE_SOC_PCT, SeriesUnit.PERCENT)
    house = tuple(
        _derived_house(h, g, p, b) for h, g, p, b in zip(house, grid, pv, battery, strict=True)
    )
    starts = tuple(day_grid.starts())
    measured = sum(1 for i in range(count) if None not in (pv[i], house[i], grid[i], battery[i]))
    coverage = measured / count
    if coverage < MIN_COVERAGE:
        return Reason(
            key="backtest.skip.incomplete_measurements",
            args={"coverage_pct": round(coverage * 100, 1)},
        )
    tariff = request.tariff
    if tariff is not None:
        prices: PriceSeries | None = _flat_prices(span, tariff.import_price, tariff.export_price)
    else:
        prices = await _stored_prices(history, site, span)
    slot_prices: list[tuple[float, float] | None] = []
    for i in range(count):
        imp = None if prices is None else prices.import_price.values[i]
        exp = None
        if prices is not None and prices.export_price is not None:
            exp = prices.export_price.values[i]
        slot_prices.append(None if imp is None or exp is None else (imp, exp))
    if all(p is None for p in slot_prices):
        return Reason(key="backtest.skip.no_prices")
    hours: list[HourInputs] = []
    perfect: list[HourInputs] = []
    for first in range(0, count, REPLAN_SLOTS):
        now = starts[first]
        known = None
        if prices is not None:
            known = (
                prices
                if tariff is not None
                else _clip_prices(prices, price_known_until(now, site.tz))
            )
        load = await _forecast(history, site.load_source_ids, LOAD_KIND, now)
        pv_fc = (
            await _forecast(history, site.pv_source_ids, PV_KIND, now)
            if site.pv_source_ids
            else None
        )
        if load is None or (site.pv_source_ids and pv_fc is None):
            return Reason(key="backtest.skip.no_forecast")
        hours.append(HourInputs(now=now, pv=pv_fc, load=load, prices=known))
        if request.perfect_foresight:
            span_starts = span.starts()
            perfect.append(
                HourInputs(
                    now=now,
                    pv=_actual_forecast(span_starts, pv, now),
                    load=_actual_forecast(span_starts, house, now),
                    prices=known,
                )
            )
    return DayData(
        day=day,
        starts=starts,
        pv_w=pv[:count],
        house_w=house[:count],
        grid_w=grid[:count],
        battery_w=battery[:count],
        soc_pct=soc[:count],
        prices=tuple(slot_prices),
        hours=tuple(hours),
        perfect_hours=tuple(perfect),
    )


# --- the run ---------------------------------------------------------------------------------

Thread = Callable[[Callable[[], DayRun]], Awaitable[DayRun]]


async def _to_thread(fn: Callable[[], DayRun]) -> DayRun:
    return await asyncio.to_thread(fn)


def _totals(runs: _Runs, days: int, battery: BatteryModel | None, perfect: bool) -> BacktestTotals:
    no_pv, pv_only = _baselines(runs)
    return BacktestTotals(
        days=days,
        actual=figures(runs.actual, runs.prices, battery),
        whatif=figures(runs.whatif, runs.prices, battery, runs.whatif_curtailed),
        perfect=figures(runs.perfect, runs.prices, battery, runs.perfect_curtailed)
        if perfect
        else None,
        baseline_no_pv_cost=no_pv,
        baseline_pv_only_cost=pv_only,
    )


async def run_backtest(
    history: HistoryReader,
    site: BacktestSite,
    request: BacktestRequest,
    *,
    today: date,
    solve: Solve = default_solve,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    thread: Thread = _to_thread,
) -> BacktestResult:
    """Replay ``request``'s window (module doc). One day of data is in memory at a time; the
    replans of a day run in ``thread`` (a worker thread). Raises :class:`BacktestWindowError`
    for a window that is not in the past, :class:`BacktestStoppedError` when ``should_stop``
    turns true (checked before every replan)."""
    if request.end >= today:
        raise BacktestWindowError(request.end.isoformat())
    effective = apply_overlay(site, request)
    battery = effective.battery.model
    total = request.days
    whatif_state, perfect_state = ReplayState(), ReplayState()
    all_runs = _Runs()
    days: list[DayResult] = []
    skipped: list[SkippedDay] = []
    hourly = _Hourly()
    solve_s: list[float] = []
    replans = fallbacks = 0
    for index in range(total):
        if should_stop is not None and should_stop():
            raise BacktestStoppedError
        day = request.start + timedelta(days=index)
        data = await load_day(history, effective, request, day)
        if isinstance(data, Reason):
            skipped.append(SkippedDay(day=day, reason=data))
            whatif_state, perfect_state = ReplayState(), ReplayState()
        else:
            replay = partial(replay_day, data, effective, solve=solve, should_stop=should_stop)
            whatif = await thread(partial(replay, whatif_state))
            perfect = None
            if request.perfect_foresight:
                perfect = await thread(partial(replay, perfect_state, perfect=True))
            result, runs = day_result(data, whatif, perfect, battery)
            days.append(result)
            all_runs.extend(runs)
            hourly.add_day(data, whatif, perfect)
            for run in (whatif, perfect):
                if run is not None:
                    replans += run.replans
                    fallbacks += run.fallbacks
                    solve_s.extend(run.solve_s)
        if progress is not None:
            progress(index + 1, total)
    limitations = list(LIMITATIONS)
    if request.tariff is None:
        limitations.append(LIMITATION_PRICES)
    return BacktestResult(
        start=request.start,
        end=request.end,
        tz=site.tz_name,
        currency=site.currency,
        tariff="stored" if request.tariff is None else "fixed",
        params=dict(request.params),
        totals=_totals(all_runs, len(days), battery, request.perfect_foresight) if days else None,
        days=days,
        skipped=skipped,
        hourly=hourly.series(request.perfect_foresight),
        limitations=limitations,
        stats=SolveStats(
            replans=replans,
            fallbacks=fallbacks,
            solve_s_total=round(math.fsum(solve_s), 3),
            solve_s_max=round(max(solve_s, default=0.0), 3),
        ),
    )


__all__ = [
    "BACKTEST_BUDGET_S",
    "LIMITATIONS",
    "MAX_REPLAN_BUDGET_S",
    "BacktestSite",
    "BacktestStoppedError",
    "BacktestWindowError",
    "BatteryLimits",
    "DayData",
    "Executed",
    "HistoryReader",
    "HourInputs",
    "ReplayState",
    "apply_overlay",
    "default_solve",
    "execute_slot",
    "load_day",
    "price_known_until",
    "replay_day",
    "run_backtest",
]
