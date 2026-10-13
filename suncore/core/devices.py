# SPDX-License-Identifier: Apache-2.0
"""Device instance model: one controllable consumer as configured by the user.

A :class:`DeviceConfig` is what the wizard stores (``devices`` config section) and
what the Phase 3 controller consumes: surplus allocation by ``priority``, hysteresis
and protection times (``min_run_s``, ``min_pause_s``, ``max_switches_per_hour``),
comfort limits and time windows. Entity ids come from the user; none are hard-coded.
The operating mode is *not* part of the device: the ``operation`` config section is the
single store of global and per-device mode (ADR-0012 item 10).

Sign convention (``core/units.py``): device power + = consumption.
"""

from __future__ import annotations

import itertools
from datetime import time
from enum import StrEnum, unique
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from suncore.core.site import EntityRef

DEVICE_ID_PATTERN: Final = r"^[a-z][a-z0-9_]{0,47}$"
ROLE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"
TEMPLATE_ID_PATTERN: Final = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
"""``<vendor>.<model>``, lowercase, e.g. ``lg.thinq_climate``, ``generic.switch``."""

DeviceId = Annotated[str, StringConstraints(pattern=DEVICE_ID_PATTERN, max_length=48)]
TemplateId = Annotated[str, StringConstraints(pattern=TEMPLATE_ID_PATTERN, max_length=64)]
RoleName = Annotated[str, StringConstraints(pattern=ROLE_PATTERN)]


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


THERMAL_KINDS: Final[frozenset[DeviceKind]] = frozenset(
    {DeviceKind.CLIMATE, DeviceKind.WATER_HEATER, DeviceKind.HEAT_PUMP}
)
"""Device kinds with a comfort band (temperature)."""


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
    """Power source of a device; refers to roles of :attr:`DeviceConfig.entities`.

    * ``measured``: needs ``power_role`` (a power sensor).
    * ``counter_derived``: needs ``energy_role``; the average power is derived from counter
      deltas (latency and quantisation apply, ``counter_update_s`` is the latency floor).
    * ``learned``: needs ``nominal_w`` as prior; ``power_role``/``energy_role`` train it.
    * ``nominal``: needs ``nominal_w``.

    The unit of the counter is read from the live state (like ``core/balance.py``); a
    non-energy unit yields quality ``invalid`` at runtime.
    """

    kind: PowerModelKind
    power_role: RoleName | None = None
    energy_role: RoleName | None = None
    nominal_w: Annotated[float, Field(gt=0, le=1_000_000)] | None = None
    counter_update_s: Annotated[int, Field(ge=1, le=86_400)] | None = None
    """Expected update cadence of the counter (LG: 3600); ``None`` = unknown, measured."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        kind = self.kind
        if kind is PowerModelKind.MEASURED and self.power_role is None:
            msg = "power_model: kind `measured` needs `power_role`"
            raise ValueError(msg)
        if kind is PowerModelKind.COUNTER_DERIVED and self.energy_role is None:
            msg = "power_model: kind `counter_derived` needs `energy_role`"
            raise ValueError(msg)
        if kind in (PowerModelKind.NOMINAL, PowerModelKind.LEARNED) and self.nominal_w is None:
            msg = f"power_model: kind `{kind.value}` needs `nominal_w`"
            raise ValueError(msg)
        if self.counter_update_s is not None and self.energy_role is None:
            msg = "power_model: `counter_update_s` without `energy_role`"
            raise ValueError(msg)
        return self


# --- control ------------------------------------------------------------------


class Step(_Frozen):
    """One level of a stepped load: the value written and the power it draws."""

    value: str | int | float
    power_w: Annotated[float, Field(ge=0, le=1_000_000)]


class Mode(_Frozen):
    """One mode of a mode-controlled device and the power it draws."""

    value: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    power_w: Annotated[float, Field(ge=0, le=1_000_000)]


class OnOffControl(_Frozen):
    """Switch on/off through ``role``."""

    kind: Literal["on_off"]
    role: RoleName


class StepsControl(_Frozen):
    """Discrete power levels written to ``role`` (select option or number)."""

    kind: Literal["steps"]
    role: RoleName
    steps: Annotated[tuple[Step, ...], Field(min_length=2, max_length=16)]

    @model_validator(mode="after")
    def _increasing(self) -> Self:
        powers = [s.power_w for s in self.steps]
        if any(a >= b for a, b in itertools.pairwise(powers)):
            msg = "control.steps: `power_w` must be strictly increasing"
            raise ValueError(msg)
        return self


class ContinuousControl(_Frozen):
    """A number entity in W, A or % of ``nominal_w`` between ``min`` and ``max``."""

    kind: Literal["continuous"]
    role: RoleName
    unit: Literal["W", "A", "%"]
    min: Annotated[float, Field(ge=0)]
    max: Annotated[float, Field(gt=0)]
    step: Annotated[float, Field(gt=0)] | None = None
    voltage_v: Annotated[float, Field(gt=0, le=1000)] = 230.0
    phases: Literal[1, 3] = 1

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if not self.min < self.max:
            msg = f"control: min ({self.min}) must be below max ({self.max})"
            raise ValueError(msg)
        if self.unit != "A" and (self.voltage_v != 230.0 or self.phases != 1):
            msg = "control: `voltage_v` and `phases` are only valid for unit `A`"
            raise ValueError(msg)
        if self.unit == "%" and self.max > 100:
            msg = "control: unit `%` allows at most 100"
            raise ValueError(msg)
        return self


class SetpointControl(_Frozen):
    """Shift the user's temperature set-point inside the comfort band (needs ``comfort``)."""

    kind: Literal["setpoint"]
    role: RoleName
    shift_max_c: Annotated[float, Field(gt=0, le=10)]
    step_c: Annotated[float, Field(gt=0, le=5)] = 0.5


class ModeControl(_Frozen):
    """Select one of several modes (select entity or climate preset)."""

    kind: Literal["mode"]
    role: RoleName
    modes: Annotated[tuple[Mode, ...], Field(min_length=2, max_length=16)]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        values = [m.value for m in self.modes]
        if len(set(values)) != len(values):
            msg = "control.modes: values must be unique"
            raise ValueError(msg)
        return self


ControlSpec = Annotated[
    OnOffControl | StepsControl | ContinuousControl | SetpointControl | ModeControl,
    Field(discriminator="kind"),
]
"""How a device is driven and which role is written (ADR-0012 item 5)."""


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
    mode: Literal["allow", "block"] = "allow"
    """``allow`` windows are united, ``block`` windows (quiet hours) are subtracted."""

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
    """One configured device (instance of a template).

    ``max_switches_per_hour`` / hysteresis / ``settle_s`` ``None`` mean "safety/controller
    default", never "unlimited" or "none" (ADR-0012 item 6).
    """

    id: DeviceId
    template_id: TemplateId
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    kind: DeviceKind
    entities: dict[RoleName, EntityRef] = Field(default_factory=dict)
    power_model: PowerModel
    control: ControlSpec
    priority: Annotated[int, Field(ge=0, le=10_000)] = 0
    """Lower value = served first."""
    min_run_s: Annotated[int, Field(ge=0, le=86_400)] = 0
    min_pause_s: Annotated[int, Field(ge=0, le=86_400)] = 0
    max_switches_per_hour: Annotated[int, Field(ge=1, le=3600)] | None = None
    hysteresis_w: Annotated[float, Field(ge=0, le=1_000_000)] | None = None
    hysteresis_delay_s: Annotated[int, Field(ge=0, le=3600)] | None = None
    settle_s: Annotated[int, Field(ge=0, le=600)] | None = None
    """Delay before the read-back after a write."""
    comfort: ComfortBand | None = None
    windows: tuple[TimeWindow, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        seen: dict[str, str] = {}
        for role, ref in self.entities.items():
            if ref.entity_id in seen:
                msg = f"entities: {ref.entity_id} is mapped to `{seen[ref.entity_id]}` and `{role}`"
                raise ValueError(msg)
            seen[ref.entity_id] = role
        if self.comfort is not None and self.kind not in THERMAL_KINDS:
            msg = f"comfort: not applicable to kind `{self.kind.value}`"
            raise ValueError(msg)
        pm = self.power_model
        for field, pm_role in (("power_role", pm.power_role), ("energy_role", pm.energy_role)):
            if pm_role is not None and pm_role not in self.entities:
                msg = f"power_model.{field}: role `{pm_role}` is not mapped in `entities`"
                raise ValueError(msg)
        if pm.energy_role is not None and self.entities[pm.energy_role].invert_sign:
            msg = "power_model.energy_role: invert_sign is meaningless for a counter"
            raise ValueError(msg)
        if self.control.role not in self.entities:
            msg = f"control.role: role `{self.control.role}` is not mapped in `entities`"
            raise ValueError(msg)
        if isinstance(self.control, SetpointControl) and self.comfort is None:
            msg = "control: kind `setpoint` needs `comfort`"
            raise ValueError(msg)
        if (
            isinstance(self.control, ContinuousControl)
            and self.control.unit == "%"
            and pm.nominal_w is None
        ):
            msg = "control: unit `%` is relative to `power_model.nominal_w`, which is missing"
            raise ValueError(msg)
        return self

    def entity_refs(self) -> list[EntityRef]:
        """All referenced entities, for subscriptions."""
        return list(self.entities.values())
