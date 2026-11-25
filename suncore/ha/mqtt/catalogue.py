# SPDX-License-Identifier: Apache-2.0
"""The entity catalogue v1 (plan Phase 6 WP3, ADR-0016 decision 4). Pure data, no I/O.

Object ids are stable: they form the ``unique_id`` and the topics. Names are rendered from
``mqtt.entity.<key>.name`` in the backend language; enum states from ``mqtt.state.<group>.<value>``.
Entities with ``command=True`` get a command topic and are announced only when the entity source
supports their ``key`` and a command port is wired (PR 6-D); the read-only entities are announced
when the entity source supports their ``key``.

The catalogue deliberately has **no energy-class sensors** that mirror the site meters (BRIEF
section 4.13: no double counting in the Energy dashboard).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from suncore.core.devices import DeviceKind


class Platform(StrEnum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    SWITCH = "switch"
    BUTTON = "button"
    SELECT = "select"
    NUMBER = "number"
    TEXT = "text"
    EVENT = "event"


class Scope(StrEnum):
    """Which HA device an entity belongs to."""

    SITE = "site"
    DEVICE = "device"
    BATTERY = "battery"


@dataclass(frozen=True, slots=True)
class EntitySpec:
    key: str
    """Catalogue id; the name key is ``mqtt.entity.<key>.name``."""
    platform: Platform
    scope: Scope
    suffix: str
    """Object id (site, battery) or the part after ``<device_id>_`` (device scope)."""
    kinds: frozenset[DeviceKind] | None = None
    """Device scope only: ``None`` = every kind."""
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    enum_group: str | None = None
    """Enum sensors: the states are ``mqtt.state.<group>.<value>`` for the values below."""
    enum_values: tuple[str, ...] = ()
    command: bool = False
    deadband_abs: float = 0.0
    deadband_rel: float = 0.0
    precision: int | None = None
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    """Number entities: the range the command handler enforces and HA's slider shows."""

    def object_id(self, device_id: str | None = None) -> str:
        if self.scope is Scope.DEVICE:
            if device_id is None:
                msg = "a device entity needs the device id"
                raise ValueError(msg)
            return f"{device_id}_{self.suffix}"
        return self.suffix

    @property
    def name_key(self) -> str:
        return f"mqtt.entity.{self.key}.name"


STATUS_VALUES: Final = ("starting", "running", "emergency_stop", "ha_unavailable")
MODE_VALUES: Final = ("shadow", "assisted", "automatic")
EV_MODE_VALUES: Final = ("off", "now", "min_pv", "pv", "target")
DECISION_VALUES: Final = ("none", "accepted", "rejected", "shadow", "proposed", "held", "failed")

EVENT_TYPES: Final = (
    "cheap_window_start",
    "cheap_window_end",
    "device_switched",
    "proposal",
    "proposal_expired",
    "island_start",
    "island_end",
    "para14a_start",
    "para14a_end",
    "program_start_now",
    "program_done",
    "ev_target_at_risk",
    "error",
)
"""Types of the ``events`` entity. Producers: the gate's decisions (``device_switched``,
``proposal``, ``error``) and the notification rules (``suncore.notify.signals``); the type
``proposal_expired`` has none yet."""

_ALL = None
_THERMAL = frozenset({DeviceKind.CLIMATE, DeviceKind.WATER_HEATER, DeviceKind.HEAT_PUMP})


def _site(key: str, platform: Platform, **kw: object) -> EntitySpec:
    return EntitySpec(key=key, platform=platform, scope=Scope.SITE, suffix=key, **kw)  # type: ignore[arg-type]


def _device(
    key: str,
    platform: Platform,
    suffix: str,
    kinds: Iterable[DeviceKind] | None = _ALL,
    **kw: object,
) -> EntitySpec:
    return EntitySpec(
        key=key,
        platform=platform,
        scope=Scope.DEVICE,
        suffix=suffix,
        kinds=None if kinds is None else frozenset(kinds),
        **kw,  # type: ignore[arg-type]
    )


SITE_ENTITIES: Final[tuple[EntitySpec, ...]] = (
    _site(
        "status",
        Platform.SENSOR,
        device_class="enum",
        enum_group="status",
        enum_values=STATUS_VALUES,
    ),
    _site("mode", Platform.SENSOR, device_class="enum", enum_group="mode", enum_values=MODE_VALUES),
    _site("next_action", Platform.SENSOR),
    _site("plan_cost_today", Platform.SENSOR, device_class="monetary", precision=2),
    _site("savings_today", Platform.SENSOR, device_class="monetary", precision=2),
    _site("savings_month", Platform.SENSOR, device_class="monetary", precision=2),
    _site(
        "autarky_today",
        Platform.SENSOR,
        unit="%",
        state_class="measurement",
        deadband_abs=1.0,
        precision=0,
    ),
    # The mean absolute error of the PV forecast on the newest day with accuracy rows. The
    # ``accuracy_day`` table stores it in W (no actual energy to divide by), so the sensor is a
    # power, not a percentage (HA core: sensor device class ``power`` takes W, state class
    # ``measurement``; verified 2026-11-25, docs/research/mqtt-discovery.md).
    _site(
        "pv_forecast_error",
        Platform.SENSOR,
        device_class="power",
        unit="W",
        state_class="measurement",
        deadband_abs=5.0,
        precision=0,
    ),
    _site(
        "month_peak",
        Platform.SENSOR,
        device_class="power",
        unit="W",
        state_class="measurement",
        deadband_abs=10.0,
        deadband_rel=0.01,
        precision=0,
    ),
    _site(
        "available_surplus",
        Platform.SENSOR,
        device_class="power",
        unit="W",
        state_class="measurement",
        deadband_abs=10.0,
        deadband_rel=0.01,
        precision=0,
    ),
    _site(
        "grid_state",
        Platform.SENSOR,
        device_class="enum",
        enum_group="grid_state",
        enum_values=("normal", "limited", "island"),
    ),
    _site("healthy", Platform.BINARY_SENSOR),
    _site("shadow_mode", Platform.BINARY_SENSOR),
    _site("emergency_stop", Platform.BINARY_SENSOR),
    _site("para14a_active", Platform.BINARY_SENSOR),
    _site("island", Platform.BINARY_SENSOR),
    _site("auto", Platform.SWITCH, command=True),
    _site("replan", Platform.BUTTON, command=True),
    EntitySpec(
        key="emergency_stop_button",
        platform=Platform.BUTTON,
        scope=Scope.SITE,
        suffix="emergency_stop_button",
        command=True,
    ),
    _site("emergency_stop_clear", Platform.BUTTON, command=True),
    _site("battery_strategy", Platform.SELECT, command=True),
    _site("battery_reserve", Platform.NUMBER, unit="%", command=True),
    _site("battery_evening_target", Platform.NUMBER, unit="%", command=True),
    _site("events", Platform.EVENT),
)

DEVICE_ENTITIES: Final[tuple[EntitySpec, ...]] = (
    _device(
        "device_mode",
        Platform.SENSOR,
        "mode",
        device_class="enum",
        enum_group="mode",
        enum_values=MODE_VALUES,
    ),
    _device(
        "device_power",
        Platform.SENSOR,
        "power",
        device_class="power",
        unit="W",
        state_class="measurement",
        deadband_abs=10.0,
        deadband_rel=0.01,
        precision=0,
    ),
    _device(
        "device_last_decision",
        Platform.SENSOR,
        "last_decision",
        device_class="enum",
        enum_group="decision",
        enum_values=DECISION_VALUES,
    ),
    _device("device_auto", Platform.SWITCH, "auto", command=True),
    _device("device_boost", Platform.BUTTON, "boost", command=True),
    _device("device_pause", Platform.BUTTON, "pause", command=True),
    _device("device_resume", Platform.BUTTON, "resume", command=True),
    _device(
        "device_boost_minutes",
        Platform.NUMBER,
        "boost_minutes",
        unit="min",
        entity_category="config",
        command=True,
        precision=0,
        min_value=1,
        max_value=1440,
        step=1,
    ),
    _device("ev_mode", Platform.SELECT, "ev_mode", {DeviceKind.WALLBOX}, command=True),
    _device(
        "target_soc", Platform.NUMBER, "target_soc", {DeviceKind.WALLBOX}, unit="%", command=True
    ),
    _device(
        "target_energy",
        Platform.NUMBER,
        "target_energy",
        {DeviceKind.WALLBOX},
        unit="kWh",
        command=True,
    ),
    _device("target_time", Platform.TEXT, "target_time", {DeviceKind.WALLBOX}, command=True),
    _device("comfort_min", Platform.NUMBER, "comfort_min", _THERMAL, unit="°C", command=True),
    _device("comfort_max", Platform.NUMBER, "comfort_max", _THERMAL, unit="°C", command=True),
    # Read-only views (PR 6-O). The plan's controls (select/number/text for mode and target)
    # need the command path of WP4 and stay command entities above; until that exists these
    # sensors show the same values without offering a control that would do nothing.
    _device(
        "ev_mode_state",
        Platform.SENSOR,
        "ev_mode_state",
        {DeviceKind.WALLBOX},
        device_class="enum",
        enum_group="ev_mode",
        enum_values=EV_MODE_VALUES,
    ),
    _device(
        "ev_target_soc_state",
        Platform.SENSOR,
        "ev_target_soc_state",
        {DeviceKind.WALLBOX},
        unit="%",
        state_class="measurement",
        precision=0,
    ),
    _device(
        "ev_target_energy_state",
        Platform.SENSOR,
        "ev_target_energy_state",
        {DeviceKind.WALLBOX},
        unit="kWh",
        state_class="measurement",
        precision=1,
    ),
    _device(
        "ev_target_time_state",
        Platform.SENSOR,
        "ev_target_time_state",
        {DeviceKind.WALLBOX},
        device_class="timestamp",
    ),
    _device(
        "ev_plugged",
        Platform.BINARY_SENSOR,
        "ev_plugged",
        {DeviceKind.WALLBOX},
        device_class="plug",
    ),
    _device(
        "ev_phases",
        Platform.SENSOR,
        "ev_phases",
        {DeviceKind.WALLBOX},
        state_class="measurement",
        precision=0,
    ),
    _device(
        "ev_vehicle_soc",
        Platform.SENSOR,
        "ev_vehicle_soc",
        {DeviceKind.WALLBOX},
        device_class="battery",
        unit="%",
        state_class="measurement",
        deadband_abs=1.0,
        precision=0,
    ),
    _device(
        "hp_sg_state",
        Platform.SENSOR,
        "hp_sg_state",
        {DeviceKind.HEAT_PUMP},
        device_class="enum",
        enum_group="sg_state",
        enum_values=("1", "2", "3", "4"),
    ),
    _device(
        "hp_lift",
        Platform.SENSOR,
        "hp_lift",
        {DeviceKind.HEAT_PUMP},
        unit="K",
        state_class="measurement",
        deadband_abs=0.1,
        precision=1,
    ),
    _device("arm", Platform.BUTTON, "arm", {DeviceKind.PROGRAM}, command=True),
    _device("cancel", Platform.BUTTON, "cancel", {DeviceKind.PROGRAM}, command=True),
    _device("finish_by", Platform.TEXT, "finish_by", {DeviceKind.PROGRAM}, command=True),
    _device(
        "job_state",
        Platform.SENSOR,
        "job_state",
        {DeviceKind.PROGRAM},
        device_class="enum",
        enum_group="job_state",
        enum_values=("idle", "armed", "running", "done", "cancelled"),
    ),
    _device(
        "planned_start",
        Platform.SENSOR,
        "planned_start",
        {DeviceKind.PROGRAM},
        device_class="timestamp",
    ),
)

BATTERY_ENTITIES: Final[tuple[EntitySpec, ...]] = (
    EntitySpec(
        key="battery_action",
        platform=Platform.SENSOR,
        scope=Scope.BATTERY,
        suffix="battery_action",
        device_class="enum",
        enum_group="battery_action",
        enum_values=("idle", "charge", "discharge", "hold"),
    ),
    EntitySpec(
        key="battery_lease",
        platform=Platform.SENSOR,
        scope=Scope.BATTERY,
        suffix="battery_lease",
        device_class="enum",
        enum_group="battery_lease",
        enum_values=("none", "held", "released"),
    ),
)

ALL_ENTITIES: Final[tuple[EntitySpec, ...]] = SITE_ENTITIES + DEVICE_ENTITIES + BATTERY_ENTITIES


def specs_for_device(kind: DeviceKind) -> list[EntitySpec]:
    """The device-scope entities of a device of this kind."""
    return [s for s in DEVICE_ENTITIES if s.kinds is None or kind in s.kinds]


def spec_by_key(key: str) -> EntitySpec | None:
    return next((s for s in ALL_ENTITIES if s.key == key), None)
