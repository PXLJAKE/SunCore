# SPDX-License-Identifier: Apache-2.0
"""Device instance model: one controllable consumer as configured by the user.

A :class:`DeviceConfig` is what the wizard stores (``devices`` config section) and
what the Phase 3 controller consumes: surplus allocation by ``priority``, hysteresis
and protection times (``min_run_s``, ``min_pause_s``, ``max_switches_per_hour``),
comfort limits and time windows. Entity ids come from the user; none are hard-coded.
The per-device :class:`~suncore.core.decisions.OperatingMode` defaults to shadow and is
not read by the safety gate before Phase 3.

Sign convention (``core/units.py``): device power + = consumption.
"""

from __future__ import annotations

from datetime import time
from enum import StrEnum, unique
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from suncore.core.decisions import OperatingMode
from suncore.core.site import EntityRef
from suncore.core.units import Dimension, Unit

DEVICE_ID_PATTERN: Final = r"^[a-z][a-z0-9_]{0,47}$"
ROLE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"
TEMPLATE_ID_PATTERN: Final = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
"""``<vendor>.<model>``, lowercase, e.g. ``lg.thinq_climate``, ``generic.switch``."""

DeviceId = Annotated[str, StringConstraints(pattern=DEVICE_ID_PATTERN)]
TemplateId = Annotated[str, StringConstraints(pattern=TEMPLATE_ID_PATTERN)]
RoleName = Annotated[str, StringConstraints(pattern=ROLE_PATTERN)]

_THERMAL_KINDS: Final = frozenset({"climate", "water_heater", "heat_pump"})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@unique
class DeviceKind(StrEnum):
    """Kind of controllable device. Templates use climate, switch and number_load for now."""

    CLIMATE = "climate"
    SWITCH = "switch"
    NUMBER_LOAD = "number_load"
    WATER_HEATER = "water_heater"
    HEAT_PUMP = "heat_pump"
    WALLBOX = "wallbox"
    PROGRAM = "program"
    GENERIC = "generic"


@unique
class PowerModelKind(StrEnum):
    """How a device's power is obtained (BRIEF §4.5); each kind yields an uncertain estimate."""

    MEASURED = "measured"
    COUNTER_DERIVED = "counter_derived"
    LEARNED = "learned"
    NOMINAL = "nominal"


@unique
class ControlKind(StrEnum):
    """How a device is driven."""

    ON_OFF = "on_off"
    STEPS = "steps"
    CONTINUOUS = "continuous"
    SETPOINT = "setpoint"
    MODE = "mode"


class PowerModel(_Frozen):
    """Power source of a device.

    * ``measured``: needs ``power`` (a power sensor).
    * ``counter_derived``: needs ``energy_counter`` and its ``energy_unit``; the
      average power is derived from counter deltas (latency and quantisation apply).
    * ``learned``: needs ``nominal_w`` as prior; ``power``/``energy_counter`` optionally train it.
    * ``nominal``: needs ``nominal_w``.
    """

    kind: PowerModelKind
    power: EntityRef | None = None
    energy_counter: EntityRef | None = None
    energy_unit: Unit | None = None
    nominal_w: Annotated[float, Field(gt=0, le=1_000_000)] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        kind = self.kind
        if self.energy_counter is not None:
            if self.energy_unit is None or self.energy_unit.dimension is not Dimension.ENERGY:
                msg = "power_model: `energy_unit` must be Wh or kWh when `energy_counter` is set"
                raise ValueError(msg)
            if self.energy_counter.invert_sign:
                msg = "power_model.energy_counter: invert_sign is meaningless for a counter"
                raise ValueError(msg)
        elif self.energy_unit is not None:
            msg = "power_model: `energy_unit` without `energy_counter`"
            raise ValueError(msg)
        if kind is PowerModelKind.MEASURED and self.power is None:
            msg = "power_model: kind `measured` needs `power`"
            raise ValueError(msg)
        if kind is PowerModelKind.COUNTER_DERIVED and self.energy_counter is None:
            msg = "power_model: kind `counter_derived` needs `energy_counter` and `energy_unit`"
            raise ValueError(msg)
        if kind in (PowerModelKind.NOMINAL, PowerModelKind.LEARNED) and self.nominal_w is None:
            msg = f"power_model: kind `{kind.value}` needs `nominal_w`"
            raise ValueError(msg)
        return self


class ComfortBand(_Frozen):
    """Allowed temperature range in °C."""

    min_c: Annotated[float, Field(ge=-50, le=150)]
    max_c: Annotated[float, Field(ge=-50, le=150)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.min_c < self.max_c:
            msg = f"comfort: min_c ({self.min_c}) must be below max_c ({self.max_c})"
            raise ValueError(msg)
        return self


class TimeWindow(_Frozen):
    """Local-time window on given weekdays (0 = Monday ... 6 = Sunday).

    ``start`` > ``end`` wraps over midnight (e.g. 22:00-06:00). ``start == end`` is rejected.
    """

    start: time
    end: time
    days: tuple[Annotated[int, Field(ge=0, le=6)], ...] = (0, 1, 2, 3, 4, 5, 6)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.start == self.end:
            msg = "window: start and end must differ"
            raise ValueError(msg)
        if not self.days:
            msg = "window: at least one weekday is required"
            raise ValueError(msg)
        if len(set(self.days)) != len(self.days):
            msg = "window: weekdays must be unique"
            raise ValueError(msg)
        return self


class DeviceConfig(_Frozen):
    """One configured device (instance of a template)."""

    id: DeviceId
    template_id: TemplateId
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    kind: DeviceKind
    entities: dict[RoleName, EntityRef] = Field(default_factory=dict)
    power_model: PowerModel
    control: ControlKind
    priority: Annotated[int, Field(ge=0, le=10_000)] = 0
    """Lower value = served first."""
    min_run_s: Annotated[int, Field(ge=0, le=86_400)] = 0
    min_pause_s: Annotated[int, Field(ge=0, le=86_400)] = 0
    max_switches_per_hour: Annotated[int, Field(ge=1, le=3600)] | None = None
    comfort: ComfortBand | None = None
    windows: tuple[TimeWindow, ...] = ()
    mode: OperatingMode = OperatingMode.SHADOW

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        seen: dict[str, str] = {}
        for role, ref in self.entities.items():
            if ref.entity_id in seen:
                msg = f"entities: {ref.entity_id} is mapped to `{seen[ref.entity_id]}` and `{role}`"
                raise ValueError(msg)
            seen[ref.entity_id] = role
        if self.comfort is not None and self.kind.value not in _THERMAL_KINDS:
            msg = f"comfort: not applicable to kind `{self.kind.value}`"
            raise ValueError(msg)
        return self

    def entity_refs(self) -> list[EntityRef]:
        """All referenced entities (role mapping and power model), for subscriptions."""
        refs = list(self.entities.values())
        pm = self.power_model
        refs.extend(r for r in (pm.power, pm.energy_counter) if r is not None)
        return refs
