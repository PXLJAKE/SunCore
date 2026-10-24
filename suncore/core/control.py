# SPDX-License-Identifier: Apache-2.0
"""Control contracts (Phase 3 WP2): power estimates, observed device state, settings, plan.

Everything here is pure data shared by the controller, the safety layer, the power
models and the planner (Phase 4). Units and signs follow ``core/units.py``: W, Wh, °C,
device power + = consumption.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from suncore.core.balance import FlowSnapshot
from suncore.core.decisions import Reason
from suncore.core.devices import RESUME_TIME_DEFAULT, DeviceConfig, SetpointControl
from suncore.core.signals import Quality
from suncore.core.validation import invalid


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- estimates and observed state --------------------------------------------------


class PowerEstimate(_Frozen):
    """A device's power with its uncertainty (BRIEF §4.5: both power paths are equivalent).

    ``value_w ± uncertainty_w`` is the band the controller and the grid invariant work
    with; ``quality`` is the worst quality of the inputs; ``age_s`` is the age of the
    newest input that produced the estimate.
    """

    value_w: float = Field(allow_inf_nan=False, ge=0)
    uncertainty_w: float = Field(allow_inf_nan=False, ge=0)
    quality: Quality = Quality.OK
    age_s: float = Field(allow_inf_nan=False, ge=0, default=0.0)

    @property
    def lower_w(self) -> float:
        return max(0.0, self.value_w - self.uncertainty_w)

    @property
    def upper_w(self) -> float:
        return self.value_w + self.uncertainty_w


class DeviceState(_Frozen):
    """What a device looks like right now, as read from the entity cache by a driver.

    Only the fields that exist for the device are set; ``on`` is ``None`` when the
    control entity is unavailable. For a climate device ``on`` is ``hvac_mode != off``
    and ``mode`` is the HVAC mode. ``quality`` is the worst quality of the entities read.
    """

    on: bool | None = None
    step: Annotated[int, Field(ge=0, le=15)] | None = None
    setpoint_c: float | None = Field(default=None, allow_inf_nan=False)
    value: float | None = Field(default=None, allow_inf_nan=False)
    """Continuous control value in the entity's unit (W, A or %)."""
    mode: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    quality: Quality = Quality.OK
    ts: AwareDatetime | None = None
    """When the newest contributing entity was refreshed."""
    temperature_c: float | None = Field(default=None, allow_inf_nan=False)
    """Measured temperature of a thermal device's ``temperature`` role (a tank probe) in °C;
    ``None`` = no probe, or the probe is unavailable, stale or not in °C (the control stack
    fills it from the entity cache, see :func:`heat_capped`)."""

    @property
    def known(self) -> bool:
        """Whether the state can be trusted for a decision (ok or stale, never missing)."""
        return self.quality in (Quality.OK, Quality.STALE)


@dataclass(frozen=True, slots=True)
class GateInputs:
    """Site view the controller computes every tick and the safety gate judges intents with.

    ``surplus`` is the **free** surplus: power available on top of everything that runs
    now (export minus reserve, after smoothing), *not* including the current draw of
    the device being judged - the grid invariant compares it with the action's added
    demand. A snapshot older than ``inputs_max_age_ticks * tick_s`` (or without ``ts``)
    is treated as unknown surplus by the gate, which then rejects every load increase.
    """

    flows: FlowSnapshot | None = None
    surplus: PowerEstimate | None = None
    estimates: Mapping[str, PowerEstimate] = field(default_factory=dict)
    """Current power per device id."""
    import_limit_w: float | None = None
    reserve_soc_pct: float | None = None
    ts: datetime | None = None
    """When the snapshot was computed (injected clock)."""


# --- settings -----------------------------------------------------------------


class ControlSettings(_Frozen):
    """Controller and safety parameters (expert mode, BRIEF §4.12) with Phase 3 defaults.

    Plan §6: tick 5 s, smoothing 60 s, assisted proposals valid 120 s, deficit delay 30 s
    with a 50 Wh battery budget. Device-level ``None`` values fall back to the defaults here.
    """

    schema_version: int = 1
    tick_s: Annotated[float, Field(ge=1, le=60)] = 5.0
    smoothing_s: Annotated[float, Field(ge=0, le=900)] = 60.0
    reserve_w: Annotated[float, Field(ge=0, le=100_000)] = 100.0
    """Surplus kept unallocated (measurement noise, small loads)."""
    deficit_delay_s: Annotated[float, Field(ge=0, le=600)] = 30.0
    """How long controlled devices may cause import/discharge before the controller backs off."""
    deficit_budget_wh: Annotated[float, Field(ge=0, le=10_000)] = 50.0
    """Battery energy a single deficit may draw before backing off."""
    proposal_ttl_s: Annotated[float, Field(ge=10, le=3600)] = 120.0
    """Assisted mode: a proposal expires after this time."""
    intent_max_age_s: Annotated[float, Field(ge=1, le=600)] = 30.0
    """Intents older than this when they reach the gate are stale and dropped."""
    inputs_max_age_ticks: Annotated[int, Field(ge=1, le=100)] = 3
    """Gate inputs (flows, surplus) older than this many ticks count as unknown."""
    default_hysteresis_w: Annotated[float, Field(ge=0, le=100_000)] = 100.0
    default_hysteresis_delay_s: Annotated[float, Field(ge=0, le=3600)] = 60.0
    default_settle_s: Annotated[float, Field(ge=0, le=600)] = 10.0
    default_max_switches_per_hour: Annotated[int, Field(ge=1, le=3600)] = 6
    min_write_interval_s: Annotated[float, Field(ge=0.25, le=600)] = 5.0
    """Per-device write rate limit (BRIEF §5 write protection)."""
    release_deadline_s: Annotated[float, Field(ge=1, le=60)] = 10.0
    """Shutdown: the release of all devices must finish within this time."""
    safe_state_cooldown_s: Annotated[float, Field(ge=0, le=3600)] = 120.0
    """Watchdog: time a cleared fault must stay clear before control resumes."""
    stale_cadence_factor: Annotated[float, Field(ge=1, le=10)] = 2.0
    """A slow sensor is stale after ``cadence * factor`` without a change."""
    max_jump_w_per_s: Annotated[float, Field(gt=0, le=1_000_000)] = 20_000.0
    """Site power changes faster than this are implausible (unit or sensor fault)."""
    frozen_pv_s: Annotated[float, Field(ge=60, le=86_400)] = 900.0
    """PV power unchanged for this long in daylight = inverter hang (BRIEF §9)."""
    frozen_pv_min_w: Annotated[float, Field(ge=0, le=1_000_000)] = 200.0
    """Below this PV power the frozen-value check does not apply (night, snow)."""
    balance_tolerance_w: Annotated[float, Field(ge=0, le=100_000)] = 500.0
    """Measured house load may deviate this much from ``pv + grid - battery``."""
    balance_tolerance_pct: Annotated[float, Field(ge=0, le=100)] = 20.0


# --- plan (minimal contract handed to Phase 4, ADR-0011) ------------------------------


@unique
class BandPreference(StrEnum):
    """What the plan would like the controller to do inside an allowed band."""

    NEUTRAL = "neutral"
    PREFER_ON = "prefer_on"
    PREFER_OFF = "prefer_off"


class DeviceBand(_Frozen):
    """Per interval and device: what the controller may do and what the planner prefers."""

    allowed: bool = True
    """``False`` = the controller must not switch the device on / raise its demand."""
    preference: BandPreference = BandPreference.NEUTRAL
    max_power_w: float | None = Field(default=None, allow_inf_nan=False, ge=0)
    """Upper bound of the device's demand in this interval (``None`` = device limit)."""


class BatteryBand(_Frozen):
    """Battery corridor per interval (Phase 4 writes it; Phase 3 reads only ``reserve``)."""

    min_power_w: float | None = Field(default=None, allow_inf_nan=False)
    max_power_w: float | None = Field(default=None, allow_inf_nan=False)
    min_soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    max_soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    charge_from_surplus: bool = True
    """``False`` = surplus goes to consumers before the battery (``climate_before_export``)."""
    priority_until_soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    """Plan v2: battery first until the *live* SoC reaches this value, then surplus may go
    to consumers. Evaluated by the controller against the measured SoC every tick (robust
    against plan deviation; an unknown SoC counts as not reached); ``None`` = no such rule.
    It only takes surplus away from the consumers (``controller.surplus``)."""
    grid_charge_max_w: float | None = Field(default=None, allow_inf_nan=False, ge=0)
    """ADR-0013 decision 4: the most the battery may charge **from the grid** in this
    interval (W); ``None`` = no grid charging planned. Set by the planner only where the
    effective grid-charge policy (price level, schedule) and the spread rule allow it. The
    gate bounds a ``grid_charge_w`` by it (invariant B2/B3, Phase 4 WP10); until then the
    controller ignores it."""
    discharge_lock: bool = False
    """ADR-0013 decision 4: the plan holds the battery's energy in this interval although
    the house draws from the grid (hold for the evening target or a later price peak).
    Only a plan interval with this flag allows a discharge lock (invariant B4, WP10);
    until then the controller ignores it."""

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        lo, hi = self.min_power_w, self.max_power_w
        if lo is not None and hi is not None and lo > hi:
            raise invalid("battery_band_power", "battery band: min_power_w above max_power_w")
        slo, shi = self.min_soc_pct, self.max_soc_pct
        if slo is not None and shi is not None and slo > shi:
            raise invalid("battery_band_soc", "battery band: min_soc_pct above max_soc_pct")
        return self


class IntervalFlows(_Frozen):
    """Plan v2: the flows the planner expects in an interval (mean power, W).

    Signs per ``core/units.py``: import/export are magnitudes, battery + = charging.
    ``pv_p10_w``/``pv_p50_w``/``pv_p90_w`` are the PV forecast's quantiles as forecast data
    (shown in the UI; ``None`` = the source has no quantiles); the objective uses P50.
    Battery targets are *not* planned on P10 PV (plan §0): they use the risk quantile of
    the learnt **net** energy error distribution (PV minus load) per weather class.
    ``net_risk_quantile`` is that quantile (``RiskLevel.quantile``, e.g. 0.1 = cautious)
    and ``net_risk_w`` the net power PV - load at that quantile the target constraints
    used (may be negative; ``None`` = no target constraint in this interval).
    ``soc_pct`` is the planned state of charge at the *end* of the interval (``None`` = no
    battery). ``devices_w`` is the planned draw of the controllable devices (not part of
    ``load_w``, the base load) and ``curtail_w`` the PV the plan does not use (export limit
    or negative export price; informational until the export-limit chain, Phase 6), so
    ``pv_p50_w + import_w + max(-battery_w, 0) = load_w + devices_w + max(battery_w, 0) +
    export_w + curtail_w`` (both additive in plan v2, default 0).
    """

    pv_p10_w: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pv_p50_w: float = Field(ge=0, allow_inf_nan=False)
    pv_p90_w: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    load_w: float = Field(ge=0, allow_inf_nan=False)
    import_w: float = Field(ge=0, allow_inf_nan=False)
    export_w: float = Field(ge=0, allow_inf_nan=False)
    battery_w: float = Field(default=0.0, allow_inf_nan=False)
    soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    net_risk_quantile: Annotated[float, Field(gt=0, lt=1)] | None = None
    net_risk_w: float | None = Field(default=None, allow_inf_nan=False)
    devices_w: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    curtail_w: float = Field(default=0.0, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _quantiles(self) -> Self:
        lo = self.pv_p10_w if self.pv_p10_w is not None else self.pv_p50_w
        hi = self.pv_p90_w if self.pv_p90_w is not None else self.pv_p50_w
        if not lo <= self.pv_p50_w <= hi:
            raise invalid("quantile_order", "quantiles: p10 <= p50 <= p90 is violated")
        if (self.net_risk_w is None) != (self.net_risk_quantile is None):
            raise invalid(
                "net_risk_pair",
                "expected flows: net_risk_w and net_risk_quantile are set together",
            )
        return self


class IntervalPrices(_Frozen):
    """Plan v2: end prices (currency/kWh) the planner used; may be negative."""

    import_price: float = Field(allow_inf_nan=False)
    export_price: float = Field(allow_inf_nan=False)


class PlanInterval(_Frozen):
    """One planning interval (15 min grid in Phase 4; any length is accepted).

    ``expected``, ``prices`` and ``reasons`` are the plan v2 additions for the UI's
    "Why?"; the controller reads only the bands and the import limit.
    """

    start: AwareDatetime
    end: AwareDatetime
    devices: dict[str, DeviceBand] = Field(default_factory=dict)
    """Device id -> band; a missing device gets :data:`DEFAULT_BAND`."""
    battery: BatteryBand = BatteryBand()
    grid_import_limit_w: float | None = Field(default=None, allow_inf_nan=False, ge=0)
    expected: IntervalFlows | None = None
    prices: IntervalPrices | None = None
    reasons: tuple[Reason, ...] = ()

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.start < self.end:
            raise invalid("plan_interval_order", "plan interval: start must be before end")
        return self

    def covers(self, at: datetime) -> bool:
        return self.start <= at < self.end


DEFAULT_BAND: Final = DeviceBand()
DEFAULT_BATTERY_BAND: Final = BatteryBand()


PLAN_SCHEMA_VERSION: Final = 2
"""Version of new plans; v1 (Phase 3, no expected flows/prices/source/stats) still loads."""

PlanSource = Literal["solver", "heuristic", "default"]
"""Who made the plan: the HiGHS model, the heuristic fallback, or nobody (allow all)."""

StatusSlug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=48)]


class SolverStats(_Frozen):
    """Plan v2: how the plan was computed (the UI explains long solves with it)."""

    solve_time_s: float = Field(ge=0, allow_inf_nan=False)
    status: StatusSlug
    """Solver or heuristic status, e.g. ``optimal``, ``time_limit``, ``infeasible``."""
    objective: float | None = Field(default=None, allow_inf_nan=False)
    """Objective value in currency (``None`` = not available, e.g. heuristic)."""
    n_vars: int = Field(default=0, ge=0)
    n_binaries: int = Field(default=0, ge=0)
    mip_gap: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Plan(_Frozen):
    """The planner's output as the controller consumes it (ADR-0011).

    Phase 3 only ever uses :meth:`Plan.default`, which has no intervals and therefore
    allows everything; the Phase 4 planner fills ``intervals`` without changing the
    controller. Intervals must not overlap and are kept in start order.

    Schema v2 (Phase 4 WP2, additive): ``source``, ``stats`` and per interval
    ``expected``/``prices``/``reasons``. A v1 payload loads unchanged (it keeps
    ``schema_version`` 1, ``source = "default"``); :func:`plan_from_payload` stamps v2.
    """

    schema_version: Annotated[int, Field(ge=1, le=PLAN_SCHEMA_VERSION)] = PLAN_SCHEMA_VERSION
    created_at: AwareDatetime | None = None
    intervals: tuple[PlanInterval, ...] = ()
    reasons: tuple[Reason, ...] = ()
    assumptions: dict[str, str | int | float | bool] = Field(default_factory=dict)
    """Forecast and price sources, mode, solver status - scalars only."""
    source: PlanSource = "default"
    stats: SolverStats | None = None

    @model_validator(mode="after")
    def _non_overlapping(self) -> Self:
        ordered = sorted(self.intervals, key=lambda i: i.start)
        for a, b in itertools.pairwise(ordered):
            if b.start < a.end:
                raise invalid("plan_overlap", "plan: intervals overlap")
        if tuple(ordered) != self.intervals:
            object.__setattr__(self, "intervals", tuple(ordered))
        return self

    @classmethod
    def default(cls) -> Plan:
        """The plan that allows everything (no intervals, no reasons)."""
        return cls()

    @property
    def is_default(self) -> bool:
        return not self.intervals

    def interval_at(self, at: datetime) -> PlanInterval | None:
        for interval in self.intervals:
            if interval.covers(at):
                return interval
        return None

    def band_for(self, device_id: str, at: datetime) -> DeviceBand:
        """The device's band at ``at``; outside every interval everything is allowed."""
        interval = self.interval_at(at)
        if interval is None:
            return DEFAULT_BAND
        return interval.devices.get(device_id, DEFAULT_BAND)

    def battery_band_at(self, at: datetime) -> BatteryBand:
        interval = self.interval_at(at)
        return DEFAULT_BATTERY_BAND if interval is None else interval.battery


def plan_from_payload(payload: Mapping[str, Any]) -> Plan:
    """Load a stored plan of any known schema version (v1 rows are stamped v2).

    Every v1 field exists unchanged in v2, so the upgrade only stamps the version. A
    payload newer than :data:`PLAN_SCHEMA_VERSION` raises ``ValueError``.
    """
    data = dict(payload)
    version = data.get("schema_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        msg = f"plan payload has an invalid schema_version {version!r}"
        raise ValueError(msg)
    if version > PLAN_SCHEMA_VERSION:
        msg = f"plan payload schema_version {version} is newer than {PLAN_SCHEMA_VERSION}"
        raise ValueError(msg)
    data["schema_version"] = PLAN_SCHEMA_VERSION
    return Plan.model_validate(data)


TEMPERATURE_ROLE: Final = "temperature"
"""The role of a thermal device's own temperature probe (water heater tank)."""


def has_heat_limit(device: DeviceConfig) -> bool:
    """A thermal load whose ``comfort.max_c`` is enforced through its ``temperature`` probe.

    Climate units (set-point control) keep their comfort band through the set-point; a
    device without a probe cannot be limited (the template says so).
    """
    return (
        device.comfort is not None
        and TEMPERATURE_ROLE in device.entities
        and not isinstance(device.control, SetpointControl)
    )


def heat_capped(device: DeviceConfig, state: DeviceState | None) -> bool:
    """No more heat: the probe reads at or above ``comfort.max_c``, or is unknown/stale.

    Shared by the controller (caps the device at level 0) and the gate's invariant (rejects
    any load increase); an unknown temperature counts as a full tank (the safe direction).
    """
    if not has_heat_limit(device):
        return False
    assert device.comfort is not None
    temperature = None if state is None else state.temperature_c
    return temperature is None or temperature >= device.comfort.max_c


__all__ = [  # RESUME_TIME_DEFAULT is re-exported from core.devices (single constant)
    "DEFAULT_BAND",
    "DEFAULT_BATTERY_BAND",
    "PLAN_SCHEMA_VERSION",
    "RESUME_TIME_DEFAULT",
    "TEMPERATURE_ROLE",
    "BandPreference",
    "BatteryBand",
    "ControlSettings",
    "DeviceBand",
    "DeviceState",
    "GateInputs",
    "IntervalFlows",
    "IntervalPrices",
    "Plan",
    "PlanInterval",
    "PlanSource",
    "PowerEstimate",
    "SolverStats",
    "has_heat_limit",
    "heat_capped",
    "plan_from_payload",
]
