# SPDX-License-Identifier: Apache-2.0
"""Flexible loads and grid limits on the planning grid (Phase 6 WP8, ADR-0017, ADR-0018).

Pure data and builders, no I/O; the LP rows live in :mod:`suncore.planner.flexmodel`, the
rules of the fallback in :mod:`suncore.planner.flexheuristic`. Every feature is behind its
input being present: without flexible loads and without a :class:`GridRequest` the planner
is exactly the Phase 4 model.

**Flexible loads** draw from three sources per slot: the PV surplus over the base load
(``S``), the grid (``G``, bounded by ``grid_max_w`` - the bounded import exception of ADR-0017
decision 4) and the home battery (``B``; programs and the heat pump's own heating only, **never
the EV**: ``battery.allow_discharge_to_ev`` does not exist yet and defaults to off, BRIEF §4.4).

* :class:`PlannerEv` (kind ``wallbox`` with an ``ev`` spec): mode ``pv`` = surplus only;
  ``min_pv`` = the minimum power as a user-demanded floor (grid up to the floor), the rest from
  surplus; ``now`` = the maximum power as a floor; ``target`` = an energy need by a deadline,
  grid power in every available slot before it. The 6 A floor is semi-continuous: ``P = 0`` or
  ``min_w <= P <= max_w``, with a binary per slot in the first 24 h and its LP relaxation
  beyond (plan §0, risk R11). **Conservative:** the target counts PV surplus only up to the
  P10 surplus (``pv_p10 - load_p50``), so the plan buys the gap between P50 and P10 rather
  than missing the deadline; the objective and the flows stay on P50.
* :class:`PlannerJob` (an armed program job): one start slot among ``candidates`` (a binary
  each), the profile convolved from there, finished by the deadline. No candidate fits ->
  start as soon as possible with ``planner.job.deadline_infeasible``. A running job is a
  fixed remainder of its profile from slot 0. Starting in slot 0 means starting at ``now``:
  that start's profile is resampled onto the grid from ``now`` (from the actual start for a
  running job), so its energy per slot is exact although the first slot is short.
* :class:`PlannerThermal` (a thermal kind with a usable learned RC model, the live temperature,
  an outdoor-temperature forecast and a comfort band): the store's temperature per slot with
  the COP from the outdoor forecast; the comfort (lift) band as soft bounds. The heating the
  device would do by itself to hold the band's minimum (``base_w``, a thermostat simulation)
  may come from grid or battery; **pre-heating** beyond it uses surplus only, unless
  ``grid_preheat``. Without a usable model the device stays energy-only (Phase 4) and says so.

**Slot 0** has only the time left after ``now`` (:mod:`suncore.planner.slots`): powers stay
powers, but every energy (EV floors and needs, the thermal step, the programs' profile) uses
the usable hours of the slot.

**Grid** (:class:`PlannerGrid`, through the resolvers of ``core.grid`` only): the effective
import limit per slot (fuse and peak shaving, the peak resets at the local month boundary),
the export limit per slot (static; 0 W at a negative export price only with a verified
inverter adapter), a peak variable per month with the capacity price, and the §14a budget for
flagged devices while the signal is active (grid charging blocked meanwhile). Loads the user
demanded (floors, programs, the heat pump's own heating) may exceed the import limit only
through a heavily penalised slack, reported as ``planner.grid.import_limit_exceeded``; the
gate enforces the limit at execution regardless.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from typing import Final, Literal

from suncore.core.decisions import Reason
from suncore.core.devices import (
    ChargerControl,
    DeviceConfig,
    LoadpointControl,
)
from suncore.core.flex import (
    EV_MODE_DEFAULT,
    FINAL_JOB_STATES,
    EvMode,
    EvState,
    JobState,
    ProgramJob,
    ev_energy_need_wh,
)
from suncore.core.grid import (
    PARA14A_INACTIVE,
    Para14aBudget,
    Para14aStatus,
    PeakShaving,
    effective_export_limit_w,
    effective_import_limit_w,
    month_key,
)
from suncore.core.series import Grid, TimeSeries, resample_power
from suncore.core.thermal import GENERIC_AIR_WATER_COP, ThermalModel, model_usable
from suncore.planner import reasons as rk
from suncore.planner.slots import slot_hours

EV_BINARY_WINDOW: Final = timedelta(hours=24)
"""The 6 A floor is modelled with binaries in this window, its LP relaxation beyond (R11)."""
EV_MIN_CURRENT_A: Final = 6.0
"""IEC 61851 minimum charging current; used for an evcc loadpoint (no ``min_current_a``)."""
PARA14A_HOLD: Final = timedelta(minutes=15)
"""How long the planner assumes an active (or unknown) §14a signal holds: the runtime replans
on every change, so only the coming quarter hour is constrained by default."""

FlexKind = Literal["ev", "job", "thermal"]


@dataclass(frozen=True, slots=True)
class FlexLoad:
    """What every flexible load has on the grid (one value per slot)."""

    device_id: str
    kind: FlexKind
    max_w: tuple[float, ...]
    """Most power per slot (0 = off in that slot)."""
    grid_max_w: tuple[float, ...]
    """Most of it from the grid per slot (ADR-0017 decision 4)."""
    battery: bool
    """The home battery may feed it (never for the EV)."""
    para14a: bool
    """Counts against the §14a budget while the signal is active."""

    def forced_max_w(self, t: int) -> float:  # noqa: ARG002 - overridden per kind
        """Upper bound of the user-demanded power in slot ``t`` (may exceed the import limit)."""
        return 0.0


@dataclass(frozen=True, slots=True)
class PlannerEv(FlexLoad):
    """One charger with its vehicle (module doc)."""

    mode: EvMode
    min_w: float
    """The semi-continuous floor (6 A on the fewest phases)."""
    floor_w: tuple[float, ...]
    """User-demanded lower bound per slot (``now``/``min_pv``)."""
    need_by_wh: float | None
    """Grid-side energy the target needs by ``deadline_slot`` (``None`` = no target)."""
    deadline_slot: int | None
    need_total_wh: float | None
    """Most energy the vehicle takes over the horizon (``None`` = unknown)."""
    value_per_kwh: float | None
    binary_slots: int
    target_at: datetime | None = None

    def forced_max_w(self, t: int) -> float:
        return self.floor_w[t]

    def binary(self, t: int) -> bool:
        return t < self.binary_slots and self.min_w > 0 and self.max_w[t] > 0

    def target_slot(self, t: int) -> bool:
        """Slot ``t`` counts toward the target."""
        return (
            self.need_by_wh is not None
            and self.deadline_slot is not None
            and t <= self.deadline_slot
            and self.max_w[t] > 0
        )


@dataclass(frozen=True, slots=True)
class PlannerJob(FlexLoad):
    """One program job: a start among ``candidates``, ``profile_w`` from there."""

    job_id: str
    profile_w: tuple[float, ...]
    candidates: tuple[int, ...]
    running: bool
    deadline: datetime
    deadline_met: bool
    """``False`` = no start finishes by the deadline: start as soon as possible."""
    previous_start: int | None = None
    now_profile_w: tuple[float, ...] = ()
    """The run started at ``now`` (or, running, at its real start) on the grid from slot 0:
    mean power over the usable part of each slot (empty = ``profile_w``, slot 0 is whole)."""

    def forced_max_w(self, t: int) -> float:
        return self.max_w[t]

    def _profile(self, start: int) -> tuple[float, ...]:
        return self.now_profile_w if start == 0 and self.now_profile_w else self.profile_w

    def length(self, start: int) -> int:
        """Slots the run covers from ``start``."""
        return len(self._profile(start))

    def power_at(self, start: int, t: int) -> float:
        profile = self._profile(start)
        k = t - start
        return profile[k] if 0 <= k < len(profile) else 0.0


@dataclass(frozen=True, slots=True)
class PlannerThermal(FlexLoad):
    """A heat pump or other thermal store with its RC model (module doc)."""

    temp0_c: float
    min_c: float
    max_c: float
    alpha: tuple[float, ...]
    """``T_t = alpha[t] * T_{t-1} + heat_k[t] * P_kw + drift[t]`` (exact for a constant input;
    per slot because the first slot is shorter)."""
    heat_k: tuple[float, ...]
    """K per kW of electric power over slot ``t`` (COP of the slot included)."""
    drift: tuple[float, ...]
    """K added in slot ``t`` without heating (outdoor losses and internal gain)."""
    base_w: tuple[float, ...]
    """Electric power the device needs to hold ``min_c`` by itself (thermostat simulation)."""
    cop: tuple[float, ...]
    grid_preheat: bool
    capacity_wh_per_k: float

    def forced_max_w(self, t: int) -> float:
        return self.base_w[t]

    def next_temp(self, t: int, temp_c: float, power_w: float) -> float:
        return self.alpha[t] * temp_c + self.heat_k[t] * power_w / 1000.0 + self.drift[t]


@dataclass(frozen=True, slots=True)
class PlannerGrid:
    """Peak variable and §14a budget per slot (limits are on :class:`PlannerInputs`)."""

    peak_price_per_kw: float = 0.0
    """Capacity price per kW and month (0 = no peak variable)."""
    month_of_slot: tuple[int, ...] = ()
    month_floor_w: tuple[float, ...] = ()
    """Per month group: the peak already reached (later months start at 0)."""
    para14a_w: tuple[float | None, ...] = ()
    """§14a budget per slot for the flagged devices (``None`` = not dimmed)."""
    para14a_status: Para14aStatus = Para14aStatus.INACTIVE

    @property
    def peak(self) -> bool:
        return self.peak_price_per_kw > 0 and bool(self.month_of_slot)

    def para14a_at(self, t: int) -> float | None:
        return self.para14a_w[t] if self.para14a_w else None


@dataclass(frozen=True, slots=True)
class FlexFlows:
    """A flexible load's planned flows (W per slot); LP and heuristic alike."""

    surplus_w: tuple[float, ...]
    grid_w: tuple[float, ...]
    battery_w: tuple[float, ...]
    temp_c: tuple[float, ...] = ()
    """Thermal loads: the store's temperature at the end of each slot."""
    start: int | None = None
    """Program jobs: the start slot."""

    def power_w(self, t: int) -> float:
        return self.surplus_w[t] + self.grid_w[t] + self.battery_w[t]


# --- requests (what the runtime reads) ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvLive:
    """Live vehicle data of one charger (``None`` = unknown)."""

    soc_pct: float | None = None
    connected: bool | None = None
    """``None`` = no ``connected`` role: assumed connected."""
    arrival: datetime | None = None
    """Expected connection while not connected (the user's or a prediction)."""
    departure: datetime | None = None
    charged_wh: float = 0.0
    """Energy charged since an energy target was set (energy targets only)."""


@dataclass(frozen=True, slots=True)
class FlexRequest:
    """Runtime state of the flexible loads (``core.flex``) and live values."""

    ev_states: Mapping[str, EvState] = field(default_factory=dict)
    ev_live: Mapping[str, EvLive] = field(default_factory=dict)
    jobs: Sequence[ProgramJob] = ()
    temperatures_c: Mapping[str, float] = field(default_factory=dict)
    """Live temperature of each thermal device's store (°C)."""
    outdoor_c: TimeSeries | None = None
    """Outdoor-temperature forecast (°C)."""


@dataclass(frozen=True, slots=True)
class GridRequest:
    """Grid limits as configured and measured (resolved through ``core.grid`` only)."""

    fuse_limit_w: float | None = None
    peak: PeakShaving = field(default_factory=PeakShaving)
    month_peak_w: float | None = None
    static_export_limit_w: float | None = None
    zero_export: bool = False
    export_dynamic: bool = False
    export_adapter_verified: bool = False
    """``inverter.limit_verified``: a dynamic 0 W limit can be enforced."""
    para14a: Para14aBudget = PARA14A_INACTIVE
    para14a_hold: timedelta = PARA14A_HOLD


@dataclass(frozen=True, slots=True)
class FlexBuild:
    """Result of :func:`build_flex`."""

    evs: tuple[PlannerEv, ...] = ()
    jobs: tuple[PlannerJob, ...] = ()
    thermals: tuple[PlannerThermal, ...] = ()
    claimed: frozenset[str] = frozenset()
    """Device ids planned here or deliberately not at all (never energy-only)."""
    reasons: tuple[Reason, ...] = ()


@dataclass(frozen=True, slots=True)
class GridBuild:
    import_limits_w: tuple[float | None, ...]
    export_limits_w: tuple[float | None, ...]
    grid: PlannerGrid
    reasons: tuple[Reason, ...] = ()


# --- builders --------------------------------------------------------------------------------


def _slot_index(starts: Sequence[datetime], step: timedelta, at: datetime) -> int:
    """Index of the slot containing ``at`` (clamped to the grid)."""
    if at <= starts[0]:
        return 0
    return min(len(starts), math.floor((at - starts[0]) / step))


def _deadline_slot(starts: Sequence[datetime], step: timedelta, at: datetime) -> int | None:
    """The last slot that ends at or before ``at`` (``None`` = before the first slot ends)."""
    if at < starts[0] + step:
        return None
    return min(len(starts) - 1, math.floor((at - starts[0]) / step) - 1)


def _charger_powers(device: DeviceConfig) -> tuple[float, float] | None:
    control = device.control
    if isinstance(control, ChargerControl):
        low_phases: Literal[1, 3] = 1 if control.phase_switching else control.phases
        return control.min_power_w(low_phases), control.max_power_w()
    if isinstance(control, LoadpointControl):
        per_phase = control.voltage_v * control.phases
        return EV_MIN_CURRENT_A * per_phase, control.max_current_a * per_phase
    return None


def _ev(  # noqa: PLR0912, PLR0915 - one rule per mode, as in the module doc
    device: DeviceConfig,
    state: EvState | None,
    live: EvLive,
    *,
    starts: Sequence[datetime],
    step: timedelta,
    hours: Sequence[float],
    found: list[Reason],
) -> PlannerEv | None:
    spec = device.ev
    powers = _charger_powers(device)
    if spec is None or powers is None:
        return None
    min_w, max_w = powers
    mode = state.mode if state is not None else spec.default_mode or EV_MODE_DEFAULT
    if mode is EvMode.OFF:
        return None
    n = len(starts)
    first = 0
    if live.connected is False:
        if live.arrival is None:
            found.append(Reason(key=rk.EV_NOT_CONNECTED, args={"device": device.id}))
            return None
        # The first whole slot after the arrival.
        first = max(0, min(n, math.ceil((live.arrival - starts[0]) / step)))
    last = n
    if live.departure is not None:
        last = max(0, min(n, math.floor((live.departure - starts[0]) / step)))
    available = [first <= t < last for t in range(n)]
    if not any(available):
        found.append(Reason(key=rk.EV_NOT_CONNECTED, args={"device": device.id}))
        return None
    eff = spec.efficiency
    soc = live.soc_pct
    capacity = spec.capacity_wh
    need_total: float | None = None
    if capacity is not None and soc is not None:
        need_total = ev_energy_need_wh(100.0, soc, capacity, eff)
    target = state.target if state is not None else None
    need_by: float | None = None
    deadline: int | None = None
    target_at: datetime | None = None
    if mode is EvMode.TARGET and target is not None:
        if target.soc_pct is not None and capacity is not None and soc is not None:
            need_by = ev_energy_need_wh(target.soc_pct, soc, capacity, eff)
        elif target.energy_wh is not None:
            # "Have charged energy_wh": energy into the car, so the grid side is /efficiency.
            need_by = max(0.0, target.energy_wh - live.charged_wh) / eff
        if need_by is None:
            found.append(Reason(key=rk.EV_NEED_UNKNOWN, args={"device": device.id}))
        else:
            deadline = _deadline_slot(starts, step, target.by)
            target_at = target.by
            if need_total is None:
                need_total = need_by
            if deadline is None or need_by <= 0:
                need_by, deadline = None, None
    floor = [0.0] * n
    grid = [0.0] * n
    if mode in (EvMode.NOW, EvMode.MIN_PV):
        level = max_w if mode is EvMode.NOW else min_w
        if need_total is None:
            found.append(Reason(key=rk.EV_NEED_UNKNOWN, args={"device": device.id}))
            # the plan is redone every 15 min: only the coming (whole) slot
            left = level * step / timedelta(hours=1)
        else:
            left = need_total
        for t in range(n):
            if not available[t]:
                continue
            dt = hours[t]
            power = min(level, left / dt)
            if power < min_w - 1e-9:
                break  # below the 6 A floor: the rest comes from surplus, if at all
            floor[t] = power
            grid[t] = max_w if mode is EvMode.NOW else power
            left -= power * dt
    elif mode is EvMode.TARGET and need_by is not None and deadline is not None:
        for t in range(deadline + 1):
            if available[t]:
                grid[t] = max_w
    binary_slots = min(n, math.ceil(EV_BINARY_WINDOW / step))
    return PlannerEv(
        device_id=device.id,
        kind="ev",
        max_w=tuple(max_w if a else 0.0 for a in available),
        grid_max_w=tuple(grid),
        battery=False,
        para14a=device.para14a,
        mode=mode,
        min_w=min_w,
        floor_w=tuple(floor),
        need_by_wh=need_by,
        deadline_slot=deadline,
        need_total_wh=need_total,
        value_per_kwh=device.planner_value_per_kwh,
        binary_slots=binary_slots,
        target_at=target_at,
    )


def job_profile_w(device: DeviceConfig, step: timedelta, nominal_w: float | None) -> list[float]:
    """Power per planning slot of one run (W): the 15-minute profile, else nominal x time."""
    spec = device.program
    if spec is None:
        return []
    hours = step / timedelta(hours=1)
    if spec.profile_wh:
        return [wh / hours for wh in spec.profile_wh]
    if nominal_w is None:
        return []
    minutes = step / timedelta(minutes=1)
    full, part = divmod(spec.runtime_min, minutes)
    profile = [nominal_w] * int(full)
    if part:
        profile.append(nominal_w * part / minutes)
    return profile


def profile_from(
    profile: Sequence[float],
    *,
    origin: datetime,
    now: datetime,
    starts: Sequence[datetime],
    step: timedelta,
) -> tuple[float, ...]:
    """A run of ``profile`` (W per ``step`` from ``origin``) on the grid: per slot the mean
    power over its usable part (slot 0 from ``now``), exact in energy, never above the
    profile's maximum. One value per slot the run overlaps after ``now``, zero-power phases
    included; empty once the run is over."""
    end = origin + step * len(profile)
    out: list[float] = []
    for t, begin in enumerate(starts):
        a = max(begin, now) if t == 0 else begin
        b = begin + step
        if a >= end:
            break
        energy = 0.0  # W x s
        for k, p in enumerate(profile):
            lo, hi = max(a, origin + step * k), min(b, origin + step * (k + 1))
            if hi > lo:
                energy += p * (hi - lo).total_seconds()
        span = (b - a).total_seconds()
        out.append(energy / span if span > 0 else 0.0)
    # Zero-power phases (a soak) count: the run lasts until ``end`` whatever it draws, so a
    # running job stays in the plan (PREFER_ON) until its runtime is over.
    return tuple(out)


def _job(
    device: DeviceConfig,
    job: ProgramJob,
    profile: list[float],
    *,
    now: datetime,
    starts: Sequence[datetime],
    step: timedelta,
    tz: tzinfo,
    found: list[Reason],
) -> PlannerJob | None:
    spec = device.program
    if spec is None or not profile:
        return None
    n = len(starts)
    runtime = timedelta(minutes=spec.runtime_min)
    deadline_text = job.deadline.astimezone(tz).strftime("%H:%M")
    now_profile: tuple[float, ...] = ()
    if job.state is JobState.RUNNING:
        started = min(job.started_at or now, now)
        rest = profile_from(profile, origin=started, now=now, starts=starts, step=step)
        if not rest:
            return None
        candidates: tuple[int, ...] = (0,)
        profile = list(rest)
        met = True
        running = True
    else:
        running = False
        fits = [
            k
            for k in range(n)
            if k + len(profile) <= n and max(now, starts[k]) + runtime <= job.deadline
        ]
        met = bool(fits)
        candidates = tuple(fits) if fits else (0,)
        if not met:
            found.append(
                Reason(
                    key=rk.JOB_DEADLINE_INFEASIBLE,
                    args={"device": device.id, "deadline": deadline_text},
                )
            )
        if 0 in candidates and now > starts[0]:
            now_profile = profile_from(profile, origin=now, now=now, starts=starts, step=step)
    cover = [0.0] * n
    for k in candidates:
        run = now_profile if k == 0 and now_profile else profile
        for i, p in enumerate(run):
            if k + i < n:
                cover[k + i] = max(cover[k + i], p)
    previous: int | None = None
    if job.state is JobState.SCHEDULED and job.planned_start is not None:
        index = _slot_index(starts, step, job.planned_start)
        previous = index if index in candidates else None
    return PlannerJob(
        device_id=device.id,
        kind="job",
        max_w=tuple(cover),
        grid_max_w=tuple(cover),
        battery=True,
        para14a=False,
        job_id=job.id,
        profile_w=tuple(profile),
        candidates=candidates,
        running=running,
        deadline=job.deadline,
        deadline_met=met,
        previous_start=previous,
        now_profile_w=now_profile,
    )


def thermal_base_w(
    model: ThermalModel,
    *,
    temp0_c: float,
    min_c: float,
    outdoor_c: Sequence[float],
    cop: Sequence[float],
    max_w: float,
    dt_s: float | Sequence[float],
) -> list[float]:
    """Electric power per slot that holds ``min_c`` from ``temp0_c`` (thermostat simulation).

    ``dt_s``: the slot length, or one per slot (the first slot is shorter, :mod:`slots`)."""
    out: list[float] = []
    temp = temp0_c
    lengths = [dt_s] * len(outdoor_c) if isinstance(dt_s, (int, float)) else list(dt_s)
    for out_c, c, length in zip(outdoor_c, cop, lengths, strict=True):
        idle = model.step(temp, out_c, 0.0, length)
        if idle >= min_c:
            out.append(0.0)
            temp = idle
            continue
        full = model.step(temp, out_c, max_w * c, length)
        # Linear in the heat: solve for the power that ends exactly at min_c.
        share = 1.0 if full <= idle else min(1.0, (min_c - idle) / (full - idle))
        power = max(0.0, share * max_w)
        out.append(power)
        temp = model.step(temp, out_c, power * c, length)
    return out


def _outdoor(series: TimeSeries | None, grid: Grid) -> list[float] | None:
    if series is None:
        return None
    values = resample_power(series, grid).values
    known = [v for v in values if v is not None]
    if not known:
        return None
    out: list[float] = []
    last = known[0]
    for v in values:
        last = last if v is None else v
        out.append(last)
    return out


def _thermal(  # noqa: PLR0911
    device: DeviceConfig,
    *,
    max_w: float | None,
    temperature: float | None,
    outdoor: list[float] | None,
    hours: Sequence[float],
    found: list[Reason],
) -> PlannerThermal | None:
    spec = device.thermal
    if spec is None:
        return None

    def energy_only(cause: str) -> None:
        found.append(Reason(key=rk.THERMAL_ENERGY_ONLY, args={"device": device.id, "cause": cause}))

    if not model_usable(spec.learned) or spec.learned is None:
        energy_only("model")
        return None
    if device.comfort is None:
        energy_only("comfort")
        return None
    if temperature is None or not math.isfinite(temperature):
        energy_only("temperature")
        return None
    if outdoor is None:
        energy_only("outdoor")
        return None
    if max_w is None or max_w <= 0:
        return None
    model = spec.learned.model
    curve = spec.cop or GENERIC_AIR_WATER_COP
    cop = [curve.cop_at(t) for t in outdoor]
    n = len(outdoor)
    lengths = [h * 3600.0 for h in hours[:n]]
    # Exact one-step response (core.thermal.ThermalModel.step), split into its linear parts.
    alpha = tuple(model.step(1.0, 0.0, 0.0, d) - model.step(0.0, 0.0, 0.0, d) for d in lengths)
    drift = [model.step(0.0, o, 0.0, d) for o, d in zip(outdoor, lengths, strict=True)]
    base = thermal_base_w(
        model,
        temp0_c=temperature,
        min_c=device.comfort.min_c,
        outdoor_c=outdoor,
        cop=cop,
        max_w=max_w,
        dt_s=lengths,
    )
    heat_k = tuple(
        (model.step(0.0, 0.0, 1000.0 * c, d) - model.step(0.0, 0.0, 0.0, d))
        for c, d in zip(cop, lengths, strict=True)
    )
    return PlannerThermal(
        device_id=device.id,
        kind="thermal",
        max_w=(max_w,) * n,
        grid_max_w=(max_w,) * n if spec.grid_preheat else tuple(base),
        battery=True,
        para14a=device.para14a,
        temp0_c=temperature,
        min_c=device.comfort.min_c,
        max_c=device.comfort.max_c,
        alpha=alpha,
        heat_k=heat_k,
        drift=tuple(drift),
        base_w=tuple(base),
        cop=tuple(cop),
        grid_preheat=spec.grid_preheat,
        capacity_wh_per_k=model.capacity_wh_per_k,
    )


def build_flex(
    devices: Sequence[DeviceConfig],
    request: FlexRequest | None,
    *,
    now: datetime,
    grid: Grid,
    tz: tzinfo,
    max_power: Mapping[str, float | None],
) -> FlexBuild:
    """The flexible loads among ``devices`` on ``grid`` (module doc).

    ``max_power`` is each device's full power (``inputs.device_max_power_w`` or an override).
    Program devices are always claimed (a program is never planned as continuous surplus
    energy); a thermal device without a usable model is left energy-only.
    """
    request = request or FlexRequest()
    starts = grid.starts()
    step = grid.step
    hours = slot_hours(now, starts, step)
    found: list[Reason] = []
    claimed: set[str] = set()
    evs: list[PlannerEv] = []
    jobs: list[PlannerJob] = []
    thermals: list[PlannerThermal] = []
    outdoor = _outdoor(request.outdoor_c, grid)
    by_device: dict[str, list[ProgramJob]] = {}
    for job in request.jobs:
        if job.state not in FINAL_JOB_STATES:
            by_device.setdefault(job.device_id, []).append(job)
    for device in devices:
        if device.ev is not None and _charger_powers(device) is not None:
            claimed.add(device.id)
            ev = _ev(
                device,
                request.ev_states.get(device.id),
                request.ev_live.get(device.id, EvLive()),
                starts=starts,
                step=step,
                hours=hours,
                found=found,
            )
            if ev is not None:
                evs.append(ev)
        elif device.program is not None:
            claimed.add(device.id)
            pending = sorted(
                by_device.get(device.id, []),
                key=lambda j: (j.state is not JobState.RUNNING, j.deadline),
            )
            if not pending:
                continue
            found.extend(
                Reason(key=rk.JOB_IGNORED, args={"device": device.id}) for _ in pending[1:]
            )
            profile = job_profile_w(device, step, max_power.get(device.id))
            planned = _job(
                device, pending[0], profile, now=now, starts=starts, step=step, tz=tz, found=found
            )
            if planned is not None:
                jobs.append(planned)
        elif device.thermal is not None:
            thermal = _thermal(
                device,
                max_w=max_power.get(device.id),
                temperature=request.temperatures_c.get(device.id),
                outdoor=outdoor if outdoor is None else outdoor[: grid.count],
                hours=hours,
                found=found,
            )
            if thermal is not None:
                claimed.add(device.id)
                thermals.append(thermal)
    return FlexBuild(
        evs=tuple(evs),
        jobs=tuple(jobs),
        thermals=tuple(thermals),
        claimed=frozenset(claimed),
        reasons=tuple(found),
    )


def build_grid(
    request: GridRequest,
    *,
    now: datetime,
    grid: Grid,
    tz: tzinfo,
    export_price: Sequence[float],
) -> GridBuild:
    """Per-slot limits, the peak months and the §14a budget (module doc)."""
    starts = grid.starts()
    current = month_key(now, tz)
    keys: list[str] = []
    imports: list[float | None] = []
    for at in starts:
        key = month_key(at, tz)
        keys.append(key)
        peak = request.month_peak_w if key == current else None
        imports.append(effective_import_limit_w(request.fuse_limit_w, request.peak, peak))
    exports = [
        effective_export_limit_w(
            request.static_export_limit_w,
            zero_export=request.zero_export,
            export_dynamic=request.export_dynamic,
            export_price=price,
            adapter_verified=request.export_adapter_verified,
        )
        for price in export_price
    ]
    order = list(dict.fromkeys(keys))
    month_of_slot = tuple(order.index(k) for k in keys)
    floors = tuple(max(0.0, request.month_peak_w or 0.0) if k == current else 0.0 for k in order)
    found: list[Reason] = []
    budget = request.para14a
    para: list[float | None] = [None] * grid.count
    if budget.status is not Para14aStatus.INACTIVE:
        hold_until = now + request.para14a_hold
        value = budget.budget_w if budget.status is Para14aStatus.ACTIVE else 0.0
        for t, at in enumerate(starts):
            if at < hold_until:
                para[t] = max(0.0, value or 0.0)
        if budget.status is Para14aStatus.ACTIVE:
            found.append(Reason(key=rk.GRID_PARA14A, args={"budget_w": round(value or 0.0)}))
        else:
            found.append(Reason(key=rk.GRID_PARA14A_UNKNOWN))
    price = request.peak.capacity_price_per_kw_month if request.peak.enabled else 0.0
    return GridBuild(
        import_limits_w=tuple(imports),
        export_limits_w=tuple(exports),
        grid=PlannerGrid(
            peak_price_per_kw=price,
            month_of_slot=month_of_slot if price > 0 else (),
            month_floor_w=floors if price > 0 else (),
            para14a_w=tuple(para) if any(v is not None for v in para) else (),
            para14a_status=budget.status,
        ),
        reasons=tuple(found),
    )


def cap_para14a_floors(
    evs: Sequence[PlannerEv], thermals: Sequence[PlannerThermal], grid: PlannerGrid
) -> tuple[tuple[PlannerEv, ...], tuple[PlannerThermal, ...]]:
    """Scale the forced power of flagged loads into the §14a budget (keeps the LP feasible).

    While dimmed, the flagged devices' floors (EV ``now``/``min_pv``, the heat pump's own
    heating) together never exceed the budget. The heat pumps' own heating comes first
    (scaled proportionally if it alone exceeds the budget, comfort then falls short), the EVs
    share what is left in order; an EV floor that no longer reaches the 6 A minimum becomes 0
    (the controller pauses the charger, ADR-0018 decision 5).
    """
    if not grid.para14a_w:
        return tuple(evs), tuple(thermals)
    n = len(grid.para14a_w)
    ev_floor = [list(e.floor_w) for e in evs]
    th_base = [list(h.base_w) for h in thermals]
    for t in range(n):
        budget = grid.para14a_at(t)
        if budget is None:
            continue
        heating = sum(th_base[i][t] for i, h in enumerate(thermals) if h.para14a)
        if heating > budget > 0 or (heating > 0 and budget <= 0):
            scale = budget / heating
            for i, h in enumerate(thermals):
                if h.para14a:
                    th_base[i][t] *= scale
            heating = budget
        left = max(0.0, budget - heating)
        for i, e in enumerate(evs):
            if not e.para14a:
                continue
            value = min(ev_floor[i][t], left)
            ev_floor[i][t] = value if value >= e.min_w else 0.0
            left -= ev_floor[i][t]
    out_ev = tuple(
        replace(
            e,
            floor_w=tuple(ev_floor[i]),
            grid_max_w=e.grid_max_w if e.mode is EvMode.NOW else tuple(ev_floor[i]),
        )
        if e.para14a and e.mode in (EvMode.NOW, EvMode.MIN_PV)
        else e
        for i, e in enumerate(evs)
    )
    out_th = tuple(
        replace(
            h,
            base_w=tuple(th_base[i]),
            grid_max_w=h.grid_max_w if h.grid_preheat else tuple(th_base[i]),
        )
        if h.para14a
        else h
        for i, h in enumerate(thermals)
    )
    return out_ev, out_th


__all__ = [
    "EV_BINARY_WINDOW",
    "EV_MIN_CURRENT_A",
    "PARA14A_HOLD",
    "EvLive",
    "FlexBuild",
    "FlexFlows",
    "FlexKind",
    "FlexLoad",
    "FlexRequest",
    "GridBuild",
    "GridRequest",
    "PlannerEv",
    "PlannerGrid",
    "PlannerJob",
    "PlannerThermal",
    "build_flex",
    "build_grid",
    "cap_para14a_floors",
    "job_profile_w",
    "profile_from",
    "thermal_base_w",
]
