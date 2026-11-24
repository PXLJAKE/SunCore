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

from suncore.core.flex import EV_MODE_DEFAULT, EvMode
from suncore.core.site import EntityRef
from suncore.core.thermal import CopCurve, LearnedThermal
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
    RELAYS = "relays"
    # Phase 6 (ADR-0017, PR 6-B; every write of these is rejected by the gate until its PR):
    CHARGER = "charger"
    SG_READY = "sg_ready"
    LOADPOINT = "loadpoint"


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
    power_update_s: Annotated[int, Field(ge=1, le=86_400)] | None = None
    """Expected update period of the power sensor when it is slow (``smartthinq_sensors``
    ``energy_current``: 300). ``None`` = unknown: the sensor is assumed to be seen at least once
    per client resync (60 s). The watchdog and the entity cache judge staleness against it."""

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
        if self.power_update_s is not None and self.power_role is None:
            raise invalid(
                "power_update_without_role",
                "power_model: `power_update_s` without `power_role`",
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


class RelayStep(_Frozen):
    """One level of a relay-switched load: the relays that are on and the power it draws."""

    relays: Annotated[tuple[RoleName, ...], Field(max_length=3)] = ()
    """Roles of the relays that are on at this step (empty = all off)."""
    power_w: Annotated[float, Field(ge=0, le=1_000_000)]


class RelaysControl(_Frozen):
    """Discrete power levels made of 1-3 switch entities (e.g. a heater with three elements).

    ``roles`` are the relay switches; step ``i`` means exactly the relays listed in
    ``steps[i].relays`` are on and the others are off. Step 0 is the lowest level
    (normally all off). Added to schema v1 as an additive union member (ADR-0012).
    """

    kind: Literal["relays"]
    roles: Annotated[tuple[RoleName, ...], Field(min_length=1, max_length=3)]
    steps: Annotated[tuple[RelayStep, ...], Field(min_length=2, max_length=8)]

    @property
    def role(self) -> RoleName:
        """The first relay: the primary control entity (identity, duplicate-unit check)."""
        return self.roles[0]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if len(set(self.roles)) != len(self.roles):
            raise invalid("relays_unique", "control.roles: relay roles must be unique")
        powers = [s.power_w for s in self.steps]
        if any(a >= b for a, b in itertools.pairwise(powers)):
            raise invalid("steps_order", "control.steps: `power_w` must be strictly increasing")
        for step in self.steps:
            unknown = [r for r in step.relays if r not in self.roles]
            if unknown:
                raise invalid(
                    "relay_step_unknown",
                    "control.steps: relay `{role}` is not in `roles`",
                    role=unknown[0],
                )
            if len(set(step.relays)) != len(step.relays):
                raise invalid("relay_step_unique", "control.steps: a relay is listed twice")
        sets = [frozenset(s.relays) for s in self.steps]
        if len(set(sets)) != len(sets):
            raise invalid("relay_step_distinct", "control.steps: two steps switch the same relays")
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


class ChargerControl(_Frozen):
    """An EV charger driven through Home Assistant entities (ADR-0017 decision 1, PR 6-I).

    ``role`` is the charging current (number entity, A); ``enable_role`` an optional switch;
    ``phases_role`` an optional select or switch whose option for one and for three phases is
    ``phase_option_1`` / ``phase_option_3``. ``phases`` is the number of phases the charger is
    wired with; ``phase_switching`` needs a ``phases_role`` and a three-phase charger (plan §0
    decision 6). ``phase_switch_pause_s`` is the pause around a switch (vendor-dependent,
    ``UNVERIFIED``), ``max_phase_switches_per_hour`` the wear limit. Written by ``ChargeAction``.
    """

    kind: Literal["charger"]
    role: RoleName
    enable_role: RoleName | None = None
    phases_role: RoleName | None = None
    phase_option_1: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    phase_option_3: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    min_current_a: Annotated[float, Field(ge=1, le=80, allow_inf_nan=False)] = 6.0
    max_current_a: Annotated[float, Field(gt=1, le=500, allow_inf_nan=False)]
    phases: Literal[1, 3] = 3
    phase_switching: bool = False
    voltage_v: Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)] = 230.0
    phase_switch_pause_s: Annotated[int, Field(ge=0, le=900)] = 120
    max_phase_switches_per_hour: Annotated[int, Field(ge=1, le=12)] = 4

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if not self.min_current_a < self.max_current_a:
            raise invalid(
                "charger_current_range",
                "control: min_current_a ({min}) must be below max_current_a ({max})",
                min=self.min_current_a,
                max=self.max_current_a,
            )
        roles = [r for r in (self.role, self.enable_role, self.phases_role) if r is not None]
        if len(set(roles)) != len(roles):
            raise invalid("charger_roles_unique", "control: charger roles must be distinct")
        options = (self.phase_option_1, self.phase_option_3)
        if self.phases_role is None and any(o is not None for o in options):
            raise invalid(
                "charger_phase_options_no_role",
                "control: phase options need `phases_role`",
            )
        if self.phases_role is not None and (
            None in options or self.phase_option_1 == self.phase_option_3
        ):
            raise invalid(
                "charger_phase_options",
                "control: `phases_role` needs two different options for 1 and 3 phases",
            )
        if self.phase_switching and (self.phases_role is None or self.phases != 3):
            raise invalid(
                "charger_phase_switching",
                "control: phase switching needs `phases_role` and a three-phase charger",
            )
        return self

    def max_power_w(self) -> float:
        """The most the charger draws (all wired phases, maximum current)."""
        return self.max_current_a * self.voltage_v * self.phases

    def min_power_w(self, phases: Literal[1, 3]) -> float:
        """The least it draws while charging on ``phases`` (the 6 A floor)."""
        return self.min_current_a * self.voltage_v * phases


class SgContacts(_Frozen):
    """The two SG-Ready contacts of one state (``True`` = closed / switch on)."""

    a: bool
    b: bool


class SgReadyControl(_Frozen):
    """SG-Ready heat pump interface: two contact switches (ADR-0017 decision 5, PR 6-K).

    Written with ``StepAction``: step 0 = SG state 2 (normal, the baseline and release),
    1 = state 3 (intensified), 2 = state 4 (forced on, only with ``allow_forced``). **State 1
    (utility block) has no step and is never written.** Which contact pair selects which state
    is vendor-specific: the template states it with a ``source`` (``UNVERIFIED`` otherwise).
    """

    kind: Literal["sg_ready"]
    contact_a: RoleName
    contact_b: RoleName
    normal: SgContacts
    intensified: SgContacts
    forced: SgContacts | None = None
    allow_forced: bool = False
    min_dwell_s: Annotated[int, Field(ge=600, le=86_400)] = 600
    """Minimum time in one SG state (plan §0 decision 9: at least 10 min)."""

    @property
    def role(self) -> RoleName:
        """The first contact: the primary control entity (duplicate-unit check)."""
        return self.contact_a

    @property
    def steps(self) -> tuple[SgContacts, ...]:
        """The writable states in step order (2, 3 and, if allowed, 4)."""
        states = (self.normal, self.intensified)
        return (*states, self.forced) if self.allow_forced and self.forced is not None else states

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.contact_a == self.contact_b:
            raise invalid("sg_contacts_unique", "control: the two SG-Ready contacts must differ")
        pairs = [p for p in (self.normal, self.intensified, self.forced) if p is not None]
        if len(set(pairs)) != len(pairs):
            raise invalid("sg_states_distinct", "control: two SG-Ready states share contacts")
        if self.allow_forced and self.forced is None:
            raise invalid(
                "sg_forced_missing", "control: `allow_forced` needs the `forced` contacts"
            )
        return self


EVCC_TOPIC_PATTERN: Final = r"^[A-Za-z0-9_-]{1,32}(/[A-Za-z0-9_-]{1,32}){0,3}$"


class LoadpointControl(_Frozen):
    """An evcc loadpoint driven over evcc's MQTT API (ADR-0017 decision 2, PR 6-J).

    No Home Assistant entity is written; ``topic_prefix`` (evcc default ``evcc``) and
    ``loadpoint_id`` address the loadpoint, ``vehicle`` the evcc vehicle name for plans.
    Loadpoint ids start at 1 in the evcc source read on 2026-11-24 (``server/mqtt.go``).
    """

    kind: Literal["loadpoint"]
    loadpoint_id: Annotated[int, Field(ge=1, le=64)]
    vehicle: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,64}$")] | None = None
    topic_prefix: Annotated[str, StringConstraints(pattern=EVCC_TOPIC_PATTERN)] = "evcc"
    max_current_a: Annotated[float, Field(gt=0, le=500, allow_inf_nan=False)] = 16.0
    phases: Literal[1, 3] = 3
    voltage_v: Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)] = 230.0


ControlSpec = Annotated[
    OnOffControl
    | StepsControl
    | ContinuousControl
    | SetpointControl
    | ModeControl
    | RelaysControl
    | ChargerControl
    | SgReadyControl
    | LoadpointControl,
    Field(discriminator="kind"),
]
"""How a device is driven and which role is written (ADR-0012 item 5)."""


PHASE6_CONTROLS: Final = (ChargerControl, SgReadyControl, LoadpointControl)
"""Control variants of Phase 6 whose writes the gate rejects until their PR (6-I, 6-J, 6-K)."""


def control_roles(control: ControlSpec) -> tuple[RoleName, ...]:
    """All entity roles a control writes (one for most; none for an evcc loadpoint)."""
    if isinstance(control, RelaysControl):
        return control.roles
    if isinstance(control, ChargerControl):
        return tuple(
            r for r in (control.role, control.enable_role, control.phases_role) if r is not None
        )
    if isinstance(control, SgReadyControl):
        return (control.contact_a, control.contact_b)
    if isinstance(control, LoadpointControl):
        return ()
    return (control.role,)


def step_power_w(control: ControlSpec, step: int) -> float | None:
    """Power of step ``step`` of a ``steps`` or ``relays`` control (``None`` = not stepped)."""
    if isinstance(control, StepsControl | RelaysControl) and 0 <= step < len(control.steps):
        return control.steps[step].power_w
    return None


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

RESUME_TIME_DEFAULT: Final = time(4, 0)
"""Override policy ``next_day``: SunCore takes over again at this local time."""


class OverridePolicy(_Frozen):
    """How long a manual change is respected before SunCore takes over again (plan §6.5).

    * ``next_day``: at ``resume_time`` (local) of the next day (default 04:00);
    * ``hours``: ``hours`` after the override was detected (1-48).
    """

    kind: Literal["next_day", "hours"] = "next_day"
    resume_time: time = RESUME_TIME_DEFAULT
    hours: Annotated[int, Field(ge=1, le=48)] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind == "hours" and self.hours is None:
            raise invalid("override_hours", "override_policy: kind `hours` needs `hours`")
        if self.kind == "next_day" and self.hours is not None:
            raise invalid("override_next_day", "override_policy: kind `next_day` takes no `hours`")
        return self


# --- Phase 6 device options (ADR-0017) ------------------------------------------------------


class EvSpec(_Frozen):
    """The vehicle behind a charger (kind ``wallbox``; one vehicle per charger in v1).

    Roles (all optional, mapped in ``DeviceConfig.entities``): ``vehicle_soc_role`` (sensor %),
    ``connected_role`` (binary sensor), ``charging_role`` (binary sensor). Without a SoC the
    targets are energy-based. ``efficiency`` is the charging efficiency (losses, default 0.9).
    """

    capacity_wh: Annotated[float, Field(gt=0, le=500_000, allow_inf_nan=False)] | None = None
    efficiency: Annotated[float, Field(gt=0.5, le=1, allow_inf_nan=False)] = 0.9
    vehicle_soc_role: RoleName | None = None
    connected_role: RoleName | None = None
    charging_role: RoleName | None = None
    default_mode: EvMode = EV_MODE_DEFAULT

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.vehicle_soc_role is not None and self.capacity_wh is None:
            raise invalid("ev_soc_needs_capacity", "ev: a vehicle SoC needs `capacity_wh`")
        if self.default_mode is EvMode.TARGET:
            raise invalid("ev_default_target", "ev: `target` needs a target, not a default")
        return self

    def roles(self) -> tuple[RoleName, ...]:
        return tuple(
            r
            for r in (self.vehicle_soc_role, self.connected_role, self.charging_role)
            if r is not None
        )


PROFILE_SLOT_MIN: Final = 15


class ProgramSpec(_Frozen):
    """A shiftable program (kind ``program``; ADR-0017 decision 7).

    ``profile_wh`` is the energy per 15 minutes of a run (``None`` = nominal power x run time);
    its length must cover ``runtime_min``. ``start_method``: ``plug`` (a smart plug switched on;
    needs ``resume_confirmed``, the user's statement that the appliance resumes after power
    returns - ``UNVERIFIED`` per appliance), ``remote`` (a ``button``/``switch`` role) or
    ``notify`` (the user is asked to start it). ``idle_w`` is the "finished" threshold.
    """

    runtime_min: Annotated[int, Field(ge=1, le=1440)]
    profile_wh: Annotated[
        tuple[Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)], ...],
        Field(max_length=96),
    ] = ()
    start_method: Literal["plug", "remote", "notify"]
    idle_w: Annotated[float, Field(ge=0, le=500, allow_inf_nan=False)] = 5.0
    resume_confirmed: bool = False

    @model_validator(mode="after")
    def _valid(self) -> Self:
        slots = -(-self.runtime_min // PROFILE_SLOT_MIN)
        if self.profile_wh and len(self.profile_wh) != slots:
            raise invalid(
                "program_profile_length",
                "program: `profile_wh` needs {slots} values for {runtime} min",
                slots=slots,
                runtime=self.runtime_min,
            )
        if self.start_method == "plug" and not self.resume_confirmed:
            raise invalid(
                "program_plug_resume",
                "program: start by plug needs the confirmation that the appliance resumes",
            )
        return self


class ThermalSpec(_Frozen):
    """Heat-pump and thermal-store options (ADR-0017 decision 6; kinds with a comfort band).

    ``cop`` ``None`` = the generic air/water assumption (``core.thermal``); ``learned`` is the
    fitted RC model (written by the learner, PR 6-K). ``grid_preheat`` allows pre-heating
    with cheap grid energy (default off, plan §0 decision 8); the lift band is the device's
    comfort band.
    """

    cop: CopCurve | None = None
    learned: LearnedThermal | None = None
    grid_preheat: bool = False


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
    planner_value_per_kwh: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    """The device's utility for the planner, currency/kWh (Phase 4). ``None`` = automatic:
    just above the export price (self-consumption beats export) and never above the import
    price (SunCore never imports for a device). An explicit value is capped the same way
    by the planner: the import price stays the ceiling."""
    # Phase 6 (PR 6-B, all optional with defaults; behaviour in later PRs):
    ev: EvSpec | None = None
    """Kind ``wallbox`` only."""
    program: ProgramSpec | None = None
    """Kind ``program`` only; a program device with this spec is a job-driven program."""
    thermal: ThermalSpec | None = None
    """Thermal kinds only (heat pump, water heater, climate)."""
    shed_rank: Annotated[int, Field(ge=0, le=10_000)] | None = None
    """Load shedding order (ADR-0018 d. 2): lower is shed first; ``None`` = reverse priority."""
    para14a: bool = False
    """A controllable consumer under §14a EnWG (wallbox, heat pump, cooling, > 4.2 kW)."""
    behaviour_confirmed: bool = False
    """The user confirmed the vendor behaviour the template lists as unverified (plan §0
    decision 17). Part of the device identity: changing it puts the device back in shadow.
    Devices with a Phase 6 control or a program spec run at most assisted until it is true."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:  # noqa: PLR0912
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
        for control_role in control_roles(self.control):
            if control_role not in self.entities:
                raise invalid(
                    "control_role_unmapped",
                    "control.role: role `{role}` is not mapped in `entities`",
                    role=control_role,
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
        self._check_phase6()
        return self

    def _check_phase6(self) -> None:
        """Kind checks of the Phase 6 controls and options (ADR-0017)."""
        control = self.control
        kind = self.kind
        if (
            isinstance(control, ChargerControl | LoadpointControl)
            and kind is not DeviceKind.WALLBOX
        ):
            raise invalid(
                "control_kind_mismatch",
                "control: kind `{control}` needs device kind `{expected}`",
                control=control.kind,
                expected=DeviceKind.WALLBOX.value,
            )
        if isinstance(control, SgReadyControl) and kind is not DeviceKind.HEAT_PUMP:
            raise invalid(
                "control_kind_mismatch",
                "control: kind `{control}` needs device kind `{expected}`",
                control=control.kind,
                expected=DeviceKind.HEAT_PUMP.value,
            )
        for field, value, kinds in (
            ("ev", self.ev, frozenset({DeviceKind.WALLBOX})),
            ("program", self.program, frozenset({DeviceKind.PROGRAM})),
            ("thermal", self.thermal, THERMAL_KINDS),
        ):
            if value is not None and kind not in kinds:
                raise invalid(
                    "option_not_applicable",
                    "{field}: not applicable to kind `{kind}`",
                    field=field,
                    kind=kind.value,
                )
        if self.ev is not None:
            for role in self.ev.roles():
                if role not in self.entities:
                    raise invalid(
                        "ev_role_unmapped",
                        "ev: role `{role}` is not mapped in `entities`",
                        role=role,
                    )
        if (
            self.program is not None
            and self.program.start_method != "notify"
            and not isinstance(control, OnOffControl)
        ):
            raise invalid(
                "program_needs_on_off",
                "program: start method `{method}` needs an on_off control",
                method=self.program.start_method,
            )

    def entity_refs(self) -> list[EntityRef]:
        """All referenced entities, for subscriptions."""
        return list(self.entities.values())


def needs_behaviour_confirmation(device: DeviceConfig) -> bool:
    """A device of the Phase 6 families (charger, evcc loadpoint, SG-Ready, program job).

    Conservative reading of plan §0 decision 17: every such device counts as running on an
    unverified template until 6-I/6-J/6-K/6-L can tell verified templates apart; Phase 3
    devices (other controls, no program spec) are never affected.
    """
    return isinstance(device.control, PHASE6_CONTROLS) or device.program is not None


def behaviour_mode_capped(device: DeviceConfig) -> bool:
    """The device runs at most assisted (decision 17): unverified and not confirmed."""
    return needs_behaviour_confirmation(device) and not device.behaviour_confirmed
