# SPDX-License-Identifier: Apache-2.0
"""What the planner plans with (Phase 4 WP8): one immutable snapshot, built by a pure function.

:func:`build_inputs` turns the services' outputs into :class:`PlannerInputs` on the 15-minute
planning grid from the quarter hour containing ``now`` to ``now + 48 h``. It does no I/O; the
runtime (WP9) collects the arguments:

* ``prices``: :meth:`suncore.tariff.TariffService.prices` (end prices, export already 0 where
  the region's negative-price rule applies). A ``spot_only`` series (no price formula
  configured) is **refused**: the planner then plans without price optimisation
  (:attr:`PriceMode.FLAT`) and says why (``planner.fallback.spot_without_formula``) - a bare
  spot price is not what the user pays and would plan grid charging on the wrong numbers.
* ``pv``: the blended PV :class:`~suncore.core.series.QuantileSeries`; ``load``: the base-load
  forecast (house minus controlled devices).
* ``morning_buffer`` / ``evening_target``: the risk model's results (WP7; anything with
  ``soc_pct`` and ``reason``, e.g. ``SocTarget`` or ``RiskTargetView``) and ``evening_at``
  (``RiskView.evening_at``). Caution enters **only** through these targets (plan §0: no P10
  constraints; the device budget before each target, ``model.Economics.device_budget``, is
  computed on P50, review D4). An evening target at max SoC is placed at the start of the
  evening's deficit, so the plan never holds a full battery while the house imports (review
  m2).
* ``goals.battery_target``: a timed soft target at every occurrence of its local time in the
  horizon (maintainer 2026-10-22: its time belongs to the planner).

Prices missing at the end of the horizon (tomorrow's prices before ~13:00) are filled with the
median of the known prices of the same kind and counted (``prices_filled_slots``); grid
charging is never planned in a slot without a known price.

Times are UTC, powers W (mean over the slot), energies Wh, prices currency/kWh; battery
+ = charging (``core/units.py``).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum, unique
from typing import Final, Literal, Protocol

from suncore.core.battery import (
    BatteryModel,
    BatterySettings,
    BatteryStrategy,
    GridChargePolicy,
    effective_reserve_pct,
)
from suncore.core.config import GoalsConfig, effective_grid_charge_policy
from suncore.core.control import Plan
from suncore.core.decisions import Reason
from suncore.core.devices import (
    ContinuousControl,
    DeviceConfig,
    ModeControl,
    RelaysControl,
    StepsControl,
    TimeWindow,
)
from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    PriceSeries,
    QuantileSeries,
    planning_grid,
    resample_prices,
    resample_quantiles,
)
from suncore.core.site import BatteryConfig
from suncore.planner import reasons as rk

PLANNER_HORIZON: Final = timedelta(hours=48)
DEFAULT_BUDGET_S: Final = 8.0
"""ADR-0003: solver time budget."""
DEFAULT_MIP_GAP: Final = 0.01
DEFAULT_BASE_LOAD_W: Final = 500.0
"""Base load assumed without a load forecast (review m4): a little above a typical
household's night load, so the battery is not planned as if the house used nothing."""
MIN_BASE_LOAD_W: Final = 100.0
FLAT_IMPORT_PRICE: Final = 1.0
"""Nominal import price of :attr:`PriceMode.FLAT` (no price optimisation): every imported kWh
counts the same, so the plan maximises self-sufficiency; the objective is then not money."""
DEVICE_VALUE_MARGIN: Final = 0.01
"""Automatic device value above the export price (currency/kWh): consumption beats export."""
DAYLIGHT_SHARE: Final = 0.02
FALLBACK_SUNSET: Final = time(19, 0)

ScalarArg = str | int | float | bool


@unique
class PriceMode(StrEnum):
    """Whether the plan optimises cost on real prices or only self-sufficiency."""

    MARKET = "market"
    FLAT = "flat"


GoalKind = Literal["morning_buffer", "evening_target", "user_target", "prefer_full"]


class TargetLike(Protocol):
    """A computed SoC with its reason (``forecast.risk.SocTarget``, ``RiskTargetView``)."""

    @property
    def soc_pct(self) -> float: ...

    @property
    def reason(self) -> Reason: ...


@dataclass(frozen=True, slots=True)
class SocGoal:
    """A soft lower bound on the SoC at the end of the slot that contains ``at``."""

    kind: GoalKind
    soc_pct: float
    at: datetime
    reason: Reason


@dataclass(frozen=True, slots=True)
class PlannerDevice:
    """A controllable consumer as flexible energy (Phase 4: no thermal model, no binaries).

    ``min_w`` is informational: the LP plans ``[0, max_w]`` (a semi-continuous minimum
    needs a binary; Phase 6). ``allowed[t]`` = the device's time windows admit slot ``t``.
    """

    device_id: str
    max_w: float
    allowed: tuple[bool, ...]
    value_per_kwh: float | None = None
    """Configured utility; ``None`` = automatic (just above the export price)."""
    min_w: float = 0.0
    priority: int = 0


@dataclass(frozen=True, slots=True)
class PlannerBattery:
    """The battery as the plan sees it (effective limits, live SoC)."""

    model: BatteryModel
    soc_pct: float
    reserve_pct: float
    """``core.battery.effective_reserve_pct`` (ADR-0013 decision 3), below ``max_soc_pct``."""
    max_soc_pct: float
    max_charge_w: float
    max_discharge_w: float
    strategy: BatteryStrategy = BatteryStrategy.SELF_CONSUMPTION

    @property
    def capacity_wh(self) -> float:
        return self.model.capacity_wh

    @property
    def soc_wh(self) -> float:
        return self.model.soc_to_wh(self.soc_pct)

    @property
    def reserve_wh(self) -> float:
        return self.model.soc_to_wh(self.reserve_pct)

    @property
    def max_wh(self) -> float:
        return self.model.soc_to_wh(self.max_soc_pct)


@dataclass(frozen=True, slots=True)
class PlannerWeights:
    """Expert weights of the objective (BRIEF §4.12); ``None`` = derived from the prices.

    Defaults (module ``model`` documents the derivation): self-sufficiency 0.01/kWh (plan
    §0), curtailment 0.0001/kWh (a tie-breaker: export at price 0 beats curtailment),
    morning buffer ``2 * max|price| + 0.5``, evening and user targets ``10 * max|price| + 1``,
    reserve ``100 * max|price| + 10`` (all per kWh of shortfall), terminal value
    ``median(import) * discharge efficiency - degradation / 2``.
    """

    autarky_per_kwh: float = 0.01
    curtail_per_kwh: float = 1e-4
    buffer_penalty_per_kwh: float | None = None
    target_penalty_per_kwh: float | None = None
    reserve_penalty_per_kwh: float | None = None
    terminal_value_per_kwh: float | None = None


@dataclass(frozen=True, slots=True)
class PlannerInputs:
    """Everything one planner run needs, on one grid (all tuples have ``len(starts)`` items)."""

    now: datetime
    starts: tuple[datetime, ...]
    step: timedelta
    tz: tzinfo
    import_price: tuple[float, ...]
    """Price used by the objective (filled; :data:`FLAT_IMPORT_PRICE` in flat mode)."""
    export_price: tuple[float, ...]
    import_known: tuple[float | None, ...]
    """The source's import price (``None`` = unknown); grid charging needs a known one."""
    price_mode: PriceMode
    pv_p10: tuple[float, ...]
    pv_p50: tuple[float, ...]
    pv_p90: tuple[float, ...]
    load_p10: tuple[float, ...]
    load_p50: tuple[float, ...]
    load_p90: tuple[float, ...]
    battery: PlannerBattery | None = None
    grid_charge_w: tuple[float, ...] = ()
    """Most the battery may charge from the grid per slot (0 = policy or spread rule says no)."""
    grid_charge_policy: GridChargePolicy = field(default_factory=GridChargePolicy)
    goals: tuple[SocGoal, ...] = ()
    morning_buffer_pct: float | None = None
    """"Battery first until X %" for the coming daytime (``priority_until_soc_pct``)."""
    export_discouraged: tuple[bool, ...] = ()
    """Slots where the region's negative-price rule zeroed the export price (proxy: export
    price <= 0 while the known import price is negative): exporting there costs a small
    penalty above curtailment (review D3; curtailment is informational until Phase 6)."""
    import_limit_w: float | None = None
    export_limit_w: float | None = None
    devices: tuple[PlannerDevice, ...] = ()
    previous: Plan | None = None
    weights: PlannerWeights = field(default_factory=PlannerWeights)
    reasons: tuple[Reason, ...] = ()
    """Plan-level reasons about the inputs (no prices, missing forecasts)."""
    assumptions: Mapping[str, ScalarArg] = field(default_factory=dict)

    def __post_init__(self) -> None:
        n = len(self.starts)
        if n == 0:
            msg = "planner inputs: empty grid"
            raise ValueError(msg)
        series = (
            self.import_price,
            self.export_price,
            self.import_known,
            self.pv_p10,
            self.pv_p50,
            self.pv_p90,
            self.load_p10,
            self.load_p50,
            self.load_p90,
        )
        if any(len(s) != n for s in series):
            msg = "planner inputs: every series needs one value per slot"
            raise ValueError(msg)
        if self.export_discouraged and len(self.export_discouraged) != n:
            msg = "planner inputs: export_discouraged needs one value per slot"
            raise ValueError(msg)
        if self.grid_charge_w and len(self.grid_charge_w) != n:
            msg = "planner inputs: grid_charge_w needs one value per slot"
            raise ValueError(msg)
        if any(len(d.allowed) != n for d in self.devices):
            msg = "planner inputs: device windows need one value per slot"
            raise ValueError(msg)
        floats = (*series[:2], *series[3:])
        if any(not math.isfinite(v) for s in floats for v in s):
            msg = "planner inputs: values must be finite"
            raise ValueError(msg)

    @property
    def n(self) -> int:
        return len(self.starts)

    @property
    def dt_h(self) -> float:
        return self.step / timedelta(hours=1)

    @property
    def end(self) -> datetime:
        return self.starts[-1] + self.step

    def discouraged(self, t: int) -> bool:
        return bool(self.export_discouraged) and self.export_discouraged[t]

    def price_estimated(self, t: int) -> bool:
        """The import price of slot ``t`` is a stand-in (median fill or flat mode)."""
        return self.import_known[t] is None

    def grid_charge_at(self, t: int) -> float:
        return self.grid_charge_w[t] if self.grid_charge_w else 0.0

    def slot_of(self, at: datetime) -> int | None:
        """Index of the slot whose *end* is the first at or after ``at`` (``None`` = outside)."""
        if at <= self.starts[0] or at > self.end:
            return None
        index = math.ceil((at - self.starts[0]) / self.step) - 1
        return max(0, min(self.n - 1, index))


# --- builder ------------------------------------------------------------------------------


def _inside(window: TimeWindow, local: datetime) -> bool:
    """Same rule as the controller's window check (``controller.allocation``)."""
    day = local.weekday()
    t = local.time().replace(tzinfo=None)
    if window.start < window.end:
        return day in window.days and window.start <= t < window.end
    previous = (day - 1) % 7
    return (day in window.days and t >= window.start) or (
        previous in window.days and t < window.end
    )


def window_allows(windows: Sequence[TimeWindow], local: datetime) -> bool:
    allows = [w for w in windows if w.mode == "allow"]
    if any(_inside(w, local) for w in windows if w.mode == "block"):
        return False
    return not allows or any(_inside(w, local) for w in allows)


def device_max_power_w(device: DeviceConfig) -> float | None:
    """The power a device draws when fully on (mirrors the controller's nominal power)."""
    nominal = device.power_model.nominal_w
    if nominal is not None:
        return nominal
    control = device.control
    if isinstance(control, StepsControl | RelaysControl):
        return control.steps[-1].power_w
    if isinstance(control, ModeControl):
        return max(m.power_w for m in control.modes)
    if isinstance(control, ContinuousControl):
        if control.unit == "W":
            return control.max
        if control.unit == "A":
            return control.max * control.voltage_v * control.phases
    return None


def _device_min_w(device: DeviceConfig) -> float:
    control = device.control
    if isinstance(control, ContinuousControl) and control.unit == "W":
        return control.min
    return 0.0


def _quantiles(
    series: QuantileSeries | None, grid: Grid, fill: float | None
) -> tuple[list[float], list[float], list[float], int]:
    """P10/P50/P90 on ``grid`` (W, never negative); a missing P50 becomes ``fill`` (``None`` =
    the mean of the known values), a missing outer quantile the P50."""
    if series is None:
        zero = [0.0 if fill is None else fill] * grid.count
        return zero, list(zero), list(zero), grid.count
    q = resample_quantiles(series, grid)
    known = [v for v in q.p50.values if v is not None]
    default = fill if fill is not None else (statistics.fmean(known) if known else 0.0)
    p10: list[float] = []
    p50: list[float] = []
    p90: list[float] = []
    for lo, mid, hi in zip(q.p10.values, q.p50.values, q.p90.values, strict=True):
        m = default if mid is None else max(0.0, mid)
        p50.append(m)
        p10.append(m if lo is None else min(m, max(0.0, lo)))
        p90.append(m if hi is None else max(m, hi))
    return p10, p50, p90, grid.count - len(known)


def _median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0


@dataclass(frozen=True, slots=True)
class _Prices:
    mode: PriceMode
    import_price: list[float]
    export_price: list[float]
    known: list[float | None]
    filled: int
    reasons: list[Reason]


def _prices(prices: PriceSeries | None, grid: Grid, tz: tzinfo) -> _Prices:
    if prices is None:
        return _Prices(
            PriceMode.FLAT,
            [FLAT_IMPORT_PRICE] * grid.count,
            [0.0] * grid.count,
            [None] * grid.count,
            0,
            [Reason(key=rk.FALLBACK_NO_PRICES)],
        )
    on_grid = resample_prices(prices, grid)
    raw_import = list(on_grid.import_price.values)
    raw_export: list[float | None] = (
        [None] * grid.count if on_grid.export_price is None else list(on_grid.export_price.values)
    )
    known_export = [v for v in raw_export if v is not None]
    export_fill = _median(known_export)
    export = [export_fill if v is None else v for v in raw_export]
    known_import = [v for v in raw_import if v is not None]
    if on_grid.price_includes == "spot_only":
        return _Prices(
            PriceMode.FLAT,
            [FLAT_IMPORT_PRICE] * grid.count,
            export,
            [None] * grid.count,
            0,
            [Reason(key=rk.FALLBACK_SPOT_WITHOUT_FORMULA, args={"source": prices.source_id})],
        )
    if not known_import:
        return _Prices(
            PriceMode.FLAT,
            [FLAT_IMPORT_PRICE] * grid.count,
            export,
            [None] * grid.count,
            0,
            [Reason(key=rk.FALLBACK_NO_PRICES)],
        )
    fill = _median(known_import)
    filled = sum(1 for v in raw_import if v is None)
    found: list[Reason] = []
    if filled:
        last = max(i for i, v in enumerate(raw_import) if v is not None)
        found.append(
            Reason(
                key=rk.INPUT_PRICES_PARTIAL,
                args={
                    "known_until": (grid.start + grid.step * (last + 1))
                    .astimezone(tz)
                    .strftime("%Y-%m-%d %H:%M"),
                    "filled_slots": filled,
                },
            )
        )
    return _Prices(
        PriceMode.MARKET,
        [fill if v is None else v for v in raw_import],
        export,
        raw_import,
        filled,
        found,
    )


def grid_charge_allowance(
    starts: Sequence[datetime],
    known: Sequence[float | None],
    *,
    load_w: Sequence[float],
    battery: PlannerBattery | None,
    policy: GridChargePolicy,
    tz: tzinfo,
    import_limit_w: float | None,
) -> list[float]:
    """Most grid-charging power per slot (W); 0 where it is not allowed.

    Allowed only where :meth:`GridChargePolicy.allows` admits the slot start with the slot's
    **known** import price (an unknown price never satisfies a price level) and, on a
    tariff whose price varies, the spread rule pays against the best known later price
    (:meth:`BatteryModel.grid_charge_pays`). The power is bounded by the battery's charge
    limit and by the import headroom ``import_limit - base load`` (ADR-0013: the site import
    limit always holds). A slot without a known price (flat mode, unpublished prices) never
    allows grid charging, even under a schedule-only policy.
    """
    n = len(starts)
    if battery is None or not policy.enabled:
        return [0.0] * n
    distinct = {round(v, 6) for v in known if v is not None}
    dynamic = len(distinct) > 1
    best_later: list[float | None] = [None] * n
    running: float | None = None
    for t in range(n - 1, -1, -1):
        best_later[t] = running
        value = known[t]
        if value is not None:
            running = value if running is None else max(running, value)
    out: list[float] = []
    for t in range(n):
        price = known[t]
        if price is None or not policy.allows(starts[t], price, tz):
            # No grid charging without a known price, whatever the policy (review D2).
            out.append(0.0)
            continue
        if dynamic:
            later = best_later[t]
            if price is None or later is None or not battery.model.grid_charge_pays(price, later):
                out.append(0.0)
                continue
        cap = battery.max_charge_w
        if import_limit_w is not None:
            cap = min(cap, max(0.0, import_limit_w - load_w[t]))
        out.append(max(0.0, cap))
    return out


def _local_occurrences(at: time, start: datetime, end: datetime, tz: tzinfo) -> list[datetime]:
    """Every instant in ``(start, end]`` whose local wall time is ``at``."""
    found: list[datetime] = []
    day = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date()
    while day <= last:
        moment = datetime.combine(day, at, tzinfo=tz)
        if start < moment <= end:
            found.append(moment)
        day += timedelta(days=1)
    return found


def sunsets(
    starts: Sequence[datetime], step: timedelta, pv: Sequence[float], tz: tzinfo
) -> list[datetime]:
    """Per local day of the grid: the end of its last slot above 2 % of the day's PV peak
    (:data:`FALLBACK_SUNSET` local time on a day without PV in the forecast)."""
    days: dict[date, list[tuple[int, float]]] = {}
    for i, (begin, value) in enumerate(zip(starts, pv, strict=True)):
        days.setdefault(begin.astimezone(tz).date(), []).append((i, value))
    out: list[datetime] = []
    for day, slots in sorted(days.items()):
        peak = max(v for _, v in slots)
        lit = [i for i, v in slots if peak > 0 and v > DAYLIGHT_SHARE * peak]
        out.append(
            starts[lit[-1]] + step if lit else datetime.combine(day, FALLBACK_SUNSET, tzinfo=tz)
        )
    return out


def buffer_goal(
    battery: PlannerBattery,
    target_pct: float,
    *,
    starts: Sequence[datetime],
    step: timedelta,
    pv: Sequence[float],
    load: Sequence[float],
    until: datetime,
) -> tuple[datetime, float] | None:
    """Where the morning buffer becomes a soft SoC target: ``(time, SoC %)`` or ``None``.

    Self-consumption simulation on P50 from the live SoC, battery first (all PV surplus to
    the battery, the battery covers the house deficit down to the reserve, no grid
    charging), until ``until`` (the coming sunset). The goal is the end of the first slot at
    or above ``target_pct``; when PV cannot get there, the highest SoC the simulation reaches
    (at the first slot it reaches it). So the buffer only orders the surplus (battery before
    devices) and never makes the plan hold energy or buy it from the grid by itself.
    ``None`` = the live SoC is already there, or PV adds nothing.
    """
    model = battery.model
    target = model.soc_to_wh(target_pct)
    energy = battery.soc_wh
    if energy >= target - 1e-6:
        return None
    hours = step / timedelta(hours=1)
    best: tuple[datetime, float] | None = None
    for t, begin in enumerate(starts):
        end = begin + step
        if end > until:
            break
        net = pv[t] - load[t]
        if net > 0:
            gain = min(net, battery.max_charge_w) * hours * model.charge_efficiency
            energy = min(battery.max_wh, energy + gain)
        else:
            loss = min(-net, battery.max_discharge_w) * hours / model.discharge_efficiency
            energy = max(min(energy, battery.reserve_wh), energy - loss)
        if energy >= target - 1e-6:
            return end, target_pct
        if net > 0 and energy > battery.soc_wh + 1e-6 and (best is None or energy > best[1] + 1e-6):
            best = (end, energy)
    if best is None:
        return None
    return best[0], round(model.wh_to_soc(best[1]), 3)


def _battery(
    settings: BatterySettings, soc_pct: float | None, site_battery: BatteryConfig | None
) -> PlannerBattery | None:
    model = settings.model
    if model is None or soc_pct is None or not math.isfinite(soc_pct):
        return None
    max_soc = model.max_soc_pct
    max_charge, max_discharge = model.max_charge_w, model.max_discharge_w
    if site_battery is not None:
        if site_battery.max_soc_pct is not None:
            max_soc = min(max_soc, site_battery.max_soc_pct)
        if site_battery.max_charge_w is not None:
            max_charge = min(max_charge, site_battery.max_charge_w)
        if site_battery.max_discharge_w is not None:
            max_discharge = min(max_discharge, site_battery.max_discharge_w)
    reserve = min(effective_reserve_pct(site_battery, model), max_soc)
    return PlannerBattery(
        model=model,
        soc_pct=min(100.0, max(0.0, soc_pct)),
        reserve_pct=reserve,
        max_soc_pct=max_soc,
        max_charge_w=max_charge,
        max_discharge_w=max_discharge,
        strategy=settings.strategy,
    )


def _soc_goals(
    battery: PlannerBattery | None,
    *,
    starts: Sequence[datetime],
    step: timedelta,
    tz: tzinfo,
    pv: Sequence[float],
    load: Sequence[float],
    goals: GoalsConfig,
    morning_buffer: TargetLike | None,
    evening_target: TargetLike | None,
    evening_at: datetime | None,
) -> tuple[list[SocGoal], float | None]:
    if battery is None:
        return [], None
    begin, end = starts[0], starts[-1] + step
    out: list[SocGoal] = []
    cap = battery.max_soc_pct
    coming = [at for at in sunsets(starts, step, pv, tz) if at > begin + step]
    evening = evening_at if evening_at is not None else (coming[0] if coming else end)
    if evening_target is not None and begin + step < evening <= end:
        soc = min(cap, evening_target.soc_pct)
        at = evening
        if soc >= cap - 1e-6:
            at = _deficit_start(starts, step, pv, load, evening)
        out.append(SocGoal("evening_target", soc, at, evening_target.reason))
    buffer_pct: float | None = None
    buffer_reason: Reason | None = None
    if morning_buffer is not None:
        buffer_pct, buffer_reason = min(cap, morning_buffer.soc_pct), morning_buffer.reason
    elif battery.strategy is BatteryStrategy.PREFER_FULL:
        buffer_pct = cap
        buffer_reason = Reason(key=rk.PREFER_FULL, args={"soc_pct": round(cap, 1)})
    if buffer_pct is not None and buffer_reason is not None:
        # The buffer is for the coming daytime: due at the latest by its sunset.
        until = evening if begin + step < evening <= end else (coming[0] if coming else end)
        until = min(until, end)
        reach = buffer_goal(
            battery, buffer_pct, starts=starts, step=step, pv=pv, load=load, until=until
        )
        if reach is not None:
            kind: GoalKind = "morning_buffer" if morning_buffer is not None else "prefer_full"
            if reach[1] < buffer_pct - 0.05:
                # PV cannot get there today: the reason names what the plan aims at (m3).
                args = dict(buffer_reason.args)
                args["soc_pct"] = round(reach[1], 1)
                buffer_reason = Reason(key=buffer_reason.key, args=args)
            out.append(SocGoal(kind, reach[1], reach[0], buffer_reason))
    target = goals.battery_target
    if target is not None:
        hh, mm = (int(x) for x in target.time.split(":"))
        for moment in _local_occurrences(time(hh, mm), begin + step, end, tz):
            soc = min(cap, max(battery.reserve_pct, target.soc_pct))
            out.append(
                SocGoal(
                    "user_target",
                    soc,
                    moment,
                    Reason(
                        key=rk.USER_TARGET,
                        args={"soc_pct": round(soc, 1), "time": target.time},
                    ),
                )
            )
    out.sort(key=lambda g: g.at)
    return out, buffer_pct


def _deficit_start(
    starts: Sequence[datetime],
    step: timedelta,
    pv: Sequence[float],
    load: Sequence[float],
    evening: datetime,
) -> datetime:
    """Start of the deficit run (load above PV) that ends at ``evening``; ``evening`` if the
    slot before it has surplus. A full battery is due there, not later (review m2)."""
    last = None
    for t, begin in enumerate(starts):
        if begin + step >= evening:
            last = t
            break
    if last is None:
        return evening
    first = last
    while first > 0 and load[first - 1] >= pv[first - 1]:
        first -= 1
    if first == last and load[last] < pv[last]:
        return evening
    return starts[first] if first > 0 else evening


def build_inputs(
    now: datetime,
    *,
    tz: tzinfo,
    load: QuantileSeries | None,
    pv: QuantileSeries | None,
    prices: PriceSeries | None,
    battery: BatterySettings,
    soc_pct: float | None,
    goals: GoalsConfig | None = None,
    site_battery: BatteryConfig | None = None,
    morning_buffer: TargetLike | None = None,
    evening_target: TargetLike | None = None,
    evening_at: datetime | None = None,
    devices: Sequence[DeviceConfig] = (),
    device_max_w: Mapping[str, float] | None = None,
    import_limit_w: float | None = None,
    export_limit_w: float | None = None,
    previous: Plan | None = None,
    weights: PlannerWeights | None = None,
    horizon: timedelta = PLANNER_HORIZON,
    load_fallback_w: float | None = None,
) -> PlannerInputs:
    """The planner's snapshot (module doc); pure."""
    goals = goals or GoalsConfig()
    grid = planning_grid(now, horizon, PLANNING_STEP)
    starts = tuple(grid.starts())
    found: list[Reason] = []
    pv10, pv50, pv90, pv_missing = _quantiles(pv, grid, 0.0)
    if pv is None:
        found.append(Reason(key=rk.INPUT_NO_PV_FORECAST))
    l10, l50, l90, load_missing = _quantiles(load, grid, None)
    no_load = load is None or load_missing == grid.count
    if no_load:
        # Never plan on 0 W base load (review m4): a flat, conservative profile - the recent
        # mean the runtime passes, else DEFAULT_BASE_LOAD_W - and no device planning.
        flat = load_fallback_w if load_fallback_w is not None else DEFAULT_BASE_LOAD_W
        flat = max(flat, MIN_BASE_LOAD_W)
        l10, l50, l90 = [flat * 0.5] * grid.count, [flat] * grid.count, [flat * 2] * grid.count
        found.append(Reason(key=rk.INPUT_NO_LOAD_FORECAST, args={"load_w": round(flat)}))
    price_data = _prices(prices, grid, tz)
    found = price_data.reasons + found
    planner_battery = _battery(battery, soc_pct, site_battery)
    if battery.model is not None and planner_battery is None:
        found.append(Reason(key=rk.INPUT_NO_BATTERY_SOC))
    policy = effective_grid_charge_policy(goals, battery)
    allowance = grid_charge_allowance(
        starts,
        price_data.known if price_data.mode is PriceMode.MARKET else [None] * grid.count,
        load_w=l50,
        battery=planner_battery,
        policy=policy,
        tz=tz,
        import_limit_w=import_limit_w,
    )
    soc_goals, buffer_pct = _soc_goals(
        planner_battery,
        starts=starts,
        step=grid.step,
        tz=tz,
        pv=pv50,
        load=l50,
        goals=goals,
        morning_buffer=morning_buffer,
        evening_target=evening_target,
        evening_at=evening_at,
    )
    planned: list[PlannerDevice] = []
    for device in [] if no_load else devices:
        override = None if device_max_w is None else device_max_w.get(device.id)
        max_w = override if override is not None else device_max_power_w(device)
        if max_w is None or max_w <= 0:
            found.append(Reason(key=rk.INPUT_DEVICE_POWER_UNKNOWN, args={"device": device.id}))
            continue
        allowed = tuple(window_allows(device.windows, s.astimezone(tz)) for s in starts)
        planned.append(
            PlannerDevice(
                device_id=device.id,
                max_w=max_w,
                allowed=allowed,
                value_per_kwh=device.planner_value_per_kwh,
                min_w=_device_min_w(device),
                priority=device.priority,
            )
        )
    assumptions: dict[str, ScalarArg] = {
        "price_mode": price_data.mode.value,
        "horizon_slots": grid.count,
        "prices_filled_slots": price_data.filled,
        "pv_missing_slots": pv_missing,
        "load_missing_slots": load_missing,
        "devices": len(planned),
        "risk": battery.risk.value,
        "strategy": battery.strategy.value,
    }
    if prices is not None:
        assumptions["price_source"] = prices.source_id
    return PlannerInputs(
        now=now,
        starts=starts,
        step=grid.step,
        tz=tz,
        import_price=tuple(price_data.import_price),
        export_price=tuple(price_data.export_price),
        import_known=tuple(price_data.known),
        price_mode=price_data.mode,
        pv_p10=tuple(pv10),
        pv_p50=tuple(pv50),
        pv_p90=tuple(pv90),
        load_p10=tuple(l10),
        load_p50=tuple(l50),
        load_p90=tuple(l90),
        battery=planner_battery,
        grid_charge_w=tuple(allowance),
        grid_charge_policy=policy,
        goals=tuple(soc_goals),
        morning_buffer_pct=buffer_pct,
        export_discouraged=tuple(
            e <= 0 and (e < 0 or (k is not None and k < 0))
            for e, k in zip(price_data.export_price, price_data.known, strict=True)
        ),
        import_limit_w=import_limit_w,
        export_limit_w=export_limit_w,
        devices=tuple(planned),
        previous=previous,
        weights=weights or PlannerWeights(autarky_per_kwh=battery.autarky_weight_per_kwh),
        reasons=tuple(found),
        assumptions=assumptions,
    )


__all__ = [
    "DEFAULT_BASE_LOAD_W",
    "DEFAULT_BUDGET_S",
    "DEFAULT_MIP_GAP",
    "DEVICE_VALUE_MARGIN",
    "FLAT_IMPORT_PRICE",
    "PLANNER_HORIZON",
    "GoalKind",
    "PlannerBattery",
    "PlannerDevice",
    "PlannerInputs",
    "PlannerWeights",
    "PriceMode",
    "SocGoal",
    "TargetLike",
    "buffer_goal",
    "build_inputs",
    "device_max_power_w",
    "grid_charge_allowance",
    "window_allows",
]
