# SPDX-License-Identifier: Apache-2.0
"""Simulated devices with simple deterministic physics (Phase 3 WP8, BRIEF 4.14).

Everything here is pure: time is injected (``advance(now, outdoor_c)``), nothing sleeps and
nothing reads a clock; randomness is not used (the physics is deterministic). The devices
know nothing about Home Assistant's transport: they expose the HA-visible states
(:meth:`SimDevice.states`) and implement the *entity side* of the verified services
(:meth:`SimDevice.call`, research/ha-websocket-api.md "Write services and contexts"), raising
:class:`ServiceError` with HA's error codes and messages. ``fake_ha`` routes calls, delays
results, assigns contexts and publishes the states.

Two layers per device, like a cloud-connected appliance: the **physical** state (what draws
power) and the **reported** state (what Home Assistant shows). A service call changes the
physical state after an ``at_s`` offset and the reported state ``push_delay_s`` later
(:class:`Effect`); the entity is then published with the call's context if the push comes
within 5 s of the call (HA ``CONTEXT_RECENT_TIME_SECONDS``), otherwise with a fresh id-only
context.

Devices:

* :class:`SimClimate` - room RC model (outdoor temperature, internal gain), inverter
  compressor power by mode and temperature error, hourly integer-Wh "today" counter with the
  LG quantisation and midnight reset, optional 5-minute polled power sensor. LG behaviours
  (device-integrations.md "Climate write behaviour"): ``set_temperature`` on a unit that
  *reports* off turns it on and blocks 2 s, ``int()`` truncation when the step is >= 1,
  ``temperature`` is ``None`` while off, state arrives by delayed push.
* :class:`SimWaterHeater` - tank temperature model, three variants (``select`` level,
  ``relays`` 1-3 switches, a dedicated ``water_heater`` entity), maximum-temperature cut-off.
* :class:`SimSwitchLoad` (pool pump on ``switch`` / ``input_boolean``) and
  :class:`SimNumberLoad` (a W number such as a heating rod limit).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Annotated, Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

HVAC_MODES: Final = ("off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only")
"""``HVACMode`` values (research/ha-websocket-api.md)."""
TARGET_TEMPERATURE: Final = 1
TURN_OFF: Final = 128
TURN_ON: Final = 256
LG_FEATURES: Final = 1 + 8 + 32 + 128 + 256 + 512
"""``supported_features`` of the maintainer's ``lg_thinq`` unit (937)."""
CONTEXT_RECENT_S: Final = 5.0
"""A push inherits the call's context if it is written within this time (HA core)."""
REPORT_PERIOD_S: Final = 30.0
"""A sensor that reports regularly (a plug's power, a tank probe) re-reports at this period."""
MAX_SUBSTEP_S: Final = 10.0
"""Longest physics sub-step (the control law is re-evaluated at every sub-step)."""
WH_PER_KJ: Final = 1.0 / 3.6


class ServiceError(Exception):
    """A service call HA would answer with ``success: false`` (code and message as in HA)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def validation_error(text: str) -> ServiceError:
    """``ServiceValidationError`` as the WebSocket reports it."""
    return ServiceError("service_validation_error", f"Validation error: {text}")


def invalid_format(text: str) -> ServiceError:
    """A schema error (``invalid_format``)."""
    return ServiceError("invalid_format", text)


@dataclass(frozen=True, slots=True)
class StateOut:
    """One HA-visible entity state of a device."""

    entity_id: str
    state: str
    attributes: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Step:
    """One change of the physical device ``at_s`` seconds after the call started."""

    at_s: float
    change: Callable[[], None]


@dataclass(frozen=True, slots=True)
class Effect:
    """What a service call (or a manual action) does to a device.

    ``blocking_s`` is how long the service call blocks (the LG entity sleeps 2 s after a
    power-on), ``steps`` the physical changes, ``push_delay_s`` the delay until the reported
    state follows a physical change.
    """

    blocking_s: float = 0.0
    steps: tuple[Step, ...] = ()
    push_delay_s: float = 0.0


# --- service schemas (probatio/voluptuous shapes, source reading only) ------------------------


def _as_float(key: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = math.nan
    if not math.isfinite(number):
        raise invalid_format(f"expected float for dictionary value @ data['{key}']")
    return number


def _as_str(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise invalid_format(f"expected str for dictionary value @ data['{key}']")
    return value


def _as_hvac(key: str, value: Any) -> str:
    if value not in HVAC_MODES:
        options = ", ".join(repr(m) for m in HVAC_MODES)
        raise invalid_format(f"expected HVACMode or one of {options} @ data['{key}']")
    return str(value)


_Coerce = Callable[[str, Any], Any]
_SCHEMAS: Final[Mapping[tuple[str, str], tuple[tuple[str, ...], Mapping[str, _Coerce]]]] = {
    ("switch", "turn_on"): ((), {}),
    ("switch", "turn_off"): ((), {}),
    ("switch", "toggle"): ((), {}),
    ("input_boolean", "turn_on"): ((), {}),
    ("input_boolean", "turn_off"): ((), {}),
    ("input_boolean", "toggle"): ((), {}),
    ("climate", "set_temperature"): (
        (),
        {
            "temperature": _as_float,
            "target_temp_low": _as_float,
            "target_temp_high": _as_float,
            "hvac_mode": _as_hvac,
        },
    ),
    ("climate", "set_hvac_mode"): (("hvac_mode",), {"hvac_mode": _as_hvac}),
    ("climate", "turn_on"): ((), {}),
    ("climate", "turn_off"): ((), {}),
    ("number", "set_value"): (("value",), {"value": _as_float}),
    ("select", "select_option"): (("option",), {"option": _as_str}),
    ("water_heater", "set_temperature"): (
        ("temperature",),
        {"temperature": _as_float, "operation_mode": _as_str},
    ),
    ("water_heater", "set_operation_mode"): (
        ("operation_mode",),
        {"operation_mode": _as_str},
    ),
    ("water_heater", "turn_on"): ((), {}),
    ("water_heater", "turn_off"): ((), {}),
}

SUPPORTED_SERVICES: Final = frozenset(_SCHEMAS)
"""The ``(domain, service)`` pairs the fake executes (the verified write services)."""


def validate_schema(domain: str, service: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and coerce the service data like the service schema does.

    Raises:
        ServiceError: ``not_found`` for an unknown service, ``invalid_format`` for a schema error.
    """
    spec = _SCHEMAS.get((domain, service))
    if spec is None:
        raise ServiceError("not_found", f"Service {domain}.{service} not found.")
    required, fields = spec
    out: dict[str, Any] = {}
    for key, value in data.items():
        coerce = fields.get(key)
        if coerce is None:
            raise invalid_format(f"extra keys not allowed @ data['{key}']")
        out[key] = coerce(key, value)
    for key in required:
        if key not in out:
            raise invalid_format(f"required key not provided @ data['{key}']")
    if (domain, service) == ("climate", "set_temperature"):
        names = ("temperature", "target_temp_high", "target_temp_low")
        if not any(n in out for n in names):
            raise invalid_format(
                "must contain at least one of temperature, target_temp_high, target_temp_low."
            )
        if "temperature" in out and ("target_temp_low" in out or "target_temp_high" in out):
            raise invalid_format("two or more values in the same group of exclusion 'temperature'")
        if ("target_temp_low" in out) != ("target_temp_high" in out):
            raise invalid_format("some but not all values in the same group of inclusion")
    return out


# --- helpers ----------------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _text(value: float) -> str:
    return f"{value + 0.0:.1f}"


class HourlyCounter:
    """LG-style cumulative Wh counter: integer, published once per local hour, midnight reset.

    ``today`` is the published integer; ``yesterday`` the previous day's final total. Energy
    accumulates continuously, the published values only change on a full local hour.
    """

    def __init__(self) -> None:
        self.total_wh = 0.0
        self.today = 0
        self.yesterday = 0

    @staticmethod
    def next_boundary(after: datetime) -> datetime:
        """The first full local hour strictly after ``after``."""
        return after.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    def add(self, power_w: float, dt_s: float) -> None:
        self.total_wh += power_w * dt_s / 3600.0

    def boundary(self, at: datetime) -> None:
        """Publish at a full hour; at local midnight roll the day over."""
        if at.hour == 0:
            self.yesterday = int(self.total_wh)
            self.total_wh = 0.0
            self.today = 0
        else:
            self.today = int(self.total_wh)


class PolledSensor:
    """A power sensor polled every ``period_s`` (sample and hold, aligned to the epoch)."""

    def __init__(self, period_s: float, initial_w: float) -> None:
        self.period_s = period_s
        self.value_w = initial_w

    def next_poll(self, after: datetime) -> datetime:
        slot = math.floor(after.timestamp() / self.period_s) + 1
        return datetime.fromtimestamp(slot * self.period_s, tz=after.tzinfo)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- the common base --------------------------------------------------------------------------


class SimDevice:
    """Common interface and the sub-stepped time integration."""

    kind: ClassVar[str] = "device"

    def __init__(self, device_id: str) -> None:
        self.id = device_id
        self._t: datetime | None = None

    # --- identity ---------------------------------------------------------------------------------

    def entity_ids(self) -> tuple[str, ...]:
        raise NotImplementedError

    def handles(self, entity_id: str) -> bool:
        return entity_id in self.entity_ids()

    def roles(self) -> dict[str, str]:
        """Template role -> entity id, for building the controller's ``DeviceConfig``."""
        raise NotImplementedError

    def cadences(self) -> dict[str, float]:
        """Entities that update slower than the usual stream -> their period in seconds."""
        return {}

    def heartbeats(self) -> dict[str, float]:
        """Entities that report again after this many seconds even if unchanged.

        Home Assistant fires no ``state_changed`` for an unchanged value, but a reporting
        sensor's ``last_reported`` moves and the client's periodic resync delivers it; the
        simulator emits the unchanged state instead. Includes :meth:`cadences`.
        """
        return self.cadences()

    # --- physics ----------------------------------------------------------------------------------

    def power_w(self) -> float:
        """The true electrical draw right now (device consumption, + = consumption)."""
        raise NotImplementedError

    def is_on(self) -> bool:
        """Whether the load is on (draws more than standby)."""
        raise NotImplementedError

    def _breakpoints(self, after: datetime) -> Iterable[datetime]:
        del after
        return ()

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        raise NotImplementedError

    def _reached(self, at: datetime) -> None:
        """Called when a breakpoint was reached exactly."""

    def advance(self, now: datetime, outdoor_c: float) -> None:
        """Integrate the physics up to ``now`` (idempotent for the same ``now``)."""
        if now.tzinfo is None:
            msg = "advance needs a timezone-aware time"
            raise ValueError(msg)
        if self._t is None:
            self._t = now
            return
        t = self._t
        while t < now:
            nxt = min(now, t + timedelta(seconds=MAX_SUBSTEP_S))
            for b in self._breakpoints(t):
                if b > t:
                    nxt = min(nxt, b)
            self._integrate(t, nxt, outdoor_c)
            t = nxt
            self._reached(t)
        self._t = now

    # --- HA side ----------------------------------------------------------------------------------

    def states(self, now: datetime) -> list[StateOut]:
        raise NotImplementedError

    def report(self) -> None:
        """A push: the reported state follows the physical state."""

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        raise NotImplementedError

    def manual(self, action: str, value: float | str | None) -> Effect:
        """A user changes the device outside SunCore (remote control, app): see subclasses."""
        del value
        msg = f"{self.kind} {self.id} does not support the manual action {action!r}"
        raise ValueError(msg)


# --- climate ----------------------------------------------------------------------------------


class ClimateParams(_Frozen):
    """Physics and integration behaviour of a split air conditioner."""

    kind: Literal["climate"] = "climate"
    entity_id: str | None = None
    friendly_name: str | None = None
    initial_temp_c: float = Field(default=22.0, ge=-30, le=60)
    initial_mode: Literal["off", "heat", "cool", "auto", "dry", "fan_only"] = "off"
    initial_setpoint_c: float = Field(default=21.0, ge=5, le=40)
    default_on_mode: Literal["heat", "cool", "auto"] = "heat"
    """The mode a unit that has never run switches on in."""
    hvac_modes: tuple[str, ...] = ("off", "heat", "cool", "dry", "auto", "fan_only")
    min_temp_c: float = 16.0
    max_temp_c: float = 30.0
    target_temp_step: float | None = 1.0
    step_none_bug: bool = False
    """LG with no ``target_temp_step``: ``None >= 1`` raises ``TypeError`` -> ``unknown_error``."""
    supported_features: int = LG_FEATURES
    nominal_w: float = Field(default=900.0, gt=0, le=20_000)
    """Highest compressor power (electrical)."""
    min_w: float = Field(default=150.0, ge=0)
    standby_w: float = Field(default=5.0, ge=0)
    idle_w: float = Field(default=25.0, ge=0)
    """Indoor unit on, compressor stopped."""
    fan_w: float = Field(default=40.0, ge=0)
    cop: float = Field(default=3.5, gt=0)
    ua_w_per_k: float = Field(default=120.0, gt=0)
    """Heat loss of the room to the outdoors."""
    capacity_wh_per_k: float = Field(default=400.0, gt=0)
    internal_gain_w: float = 0.0
    band_c: float = Field(default=0.6, gt=0)
    """Temperature error at which the compressor runs flat out."""
    hysteresis_c: float = Field(default=0.5, ge=0)
    push_delay_s: float = Field(default=3.0, ge=0)
    """Cloud push latency until HA shows a change."""
    api_latency_s: float = Field(default=1.0, ge=0)
    """Time until a command reaches the device."""
    call_s: float = Field(default=0.3, ge=0)
    """Round trip of a service call without the 2 s sleeps."""
    sleep_s: float = Field(default=2.0, ge=0)
    """The 2 s ``lg_thinq`` sleep after a power-on and after a mode change."""
    temp_report_s: float = Field(default=300.0, gt=0)
    energy_counter: bool = True
    """Create the hourly Wh counter entities (``sensor.sim_<id>_energy_today`` ...)."""
    power_sensor: bool = False
    """Create a polled power sensor (``sensor.sim_<id>_power``)."""
    energy_today_entity_id: str | None = None
    energy_yesterday_entity_id: str | None = None
    power_entity_id: str | None = None
    power_poll_s: float = Field(default=300.0, gt=0)
    mirror_entity_id: str | None = None
    """A second climate entity of the same unit (another integration)."""
    mirror_friendly_name: str | None = None
    mirror_hvac_modes: tuple[str, ...] = ("off", "cool", "heat_cool", "fan_only")
    mirror_min_temp_c: float = 16.0


@dataclass(slots=True)
class _View:
    entity_id: str
    name: str
    hvac_modes: tuple[str, ...]
    min_temp: float
    max_temp: float


class SimClimate(SimDevice):
    """A split air conditioner: RC room model, inverter compressor, LG-like integration."""

    kind: ClassVar[str] = "climate"

    def __init__(self, device_id: str, params: ClimateParams) -> None:
        super().__init__(device_id)
        p = params
        self.p = p
        entity = p.entity_id or f"climate.sim_{device_id}"
        self.entity = entity
        self._views: dict[str, _View] = {
            entity: _View(
                entity,
                p.friendly_name or f"Sim {device_id}",
                p.hvac_modes,
                p.min_temp_c,
                p.max_temp_c,
            )
        }
        if p.mirror_entity_id is not None:
            self._views[p.mirror_entity_id] = _View(
                p.mirror_entity_id,
                p.mirror_friendly_name or f"Sim {device_id} (mirror)",
                p.mirror_hvac_modes,
                p.mirror_min_temp_c,
                p.max_temp_c,
            )
        self.temp_c = p.initial_temp_c
        self.mode = p.initial_mode
        self.setpoint_c = p.initial_setpoint_c
        self._last_on_mode: str = p.initial_mode if p.initial_mode != "off" else p.default_on_mode
        self._running = False
        self._elec_w = p.standby_w if p.initial_mode == "off" else p.idle_w
        self.energy_today_id = p.energy_today_entity_id or (
            f"sensor.sim_{device_id}_energy_today" if p.energy_counter else None
        )
        self.energy_yesterday_id = p.energy_yesterday_entity_id or (
            f"sensor.sim_{device_id}_energy_yesterday" if p.energy_counter else None
        )
        self.power_id = p.power_entity_id or (
            f"sensor.sim_{device_id}_power" if p.power_sensor else None
        )
        self.counter = HourlyCounter() if self.energy_today_id else None
        self.sensor = PolledSensor(p.power_poll_s, self._elec_w) if self.power_id else None
        self._rep_mode = self.mode
        self._rep_setpoint = self.setpoint_c
        self._rep_temp = round(self.temp_c, 1)
        self._next_temp_report: datetime | None = None

    # --- identity ---------------------------------------------------------------------------------

    def entity_ids(self) -> tuple[str, ...]:
        extra = [
            e
            for e in (self.energy_today_id, self.energy_yesterday_id, self.power_id)
            if e is not None
        ]
        return (*self._views, *extra)

    def roles(self) -> dict[str, str]:
        out = {"climate": self.entity}
        for role, entity in (
            ("energy_today", self.energy_today_id),
            ("energy_yesterday", self.energy_yesterday_id),
            ("power", self.power_id),
        ):
            if entity is not None:
                out[role] = entity
        return out

    def cadences(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if self.power_id is not None:
            out[self.power_id] = self.p.power_poll_s
        if self.energy_today_id is not None:
            out[self.energy_today_id] = 3600.0
        if self.energy_yesterday_id is not None:
            out[self.energy_yesterday_id] = 86_400.0
        return out

    # --- physics ----------------------------------------------------------------------------------

    def power_w(self) -> float:
        return self._elec_w

    def is_on(self) -> bool:
        return self.mode != "off"

    def _kind(self, outdoor_c: float) -> str:
        if self.mode in ("heat", "cool"):
            return self.mode
        if self.mode in ("auto", "heat_cool"):
            return "heat" if outdoor_c < self.setpoint_c else "cool"
        return "none"

    def _control(self, outdoor_c: float) -> tuple[float, float]:
        """Electrical and thermal power (+ = heating the room) for the current state."""
        p = self.p
        if self.mode == "off":
            self._running = False
            return p.standby_w, 0.0
        kind = self._kind(outdoor_c)
        if kind == "none":
            self._running = False
            return (p.fan_w if self.mode in ("dry", "fan_only") else p.idle_w), 0.0
        err = self.setpoint_c - self.temp_c if kind == "heat" else self.temp_c - self.setpoint_c
        if err > 0:
            self._running = True
        elif err < -p.hysteresis_c:
            self._running = False
        if not self._running:
            return p.idle_w, 0.0
        fraction = _clamp(err / p.band_c, 0.0, 1.0)
        elec = p.min_w + (p.nominal_w - p.min_w) * fraction
        sign = 1.0 if kind == "heat" else -1.0
        return elec, sign * p.cop * elec

    def _breakpoints(self, after: datetime) -> Iterable[datetime]:
        out: list[datetime] = []
        if self.counter is not None:
            out.append(HourlyCounter.next_boundary(after))
        if self.sensor is not None:
            out.append(self.sensor.next_poll(after))
        return out

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        p = self.p
        dt = (t1 - t0).total_seconds()
        if dt <= 0:
            return
        elec, thermal = self._control(outdoor_c)
        self._elec_w = elec
        if self.counter is not None:
            self.counter.add(elec, dt)
        t_eq = outdoor_c + (p.internal_gain_w + thermal) / p.ua_w_per_k
        tau_s = p.capacity_wh_per_k * 3600.0 / p.ua_w_per_k
        self.temp_c = t_eq + (self.temp_c - t_eq) * math.exp(-dt / tau_s)
        if self._next_temp_report is None:
            self._next_temp_report = t0 + timedelta(seconds=p.temp_report_s)
        if t1 >= self._next_temp_report:
            self._rep_temp = round(self.temp_c, 1)
            self._next_temp_report = t1 + timedelta(seconds=p.temp_report_s)

    def _reached(self, at: datetime) -> None:
        if self.counter is not None and at.minute == 0 and at.second == 0 and at.microsecond == 0:
            self.counter.boundary(at)
        if self.sensor is not None and at.timestamp() % self.sensor.period_s == 0:
            self.sensor.value_w = self._elec_w

    # --- HA side ----------------------------------------------------------------------------------

    def report(self) -> None:
        self._rep_mode = self.mode
        self._rep_setpoint = self.setpoint_c
        self._rep_temp = round(self.temp_c, 1)

    def states(self, now: datetime) -> list[StateOut]:
        p = self.p
        out: list[StateOut] = []
        for view in self._views.values():
            attrs: dict[str, Any] = {
                "friendly_name": view.name,
                "hvac_modes": list(view.hvac_modes),
                "min_temp": view.min_temp,
                "max_temp": view.max_temp,
                "temperature": None if self._rep_mode == "off" else self._rep_setpoint,
                "current_temperature": self._rep_temp,
                "supported_features": p.supported_features,
            }
            if p.target_temp_step is not None:
                attrs["target_temp_step"] = p.target_temp_step
            out.append(StateOut(view.entity_id, self._rep_mode, attrs))
        if self.energy_today_id is not None and self.counter is not None:
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            out.append(
                StateOut(
                    self.energy_today_id,
                    str(self.counter.today),
                    {
                        "friendly_name": f"Sim {self.id} energy today",
                        "unit_of_measurement": "Wh",
                        "device_class": "energy",
                        "state_class": "total",
                        "last_reset": midnight.isoformat(),
                    },
                )
            )
        if self.energy_yesterday_id is not None and self.counter is not None:
            out.append(
                StateOut(
                    self.energy_yesterday_id,
                    str(self.counter.yesterday),
                    {
                        "friendly_name": f"Sim {self.id} energy yesterday",
                        "unit_of_measurement": "Wh",
                        "device_class": "energy",
                        "state_class": "total",
                    },
                )
            )
        if self.power_id is not None and self.sensor is not None:
            out.append(
                StateOut(
                    self.power_id,
                    f"{self.sensor.value_w:.0f}",
                    {
                        "friendly_name": f"Sim {self.id} energy current",
                        "unit_of_measurement": "W",
                        "device_class": "power",
                        "state_class": "measurement",
                    },
                )
            )
        return out

    # --- physical mutators ------------------------------------------------------------------------

    def _power_on(self) -> None:
        if self.mode == "off":
            self.mode = self._last_on_mode  # type: ignore[assignment]

    def _power_off(self) -> None:
        if self.mode != "off":
            self._last_on_mode = self.mode
            self.mode = "off"

    def _set_mode(self, mode: str) -> None:
        if mode == "off":
            self._power_off()
            return
        self.mode = mode  # type: ignore[assignment]
        self._last_on_mode = mode

    def _set_target(self, target: float) -> None:
        self.setpoint_c = target

    # --- services ---------------------------------------------------------------------------------

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        del now
        view = self._views[entity_id]
        if domain != "climate":
            raise validation_error(f"{entity_id} does not support {domain}.{service}")
        if service == "set_temperature":
            return self._set_temperature(view, data)
        if service == "set_hvac_mode":
            return self._set_hvac_mode(view, data["hvac_mode"])
        if service in ("turn_on", "turn_off"):
            feature = TURN_ON if service == "turn_on" else TURN_OFF
            if not self.p.supported_features & feature:
                raise validation_error(
                    f"Entity {entity_id} does not support action climate.{service}."
                )
            step = Step(
                self.p.api_latency_s, self._power_on if service == "turn_on" else self._power_off
            )
            return Effect(self.p.call_s, (step,), self.p.push_delay_s)
        raise ServiceError("not_found", f"Service climate.{service} not found.")  # pragma: no cover

    def _check_mode(self, view: _View, mode: str) -> None:
        if mode not in view.hvac_modes:
            raise validation_error(
                f"HVAC mode {mode} is not valid. "
                f"Valid HVAC modes are: {', '.join(view.hvac_modes)}."
            )

    def _set_temperature(self, view: _View, data: dict[str, Any]) -> Effect:
        p = self.p
        if "target_temp_low" in data:
            raise validation_error(
                f"Entity {view.entity_id} does not support setting a temperature range."
            )
        if not p.supported_features & TARGET_TEMPERATURE:
            raise validation_error(
                f"Entity {view.entity_id} does not support action climate.set_temperature."
            )
        target = float(data["temperature"])
        if not view.min_temp <= target <= view.max_temp:
            raise validation_error(
                f"Provided temperature {target} is not valid. "
                f"Accepted range is {view.min_temp} to {view.max_temp}."
            )
        hvac_mode = data.get("hvac_mode")
        if hvac_mode == "off":  # only turns off, the temperature is ignored
            return Effect(p.call_s, (Step(p.api_latency_s, self._power_off),), p.push_delay_s)
        if hvac_mode is not None:
            self._check_mode(view, hvac_mode)
        step = p.target_temp_step
        if step is None:
            if p.step_none_bug:
                raise ServiceError(
                    "unknown_error",
                    "'>=' not supported between instances of 'NoneType' and 'int'",
                )
        elif step >= 1:
            target = float(int(target))  # truncation toward zero, not rounding
        steps: list[Step] = []
        elapsed = 0.0
        if self._rep_mode == "off":  # `data.is_on` is the reported flag
            steps.append(Step(elapsed + p.api_latency_s, self._power_on))
            elapsed += p.sleep_s
        if hvac_mode is not None and hvac_mode != self._rep_mode:
            mode = hvac_mode
            steps.append(Step(elapsed + p.api_latency_s, partial(self._set_mode, mode)))
            elapsed += p.sleep_s
        reported = None if self._rep_mode == "off" else self._rep_setpoint
        if reported is None or target != reported:
            steps.append(Step(elapsed + p.api_latency_s, partial(self._set_target, target)))
        return Effect(elapsed + p.call_s, tuple(steps), p.push_delay_s)

    def _set_hvac_mode(self, view: _View, mode: str) -> Effect:
        p = self.p
        self._check_mode(view, mode)
        if mode == "off":
            return Effect(p.call_s, (Step(p.api_latency_s, self._power_off),), p.push_delay_s)
        steps: list[Step] = []
        elapsed = 0.0
        if self._rep_mode == "off":
            steps.append(Step(elapsed + p.api_latency_s, self._power_on))
            elapsed += p.sleep_s
        steps.append(Step(elapsed + p.api_latency_s, lambda: self._set_mode(mode)))
        return Effect(elapsed + p.call_s, tuple(steps), p.push_delay_s)

    # --- the user ---------------------------------------------------------------------------------

    def manual(self, action: str, value: float | str | None) -> Effect:
        """The user's remote control: an immediate physical change, reported by push."""
        p = self.p
        if action == "turn_off":
            return Effect(0.0, (Step(0.0, self._power_off),), p.push_delay_s)
        if action == "turn_on":
            return Effect(0.0, (Step(0.0, self._power_on),), p.push_delay_s)
        if action == "set_hvac_mode" and isinstance(value, str):
            return Effect(0.0, (Step(0.0, lambda: self._set_mode(value)),), p.push_delay_s)
        if action == "set_temperature" and value is not None and not isinstance(value, str):
            target = float(value)
            return Effect(0.0, (Step(0.0, lambda: self._set_target(target)),), p.push_delay_s)
        return super().manual(action, value)


# --- on/off and number loads ------------------------------------------------------------------


class SwitchParams(_Frozen):
    """A switchable load (pool pump) on a ``switch`` or ``input_boolean`` entity."""

    kind: Literal["switch"] = "switch"
    entity_id: str | None = None
    friendly_name: str | None = None
    nominal_w: float = Field(default=750.0, gt=0, le=100_000)
    initial_on: bool = False
    push_delay_s: float = Field(default=0.0, ge=0)
    call_s: float = Field(default=0.1, ge=0)
    power_sensor: bool = False
    power_entity_id: str | None = None


class SimSwitchLoad(SimDevice):
    kind: ClassVar[str] = "switch"

    def __init__(self, device_id: str, params: SwitchParams) -> None:
        super().__init__(device_id)
        self.p = params
        self.entity = params.entity_id or f"switch.sim_{device_id}"
        self.domain = self.entity.split(".", 1)[0]
        self.power_id = params.power_entity_id or (
            f"sensor.sim_{device_id}_power" if params.power_sensor else None
        )
        self.on = params.initial_on
        self._rep_on = params.initial_on

    def entity_ids(self) -> tuple[str, ...]:
        extra = (self.power_id,) if self.power_id else ()
        return (self.entity, *extra)

    def roles(self) -> dict[str, str]:
        out = {"switch": self.entity}
        if self.power_id:
            out["power"] = self.power_id
        return out

    def heartbeats(self) -> dict[str, float]:
        return {self.power_id: REPORT_PERIOD_S} if self.power_id else {}

    def power_w(self) -> float:
        return self.p.nominal_w if self.on else 0.0

    def is_on(self) -> bool:
        return self.on

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        del t0, t1, outdoor_c

    def report(self) -> None:
        self._rep_on = self.on

    def states(self, now: datetime) -> list[StateOut]:
        del now
        out = [
            StateOut(
                self.entity,
                "on" if self._rep_on else "off",
                {"friendly_name": self.p.friendly_name or f"Sim {self.id}"},
            )
        ]
        if self.power_id is not None:
            out.append(
                StateOut(
                    self.power_id,
                    _text(self.p.nominal_w if self._rep_on else 0.0),
                    {
                        "friendly_name": f"Sim {self.id} power",
                        "unit_of_measurement": "W",
                        "device_class": "power",
                        "state_class": "measurement",
                    },
                )
            )
        return out

    def _set(self, on: bool) -> None:
        self.on = on

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        del now, data, entity_id
        if domain != self.domain:
            raise validation_error(f"{self.entity} does not support {domain}.{service}")
        target = {"turn_on": True, "turn_off": False, "toggle": not self.on}[service]
        return Effect(self.p.call_s, (Step(0.0, lambda: self._set(target)),), self.p.push_delay_s)

    def manual(self, action: str, value: float | str | None) -> Effect:
        if action in ("turn_on", "turn_off"):
            return Effect(
                0.0, (Step(0.0, lambda: self._set(action == "turn_on")),), self.p.push_delay_s
            )
        return super().manual(action, value)


class NumberParams(_Frozen):
    """A number entity whose value is the load in W (a heating rod limit)."""

    kind: Literal["number"] = "number"
    entity_id: str | None = None
    friendly_name: str | None = None
    min: float = 0.0
    max: float = Field(default=3000.0, gt=0)
    step: float = Field(default=100.0, gt=0)
    initial: float = 0.0
    push_delay_s: float = Field(default=0.0, ge=0)
    call_s: float = Field(default=0.1, ge=0)
    power_sensor: bool = False
    power_entity_id: str | None = None


class SimNumberLoad(SimDevice):
    kind: ClassVar[str] = "number"

    def __init__(self, device_id: str, params: NumberParams) -> None:
        super().__init__(device_id)
        self.p = params
        self.entity = params.entity_id or f"number.sim_{device_id}"
        self.power_id = params.power_entity_id or (
            f"sensor.sim_{device_id}_power" if params.power_sensor else None
        )
        self.value = _clamp(params.initial, params.min, params.max)
        self._rep = self.value

    def entity_ids(self) -> tuple[str, ...]:
        extra = (self.power_id,) if self.power_id else ()
        return (self.entity, *extra)

    def roles(self) -> dict[str, str]:
        out = {"number": self.entity}
        if self.power_id:
            out["power"] = self.power_id
        return out

    def heartbeats(self) -> dict[str, float]:
        return {self.power_id: REPORT_PERIOD_S} if self.power_id else {}

    def power_w(self) -> float:
        return self.value

    def is_on(self) -> bool:
        return self.value > 0

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        del t0, t1, outdoor_c

    def report(self) -> None:
        self._rep = self.value

    def states(self, now: datetime) -> list[StateOut]:
        del now
        out = [
            StateOut(
                self.entity,
                _text(self._rep),
                {
                    "friendly_name": self.p.friendly_name or f"Sim {self.id}",
                    "unit_of_measurement": "W",
                    "min": self.p.min,
                    "max": self.p.max,
                    "step": self.p.step,
                    "mode": "slider",
                },
            )
        ]
        if self.power_id is not None:
            out.append(
                StateOut(
                    self.power_id,
                    _text(self._rep),
                    {
                        "friendly_name": f"Sim {self.id} power",
                        "unit_of_measurement": "W",
                        "device_class": "power",
                        "state_class": "measurement",
                    },
                )
            )
        return out

    def _set(self, value: float) -> None:
        self.value = value

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        del now
        if domain != "number" or service != "set_value":
            raise validation_error(f"{entity_id} does not support {domain}.{service}")
        value = float(data["value"])
        p = self.p
        if not p.min <= value <= p.max:  # the step is not enforced (core)
            raise validation_error(
                f"Value {value} for {entity_id} is outside valid range {p.min} - {p.max}."
            )
        return Effect(p.call_s, (Step(0.0, lambda: self._set(value)),), p.push_delay_s)

    def manual(self, action: str, value: float | str | None) -> Effect:
        if action == "set_value" and value is not None and not isinstance(value, str):
            target = _clamp(float(value), self.p.min, self.p.max)
            return Effect(0.0, (Step(0.0, lambda: self._set(target)),), self.p.push_delay_s)
        return super().manual(action, value)


# --- water heater -----------------------------------------------------------------------------

_DEFAULT_DRAW_W: Final[tuple[float, ...]] = (
    0, 0, 0, 0, 0, 0, 1800, 2400, 600, 0, 0, 300,
    600, 300, 0, 0, 0, 300, 1500, 2100, 900, 300, 0, 0,
)  # fmt: skip


class WaterHeaterParams(_Frozen):
    """A hot water tank heated in discrete steps; ``variant`` picks how HA controls it."""

    kind: Literal["water_heater"] = "water_heater"
    variant: Literal["select", "relays", "entity"] = "select"
    entity_id: str | None = None
    """Select (``select``), dedicated entity (``entity``); for ``relays`` the first relay."""
    friendly_name: str | None = None
    temperature_entity_id: str | None = None
    probe: bool = True
    """Whether the tank has a temperature probe entity."""
    levels: tuple[tuple[str, float], ...] = (("off", 0.0), ("low", 1000.0), ("high", 3000.0))
    """``select`` and ``entity``: (option, electrical power in W), ascending."""
    relay_entity_ids: tuple[str, ...] = ()
    relay_elements_w: tuple[float, ...] = (2000.0, 2000.0, 2000.0)
    volume_l: float = Field(default=200.0, gt=0)
    initial_temp_c: float = 45.0
    ambient_c: float = 18.0
    loss_w_per_k: float = Field(default=1.6, ge=0)
    draw_w: tuple[float, ...] = Field(default=_DEFAULT_DRAW_W, min_length=24, max_length=24)
    """Thermal power taken out by hot water use, per local hour."""
    max_temp_c: float = 65.0
    """Safety thermostat: the elements are cut off above this temperature."""
    cutoff_hysteresis_k: float = 3.0
    initial_level: str | None = None
    push_delay_s: float = Field(default=0.0, ge=0)
    call_s: float = Field(default=0.2, ge=0)
    power_sensor: bool = False
    power_entity_id: str | None = None


class SimWaterHeater(SimDevice):
    kind: ClassVar[str] = "water_heater"

    def __init__(self, device_id: str, params: WaterHeaterParams) -> None:
        super().__init__(device_id)
        p = params
        self.p = p
        self.temp_c = p.initial_temp_c
        self.cut_off = False
        self.target_c = min(p.max_temp_c, 60.0)
        if p.variant == "relays":
            ids = p.relay_entity_ids or tuple(
                f"switch.sim_{device_id}_relay_{i}" for i in range(1, len(p.relay_elements_w) + 1)
            )
            if len(ids) > len(p.relay_elements_w):
                msg = "relay_elements_w needs a value per relay"
                raise ValueError(msg)
            self.relay_ids = ids
            self.entity = ids[0]
            self.relays = [False] * len(ids)
            self._rep_relays = list(self.relays)
        else:
            self.relay_ids = ()
            self.entity = p.entity_id or (
                f"select.sim_{device_id}_level"
                if p.variant == "select"
                else f"water_heater.sim_{device_id}"
            )
            self.relays = []
            self._rep_relays = []
        self.options = tuple(o for o, _ in p.levels)
        self.level = p.initial_level or self.options[0]
        if self.level not in self.options:
            msg = f"initial_level {self.level!r} is not one of {self.options}"
            raise ValueError(msg)
        self._rep_level = self.level
        self._rep_target = self.target_c
        self._rep_temp = round(self.temp_c, 1)
        self._last_on_level = next((o for o, w in p.levels if w > 0), self.level)
        self.temp_entity: str | None = (
            (p.temperature_entity_id or f"sensor.sim_{device_id}_temperature") if p.probe else None
        )
        self.energy_wh = 0.0
        self.power_id = p.power_entity_id or (
            f"sensor.sim_{device_id}_power" if p.power_sensor else None
        )

    # --- identity ---------------------------------------------------------------------------------

    def entity_ids(self) -> tuple[str, ...]:
        main = self.relay_ids or (self.entity,)
        extra = (self.power_id,) if self.power_id else ()
        probe = (self.temp_entity,) if self.temp_entity else ()
        return (*main, *probe, *extra)

    def roles(self) -> dict[str, str]:
        if self.p.variant == "relays":
            out = {f"relay_{i}": e for i, e in enumerate(self.relay_ids, start=1)}
        else:
            out = {"level": self.entity}
        if self.temp_entity:
            out["temperature"] = self.temp_entity
        if self.power_id:
            out["power"] = self.power_id
        return out

    def heartbeats(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if self.temp_entity:
            out[self.temp_entity] = REPORT_PERIOD_S * 2
        if self.power_id:
            out[self.power_id] = REPORT_PERIOD_S
        return out

    # --- physics ----------------------------------------------------------------------------------

    def _level_w(self) -> float:
        return dict(self.p.levels)[self.level]

    def _commanded_w(self) -> float:
        if self.p.variant == "relays":
            return sum(w for w, on in zip(self.p.relay_elements_w, self.relays, strict=False) if on)
        return self._level_w()

    def power_w(self) -> float:
        return 0.0 if self.cut_off else self._commanded_w()

    def is_on(self) -> bool:
        return self._commanded_w() > 0

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        del outdoor_c
        p = self.p
        dt = (t1 - t0).total_seconds()
        limit = self.target_c if p.variant == "entity" else p.max_temp_c
        if self.temp_c >= limit:
            self.cut_off = True
        elif self.temp_c <= limit - p.cutoff_hysteresis_k:
            self.cut_off = False
        heat_w = self.power_w()
        loss_w = p.loss_w_per_k * (self.temp_c - p.ambient_c) + p.draw_w[t0.hour]
        capacity_wh_per_k = p.volume_l * 1.163
        self.temp_c = max(
            p.ambient_c, self.temp_c + (heat_w - loss_w) * dt / 3600.0 / capacity_wh_per_k
        )
        self.energy_wh += heat_w * dt / 3600.0
        # a tank cannot be hotter than the elements can make it
        self.temp_c = min(self.temp_c, p.max_temp_c + 5.0)

    # --- HA side ----------------------------------------------------------------------------------

    def report(self) -> None:
        self._rep_level = self.level
        self._rep_relays = list(self.relays)
        self._rep_target = self.target_c
        self._rep_temp = round(self.temp_c, 1)

    def states(self, now: datetime) -> list[StateOut]:
        del now
        p = self.p
        name = p.friendly_name or f"Sim {self.id}"
        out: list[StateOut] = []
        if p.variant == "select":
            out.append(
                StateOut(
                    self.entity,
                    self._rep_level,
                    {"friendly_name": name, "options": list(self.options)},
                )
            )
        elif p.variant == "relays":
            for index, entity in enumerate(self.relay_ids):
                out.append(
                    StateOut(
                        entity,
                        "on" if self._rep_relays[index] else "off",
                        {"friendly_name": f"{name} relay {index + 1}"},
                    )
                )
        else:
            out.append(
                StateOut(
                    self.entity,
                    self._rep_level,
                    {
                        "friendly_name": name,
                        "operation_list": list(self.options),
                        "operation_mode": self._rep_level,
                        "temperature": self._rep_target,
                        "current_temperature": self._rep_temp,
                        "min_temp": 30.0,
                        "max_temp": p.max_temp_c,
                        "supported_features": 1 + 2 + 8,
                    },
                )
            )
        if self.temp_entity is not None:
            out.append(
                StateOut(
                    self.temp_entity,
                    _text(self._rep_temp),
                    {
                        "friendly_name": f"{name} temperature",
                        "unit_of_measurement": "°C",
                        "device_class": "temperature",
                        "state_class": "measurement",
                    },
                )
            )
        if self.power_id is not None:
            reported = (
                sum(w for w, on in zip(p.relay_elements_w, self._rep_relays, strict=False) if on)
                if p.variant == "relays"
                else dict(p.levels)[self._rep_level]
            )
            out.append(
                StateOut(
                    self.power_id,
                    _text(0.0 if self.cut_off else reported),
                    {
                        "friendly_name": f"{name} power",
                        "unit_of_measurement": "W",
                        "device_class": "power",
                        "state_class": "measurement",
                    },
                )
            )
        return out

    # --- services ---------------------------------------------------------------------------------

    def _set_level(self, level: str) -> None:
        self.level = level
        if dict(self.p.levels)[level] > 0:
            self._last_on_level = level

    def _set_relay(self, index: int, on: bool) -> None:
        self.relays[index] = on

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        del now
        p = self.p
        push = p.push_delay_s
        if p.variant == "select" and (domain, service) == ("select", "select_option"):
            option = str(data["option"])
            if option not in self.options:
                raise validation_error(
                    f"Option {option} is not valid for entity {entity_id}, "
                    f"valid options are: {', '.join(self.options)}."
                )
            return Effect(p.call_s, (Step(0.0, lambda: self._set_level(option)),), push)
        if (
            p.variant == "relays"
            and domain == "switch"
            and service in ("turn_on", "turn_off", "toggle")
        ):
            index = self.relay_ids.index(entity_id)
            target = {"turn_on": True, "turn_off": False, "toggle": not self.relays[index]}[service]
            return Effect(p.call_s, (Step(0.0, lambda: self._set_relay(index, target)),), push)
        if p.variant == "entity" and domain == "water_heater":
            return self._water_heater_entity(service, entity_id, data)
        raise validation_error(f"{entity_id} does not support {domain}.{service}")

    def _check_operation(self, entity_id: str, mode: str) -> None:
        if mode not in self.options:
            raise validation_error(
                f"Operation mode {mode} is not valid for {entity_id}. "
                f"Valid operation modes are: {', '.join(self.options)}."
            )

    def _water_heater_entity(self, service: str, entity_id: str, data: dict[str, Any]) -> Effect:
        p = self.p
        steps: list[Step] = []
        if service == "set_operation_mode":
            mode = str(data["operation_mode"])
            self._check_operation(entity_id, mode)
            steps.append(Step(0.0, lambda: self._set_level(mode)))
        elif service == "set_temperature":
            target = float(data["temperature"])  # core does not range check
            steps.append(Step(0.0, lambda: setattr(self, "target_c", target)))
            if "operation_mode" in data:
                mode = str(data["operation_mode"])
                self._check_operation(entity_id, mode)
                steps.append(Step(0.0, lambda: self._set_level(mode)))
        elif service == "turn_on":
            on_level = self._last_on_level
            steps.append(Step(0.0, lambda: self._set_level(on_level)))
        elif service == "turn_off":
            steps.append(Step(0.0, lambda: self._set_level(self.options[0])))
        else:  # pragma: no cover - schema rejects the rest
            raise ServiceError("not_found", f"Service water_heater.{service} not found.")
        return Effect(p.call_s, tuple(steps), p.push_delay_s)

    def manual(self, action: str, value: float | str | None) -> Effect:
        p = self.p
        if action == "select_option" and isinstance(value, str) and p.variant != "relays":
            level = value
            if level not in self.options:
                msg = f"unknown option {level!r}"
                raise ValueError(msg)
            return Effect(0.0, (Step(0.0, lambda: self._set_level(level)),), p.push_delay_s)
        if action == "turn_off" and p.variant != "relays":
            return Effect(
                0.0, (Step(0.0, lambda: self._set_level(self.options[0])),), p.push_delay_s
            )
        return super().manual(action, value)


# --- the set ----------------------------------------------------------------------------------

DeviceParams = Annotated[
    ClimateParams | SwitchParams | NumberParams | WaterHeaterParams, Field(discriminator="kind")
]


def build_device(
    device_id: str, params: ClimateParams | SwitchParams | NumberParams | WaterHeaterParams
) -> SimDevice:
    """The simulated device for ``params``."""
    if isinstance(params, ClimateParams):
        return SimClimate(device_id, params)
    if isinstance(params, SwitchParams):
        return SimSwitchLoad(device_id, params)
    if isinstance(params, NumberParams):
        return SimNumberLoad(device_id, params)
    return SimWaterHeater(device_id, params)


@dataclass(slots=True)
class SimDeviceSet:
    """All simulated devices of one scenario; routes entities to devices."""

    devices: dict[str, SimDevice] = field(default_factory=dict)
    _by_entity: dict[str, SimDevice] = field(default_factory=dict)

    def add(self, device: SimDevice) -> None:
        if device.id in self.devices:
            msg = f"duplicate simulated device {device.id!r}"
            raise ValueError(msg)
        self.devices[device.id] = device
        for entity in device.entity_ids():
            if entity in self._by_entity:
                msg = f"entity {entity} belongs to two simulated devices"
                raise ValueError(msg)
            self._by_entity[entity] = device

    def of_entity(self, entity_id: str) -> SimDevice | None:
        return self._by_entity.get(entity_id)

    def entity_ids(self) -> tuple[str, ...]:
        return tuple(self._by_entity)

    def advance(self, now: datetime, outdoor_c: float) -> None:
        for device in self.devices.values():
            device.advance(now, outdoor_c)

    def total_power_w(self) -> float:
        return sum(d.power_w() for d in self.devices.values())

    def states(self, now: datetime) -> list[StateOut]:
        return [s for d in self.devices.values() for s in d.states(now)]

    def cadences(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for device in self.devices.values():
            out.update(device.cadences())
        return out

    def heartbeats(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for device in self.devices.values():
            out.update(device.heartbeats())
        return out


def build_device_set(
    specs: Sequence[tuple[str, ClimateParams | SwitchParams | NumberParams | WaterHeaterParams]],
) -> SimDeviceSet:
    """A :class:`SimDeviceSet` from ``(device id, params)`` pairs."""
    result = SimDeviceSet()
    for device_id, params in specs:
        result.add(build_device(device_id, params))
    return result


__all__ = [
    "CONTEXT_RECENT_S",
    "HVAC_MODES",
    "LG_FEATURES",
    "SUPPORTED_SERVICES",
    "ClimateParams",
    "DeviceParams",
    "Effect",
    "HourlyCounter",
    "NumberParams",
    "PolledSensor",
    "ServiceError",
    "SimClimate",
    "SimDevice",
    "SimDeviceSet",
    "SimNumberLoad",
    "SimSwitchLoad",
    "SimWaterHeater",
    "StateOut",
    "Step",
    "SwitchParams",
    "WaterHeaterParams",
    "build_device",
    "build_device_set",
    "invalid_format",
    "validate_schema",
    "validation_error",
]
