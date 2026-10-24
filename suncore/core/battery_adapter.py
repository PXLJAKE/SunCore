# SPDX-License-Identifier: Apache-2.0
"""What a battery adapter needs from its template, as plain core data (Phase 4 WP10).

``suncore.devices`` imports only ``core`` (``tests/devices/test_imports.py``), so the battery
adapters cannot read ``suncore.templates`` models. The template converts itself into a
:class:`BatteryAdapterSpec` (``BatteryTemplate.adapter_spec()``) and the composition root hands
that to ``suncore.devices.battery.create_battery_driver``. The vocabulary
(:class:`BatteryModeMeaning`, the ``kostal_modbus`` roles) lives here; the template models re-use
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final, Literal

BatteryDriverKind = Literal["generic", "kostal_plenticore", "kostal_modbus"]
"""Which adapter executes a battery template."""

LimitKind = Literal["min_soc", "max_charge", "max_discharge", "grid_charge_power"]
LimitUnit = Literal["W", "kW", "%"]


@unique
class BatteryModeMeaning(StrEnum):
    """What selecting a battery mode option does."""

    AUTO = "auto"
    """The inverter's own control (the release baseline)."""
    FORCE_CHARGE = "force_charge"
    """Charge, also from the grid."""
    NO_DISCHARGE = "no_discharge"
    NO_CHARGE = "no_charge"
    OTHER = "other"
    """Any other option; SunCore never selects it."""


KOSTAL_MODBUS_ROLES: Final = (
    "external_control",
    "charge_limit",
    "discharge_limit",
    "fallback_charge_limit",
    "fallback_discharge_limit",
    "fallback_timeout",
    "own_charge_limit",
    "own_discharge_limit",
)
"""Read-back roles the ``kostal_modbus`` driver needs: modbus sensors of the registers of the
register map (1080, 1280, 1282, 1284, 1286, 1288) and of the battery's own limits (1076, 1078)."""


@dataclass(frozen=True, slots=True)
class NumberLimitSpec:
    """A ``number`` role that sets one limit (``%`` of the battery's maximum power for the
    power kinds when ``unit`` is ``%``)."""

    role: str
    kind: LimitKind
    unit: LimitUnit
    min: float
    max: float
    step: float | None = None


@dataclass(frozen=True, slots=True)
class ModeOptionSpec:
    value: str
    meaning: BatteryModeMeaning


@dataclass(frozen=True, slots=True)
class ModeSelectSpec:
    role: str
    options: tuple[ModeOptionSpec, ...]


@dataclass(frozen=True, slots=True)
class ModbusRegisterSpec:
    """Register map of a limit-with-fallback interface (Kostal §3.5)."""

    max_charge: int
    max_discharge: int
    fallback_max_charge: int
    fallback_max_discharge: int
    fallback_timeout: int
    fallback_timeout_min_s: int
    fallback_timeout_max_s: int
    external_control_register: int
    external_control_value: int


@dataclass(frozen=True, slots=True)
class BatteryAdapterSpec:
    """The template, as far as an adapter is concerned."""

    template_id: str
    driver: BatteryDriverKind
    settle_s: float | None = None
    numbers: tuple[NumberLimitSpec, ...] = ()
    mode: ModeSelectSpec | None = None
    force_switch_role: str | None = None
    modbus: ModbusRegisterSpec | None = None

    def control_kinds(self) -> frozenset[str]:
        kinds: set[str] = {n.kind for n in self.numbers}
        if self.mode is not None:
            kinds.add("mode")
        if self.force_switch_role is not None:
            kinds.add("force_charge")
        return frozenset(kinds)


__all__ = [
    "KOSTAL_MODBUS_ROLES",
    "BatteryAdapterSpec",
    "BatteryDriverKind",
    "BatteryModeMeaning",
    "LimitKind",
    "LimitUnit",
    "ModbusRegisterSpec",
    "ModeOptionSpec",
    "ModeSelectSpec",
    "NumberLimitSpec",
]
