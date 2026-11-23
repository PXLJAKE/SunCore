# SPDX-License-Identifier: Apache-2.0
"""Battery model, strategies and the ``battery`` configuration section (Phase 4 WP2).

Units and signs per ``core/units.py``: W, Wh, percent, currency/kWh; battery + = charging.
The defaults are the maintainer's answers to plan §6 question 6 (efficiency 95 % each way,
degradation 0.04/kWh throughput, grid-charge margin 0.03/kWh, BMS-drift margin 5 %), all
expert parameters.

:class:`BatteryAction` (the gate's battery intent) lives in :mod:`suncore.core.decisions`
with the other action variants and is re-exported here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import StrEnum, unique
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from suncore.core.decisions import BatteryAction
from suncore.core.devices import RoleName, TemplateId
from suncore.core.site import BatteryConfig, EntityRef
from suncore.core.tariff import WeeklySchedule
from suncore.core.validation import invalid

Pct = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Efficiency = Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
PosW = Annotated[float, Field(gt=0, le=1_000_000, allow_inf_nan=False)]
UnitCost = Annotated[float, Field(ge=0, le=10, allow_inf_nan=False)]
"""Currency per kWh."""

DEFAULT_EFFICIENCY: Final = 0.95
DEFAULT_DEGRADATION_PER_KWH: Final = 0.04
DEFAULT_GRID_CHARGE_MARGIN_PER_KWH: Final = 0.03
DEFAULT_BMS_DRIFT_PCT: Final = 5.0
DEFAULT_RESERVE_PCT: Final = 10.0


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


BATTERY_ASSET_ID: Final = "battery"
"""The asset id of the (one) battery in intents, decisions and the gate (ADR-0013 decision 6)."""


@unique
class BatteryCapability(StrEnum):
    """What a battery adapter can execute (ADR-0013 decision 1)."""

    CHARGE_LIMIT = "charge_limit"
    """``max_charge_w``: cap the charging power of the inverter's own control."""
    DISCHARGE_LIMIT = "discharge_limit"
    """``max_discharge_w``: cap the discharging power (0 = discharge lock)."""
    MIN_SOC = "min_soc"
    """``min_soc_pct``: the inverter's discharge floor (reserve, discharge lock)."""
    GRID_CHARGE = "grid_charge"
    """``grid_charge_w``: forced charging from the grid."""
    HARDWARE_FALLBACK = "hardware_fallback"
    """The inverter falls back to its own limits when SunCore stops re-writing (Kostal §3.5)."""


BATTERY_ACTION_FIELDS: Final = ("max_charge_w", "max_discharge_w", "min_soc_pct", "grid_charge_w")
"""The limit fields of a :class:`BatteryAction`, in a fixed order."""

FIELD_CAPABILITY: Final[dict[str, BatteryCapability]] = {
    "max_charge_w": BatteryCapability.CHARGE_LIMIT,
    "max_discharge_w": BatteryCapability.DISCHARGE_LIMIT,
    "min_soc_pct": BatteryCapability.MIN_SOC,
    "grid_charge_w": BatteryCapability.GRID_CHARGE,
}
"""The capability a non-``None`` field of a :class:`BatteryAction` needs."""


def action_fields(action: BatteryAction) -> dict[str, float | None]:
    """The limit fields of ``action`` by name (``None`` = baseline)."""
    return {name: getattr(action, name) for name in BATTERY_ACTION_FIELDS}


def resolve_battery_action(action: BatteryAction, baseline: BatteryAction | None) -> BatteryAction:
    """The full state ``action`` asks for: every ``None`` field taken from ``baseline``.

    ADR-0013 decision 2: ``None`` = the baseline value of that field. A field the baseline does
    not know either stays ``None`` (the adapter leaves it untouched). The validator of
    :class:`BatteryAction` still holds: a baseline charge cap below the requested grid-charge
    power is not inherited (forced charging asks for its own power; the gate bounds it), and an
    inherited grid-charge power above a requested cap is lowered to the cap.
    """
    if baseline is None:
        return action
    values = action_fields(action)
    for name, value in action_fields(baseline).items():
        if values[name] is None and value is not None:
            values[name] = value
    grid, cap = values["grid_charge_w"], values["max_charge_w"]
    if grid is not None and cap is not None and grid > cap:
        if action.max_charge_w is None:
            values["max_charge_w"] = None  # inherited cap: forced charging brings its own power
        else:
            values["grid_charge_w"] = cap  # inherited forced charging: SunCore's cap wins
    return BatteryAction(**values)


@dataclass(frozen=True, slots=True)
class FieldRange:
    """The live range of one battery limit (in the field's unit: W or %)."""

    low: float
    high: float
    step: float | None = None
    """Quantisation of the entity (e.g. Kostal ``Battery:MinSoc`` step 5); ``None`` = none."""

    def contains(self, value: float, tolerance: float = 1e-6) -> bool:
        return self.low - tolerance <= value <= self.high + tolerance


@dataclass(frozen=True, slots=True)
class BatteryRanges:
    """What the adapter can write right now, per field (``None`` = unknown, nothing allowed)."""

    max_charge_w: FieldRange | None = None
    max_discharge_w: FieldRange | None = None
    min_soc_pct: FieldRange | None = None
    grid_charge_w: FieldRange | None = None

    def of(self, name: str) -> FieldRange | None:
        value: FieldRange | None = getattr(self, name)
        return value


def round_up_to_step(value: float, step: float | None, low: float = 0.0) -> float:
    """``value`` rounded **up** onto ``low + k * step`` (a floor is never lowered by rounding)."""
    if step is None or step <= 0:
        return value
    k = math.ceil((value - low) / step - 1e-9)
    return round(low + k * step, 6)


class BatteryModel(_Frozen):
    """What the planner knows about the battery.

    The planning floor is ``reserve_pct + bms_drift_margin_pct``: the reserve is the user's
    backup energy, the drift margin keeps SunCore off the BMS's own cut-off when the SoC
    reading sags (the maintainer's BMS drops from 10 % to 6-7 %). SunCore never plans to
    discharge below it; ``max_soc_pct`` caps charging (battery care).
    """

    capacity_wh: Annotated[float, Field(gt=0, le=100_000_000, allow_inf_nan=False)]
    charge_efficiency: Efficiency = DEFAULT_EFFICIENCY
    discharge_efficiency: Efficiency = DEFAULT_EFFICIENCY
    max_charge_w: PosW
    max_discharge_w: PosW
    reserve_pct: Pct = DEFAULT_RESERVE_PCT
    bms_drift_margin_pct: Pct = DEFAULT_BMS_DRIFT_PCT
    max_soc_pct: Pct = 100.0
    degradation_cost_per_kwh: UnitCost = DEFAULT_DEGRADATION_PER_KWH
    """Cost of one kWh of throughput (charged energy), currency/kWh."""
    grid_charge_margin_per_kwh: UnitCost = DEFAULT_GRID_CHARGE_MARGIN_PER_KWH
    """Safety margin on top of losses and degradation before grid charging pays."""

    @model_validator(mode="after")
    def _corridor(self) -> Self:
        if not self.min_soc_pct < self.max_soc_pct:
            raise invalid(
                "battery_corridor",
                "battery: reserve + drift margin ({floor}) must be below max_soc_pct ({max})",
                floor=self.min_soc_pct,
                max=self.max_soc_pct,
            )
        return self

    @property
    def min_soc_pct(self) -> float:
        """Planning floor: reserve plus BMS-drift margin."""
        return self.reserve_pct + self.bms_drift_margin_pct

    @property
    def round_trip_efficiency(self) -> float:
        return self.charge_efficiency * self.discharge_efficiency

    def soc_to_wh(self, soc_pct: float) -> float:
        return self.capacity_wh * soc_pct / 100

    def wh_to_soc(self, energy_wh: float) -> float:
        return 100 * energy_wh / self.capacity_wh

    @property
    def usable_wh(self) -> float:
        """Stored energy between the planning floor and ``max_soc_pct``."""
        return self.soc_to_wh(self.max_soc_pct - self.min_soc_pct)

    def min_later_price(self, charge_price: float) -> float:
        """Spread rule: the import price later must exceed this for grid charging to pay.

        One kWh bought at ``charge_price`` delivers ``round_trip_efficiency`` kWh later and
        costs degradation and the safety margin on top:
        ``later * rte > charge_price + degradation + margin``.
        """
        cost = charge_price + self.degradation_cost_per_kwh + self.grid_charge_margin_per_kwh
        return cost / self.round_trip_efficiency

    def grid_charge_pays(self, charge_price: float, later_price: float) -> bool:
        return later_price > self.min_later_price(charge_price)


def effective_reserve_pct(site_battery: BatteryConfig | None, model: BatteryModel) -> float:
    """The one SoC floor of planner, controller and gate (ADR-0013 decision 3).

    ``max(site.battery.min_soc_pct or 0, model.reserve_pct + model.bms_drift_margin_pct)``: the
    planning floor of the model, raised to the minimum SoC configured for the site battery if
    that is higher (the site value is what the user set in the wizard for the inverter).
    """
    site_min = (
        0.0
        if site_battery is None or site_battery.min_soc_pct is None
        else site_battery.min_soc_pct
    )
    return max(site_min, model.min_soc_pct)


@unique
class BatteryStrategy(StrEnum):
    """How the planner uses the battery (plan §1 item 5, answers 2026-10-16)."""

    SELF_CONSUMPTION = "self_consumption"
    """Default: store surplus, discharge for the house, no grid charging unless allowed."""
    PREFER_FULL = "prefer_full"
    """As full as possible: target 100 % (capped by ``max_soc_pct``) as early as the net
    energy forecast at the risk quantile (``BatterySettings.risk``) allows, PV surplus to
    the battery before consumers until full, discharge only for the house (never for
    export)."""
    TARGET_BY_TIME = "target_by_time"
    """Reach ``goals.battery_target`` by its time, planned on the risk quantile
    (``BatterySettings.risk``) of the net energy forecast (PV minus load), so the target
    is met with the risk level's probability."""


@unique
class RiskLevel(StrEnum):
    """How sure the plan must be that a battery target is met (forecast/buffer concept).

    The planner plans the target on a quantile of the net energy forecast (PV minus load):
    ``cautious`` meets it with probability 0.9 (10 % quantile), ``balanced`` 0.75 (25 %),
    ``bold`` 0.5 (median).
    """

    CAUTIOUS = "cautious"
    BALANCED = "balanced"
    BOLD = "bold"

    @property
    def probability(self) -> float:
        """Probability with which the target is met."""
        return _RISK_PROBABILITY[self]

    @property
    def quantile(self) -> float:
        """Quantile of the net energy forecast the target constraint uses."""
        return round(1.0 - self.probability, 6)


_RISK_PROBABILITY: Final[dict[RiskLevel, float]] = {
    RiskLevel.CAUTIOUS: 0.9,
    RiskLevel.BALANCED: 0.75,
    RiskLevel.BOLD: 0.5,
}

DEFAULT_AUTARKY_WEIGHT_PER_KWH: Final = 0.01


class GridChargePolicy(_Frozen):
    """When the battery may be charged from the grid (answers 2026-10-16, question 7).

    Off by default. When enabled, a ``max_price`` (charge only at or below this import
    price) and/or a ``schedule`` (only inside these weekly windows) must be set; both given
    means both must hold. On a dynamic tariff the planner's spread rule
    (:meth:`BatteryModel.grid_charge_pays`) applies in addition.

    The planner applies :func:`suncore.core.config.effective_grid_charge_policy`: this
    policy wins over ``goals.grid_charge_schedule``, which only fills in a missing schedule
    of an enabled policy and never enables charging. Hence an enabled policy must be
    complete on its own (the validator below), and the goals schedule can only narrow it.
    """

    enabled: bool = False
    max_price: Annotated[float, Field(ge=-10, le=100, allow_inf_nan=False)] | None = None
    """Currency/kWh; may be negative (charge only when paid to import)."""
    schedule: WeeklySchedule | None = None

    @model_validator(mode="after")
    def _limited(self) -> Self:
        if self.enabled and self.max_price is None and self.schedule is None:
            raise invalid(
                "grid_charge_needs_limit",
                "battery.grid_charge: enabling needs a price level or a schedule",
            )
        return self

    def allows(self, at: datetime, import_price: float | None, tz: tzinfo) -> bool:
        """Whether this policy admits grid charging at ``at`` with ``import_price``.

        ``tz`` is the region's zone the schedule is judged in. An unknown price never
        satisfies a price level.
        """
        if not self.enabled:
            return False
        if self.schedule is not None and not self.schedule.contains(at, tz):
            return False
        if self.max_price is not None:
            return import_price is not None and import_price <= self.max_price
        return True


class CalibrationCharge(_Frozen):
    """Periodic full charge for the BMS's SoC calibration (off by default, question 11).

    When enabled the planner schedules a charge to ``target_soc_pct`` once every
    ``interval_days`` into a sunny day (from PV; grid only if the policy allows it).
    """

    enabled: bool = False
    interval_days: Annotated[int, Field(ge=1, le=365)] = 30
    target_soc_pct: Pct = 100.0


@unique
class ModbusByteOrder(StrEnum):
    """Word order of 32-bit values (Kostal register 5: ``0x00`` little, ``0x01`` big)."""

    LITTLE = "little"
    """CDAB: the low word first (Kostal default)."""
    BIG = "big"
    """ABCD: the high word first (SunSpec)."""


HubName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_\-]{1,64}$")]


class ModbusLink(_Frozen):
    """How the ``kostal_modbus`` adapter reaches the inverter through ``modbus.write_register``.

    ``hub`` is the ``name`` of the user's ``modbus:`` YAML hub (HA's default ``modbus_hub``),
    ``slave`` the inverter's unit id (Kostal default 71; the action itself would default to 1,
    so it is mandatory here), ``byte_order`` what register 5 reports (the adapter never guesses
    it). ``fallback_timeout_s`` is written to register 1288 (30-10800 s) and ``heartbeat_s`` is
    the re-write period of 1280/1282; it must be at most a third of the timeout. Research:
    ``docs/research/kostal-plenticore-modbus.md`` §3.5, ``ha-websocket-api.md`` "Modbus write
    action". ``UNVERIFIED`` on hardware (external battery management is not enabled on the
    maintainer's inverter yet).
    """

    hub: HubName = "modbus_hub"
    slave: Annotated[int, Field(ge=1, le=255)]
    byte_order: ModbusByteOrder
    heartbeat_s: Annotated[float, Field(ge=5, le=3600)] = 20.0
    fallback_timeout_s: Annotated[int, Field(ge=30, le=10_800)] = 60

    @model_validator(mode="after")
    def _heartbeat(self) -> Self:
        if self.heartbeat_s * 3 > self.fallback_timeout_s:
            raise invalid(
                "modbus_heartbeat_too_slow",
                "battery.modbus: heartbeat_s ({heartbeat_s}) must be at most a third of "
                "fallback_timeout_s ({timeout_s})",
                heartbeat_s=self.heartbeat_s,
                timeout_s=self.fallback_timeout_s,
            )
        return self


BATTERY_SCHEMA_VERSION: Final = 1


class BatterySettings(_Frozen):
    """Configuration section ``battery`` (schema v1).

    ``adapter_template_id = None`` means *observe only*: SunCore reads the battery through
    the ``site`` section and plans with it, but never writes. With an adapter, ``entities``
    maps the template's roles; the battery starts in shadow like every device (the mode
    lives in the ``operation`` section). ``target_by_time`` uses ``goals.battery_target``
    (target SoC and time); reserve and max SoC are part of ``model``.
    """

    schema_version: int = BATTERY_SCHEMA_VERSION
    adapter_template_id: TemplateId | None = None
    entities: dict[RoleName, EntityRef] = Field(default_factory=dict)
    model: BatteryModel | None = None
    """``None`` until the user (wizard step 5) provides capacity and limits."""
    strategy: BatteryStrategy = BatteryStrategy.SELF_CONSUMPTION
    grid_charge: GridChargePolicy = GridChargePolicy()
    calibration: CalibrationCharge = CalibrationCharge()
    risk: RiskLevel = RiskLevel.CAUTIOUS
    """Maintainer's choice: targets are met with probability 0.9 by default."""
    morning_buffer_pct: Pct | None = None
    """Manual floor of "battery first until X %" in the morning; ``None`` = automatic from
    the forecast uncertainty."""
    bridge_next_day: bool = True
    """Raise the evening target up to ``max_soc_pct`` when the 48 h horizon shows a poor
    next day."""
    autarky_weight_per_kwh: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = (
        DEFAULT_AUTARKY_WEIGHT_PER_KWH
    )
    """Currency/kWh penalty on grid import added to the cost objective. The default
    (0.01) is a tie-breaker: it prefers self-sufficiency only where the cost difference is
    below 1 ct/kWh, which also removes LP degeneracy on flat tariffs; 0 = pure cost."""

    modbus: ModbusLink | None = None
    """``kostal_modbus`` adapter only: hub, unit id and word order (additive in v1, WP10)."""
    fallback_measured: bool = False
    """The maintainer measured the adapter's hardware fallback (Kostal §3.5 timer) on the real
    inverter. Until then the gate caps an adapter with ``hardware_fallback`` at **assisted**
    (ADR-0013 decision 6, plan §6 question 3). Additive in v1 (WP10)."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.modbus is not None and self.adapter_template_id is None:
            raise invalid(
                "battery_modbus_needs_adapter",
                "battery: a modbus link needs an adapter template",
            )
        if self.entities and self.adapter_template_id is None:
            raise invalid(
                "battery_entities_need_adapter",
                "battery: role entities need an adapter template (observe only has none)",
            )
        seen: set[str] = set()
        for ref in self.entities.values():
            if ref.entity_id in seen:
                raise invalid(
                    "entity_reused",
                    "entity {entity_id} is used more than once",
                    entity_id=ref.entity_id,
                )
            seen.add(ref.entity_id)
        return self

    @property
    def observe_only(self) -> bool:
        return self.adapter_template_id is None

    def entity_refs(self) -> list[EntityRef]:
        return list(self.entities.values())


__all__ = [
    "BATTERY_ACTION_FIELDS",
    "BATTERY_ASSET_ID",
    "BATTERY_SCHEMA_VERSION",
    "DEFAULT_AUTARKY_WEIGHT_PER_KWH",
    "DEFAULT_BMS_DRIFT_PCT",
    "DEFAULT_DEGRADATION_PER_KWH",
    "DEFAULT_EFFICIENCY",
    "DEFAULT_GRID_CHARGE_MARGIN_PER_KWH",
    "DEFAULT_RESERVE_PCT",
    "FIELD_CAPABILITY",
    "BatteryAction",
    "BatteryCapability",
    "BatteryModel",
    "BatteryRanges",
    "BatterySettings",
    "BatteryStrategy",
    "CalibrationCharge",
    "FieldRange",
    "GridChargePolicy",
    "ModbusByteOrder",
    "ModbusLink",
    "RiskLevel",
    "action_fields",
    "effective_reserve_pct",
    "resolve_battery_action",
    "round_up_to_step",
]
