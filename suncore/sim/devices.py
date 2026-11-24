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
* :class:`SimHeatPump` - air/water heat pump on a hot-water store (PR 6-K): SG-Ready contacts
  or a ``water_heater`` set-point, its own thermostat per SG state and anti-short-cycle time.
* :class:`SimSwitchLoad` (pool pump on ``switch`` / ``input_boolean``) and
  :class:`SimNumberLoad` (a W number such as a heating rod limit).
* :class:`SimLoadpoint` (PR 6-J) - an evcc loadpoint behind the fake evcc (MQTT, no HA entities).
* :class:`SimWallbox` (PR 6-I) - a wallbox (current number in A, pause switch, optional phase
  select) with a vehicle (SoC, arrival/departure, minimum charging current).
* :class:`SimProgram` (PR 6-L) - a shiftable programme (profile, start lag, resumes after power
  returns or not) behind a smart plug, a remote-start switch or only a power sensor.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Annotated, Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from suncore.sim.fake_evcc import FakeEvccBus

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


# --- heat pump ----------------------------------------------------------------------------------

_SG_PATTERN: Final = re.compile(r"^[01]{2}$")


def _pair(text: str) -> tuple[bool, bool]:
    if not _SG_PATTERN.match(text):
        msg = f"an SG-Ready contact pair is two digits 0/1, got {text!r}"
        raise ValueError(msg)
    return text[0] == "1", text[1] == "1"


class HeatPumpParams(_Frozen):
    """An air/water heat pump heating a hot-water store (PR 6-K).

    ``variant`` ``sg_ready``: two contact switches select the SG-Ready state (pairs as
    ``"ab"`` digits, A first); the heat pump's own thermostat runs the compressor between
    ``off_c - start_k`` and ``off_c`` of the state - state 1 (block) keeps it off, state 3
    raises the stop temperature to ``intensified_off_c``, state 4 to ``forced_off_c``. An
    unknown pair counts as state 2. ``water_heater``: a dedicated ``water_heater`` entity whose
    target temperature is the thermostat's stop temperature (``set_temperature``), operation
    modes ``off`` / ``heat_pump``. The compressor keeps its own minimum off time.
    """

    kind: Literal["heat_pump"] = "heat_pump"
    variant: Literal["sg_ready", "water_heater"] = "sg_ready"
    friendly_name: str | None = None
    contact_a_entity_id: str | None = None
    contact_b_entity_id: str | None = None
    entity_id: str | None = None
    """``water_heater`` variant: the entity (default ``water_heater.sim_<id>``)."""
    temperature_entity_id: str | None = None
    probe: bool = True
    """``sg_ready``: whether the store has a temperature probe entity."""
    power_sensor: bool = False
    power_entity_id: str | None = None
    normal: str = "00"
    intensified: str = "01"
    forced: str = "11"
    block: str = "10"
    initial_contacts: str = "00"
    nominal_w: float = Field(default=1500.0, gt=0, le=50_000)
    """Electrical power while the compressor runs."""
    cop_at_7c: float = Field(default=3.0, gt=1, le=8)
    cop_per_k: float = Field(default=0.08, ge=0, le=0.5)
    """COP change per K of outdoor temperature (clamped to 1.5..6)."""
    volume_l: float = Field(default=300.0, gt=0)
    initial_temp_c: float = 47.0
    ambient_c: float = 18.0
    loss_w_per_k: float = Field(default=2.0, ge=0)
    draw_w: tuple[float, ...] = Field(default=_DEFAULT_DRAW_W, min_length=24, max_length=24)
    normal_off_c: float = 50.0
    intensified_off_c: float = 55.0
    forced_off_c: float = 60.0
    start_k: float = Field(default=5.0, gt=0)
    """Thermostat hysteresis in state 2 (and of the ``water_heater`` target)."""
    boost_start_k: float = Field(default=2.0, gt=0)
    """Hysteresis in states 3 and 4 (the heat pump starts soon after the signal)."""
    max_temp_c: float = 62.0
    """Hard limit of the store (the compressor never heats above it)."""
    initial_target_c: float = 50.0
    initial_mode: Literal["off", "heat_pump"] = "heat_pump"
    min_off_s: float = Field(default=300.0, ge=0)
    """The compressor's own anti-short-cycle time."""
    push_delay_s: float = Field(default=0.0, ge=0)
    call_s: float = Field(default=0.1, ge=0)

    @model_validator(mode="after")
    def _pairs(self) -> HeatPumpParams:
        pairs = [_pair(p) for p in (self.normal, self.intensified, self.forced, self.block)]
        if len(set(pairs)) != len(pairs):
            msg = "the four SG-Ready states need four different contact pairs"
            raise ValueError(msg)
        _pair(self.initial_contacts)
        return self


WATER_HEATER_HP_MODES: Final = ("off", "heat_pump")


class SimHeatPump(SimDevice):
    """Heat pump with a hot-water store: SG-Ready contacts or a water heater set-point."""

    kind: ClassVar[str] = "heat_pump"

    def __init__(self, device_id: str, params: HeatPumpParams) -> None:
        super().__init__(device_id)
        p = params
        self.p = p
        self.temp_c = p.initial_temp_c
        self.running = False
        self._off_since: datetime | None = None
        self.starts = 0
        self.energy_wh = 0.0
        self.max_seen_c = p.initial_temp_c
        self.states_seen: set[int] = set()
        """SG-Ready states the contacts selected at any physics sub-step (evidence)."""
        if p.variant == "sg_ready":
            self.contact_ids: tuple[str, ...] = (
                p.contact_a_entity_id or f"switch.sim_{device_id}_sg_a",
                p.contact_b_entity_id or f"switch.sim_{device_id}_sg_b",
            )
            self.entity = self.contact_ids[0]
        else:
            self.contact_ids = ()
            self.entity = p.entity_id or f"water_heater.sim_{device_id}"
        self.contacts = list(_pair(p.initial_contacts))
        self._rep_contacts = list(self.contacts)
        self.target_c = p.initial_target_c
        self.mode: str = p.initial_mode
        self._rep_target = self.target_c
        self._rep_mode: str = self.mode
        self._rep_temp = round(self.temp_c, 1)
        self._rep_running = False
        self.temp_entity: str | None = (
            (p.temperature_entity_id or f"sensor.sim_{device_id}_temperature")
            if p.probe and p.variant == "sg_ready"
            else None
        )
        self.power_id = p.power_entity_id or (
            f"sensor.sim_{device_id}_power" if p.power_sensor else None
        )

    # --- identity ---------------------------------------------------------------------------------

    def entity_ids(self) -> tuple[str, ...]:
        main = self.contact_ids or (self.entity,)
        probe = (self.temp_entity,) if self.temp_entity else ()
        extra = (self.power_id,) if self.power_id else ()
        return (*main, *probe, *extra)

    def roles(self) -> dict[str, str]:
        if self.p.variant == "sg_ready":
            out = {"contact_a": self.contact_ids[0], "contact_b": self.contact_ids[1]}
        else:
            out = {"water_heater": self.entity}
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
        if self.p.variant == "water_heater":
            out[self.entity] = REPORT_PERIOD_S * 2  # current_temperature moves
        return out

    # --- physics ----------------------------------------------------------------------------------

    def sg_state(self) -> int:
        """The SG-Ready state (1-4) the contacts select; an unknown pair counts as 2."""
        pair = f"{int(self.contacts[0])}{int(self.contacts[1])}"
        p = self.p
        return {p.block: 1, p.normal: 2, p.intensified: 3, p.forced: 4}.get(pair, 2)

    def _thermostat(self) -> tuple[float, float] | None:
        """(stop temperature, hysteresis) of the current command; ``None`` = must stay off."""
        p = self.p
        if p.variant == "water_heater":
            if self.mode == "off":
                return None
            return min(self.target_c, p.max_temp_c), p.start_k
        state = self.sg_state()
        if state == 1:
            return None
        if state == 3:
            return p.intensified_off_c, p.boost_start_k
        if state == 4:
            return p.forced_off_c, p.boost_start_k
        return p.normal_off_c, p.start_k

    def cop(self, outdoor_c: float) -> float:
        return _clamp(self.p.cop_at_7c + self.p.cop_per_k * (outdoor_c - 7.0), 1.5, 6.0)

    def power_w(self) -> float:
        return self.p.nominal_w if self.running else 0.0

    def is_on(self) -> bool:
        return self.running

    def _may_start(self, at: datetime) -> bool:
        if self._off_since is None:
            return True
        return (at - self._off_since).total_seconds() >= self.p.min_off_s

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        p = self.p
        command = self._thermostat()
        if command is None or self.temp_c >= p.max_temp_c:
            if self.running:
                self.running = False
                self._off_since = t0
        else:
            stop_c, hysteresis = command
            if self.running and self.temp_c >= stop_c:
                self.running = False
                self._off_since = t0
            elif not self.running and self.temp_c <= stop_c - hysteresis and self._may_start(t0):
                self.running = True
                self.starts += 1
        dt = (t1 - t0).total_seconds()
        heat_w = self.power_w() * self.cop(outdoor_c)
        loss_w = p.loss_w_per_k * (self.temp_c - p.ambient_c) + p.draw_w[t0.hour]
        capacity_wh_per_k = p.volume_l * 1.163
        self.temp_c = max(
            p.ambient_c, self.temp_c + (heat_w - loss_w) * dt / 3600.0 / capacity_wh_per_k
        )
        self.energy_wh += self.power_w() * dt / 3600.0
        self.max_seen_c = max(self.max_seen_c, self.temp_c)
        self.states_seen.add(self.sg_state())
        # the probe and the power sensor measure the physics (they report on their own, not
        # only when the contacts or the set-point are pushed)
        self._rep_temp = round(self.temp_c, 1)
        self._rep_running = self.running

    # --- HA side ----------------------------------------------------------------------------------

    def report(self) -> None:
        self._rep_contacts = list(self.contacts)
        self._rep_target = self.target_c
        self._rep_mode = self.mode
        self._rep_temp = round(self.temp_c, 1)
        self._rep_running = self.running

    def states(self, now: datetime) -> list[StateOut]:
        del now
        p = self.p
        name = p.friendly_name or f"Sim {self.id}"
        out: list[StateOut] = []
        if p.variant == "sg_ready":
            for index, entity in enumerate(self.contact_ids):
                out.append(
                    StateOut(
                        entity,
                        "on" if self._rep_contacts[index] else "off",
                        {"friendly_name": f"{name} SG-Ready contact {'AB'[index]}"},
                    )
                )
        else:
            out.append(
                StateOut(
                    self.entity,
                    self._rep_mode,
                    {
                        "friendly_name": name,
                        "operation_list": list(WATER_HEATER_HP_MODES),
                        "operation_mode": self._rep_mode,
                        "temperature": self._rep_target,
                        "current_temperature": self._rep_temp,
                        "min_temp": 30.0,
                        "max_temp": 60.0,
                        "target_temp_step": 0.5,
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
                        "friendly_name": f"{name} store temperature",
                        "unit_of_measurement": "°C",
                        "device_class": "temperature",
                        "state_class": "measurement",
                    },
                )
            )
        if self.power_id is not None:
            out.append(
                StateOut(
                    self.power_id,
                    _text(p.nominal_w if self._rep_running else 0.0),
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

    def _set_contact(self, index: int, on: bool) -> None:
        self.contacts[index] = on

    def _set_contacts(self, pair: tuple[bool, bool]) -> None:
        self.contacts = list(pair)

    def _set_target(self, target: float) -> None:
        self.target_c = target

    def _set_mode(self, mode: str) -> None:
        self.mode = mode

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        del now
        p = self.p
        push = p.push_delay_s
        if (
            p.variant == "sg_ready"
            and domain == "switch"
            and service in ("turn_on", "turn_off", "toggle")
            and entity_id in self.contact_ids
        ):
            index = self.contact_ids.index(entity_id)
            target = {"turn_on": True, "turn_off": False, "toggle": not self.contacts[index]}[
                service
            ]
            return Effect(p.call_s, (Step(0.0, lambda: self._set_contact(index, target)),), push)
        if p.variant == "water_heater" and domain == "water_heater":
            return self._water_heater(service, entity_id, data)
        raise validation_error(f"{entity_id} does not support {domain}.{service}")

    def _check_mode(self, entity_id: str, mode: str) -> None:
        if mode not in WATER_HEATER_HP_MODES:
            raise validation_error(
                f"Operation mode {mode} is not valid for {entity_id}. "
                f"Valid operation modes are: {', '.join(WATER_HEATER_HP_MODES)}."
            )

    def _water_heater(self, service: str, entity_id: str, data: dict[str, Any]) -> Effect:
        steps: list[Step] = []
        if service == "set_temperature":
            target = float(data["temperature"])  # core does not range check
            steps.append(Step(0.0, lambda: self._set_target(target)))
            if "operation_mode" in data:
                mode = str(data["operation_mode"])
                self._check_mode(entity_id, mode)
                steps.append(Step(0.0, lambda: self._set_mode(mode)))
        elif service == "set_operation_mode":
            mode = str(data["operation_mode"])
            self._check_mode(entity_id, mode)
            steps.append(Step(0.0, lambda: self._set_mode(mode)))
        elif service in ("turn_on", "turn_off"):
            mode = "heat_pump" if service == "turn_on" else "off"
            steps.append(Step(0.0, lambda: self._set_mode(mode)))
        else:  # pragma: no cover - schema rejects the rest
            raise ServiceError("not_found", f"Service water_heater.{service} not found.")
        return Effect(self.p.call_s, tuple(steps), self.p.push_delay_s)

    def manual(self, action: str, value: float | str | None) -> Effect:
        """``set_contacts`` (``"10"`` = the utility block), ``set_temperature``, ``turn_off``."""
        p = self.p
        if action == "set_contacts" and isinstance(value, str) and p.variant == "sg_ready":
            pair = _pair(value)
            return Effect(0.0, (Step(0.0, lambda: self._set_contacts(pair)),), p.push_delay_s)
        if action == "set_temperature" and p.variant == "water_heater" and value is not None:
            target = float(value)
            return Effect(0.0, (Step(0.0, lambda: self._set_target(target)),), p.push_delay_s)
        if action == "turn_off" and p.variant == "water_heater":
            return Effect(0.0, (Step(0.0, lambda: self._set_mode("off")),), p.push_delay_s)
        return super().manual(action, value)


# --- the set ----------------------------------------------------------------------------------

# --- EV wallbox (PR 6-I) ----------------------------------------------------------------------


class WallboxParams(_Frozen):
    """A wallbox with a vehicle (PR 6-I): the generic HA wallbox entities.

    Entities: the charging current ``number.sim_<id>_current`` (A, ``min``/``max``/``step``
    attributes), the pause/resume ``switch.sim_<id>_charging``, optionally the phases
    ``select.sim_<id>_phases`` (options ``"1"``/``"3"``), the charging power
    ``sensor.sim_<id>_power`` (W), the vehicle SoC ``sensor.sim_<id>_soc`` (%) and
    ``binary_sensor.sim_<id>_connected``.

    Physics: the car draws ``current x voltage x phases`` while it is connected, the switch is
    on and the current is at least ``car_min_a`` (below that the car does not charge, like a
    real EV under the IEC 61851 minimum); it stops at ``car_max_soc_pct``. ``arrive_s`` /
    ``depart_s`` are seconds after the scenario start (``None`` = connected from the start /
    never leaves). A phase change takes ``phase_switch_s`` during which the car draws nothing.
    """

    kind: Literal["wallbox"] = "wallbox"
    current_min: float = Field(default=0.0, ge=0)
    """The ``number`` entity's ``min`` (0 = the current can pause the car on its own)."""
    current_max: float = Field(default=16.0, gt=0, le=80)
    current_step: float = Field(default=1.0, gt=0)
    initial_current_a: float = Field(default=16.0, ge=0)
    initial_enabled: bool = False
    phases: Literal[1, 3] = 3
    phase_select: bool = False
    initial_phases: Literal[1, 3] = 3
    phase_switch_s: float = Field(default=0.0, ge=0)
    voltage_v: float = Field(default=230.0, gt=0)
    car_min_a: float = Field(default=6.0, ge=0)
    capacity_wh: float = Field(default=60_000.0, gt=0)
    efficiency: float = Field(default=0.9, gt=0, le=1)
    initial_soc_pct: float = Field(default=40.0, ge=0, le=100)
    car_max_soc_pct: float = Field(default=100.0, ge=0, le=100)
    arrive_s: float | None = Field(default=None, ge=0)
    depart_s: float | None = Field(default=None, gt=0)
    connected_sensor: bool = True
    soc_sensor: bool = True
    push_delay_s: float = Field(default=0.0, ge=0)
    call_s: float = Field(default=0.1, ge=0)


class SimWallbox(SimDevice):
    """See :class:`WallboxParams`."""

    kind: ClassVar[str] = "wallbox"

    def __init__(self, device_id: str, params: WallboxParams) -> None:
        super().__init__(device_id)
        self.p = params
        base = f"sim_{device_id}"
        self.current_id = f"number.{base}_current"
        self.enable_id = f"switch.{base}_charging"
        self.phases_id = f"select.{base}_phases" if params.phase_select else None
        self.power_id = f"sensor.{base}_power"
        self.soc_id = f"sensor.{base}_soc" if params.soc_sensor else None
        self.connected_id = f"binary_sensor.{base}_connected" if params.connected_sensor else None
        self.current_a = _clamp(params.initial_current_a, params.current_min, params.current_max)
        self.enabled = params.initial_enabled
        self.active_phases: int = params.initial_phases if params.phase_select else params.phases
        self.soc_pct = params.initial_soc_pct
        self.charged_wh = 0.0
        self._start: datetime | None = None
        self._switching_until: datetime | None = None
        self._rep: tuple[float, bool, int] = (self.current_a, self.enabled, self.active_phases)
        self.phase_changes = 0

    # --- identity ---------------------------------------------------------------------------

    def entity_ids(self) -> tuple[str, ...]:
        ids = (
            self.current_id,
            self.enable_id,
            self.phases_id,
            self.power_id,
            self.soc_id,
            self.connected_id,
        )
        return tuple(e for e in ids if e is not None)

    def roles(self) -> dict[str, str]:
        out = {
            "current": self.current_id,
            "enable": self.enable_id,
            "charger_power": self.power_id,
        }
        if self.phases_id is not None:
            out["phases"] = self.phases_id
        if self.soc_id is not None:
            out["vehicle_soc"] = self.soc_id
        if self.connected_id is not None:
            out["connected"] = self.connected_id
        return out

    def heartbeats(self) -> dict[str, float]:
        out = {self.power_id: REPORT_PERIOD_S}
        if self.soc_id is not None:
            out[self.soc_id] = REPORT_PERIOD_S
        return out

    # --- physics ----------------------------------------------------------------------------

    def connected_at(self, now: datetime | None) -> bool:
        if now is None or self._start is None:
            return self.p.arrive_s is None or self.p.arrive_s == 0
        offset = (now - self._start).total_seconds()
        if self.p.arrive_s is not None and offset < self.p.arrive_s:
            return False
        return self.p.depart_s is None or offset < self.p.depart_s

    def _drawing(self, at: datetime | None) -> bool:
        p = self.p
        return (
            self.connected_at(at)
            and self.enabled
            and self.current_a >= p.car_min_a
            and self.current_a > 0
            and self.soc_pct < p.car_max_soc_pct
            and (self._switching_until is None or (at is not None and at >= self._switching_until))
        )

    def power_w(self) -> float:
        if not self._drawing(self._t):
            return 0.0
        return self.current_a * self.p.voltage_v * self.active_phases

    def is_on(self) -> bool:
        return self.power_w() > 0

    def _breakpoints(self, after: datetime) -> Iterable[datetime]:
        out: list[datetime] = []
        if self._switching_until is not None and self._switching_until > after:
            out.append(self._switching_until)
        if self._start is not None:
            for offset in (self.p.arrive_s, self.p.depart_s):
                if offset is not None:
                    at = self._start + timedelta(seconds=offset)
                    if at > after:
                        out.append(at)
        return out

    def advance(self, now: datetime, outdoor_c: float) -> None:
        if self._start is None:
            self._start = now
        super().advance(now, outdoor_c)

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        del outdoor_c
        if not self._drawing(t0):
            return
        dt_h = (t1 - t0).total_seconds() / 3600.0
        drawn_wh = self.current_a * self.p.voltage_v * self.active_phases * dt_h
        self.charged_wh += drawn_wh
        stored = drawn_wh * self.p.efficiency
        self.soc_pct = min(
            self.p.car_max_soc_pct, self.soc_pct + stored / self.p.capacity_wh * 100.0
        )

    # --- HA side ----------------------------------------------------------------------------

    def report(self) -> None:
        self._rep = (self.current_a, self.enabled, self.active_phases)

    def states(self, now: datetime) -> list[StateOut]:
        current, enabled, phases = self._rep
        p = self.p
        out = [
            StateOut(
                self.current_id,
                _text(current),
                {
                    "friendly_name": f"Sim {self.id} charging current",
                    "unit_of_measurement": "A",
                    "device_class": "current",
                    "min": p.current_min,
                    "max": p.current_max,
                    "step": p.current_step,
                    "mode": "slider",
                },
            ),
            StateOut(
                self.enable_id,
                "on" if enabled else "off",
                {"friendly_name": f"Sim {self.id} charging"},
            ),
            StateOut(
                self.power_id,
                _text(self.power_w() if self._t is not None else 0.0),
                {
                    "friendly_name": f"Sim {self.id} charging power",
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                },
            ),
        ]
        if self.phases_id is not None:
            out.append(
                StateOut(
                    self.phases_id,
                    str(phases),
                    {"friendly_name": f"Sim {self.id} phases", "options": ["1", "3"]},
                )
            )
        if self.soc_id is not None:
            out.append(
                StateOut(
                    self.soc_id,
                    _text(self.soc_pct),
                    {
                        "friendly_name": f"Sim {self.id} vehicle SoC",
                        "unit_of_measurement": "%",
                        "device_class": "battery",
                        "state_class": "measurement",
                    },
                )
            )
        if self.connected_id is not None:
            out.append(
                StateOut(
                    self.connected_id,
                    "on" if self.connected_at(now) else "off",
                    {"friendly_name": f"Sim {self.id} vehicle connected", "device_class": "plug"},
                )
            )
        return out

    def _set_current(self, value: float) -> None:
        self.current_a = value

    def _set_enabled(self, on: bool) -> None:
        self.enabled = on

    def _set_phases(self, phases: int) -> None:
        if phases != self.active_phases:
            self.phase_changes += 1
            if self.p.phase_switch_s > 0 and self._t is not None:
                self._switching_until = self._t + timedelta(seconds=self.p.phase_switch_s)
        self.active_phases = phases

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        del now
        p = self.p
        if entity_id == self.current_id and (domain, service) == ("number", "set_value"):
            value = float(data["value"])
            if not p.current_min <= value <= p.current_max:
                raise validation_error(
                    f"Value {value} for {entity_id} is outside valid range "
                    f"{p.current_min} - {p.current_max}."
                )
            return Effect(p.call_s, (Step(0.0, partial(self._set_current, value)),), p.push_delay_s)
        if (
            entity_id == self.enable_id
            and domain == "switch"
            and service in ("turn_on", "turn_off")
        ):
            on = service == "turn_on"
            return Effect(p.call_s, (Step(0.0, partial(self._set_enabled, on)),), p.push_delay_s)
        if entity_id == self.phases_id and (domain, service) == ("select", "select_option"):
            option = str(data["option"])
            if option not in ("1", "3"):
                raise validation_error(f"Option {option} is not valid for entity {entity_id}")
            target = int(option)
            return Effect(p.call_s, (Step(0.0, partial(self._set_phases, target)),), p.push_delay_s)
        raise validation_error(f"{entity_id} does not support {domain}.{service}")

    def manual(self, action: str, value: float | str | None) -> Effect:
        if action in ("turn_on", "turn_off"):
            on = action == "turn_on"
            return Effect(0.0, (Step(0.0, partial(self._set_enabled, on)),), self.p.push_delay_s)
        if action == "set_value" and value is not None and not isinstance(value, str):
            target = _clamp(float(value), self.p.current_min, self.p.current_max)
            return Effect(
                0.0, (Step(0.0, partial(self._set_current, target)),), self.p.push_delay_s
            )
        return super().manual(action, value)


# --- shiftable program (PR 6-L) ---------------------------------------------------------------


class ProgramParams(_Frozen):
    """A washing machine / dishwasher / dryer with a programme profile (PR 6-L).

    ``method``: ``plug`` (a smart plug ``switch.sim_<id>`` in front of it), ``remote`` (a
    remote-start switch ``switch.sim_<id>_start``), ``notify`` (only the power sensor). The user
    *loads* the appliance (a manual ``turn_on`` on the device: programme selected, start pressed)
    at ``loaded_at_s`` (``None`` = only by a manual event). A loaded appliance first draws
    ``lag_w`` (door lock, water intake) for the first ``lag_s`` of its profile (W per 15 minutes),
    which lasts ``slot_s`` x the number of values. With
    the plug off it draws nothing and the programme pauses; when the power returns it resumes
    if ``resumes`` (else the programme is lost). A remote start begins the programme of a loaded
    appliance (a notify or plug appliance runs as soon as it is loaded and powered).
    """

    kind: Literal["program"] = "program"
    method: Literal["plug", "remote", "notify"] = "plug"
    profile_w: tuple[float, ...] = Field(default=(2000.0, 480.0, 240.0, 240.0), min_length=1)
    slot_s: float = Field(default=900.0, gt=0)
    lag_s: float = Field(default=120.0, ge=0)
    lag_w: float = Field(default=2.0, ge=0)
    standby_w: float = Field(default=0.5, ge=0)
    resumes: bool = True
    initial_plug_on: bool = True
    loaded_at_s: float | None = None
    push_delay_s: float = Field(default=0.0, ge=0)
    call_s: float = Field(default=0.1, ge=0)


class SimProgram(SimDevice):
    """See :class:`ProgramParams`. ``runs`` counts completed programmes, ``interruptions`` the
    times the power was cut while the programme drew more than ``lag_w``."""

    kind: ClassVar[str] = "program"

    def __init__(self, device_id: str, params: ProgramParams) -> None:
        super().__init__(device_id)
        self.p = params
        base = f"sim_{device_id}"
        self.switch_id = (
            f"switch.{base}"
            if params.method == "plug"
            else f"switch.{base}_start"
            if params.method == "remote"
            else None
        )
        self.power_id = f"sensor.{base}_power"
        self.plug_on = params.initial_plug_on if params.method == "plug" else True
        self.remote_on = False
        self.loaded = False
        self.started = False
        """The programme was started (remote) - plug and notify appliances start when loaded."""
        self.elapsed_s = 0.0
        self.runs = 0
        self.interruptions = 0
        self.lost = 0
        self.started_at: list[datetime] = []
        self.finished_at: list[datetime] = []
        self._origin: datetime | None = None
        self._rep_switch = self.plug_on if params.method == "plug" else self.remote_on

    # --- identity -----------------------------------------------------------------------------

    def entity_ids(self) -> tuple[str, ...]:
        return (self.power_id,) if self.switch_id is None else (self.switch_id, self.power_id)

    def roles(self) -> dict[str, str]:
        out = {"power": self.power_id}
        if self.p.method == "plug" and self.switch_id is not None:
            out["switch"] = self.switch_id
        elif self.switch_id is not None:
            out["start"] = self.switch_id
        return out

    def heartbeats(self) -> dict[str, float]:
        return {self.power_id: REPORT_PERIOD_S}

    # --- physics ------------------------------------------------------------------------------

    @property
    def runtime_s(self) -> float:
        return self.p.slot_s * len(self.p.profile_w)

    def _running(self) -> bool:
        if not self.loaded or not self.plug_on:
            return False
        return self.started or self.p.method != "remote"

    def power_w(self) -> float:
        if not self.plug_on:
            return 0.0
        if not self._running():
            return self.p.standby_w
        if self.elapsed_s < self.p.lag_s:
            return self.p.lag_w
        slot = int(self.elapsed_s // self.p.slot_s)
        return self.p.profile_w[min(slot, len(self.p.profile_w) - 1)]

    def is_on(self) -> bool:
        return self._running()

    def _breakpoints(self, after: datetime) -> Iterable[datetime]:
        if self._origin is not None and self.p.loaded_at_s is not None and not self.loaded:
            yield self._origin + timedelta(seconds=self.p.loaded_at_s)
        if self._running():
            yield after + timedelta(seconds=max(0.001, self.runtime_s - self.elapsed_s))

    def advance(self, now: datetime, outdoor_c: float) -> None:
        if self._origin is None:
            self._origin = now
        super().advance(now, outdoor_c)

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        del outdoor_c
        if self._running():
            self.elapsed_s += (t1 - t0).total_seconds()

    def _reached(self, at: datetime) -> None:
        if (
            self._origin is not None
            and self.p.loaded_at_s is not None
            and not self.loaded
            and self.runs == 0
            and at >= self._origin + timedelta(seconds=self.p.loaded_at_s)
        ):
            self._load(at)
        if self._running() and self.elapsed_s >= self.runtime_s - 1e-6:
            self.runs += 1
            self.finished_at.append(at)
            self.loaded = False
            self.started = False
            self.elapsed_s = 0.0

    def _load(self, at: datetime | None) -> None:
        if self.loaded:
            return
        self.loaded = True
        self.elapsed_s = 0.0
        if at is not None:
            self.started_at.append(at)

    def _set_plug(self, on: bool) -> None:
        if self.plug_on and not on and self._running():
            if self.elapsed_s > self.p.lag_s:
                self.interruptions += 1
            if not self.p.resumes:
                self.loaded = False
                self.started = False
                self.elapsed_s = 0.0
                self.lost += 1
        self.plug_on = on

    def _set_remote(self, on: bool) -> None:
        self.remote_on = on
        if on and self.loaded:
            self.started = True

    # --- HA side ------------------------------------------------------------------------------

    def report(self) -> None:
        self._rep_switch = self.plug_on if self.p.method == "plug" else self.remote_on

    def states(self, now: datetime) -> list[StateOut]:
        del now
        out = [
            StateOut(
                self.power_id,
                _text(self.power_w() if self._t is not None else 0.0),
                {
                    "friendly_name": f"Sim {self.id} power",
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                },
            )
        ]
        if self.switch_id is not None:
            out.insert(
                0,
                StateOut(
                    self.switch_id,
                    "on" if self._rep_switch else "off",
                    {"friendly_name": f"Sim {self.id} switch"},
                ),
            )
        return out

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        del now, data
        if self.switch_id is None or entity_id != self.switch_id or domain != "switch":
            raise validation_error(f"{entity_id} does not support {domain}.{service}")
        if service not in ("turn_on", "turn_off"):
            raise validation_error(f"{entity_id} does not support {domain}.{service}")
        on = service == "turn_on"
        change = partial(self._set_plug if self.p.method == "plug" else self._set_remote, on)
        return Effect(self.p.call_s, (Step(0.0, change),), self.p.push_delay_s)

    def manual(self, action: str, value: float | str | None) -> Effect:
        """``turn_on``: the user loads the appliance and presses start (``turn_off`` pulls the
        plug of a plug appliance)."""
        if action == "turn_on":
            return Effect(0.0, (Step(0.0, partial(self._load, self._t)),), self.p.push_delay_s)
        if action == "turn_off" and self.p.method == "plug":
            return Effect(0.0, (Step(0.0, partial(self._set_plug, False)),), self.p.push_delay_s)
        return super().manual(action, value)


class LoadpointParams(_Frozen):
    """An evcc loadpoint with a vehicle (PR 6-J): no Home Assistant entities, evcc's MQTT API.

    The fake evcc (:class:`suncore.sim.fake_evcc.FakeEvccBus`) publishes the retained values and
    takes the ``/set`` messages; its regulation is a model, not evcc's code: ``smart`` charges
    with the PV surplus (own draw minus the grid import of the previous step), at least
    ``min_current_a``, at most ``maxCurrent``, with ``start_delay_s`` / ``stop_delay_s`` of
    hysteresis; ``now`` draws ``maxCurrent``; ``off`` nothing. The car stops at ``limitSoc`` and at
    ``car_max_soc_pct``. ``reset_mode_on_disconnect`` models evcc resetting the mode to its
    default when the car leaves (``UNVERIFIED`` behaviour, see ``templates/device/evcc.loadpoint``).
    """

    kind: Literal["loadpoint"] = "loadpoint"
    topic_prefix: str = "evcc"
    loadpoint_id: int = Field(default=1, ge=1, le=64)
    initial_mode: Literal["off", "smart", "now"] = "off"
    initial_max_current_a: float = Field(default=16.0, gt=0)
    initial_limit_soc_pct: int = Field(default=100, ge=0, le=100)
    min_current_a: float = Field(default=6.0, gt=0)
    max_current_a: float = Field(default=16.0, gt=0)
    phases: Literal[1, 3] = 3
    voltage_v: float = Field(default=230.0, gt=0)
    capacity_wh: float = Field(default=60_000.0, gt=0)
    efficiency: float = Field(default=0.9, gt=0, le=1)
    initial_soc_pct: float = Field(default=40.0, ge=0, le=100)
    car_max_soc_pct: float = Field(default=100.0, ge=0, le=100)
    arrive_s: float | None = Field(default=None, ge=0)
    depart_s: float | None = Field(default=None, gt=0)
    start_delay_s: float = Field(default=30.0, ge=0)
    stop_delay_s: float = Field(default=30.0, ge=0)
    reset_mode_on_disconnect: bool = False
    always_charge: bool = False
    """evcc imports from the grid in ``smart`` by its own rules (a vehicle plan, a smart-cost
    limit, ``alwaysCharge``): it draws ``maxCurrent`` regardless of the surplus."""


def _number(value: float, decimals: int = 3) -> str:
    """evcc's float payloads: up to three decimals, no trailing zeros."""
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")


class SimLoadpoint(SimDevice):
    """See :class:`LoadpointParams`."""

    kind: ClassVar[str] = "loadpoint"

    def __init__(self, device_id: str, params: LoadpointParams) -> None:
        super().__init__(device_id)
        self.p = params
        self.mode: str = params.initial_mode
        self.max_current_a = params.initial_max_current_a
        self.limit_soc_pct = params.initial_limit_soc_pct
        self.soc_pct = params.initial_soc_pct
        self.current_a = 0.0
        self.charged_wh = 0.0
        self.bus: FakeEvccBus | None = None
        self.grid_w: Callable[[], float] = lambda: 0.0
        """The site's grid power (+ = import) of the previous step; set by the runner."""
        self._start: datetime | None = None
        self._want_since: datetime | None = None
        self._stop_since: datetime | None = None
        self._was_connected = False
        self.hide_limit_soc = False
        """evcc publishes no ``limitSoc`` (a vehicle without one); a test sets it."""

    # --- identity ---------------------------------------------------------------------------

    def entity_ids(self) -> tuple[str, ...]:
        return ()  # evcc is reached over MQTT, not through Home Assistant

    def roles(self) -> dict[str, str]:
        return {}

    def attach(self, bus: FakeEvccBus, grid_w: Callable[[], float]) -> None:
        """Connect the loadpoint to the fake evcc: register the setter, publish the start state."""
        self.bus = bus
        self.grid_w = grid_w
        bus.register(self.p.topic_prefix, self.p.loadpoint_id, self._set)
        self.publish_all()

    # --- physics ----------------------------------------------------------------------------

    def connected_at(self, now: datetime | None) -> bool:
        if now is None or self._start is None:
            return self.p.arrive_s is None or self.p.arrive_s == 0
        offset = (now - self._start).total_seconds()
        if self.p.arrive_s is not None and offset < self.p.arrive_s:
            return False
        return self.p.depart_s is None or offset < self.p.depart_s

    def _can_charge(self, at: datetime | None) -> bool:
        return (
            self.connected_at(at)
            and self.soc_pct < min(self.p.car_max_soc_pct, float(self.limit_soc_pct))
            and self.mode != "off"
        )

    def power_w(self) -> float:
        return self.current_a * self.p.voltage_v * self.p.phases

    def is_on(self) -> bool:
        return self.power_w() > 0

    def _breakpoints(self, after: datetime) -> Iterable[datetime]:
        out: list[datetime] = []
        if self._start is not None:
            for offset in (self.p.arrive_s, self.p.depart_s):
                if offset is not None:
                    at = self._start + timedelta(seconds=offset)
                    if at > after:
                        out.append(at)
        return out

    def advance(self, now: datetime, outdoor_c: float) -> None:
        if self._start is None:
            self._start = now
        super().advance(now, outdoor_c)
        self.publish_all()

    def _regulate(self, t: datetime) -> None:
        """evcc's control law for the sub-step starting at ``t`` (a model, see the params)."""
        p = self.p
        if not self._can_charge(t):
            self.current_a = 0.0
            self._want_since = self._stop_since = None
            return
        if self.mode == "now" or (self.mode == "smart" and p.always_charge):
            self.current_a = self.max_current_a
            return
        per_a = p.voltage_v * p.phases
        want_a = min(self.max_current_a, (self.power_w() - self.grid_w()) / per_a)
        if want_a >= p.min_current_a:
            self._stop_since = None
            if self.current_a > 0:
                self.current_a = max(p.min_current_a, math.floor(want_a * 10) / 10)
            else:
                self._want_since = self._want_since or t
                if (t - self._want_since).total_seconds() >= p.start_delay_s:
                    self.current_a = max(p.min_current_a, math.floor(want_a * 10) / 10)
        else:
            self._want_since = None
            if self.current_a > 0:
                self._stop_since = self._stop_since or t
                if (t - self._stop_since).total_seconds() >= p.stop_delay_s:
                    self.current_a = 0.0
                    self._stop_since = None

    def _integrate(self, t0: datetime, t1: datetime, outdoor_c: float) -> None:
        del outdoor_c
        connected = self.connected_at(t0)
        if self._was_connected and not connected and self.p.reset_mode_on_disconnect:
            self.mode = self.p.initial_mode
        self._was_connected = connected
        self._regulate(t0)
        if self.current_a <= 0:
            return
        drawn_wh = self.power_w() * (t1 - t0).total_seconds() / 3600.0
        self.charged_wh += drawn_wh
        stored_pct = drawn_wh * self.p.efficiency / self.p.capacity_wh * 100.0
        self.soc_pct = min(self.p.car_max_soc_pct, self.soc_pct + stored_pct)

    # --- the MQTT side ------------------------------------------------------------------------

    def _topics(self) -> dict[str, str]:
        connected = self.connected_at(self._t)
        topics = {
            "mode": self.mode,
            "maxCurrent": _number(self.max_current_a),
            "minCurrent": _number(self.p.min_current_a),
            "limitSoc": str(self.limit_soc_pct),
            "connected": "true" if connected else "false",
            "charging": "true" if self.current_a > 0 else "false",
            "chargePower": _number(self.power_w(), 1),
            "vehicleSoc": _number(self.soc_pct, 1) if connected else "0",
            "phasesActive": str(self.p.phases if self.current_a > 0 else 0),
        }
        if self.hide_limit_soc:
            del topics["limitSoc"]
        return topics

    def publish_all(self) -> None:
        """Publish every value that changed (evcc publishes on change, retained) and beat."""
        bus = self.bus
        if bus is None:
            return
        for key, payload in self._topics().items():
            bus.publish(self.p.topic_prefix, self.p.loadpoint_id, key, payload)
        bus.beat(self.p.topic_prefix)

    def _set(self, key: str, payload: str) -> bool:
        """evcc's setter: a bad value is logged and ignored (``core/loadpoint_api.go``)."""
        try:
            if key == "mode":
                if payload not in ("off", "smart", "now"):
                    return False
                self.mode = payload
            elif key == "maxCurrent":
                value = float(payload)
                if not self.p.min_current_a <= value <= self.p.max_current_a:
                    return False
                self.max_current_a = value
            elif key == "limitSoc":
                limit = int(payload)
                if not 0 <= limit <= 100:
                    return False
                self.limit_soc_pct = limit
            else:
                return False
        except ValueError:
            return False
        self.publish_all()
        return True

    # --- HA side (none) -----------------------------------------------------------------------

    def states(self, now: datetime) -> list[StateOut]:
        del now
        return []

    def report(self) -> None:
        self.publish_all()

    def call(
        self, now: datetime, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> Effect:
        del now, data
        raise validation_error(f"{entity_id} does not support {domain}.{service}")

    def manual(self, action: str, value: float | str | None) -> Effect:
        """The user changes evcc's mode in evcc's own app (``select_option`` with the mode)."""
        if action == "select_option" and value in ("off", "smart", "now"):
            return Effect(0.0, (Step(0.0, partial(self._set_mode, str(value))),), 0.0)
        return super().manual(action, value)

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.publish_all()


AnyDeviceParams = (
    ClimateParams
    | SwitchParams
    | NumberParams
    | WaterHeaterParams
    | WallboxParams
    | LoadpointParams
    | ProgramParams
    | HeatPumpParams
)
AnyParams = AnyDeviceParams
DeviceParams = Annotated[AnyDeviceParams, Field(discriminator="kind")]


def build_device(device_id: str, params: AnyDeviceParams) -> SimDevice:  # noqa: PLR0911
    """The simulated device for ``params``."""
    if isinstance(params, WallboxParams):
        return SimWallbox(device_id, params)
    if isinstance(params, ProgramParams):
        return SimProgram(device_id, params)
    if isinstance(params, LoadpointParams):
        return SimLoadpoint(device_id, params)
    if isinstance(params, ClimateParams):
        return SimClimate(device_id, params)
    if isinstance(params, SwitchParams):
        return SimSwitchLoad(device_id, params)
    if isinstance(params, NumberParams):
        return SimNumberLoad(device_id, params)
    if isinstance(params, HeatPumpParams):
        return SimHeatPump(device_id, params)
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
    specs: Sequence[tuple[str, AnyDeviceParams]],
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
    "AnyParams",
    "ClimateParams",
    "DeviceParams",
    "Effect",
    "HeatPumpParams",
    "HourlyCounter",
    "LoadpointParams",
    "NumberParams",
    "PolledSensor",
    "ProgramParams",
    "ServiceError",
    "SimClimate",
    "SimDevice",
    "SimDeviceSet",
    "SimHeatPump",
    "SimLoadpoint",
    "SimNumberLoad",
    "SimProgram",
    "SimSwitchLoad",
    "SimWallbox",
    "SimWaterHeater",
    "StateOut",
    "Step",
    "SwitchParams",
    "WallboxParams",
    "WaterHeaterParams",
    "build_device",
    "build_device_set",
    "invalid_format",
    "validate_schema",
    "validation_error",
]
