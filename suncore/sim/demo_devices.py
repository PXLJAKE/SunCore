# SPDX-License-Identifier: Apache-2.0
"""Demo entities beyond the five site sensors: devices, price and forecast (Phase 2 WP3).

They give the setup wizard and the template conformance tests real content:

* an LG-ThinQ-like air conditioner ``climate.sim_living_room_ac`` with an hourly quantised
  Wh counter (``sensor.sim_living_room_ac_energy_today``, reset at local midnight) and the
  previous day's total (``..._energy_yesterday``, updated at midnight);
* the same unit a second time, as the HACS integration ``smartthinq_sensors`` creates it (another
  registry device with the same device name): a second climate entity
  ``climate.sim_living_room_ac_smartthinq`` and a measured power sensor
  ``sensor.sim_living_room_ac_energy_current`` (W) driven by the air conditioner's power and
  refreshed only every 5 minutes, like the real polling (see ``device-integrations.md``);
* a plug ``switch.sim_pool_pump`` with ``sensor.sim_pool_pump_power`` and an energy counter;
* a number entity ``number.sim_heating_rod_limit`` (W limit of a load);
* a Tibber-like price sensor ``sensor.sim_electricity_price`` (``EUR/kWh``, 15 min slots);
* Solcast-like day sensors ``sensor.sim_solcast_forecast_today`` / ``..._tomorrow`` (kWh)
  whose ``detailedForecast`` attribute holds 30 min slots in kW average power (P50/P10/P90)
  derived from the virtual house's PV model;
* kWh energy counters for grid, PV and battery so the fake Energy dashboard preferences have
  real statistics.

The device loads are *sub-loads of the house load* the house profile already contains: they
do not change the house physics or the energy balance (existing tests stay valid). Everything
is deterministic and driven by the simulated clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

from suncore.sim.house import HouseState, clear_sky_w
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
PRICE_ID: Final = "sensor.sim_electricity_price"
FORECAST_TODAY_ID: Final = "sensor.sim_solcast_forecast_today"
FORECAST_TOMORROW_ID: Final = "sensor.sim_solcast_forecast_tomorrow"
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
    PRICE_ID,
    FORECAST_TODAY_ID,
    FORECAST_TOMORROW_ID,
    GRID_IMPORT_KWH_ID,
    GRID_EXPORT_KWH_ID,
    PV_KWH_ID,
    BATTERY_IN_KWH_ID,
    BATTERY_OUT_KWH_ID,
)
"""All additional demo entities (the five site sensors are ``SIM_ENTITY_IDS``)."""

AC_POWER_W: Final = 900.0
AC_B_POLL_S: Final = 300
"""The ``smartthinq_sensors`` power sensor is polled every 5 minutes."""
AC_STANDBY_W: Final = 5.0
"""Electrical power of the demo air conditioner while it cools."""
POOL_PUMP_POWER_W: Final = 750.0
PRICE_CURRENCY: Final = "EUR"
SLOT_MIN: Final = 30
TOMORROW_CLOUD_FACTOR: Final = 0.8
"""Assumed cloud factor of the demo forecast for the next day."""


def _gauss(x: float, centre: float, sigma: float) -> float:
    return math.exp(-0.5 * ((x - centre) / sigma) ** 2)


def sim_price(when: datetime) -> float:
    """Synthetic dynamic price in EUR/kWh for the 15 min slot containing ``when``."""
    hour = when.hour + (when.minute // 15) * 0.25
    price = 0.30 + 0.12 * _gauss(hour, 19.0, 2.0) - 0.10 * _gauss(hour, 13.0, 2.5)
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
    ac_wh: float = 0.0
    ac_published_wh: int = 0
    ac_yesterday_wh: int = 0
    ac_hour: tuple[date, int] | None = None
    ac_day: date | None = None
    ac_b_slot: int | None = None
    ac_b_power_w: float = AC_STANDBY_W
    pool_kwh: float = 0.0
    grid_import_kwh: float = 0.0
    grid_export_kwh: float = 0.0
    pv_kwh: float = 0.0
    battery_in_kwh: float = 0.0
    battery_out_kwh: float = 0.0


@dataclass(slots=True)
class DemoDevices:
    """Stateful model of the demo entities; call :meth:`step` once per house step."""

    scenario: Scenario
    _c: _Counters = field(default_factory=_Counters)
    _state: HouseState | None = None
    _forecast_key: tuple[date, int] | None = None
    _forecasts: tuple[DemoEntity, DemoEntity] | None = None
    _cloud: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._cloud = self.scenario.cloud_series()

    # --- model ----------------------------------------------------------------

    @staticmethod
    def ac_on(state: HouseState) -> bool:
        """Cooling during sunny afternoons: local 11:00-19:00 with PV above 2 kW."""
        return 11 <= state.time.hour < 19 and state.pv_w > 2000

    @staticmethod
    def pool_on(state: HouseState) -> bool:
        """Pool pump runs 10:00-14:00 local."""
        return 10 <= state.time.hour < 14

    def step(self, state: HouseState) -> None:
        """Integrate one house step."""
        c = self._c
        hours = self.scenario.step_s / 3600
        day = state.time.date()
        if c.ac_day != day:
            if c.ac_day is not None:
                c.ac_yesterday_wh = round(c.ac_wh)
            c.ac_wh = 0.0
            c.ac_published_wh = 0
            c.ac_day = day
            c.ac_hour = (day, state.time.hour)
        elif c.ac_hour != (day, state.time.hour):
            c.ac_published_wh = round(c.ac_wh)  # the cloud counter updates once per hour
            c.ac_hour = (day, state.time.hour)
        if self.ac_on(state):
            c.ac_wh += AC_POWER_W * hours
        slot = int(state.time.timestamp() // AC_B_POLL_S)
        if c.ac_b_slot != slot:  # sample and hold: the sensor only polls every 5 minutes
            c.ac_b_slot = slot
            c.ac_b_power_w = AC_POWER_W if self.ac_on(state) else AC_STANDBY_W
        if self.pool_on(state):
            c.pool_kwh += POOL_PUMP_POWER_W * hours / 1000
        c.grid_import_kwh += max(state.grid_w, 0.0) * hours / 1000
        c.grid_export_kwh += max(-state.grid_w, 0.0) * hours / 1000
        c.pv_kwh += state.pv_w * hours / 1000
        c.battery_in_kwh += max(state.battery_w, 0.0) * hours / 1000
        c.battery_out_kwh += max(-state.battery_w, 0.0) * hours / 1000
        self._state = state

    # --- entities ---------------------------------------------------------------

    def entities(self) -> list[DemoEntity]:
        """Current demo entities (empty before the first :meth:`step`)."""
        state = self._state
        if state is None:
            return []
        c = self._c
        ac_on = self.ac_on(state)
        pool_on = self.pool_on(state)
        midnight = state.time.replace(hour=0, minute=0, second=0, microsecond=0)
        today, tomorrow = self._forecast_entities(state.time)
        return [
            DemoEntity(
                AC_ID,
                "cool" if ac_on else "off",
                {
                    "friendly_name": "Sim living room AC",
                    "hvac_modes": ["off", "cool", "auto", "fan_only"],
                    "min_temp": 18,
                    "max_temp": 30,
                    "target_temp_step": 1,
                    "temperature": 24,
                    "current_temperature": round(22 + 6 * min(1.0, state.pv_w / 6000), 1),
                },
            ),
            DemoEntity(
                AC_B_ID,
                "cool" if ac_on else "off",
                {
                    "friendly_name": "Sim living room AC (SmartThinQ)",
                    "hvac_modes": ["off", "cool", "heat_cool", "fan_only"],
                    "min_temp": 16,
                    "max_temp": 30,
                    "target_temp_step": 1,
                    "temperature": 24,
                    "current_temperature": round(22 + 6 * min(1.0, state.pv_w / 6000), 1),
                },
            ),
            DemoEntity(
                AC_B_POWER_ID,
                f"{c.ac_b_power_w:.0f}",
                {
                    "friendly_name": "Sim living room AC energy current",
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                },
            ),
            DemoEntity(
                AC_ENERGY_TODAY_ID,
                str(c.ac_published_wh),
                {
                    "friendly_name": "Sim living room AC energy today",
                    "unit_of_measurement": "Wh",
                    "device_class": "energy",
                    "state_class": "total",
                    "last_reset": midnight.isoformat(),
                },
            ),
            DemoEntity(
                AC_ENERGY_YESTERDAY_ID,
                str(c.ac_yesterday_wh),
                {
                    "friendly_name": "Sim living room AC energy yesterday",
                    "unit_of_measurement": "Wh",
                    "device_class": "energy",
                    "state_class": "total",
                },
            ),
            DemoEntity(
                POOL_SWITCH_ID, "on" if pool_on else "off", {"friendly_name": "Sim pool pump"}
            ),
            DemoEntity(
                POOL_POWER_ID,
                f"{POOL_PUMP_POWER_W if pool_on else 0.0:.1f}",
                {
                    "friendly_name": "Sim pool pump power",
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                },
            ),
            self._kwh(POOL_ENERGY_ID, "Sim pool pump energy", c.pool_kwh),
            DemoEntity(
                ROD_LIMIT_ID,
                "0.0",
                {
                    "friendly_name": "Sim heating rod limit",
                    "unit_of_measurement": "W",
                    "min": 0,
                    "max": 3000,
                    "step": 100,
                    "mode": "slider",
                },
            ),
            DemoEntity(
                PRICE_ID,
                f"{sim_price(state.time):.3f}",
                {
                    "friendly_name": "Sim electricity price",
                    "unit_of_measurement": f"{PRICE_CURRENCY}/kWh",
                    "state_class": "measurement",
                },
            ),
            today,
            tomorrow,
            self._kwh(GRID_IMPORT_KWH_ID, "Sim grid energy import", c.grid_import_kwh),
            self._kwh(GRID_EXPORT_KWH_ID, "Sim grid energy export", c.grid_export_kwh),
            self._kwh(PV_KWH_ID, "Sim PV energy", c.pv_kwh),
            self._kwh(BATTERY_IN_KWH_ID, "Sim battery energy in", c.battery_in_kwh),
            self._kwh(BATTERY_OUT_KWH_ID, "Sim battery energy out", c.battery_out_kwh),
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

    def _day_slots(self, midnight: datetime, *, tomorrow: bool) -> list[dict[str, Any]]:
        pv = self.scenario.house.pv
        slots: list[dict[str, Any]] = []
        for i in range(24 * 60 // SLOT_MIN):
            start = midnight + timedelta(minutes=i * SLOT_MIN)
            mid = start + timedelta(minutes=SLOT_MIN / 2)
            clear_kw = clear_sky_w(mid, pv) / 1000
            cloud = TOMORROW_CLOUD_FACTOR if tomorrow else self._cloud_at(mid)
            p50 = clear_kw * cloud
            slots.append(
                {
                    "period_start": start.isoformat(),
                    "pv_estimate": round(p50, 4),
                    "pv_estimate10": round(p50 * 0.6, 4),
                    "pv_estimate90": round(min(clear_kw, p50 * 1.15), 4),
                }
            )
        return slots

    def _forecast_entities(self, now: datetime) -> tuple[DemoEntity, DemoEntity]:
        key = (now.date(), now.hour)
        if self._forecasts is not None and self._forecast_key == key:
            return self._forecasts
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result: list[DemoEntity] = []
        for entity_id, name, day, tomorrow in (
            (FORECAST_TODAY_ID, "Sim Solcast forecast today", midnight, False),
            (
                FORECAST_TOMORROW_ID,
                "Sim Solcast forecast tomorrow",
                midnight + timedelta(days=1),
                True,
            ),
        ):
            slots = self._day_slots(day, tomorrow=tomorrow)
            total_kwh = sum(s["pv_estimate"] for s in slots) * SLOT_MIN / 60
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
        self._forecast_key = key
        self._forecasts = (result[0], result[1])
        return self._forecasts
