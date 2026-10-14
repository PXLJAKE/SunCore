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
from suncore.core.validation import invalid

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
            raise invalid("power_model_measured", "power_model: kind `measured` needs `power_role`")
        if kind is PowerModelKind.COUNTER_DERIVED and self.energy_role is None:
            raise invalid(
                "power_model_counter", "power_model: kind `counter_derived` needs `energy_role`"
            )
        if kind in (PowerModelKind.NOMINAL, PowerModelKind.LEARNED) and self.nominal_w is None:
            raise invalid(
                "power_model_nominal",
                "power_model: kind `{kind}` needs `nominal_w`",
                kind=kind.value,
            )
        if self.counter_update_s is not None and self.energy_role is None:
            raise invalid(
                "counter_update_without_role",
                "power_model: `counter_update_s` without `energy_role`",
            )
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
            raise invalid("steps_order", "control.steps: `power_w` must be strictly increasing")
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
            raise invalid(
                "control_range",
                "control: min ({min}) must be below max ({max})",
                min=self.min,
                max=self.max,
            )
        if self.unit != "A" and (self.voltage_v != 230.0 or self.phases != 1):
            raise invalid(
                "control_amps_only", "control: `voltage_v` and `phases` are only valid for unit `A`"
            )
        if self.unit == "%" and self.max > 100:
            raise invalid("control_percent_max", "control: unit `%` allows at most 100")
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
            raise invalid("modes_unique", "control.modes: values must be unique")
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
            raise invalid(
                "comfort_order",
                "comfort: min_c ({min_c}) must be below max_c ({max_c})",
                min_c=self.min_c,
                max_c=self.max_c,
            )
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
            raise invalid("window_empty", "window: start and end must differ")
        if not self.days:
            raise invalid("window_no_days", "window: at least one weekday is required")
        if len(set(self.days)) != len(self.days):
            raise invalid("window_days_unique", "window: weekdays must be unique")
        return self


HVAC_MODES: Final[tuple[str, ...]] = ("heat", "cool", "heat_cool", "auto", "dry", "fan_only")
"""HA ``HVACMode`` values besides ``off`` (verified in research/ha-websocket-api.md)."""

SWITCH_ON_HVAC_MODES: Final[tuple[str, ...]] = ("heat", "cool", "heat_cool", "auto")
"""HVAC modes SunCore may use to switch a climate device on (never ``dry``/``fan_only``)."""


class OverridePolicy(_Frozen):
    """How long a manual change is respected before SunCore takes over again (plan §6.5).

    * ``next_day``: at ``resume_time`` (local) of the next day (default 04:00);
    * ``hours``: ``hours`` after the override was detected (1-48).
    """

    kind: Literal["next_day", "hours"] = "next_day"
    resume_time: time = time(4, 0)
    hours: Annotated[int, Field(ge=1, le=48)] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind == "hours" and self.hours is None:
            raise invalid("override_hours", "override_policy: kind `hours` needs `hours`")
        if self.kind == "next_day" and self.hours is not None:
            raise invalid("override_next_day", "override_policy: kind `next_day` takes no `hours`")
        return self


class DeviceConfig(_Frozen):
    """One configured device (instance of a template).

    ``max_switches_per_hour`` / hysteresis / ``settle_s`` ``None`` mean "safety/controller
    default", never "unlimited" or "none" (ADR-0012 item 6).

    Phase 3 additions (all with defaults, old documents stay valid): ``allow_switch_on``
    and ``switch_on_hvac_mode`` (climate only: SunCore may switch the unit on through the
    HVAC mode; a set-point write while the unit is off also switches it on, see
    research/device-integrations.md), ``override_policy``.
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
    allow_switch_on: bool = False
    """Climate: SunCore may switch the unit on (opt-in per device, plan §6.4)."""
    switch_on_hvac_mode: Literal["heat", "cool", "heat_cool", "auto"] | None = None
    """Climate: the HVAC mode used for switching on; required with ``allow_switch_on``."""
    override_policy: OverridePolicy = OverridePolicy()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.allow_switch_on and self.kind is not DeviceKind.CLIMATE:
            raise invalid(
                "switch_on_not_climate",
                "allow_switch_on: only climate devices switch on through an HVAC mode",
            )
        if self.allow_switch_on and self.switch_on_hvac_mode is None:
            raise invalid("switch_on_needs_mode", "allow_switch_on: needs `switch_on_hvac_mode`")
        if self.switch_on_hvac_mode is not None and self.kind is not DeviceKind.CLIMATE:
            raise invalid(
                "hvac_mode_not_climate", "switch_on_hvac_mode: only valid for climate devices"
            )
        seen: dict[str, str] = {}
        for role, ref in self.entities.items():
            if ref.entity_id in seen:
                raise invalid(
                    "entity_mapped_twice",
                    "entities: {entity_id} is mapped to `{first}` and `{second}`",
                    entity_id=ref.entity_id,
                    first=seen[ref.entity_id],
                    second=role,
                )
            seen[ref.entity_id] = role
        if self.comfort is not None and self.kind not in THERMAL_KINDS:
            raise invalid(
                "comfort_not_applicable",
                "comfort: not applicable to kind `{kind}`",
                kind=self.kind.value,
            )
        pm = self.power_model
        for field, pm_role in (("power_role", pm.power_role), ("energy_role", pm.energy_role)):
            if pm_role is not None and pm_role not in self.entities:
                raise invalid(
                    "power_role_unmapped",
                    "power_model.{field}: role `{role}` is not mapped in `entities`",
                    field=field,
                    role=pm_role,
                )
        if pm.energy_role is not None and self.entities[pm.energy_role].invert_sign:
            raise invalid(
                "counter_invert",
                "power_model.energy_role: invert_sign is meaningless for a counter",
            )
        if self.control.role not in self.entities:
            raise invalid(
                "control_role_unmapped",
                "control.role: role `{role}` is not mapped in `entities`",
                role=self.control.role,
            )
        if isinstance(self.control, SetpointControl) and self.comfort is None:
            raise invalid("setpoint_needs_comfort", "control: kind `setpoint` needs `comfort`")
        if (
            isinstance(self.control, ContinuousControl)
            and self.control.unit == "%"
            and pm.nominal_w is None
        ):
            raise invalid(
                "percent_needs_nominal",
                "control: unit `%` is relative to `power_model.nominal_w`, which is missing",
            )
        return self

    def entity_refs(self) -> list[EntityRef]:
        """All referenced entities, for subscriptions."""
        return list(self.entities.values())
