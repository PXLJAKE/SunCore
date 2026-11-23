# SPDX-License-Identifier: Apache-2.0
"""Demo entities beyond the five site sensors: devices, price and forecast (Phase 2-3).

They give the setup wizard, the template conformance tests and the controller's shadow-mode
demo real content:

* an LG-ThinQ-like air conditioner ``climate.sim_living_room_ac`` (``suncore.sim.devices``
  ``SimClimate``: room RC model, inverter compressor, delayed cloud push) with an hourly
  quantised Wh counter (``sensor.sim_living_room_ac_energy_today``, reset at local midnight)
  and the previous day's total (``..._energy_yesterday``, updated at midnight);
* the same unit a second time, as the HACS integration ``smartthinq_sensors`` creates it
  (another registry device with the same device name): a second climate entity
  ``climate.sim_living_room_ac_smartthinq`` and a measured power sensor
  ``sensor.sim_living_room_ac_energy_current`` (W) of the air conditioner's *real* power,
  refreshed only every 5 minutes, like the real polling (see ``device-integrations.md``);
* a plug ``switch.sim_pool_pump`` with ``sensor.sim_pool_pump_power`` and an energy counter;
* a number entity ``number.sim_heating_rod_limit`` (the W limit of a load, which draws it);
* two stepped water heaters (Phase 3 templates ``generic.water_heater_steps`` / ``_relays``):
  ``select.sim_water_heater_level`` with its probe ``sensor.sim_water_heater_temperature`` (one
  registry device) and three relay switches ``switch.sim_water_heater_relay_1..3`` (another
  registry device), with a tank model and a maximum-temperature cut-off;
* a Tibber-like price sensor ``sensor.sim_electricity_price`` (``EUR/kWh``, 15 min slots);
* Solcast-like day sensors ``sensor.sim_solcast_forecast_today`` / ``..._tomorrow`` (kWh)
  whose ``detailedForecast`` attribute holds 30 min slots in kW average power (P50/P10/P90)
  derived from the virtual house's PV model with deterministic noise
  (``suncore.sim.forecasts``), Open-Meteo-like sensors
  ``sensor.sim_open_meteo_energy_production_today`` / ``..._tomorrow`` (``wh_period_15m``) and a
  template sensor ``sensor.sim_pv_forecast_template`` (points ``{start, power_w}``), all
  refreshed hourly of house time;
* kWh energy counters for grid, PV and battery so the fake Energy dashboard preferences have
  real statistics.

The devices are **physical**: their draw is part of the house energy balance
(``grid = base load + devices + battery - pv``, :meth:`DemoDevices.devices_power_w`), a
write through the fake HA connection changes them, and the controller can run on them. In the
default demo a *demo user* operates the unit and the pump by the clock (the air conditioner in
sunny afternoons, the pump 10:00-14:00) so there is something to observe in shadow mode. A
scenario with its own ``devices`` replaces the default devices and the demo user. Everything
is deterministic and driven by the simulated clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

from suncore.sim.devices import (
    ClimateParams,
    NumberParams,
    SimClimate,
    SimDevice,
    SimDeviceSet,
    SimSwitchLoad,
    SwitchParams,
    WaterHeaterParams,
    build_device_set,
)
from suncore.sim.forecasts import (
    SOLCAST_SLOT_MIN,
    SimForecastModel,
)
from suncore.sim.house import HouseState
from suncore.sim.scenario import Scenario

AC_ID: Final = "climate.sim_living_room_ac"
AC_ENERGY_TODAY_ID: Final = "sensor.sim_living_room_ac_energy_today"
AC_ENERGY_YESTERDAY_ID: Final = "sensor.sim_living_room_ac_energy_yesterday"
AC_B_ID: Final = "climate.sim_living_room_ac_smartthinq"
AC_B_POWER_ID: Final = "sensor.sim_living_room_ac_energy_current"
POOL_SWITCH_ID: Final = "switch.sim_pool_pump"
POOL_POWER_ID: Final = "sensor.sim_pool_pump_power"
POOL_ENERGY_ID: Final = "sensor.sim_pool_pump_energy"
ROD_LIMIT_ID: Final = "number.sim_heating_rod_limit"
WATER_HEATER_LEVEL_ID: Final = "select.sim_water_heater_level"
WATER_HEATER_TEMP_ID: Final = "sensor.sim_water_heater_temperature"
WATER_HEATER_RELAY_IDS: Final[tuple[str, ...]] = (
    "switch.sim_water_heater_relay_1",
    "switch.sim_water_heater_relay_2",
    "switch.sim_water_heater_relay_3",
)
PRICE_ID: Final = "sensor.sim_electricity_price"
FORECAST_TODAY_ID: Final = "sensor.sim_solcast_forecast_today"
FORECAST_TOMORROW_ID: Final = "sensor.sim_solcast_forecast_tomorrow"
OPEN_METEO_TODAY_ID: Final = "sensor.sim_open_meteo_energy_production_today"
OPEN_METEO_TOMORROW_ID: Final = "sensor.sim_open_meteo_energy_production_tomorrow"
TEMPLATE_FORECAST_ID: Final = "sensor.sim_pv_forecast_template"
WORKDAY_ID: Final = "binary_sensor.sim_workday_sensor"
"""Workday-integration-like sensor (``on`` Mon-Fri, deterministic; no holidays)."""
WEATHER_ID: Final = "weather.sim_home"
"""Weather entity: ``temperature`` (°C) from the scenario's outdoor model; hourly forecast via
the read-only action ``weather.get_forecasts``."""
GRID_IMPORT_KWH_ID: Final = "sensor.sim_grid_energy_import"
GRID_EXPORT_KWH_ID: Final = "sensor.sim_grid_energy_export"
PV_KWH_ID: Final = "sensor.sim_pv_energy"
BATTERY_IN_KWH_ID: Final = "sensor.sim_battery_energy_in"
BATTERY_OUT_KWH_ID: Final = "sensor.sim_battery_energy_out"

SIM_DEVICE_ENTITY_IDS: Final[tuple[str, ...]] = (
    AC_ID,
    AC_ENERGY_TODAY_ID,
    AC_ENERGY_YESTERDAY_ID,
    AC_B_ID,
    AC_B_POWER_ID,
    POOL_SWITCH_ID,
    POOL_POWER_ID,
    POOL_ENERGY_ID,
    ROD_LIMIT_ID,
    WATER_HEATER_LEVEL_ID,
    WATER_HEATER_TEMP_ID,
    *WATER_HEATER_RELAY_IDS,
    PRICE_ID,
    FORECAST_TODAY_ID,
    FORECAST_TOMORROW_ID,
    OPEN_METEO_TODAY_ID,
    OPEN_METEO_TOMORROW_ID,
    TEMPLATE_FORECAST_ID,
    WORKDAY_ID,
    WEATHER_ID,
    GRID_IMPORT_KWH_ID,
    GRID_EXPORT_KWH_ID,
    PV_KWH_ID,
    BATTERY_IN_KWH_ID,
    BATTERY_OUT_KWH_ID,
)
"""All additional demo entities (the five site sensors are ``SIM_ENTITY_IDS``)."""

AC_POWER_W: Final = 900.0
"""Highest electrical power of the demo air conditioner (compressor flat out)."""
AC_B_POLL_S: Final = 300
"""The ``smartthinq_sensors`` power sensor is polled every 5 minutes."""
AC_STANDBY_W: Final = 5.0
"""Electrical power of the demo air conditioner in standby."""
POOL_PUMP_POWER_W: Final = 750.0
PRICE_CURRENCY: Final = "EUR"


def _gauss(x: float, centre: float, sigma: float) -> float:
    return math.exp(-0.5 * ((x - centre) / sigma) ** 2)


def sim_price(when: datetime, profile: str = "default") -> float:
    """Synthetic dynamic price in EUR/kWh for the 15 min slot containing ``when``.

    ``profile`` ``negative_midday`` deepens the midday dip so that the price is negative
    between about 11:30 and 14:30 (the minimum is -0.08 EUR/kWh at 13:00).
    """
    hour = when.hour + (when.minute // 15) * 0.25
    dip = 0.38 if profile == "negative_midday" else 0.10
    sigma = 2.0 if profile == "negative_midday" else 2.5
    price = 0.30 + 0.12 * _gauss(hour, 19.0, 2.0) - dip * _gauss(hour, 13.0, sigma)
    if hour < 5:
        price -= 0.04
    return round(price, 3)


@dataclass(frozen=True, slots=True)
class DemoEntity:
    """One demo entity: state text and attributes."""

    entity_id: str
    state: str
    attributes: dict[str, Any]


@dataclass(slots=True)
class _Counters:
    pool_kwh: float = 0.0
    grid_import_kwh: float = 0.0
    grid_export_kwh: float = 0.0
    pv_kwh: float = 0.0
    battery_in_kwh: float = 0.0
    battery_out_kwh: float = 0.0


AC_DEVICE_ID: Final = "living_room_ac"
POOL_DEVICE_ID: Final = "pool_pump"
ROD_DEVICE_ID: Final = "heating_rod"
WATER_HEATER_DEVICE_ID: Final = "water_heater"
WATER_HEATER_RELAYS_DEVICE_ID: Final = "water_heater_relays"


def default_demo_devices() -> SimDeviceSet:
    """The default demo devices (ids and entities above), all off at the start."""
    return build_device_set(
        [
            (
                AC_DEVICE_ID,
                ClimateParams(
                    entity_id=AC_ID,
                    friendly_name="Sim living room AC",
                    initial_temp_c=24.5,
                    initial_mode="off",
                    initial_setpoint_c=24.0,
                    default_on_mode="cool",
                    hvac_modes=("off", "cool", "auto", "fan_only"),
                    min_temp_c=18.0,
                    max_temp_c=30.0,
                    nominal_w=AC_POWER_W,
                    standby_w=AC_STANDBY_W,
                    ua_w_per_k=250.0,
                    internal_gain_w=500.0,
                    energy_today_entity_id=AC_ENERGY_TODAY_ID,
                    energy_yesterday_entity_id=AC_ENERGY_YESTERDAY_ID,
                    power_entity_id=AC_B_POWER_ID,
                    power_poll_s=AC_B_POLL_S,
                    mirror_entity_id=AC_B_ID,
                    mirror_friendly_name="Sim living room AC (SmartThinQ)",
                ),
            ),
            (
                POOL_DEVICE_ID,
                SwitchParams(
                    entity_id=POOL_SWITCH_ID,
                    friendly_name="Sim pool pump",
                    nominal_w=POOL_PUMP_POWER_W,
                    power_entity_id=POOL_POWER_ID,
                ),
            ),
            (
                ROD_DEVICE_ID,
                NumberParams(
                    entity_id=ROD_LIMIT_ID,
                    friendly_name="Sim heating rod limit",
                    max=3000.0,
                    step=100.0,
                ),
            ),
            (
                WATER_HEATER_DEVICE_ID,
                WaterHeaterParams(
                    variant="select",
                    entity_id=WATER_HEATER_LEVEL_ID,
                    temperature_entity_id=WATER_HEATER_TEMP_ID,
                    friendly_name="Sim water heater",
                    initial_temp_c=48.0,
                ),
            ),
            (
                WATER_HEATER_RELAYS_DEVICE_ID,
                WaterHeaterParams(
                    variant="relays",
                    relay_entity_ids=WATER_HEATER_RELAY_IDS,
                    friendly_name="Sim water heater",
                    probe=False,
                    initial_temp_c=48.0,
                ),
            ),
        ]
    )


@dataclass(slots=True)
class DemoDevices:
    """Stateful model of the demo entities; call :meth:`step` once per house step."""

    scenario: Scenario
    devices: SimDeviceSet = field(init=False)
    """The physical devices (the scenario's, or the default demo devices)."""
    is_default: bool = field(init=False)
    _c: _Counters = field(default_factory=_Counters)
    _state: HouseState | None = None
    _forecast_key: tuple[Any, int] | None = None
    _forecasts: list[DemoEntity] | None = None
    _model: SimForecastModel = field(init=False)
    _cloud: list[float] = field(default_factory=list)
    _user_ac_on: bool = False
    _user_pool_on: bool = False

    def __post_init__(self) -> None:
        self._cloud = self.scenario.cloud_series()
        self._model = SimForecastModel(self.scenario, self._cloud_at)
        specs = [(spec.id, spec.physics) for spec in self.scenario.devices]
        self.is_default = not specs
        self.devices = build_device_set(specs) if specs else default_demo_devices()

    # --- model ----------------------------------------------------------------

    @staticmethod
    def ac_on(state: HouseState) -> bool:
        """The demo user cools in sunny afternoons: local 11:00-19:00 with PV above 2 kW."""
        return 11 <= state.time.hour < 19 and state.pv_w > 2000

    @staticmethod
    def pool_on(state: HouseState) -> bool:
        """The demo user runs the pool pump 10:00-14:00 local."""
        return 10 <= state.time.hour < 14

    def outdoor_c(self, when: datetime) -> float:
        return self.scenario.outdoor.at(when)

    def advance_devices(self, when: datetime) -> None:
        """Integrate the device physics up to ``when`` (idempotent)."""
        self.devices.advance(when, self.outdoor_c(when))

    def devices_power_w(self) -> float:
        """The draw of all simulated devices now (the house balance includes it)."""
        return self.devices.total_power_w()

    def apply_now(self, device: SimDevice, action: str, value: float | str | None) -> None:
        """The user operates a device: the change and its report happen at once."""
        effect = device.manual(action, value)
        for step in effect.steps:
            step.change()
        device.report()

    def _demo_user(self, state: HouseState) -> None:
        ac = self.devices.devices.get(AC_DEVICE_ID)
        if isinstance(ac, SimClimate):
            wants = self.ac_on(state)
            if wants != self._user_ac_on:
                self._user_ac_on = wants
                if wants:
                    self.apply_now(ac, "turn_on", None)
                    self.apply_now(ac, "set_temperature", 24.0)
                else:
                    self.apply_now(ac, "turn_off", None)
        pool = self.devices.devices.get(POOL_DEVICE_ID)
        if isinstance(pool, SimSwitchLoad):
            wants = self.pool_on(state)
            if wants != self._user_pool_on:
                self._user_pool_on = wants
                self.apply_now(pool, "turn_on" if wants else "turn_off", None)

    def step(self, state: HouseState) -> None:
        """Integrate one house step."""
        c = self._c
        hours = self.scenario.step_s / 3600
        self.advance_devices(state.time)
        if self.is_default:
            self._demo_user(state)
            pool = self.devices.devices[POOL_DEVICE_ID]
            c.pool_kwh += pool.power_w() * hours / 1000
        c.grid_import_kwh += max(state.grid_w, 0.0) * hours / 1000
        c.grid_export_kwh += max(-state.grid_w, 0.0) * hours / 1000
        c.pv_kwh += state.pv_w * hours / 1000
        c.battery_in_kwh += max(state.battery_w, 0.0) * hours / 1000
        c.battery_out_kwh += max(-state.battery_w, 0.0) * hours / 1000
        self._state = state

    # --- entities ---------------------------------------------------------------

    def device_entity_ids(self) -> frozenset[str]:
        """Entities that belong to a physical device (published when they change)."""
        return frozenset(self.devices.entity_ids())

    def entities(self) -> list[DemoEntity]:
        """Current demo entities (empty before the first :meth:`step`)."""
        state = self._state
        if state is None:
            return []
        c = self._c
        forecasts = self._forecast_entities(state.time)
        entities = [
            DemoEntity(s.entity_id, s.state, s.attributes) for s in self.devices.states(state.time)
        ]
        entities.extend(
            [
                DemoEntity(
                    PRICE_ID,
                    f"{sim_price(state.time, self.scenario.prices.profile):.3f}",
                    {
                        "friendly_name": "Sim electricity price",
                        "unit_of_measurement": f"{PRICE_CURRENCY}/kWh",
                        "state_class": "measurement",
                    },
                ),
                *forecasts,
                self._workday(state.time),
                self._weather(state.time),
                self._kwh(GRID_IMPORT_KWH_ID, "Sim grid energy import", c.grid_import_kwh),
                self._kwh(GRID_EXPORT_KWH_ID, "Sim grid energy export", c.grid_export_kwh),
                self._kwh(PV_KWH_ID, "Sim PV energy", c.pv_kwh),
                self._kwh(BATTERY_IN_KWH_ID, "Sim battery energy in", c.battery_in_kwh),
                self._kwh(BATTERY_OUT_KWH_ID, "Sim battery energy out", c.battery_out_kwh),
            ]
        )
        if self.is_default:
            entities.append(self._kwh(POOL_ENERGY_ID, "Sim pool pump energy", c.pool_kwh))
        return entities

    @staticmethod
    def is_workday(day: date) -> bool:
        """The demo's workday calendar: Monday to Friday, no holidays."""
        return day.weekday() < 5

    def _workday(self, now: datetime) -> DemoEntity:
        return DemoEntity(
            WORKDAY_ID,
            "on" if self.is_workday(now.astimezone(self.scenario.start.tzinfo).date()) else "off",
            {
                "friendly_name": "Sim workday sensor",
                "workdays": ["mon", "tue", "wed", "thu", "fri"],
                "excludes": ["sat", "sun", "holiday"],
                "days_offset": 0,
            },
        )

    def _weather(self, now: datetime) -> DemoEntity:
        return DemoEntity(
            WEATHER_ID,
            "sunny" if self._cloud_at(now) >= 0.8 else "cloudy",
            {
                "friendly_name": "Sim home",
                "temperature": round(self.outdoor_c(now), 1),
                "temperature_unit": "°C",
            },
        )

    def hourly_weather(self, now: datetime, hours: int = 48) -> list[dict[str, Any]]:
        """``weather.get_forecasts`` items (type ``hourly``) from the next full hour on."""
        first = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return [
            {
                "datetime": (first + timedelta(hours=h)).isoformat(),
                "temperature": round(self.outdoor_c(first + timedelta(hours=h)), 1),
                "condition": "sunny",
            }
            for h in range(hours)
        ]

    @staticmethod
    def _kwh(entity_id: str, name: str, value: float) -> DemoEntity:
        return DemoEntity(
            entity_id,
            f"{value:.3f}",
            {
                "friendly_name": name,
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
            },
        )

    # --- forecast ---------------------------------------------------------------

    def _cloud_at(self, when: datetime) -> float:
        index = int((when - self.scenario.start).total_seconds() // self.scenario.step_s)
        if not self._cloud:
            return 1.0
        return self._cloud[min(max(index, 0), len(self._cloud) - 1)]

    def solar_forecast(self, now: datetime) -> dict[str, dict[str, float]]:
        """``energy/solar_forecast`` of the demo: the two entries linked in the Energy dashboard.

        ``sim_entry_forecast_solar``: hourly Wh, a zero at midnight dropped (Forecast.Solar);
        ``sim_entry_solcast_solar``: half-hourly Wh of the P50 (Solcast's energy platform).
        """
        return {
            "sim_entry_forecast_solar": self._model.forecast_solar_wh_hours(now),
            "sim_entry_solcast_solar": self._model.solcast_wh_hours(
                now.replace(minute=0, second=0, microsecond=0), 48, now
            ),
        }

    def _forecast_entities(self, now: datetime) -> list[DemoEntity]:
        key = (now.date(), now.hour)
        if self._forecasts is not None and self._forecast_key == key:
            return self._forecasts
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        model = self._model
        result: list[DemoEntity] = []
        for entity_id, name, day in (
            (FORECAST_TODAY_ID, "Sim Solcast forecast today", midnight),
            (FORECAST_TOMORROW_ID, "Sim Solcast forecast tomorrow", midnight + timedelta(days=1)),
        ):
            slots = model.solcast_day(day, now)
            total_kwh = sum(s["pv_estimate"] for s in slots) * SOLCAST_SLOT_MIN / 60
            result.append(
                DemoEntity(
                    entity_id,
                    f"{total_kwh:.2f}",
                    {
                        "friendly_name": name,
                        "unit_of_measurement": "kWh",
                        "device_class": "energy",
                        "state_class": "total",
                        "dayname": day.strftime("%A"),
                        "dataCorrect": True,
                        "detailedForecast": slots,
                    },
                )
            )
        for entity_id, name, day in (
            (OPEN_METEO_TODAY_ID, "Sim Open-Meteo energy production today", midnight),
            (
                OPEN_METEO_TOMORROW_ID,
                "Sim Open-Meteo energy production tomorrow",
                midnight + timedelta(days=1),
            ),
        ):
            wh_15m = model.open_meteo_day(day, now)
            result.append(
                DemoEntity(
                    entity_id,
                    f"{sum(wh_15m.values()) / 1000:.3f}",
                    {
                        "friendly_name": name,
                        "unit_of_measurement": "kWh",
                        "device_class": "energy",
                        "state_class": "total",
                        "wh_period_15m": wh_15m,
                    },
                )
            )
        points = model.template_points(midnight, 2, now)
        template_kwh = sum(p["power_w"] for p in points) * 0.5 / 1000
        result.append(
            DemoEntity(
                TEMPLATE_FORECAST_ID,
                f"{template_kwh:.2f}",
                {
                    "friendly_name": "Sim PV forecast (template sensor)",
                    "unit_of_measurement": "kWh",
                    "device_class": "energy",
                    "forecast": points,
                },
            )
        )
        self._forecast_key = key
        self._forecasts = result
        return result
