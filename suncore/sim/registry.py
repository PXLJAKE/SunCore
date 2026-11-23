# SPDX-License-Identifier: Apache-2.0
"""Registry and energy-preference data of the fake Home Assistant (Phase 2 WP3).

Static tables plus builders for the payloads of the verified WebSocket commands
(``docs/research/ha-websocket-api.md``, HA core ``14f093e``):

* ``config/entity_registry/list_for_display``: ``{"entity_categories": {...}, "entities": [...]}``
  with the short keys ``ei``, ``pl``, ``di``, ``ai``, ``tk``, ``hn``, ``en``, ``ec`` (optional
  keys are absent when empty);
* ``config/entity_registry/list``: array with all keys always present (``null`` when unset);
* ``config/device_registry/list``: array of devices (``config_entry_id``, ``manufacturer``,
  ``model``, ``area_id``, ``name``, ``name_by_user``, ``identifiers``, ...);
* ``config/area_registry/list``: array of areas;
* ``energy/get_prefs``: ``{"energy_sources": [...], "device_consumption": [...]}``.

The WebSocket side is WP5; ``FakeHAConnection`` exposes these as accessor methods.
Platforms and translation keys of the third-party entities are the ones the template
research derived from the integration sources (``lg_thinq``, ``tibber``, ``solcast_solar``);
entity ids are simulator-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from suncore.sim import demo_devices as d

SIM_PLATFORM: Final = "suncore_sim"
SIM_TIME: Final = "2026-01-01T00:00:00+00:00"
ENTITY_CATEGORIES: Final[dict[str, str]] = {"0": "config", "1": "diagnostic"}

AREA_LIVING: Final = "living_room"
AREA_GARDEN: Final = "garden"
DEV_HOUSE: Final = "sim_house"
DEV_AC: Final = "sim_dev_living_room_ac"
DEV_AC_B: Final = "sim_dev_living_room_ac_smartthinq"
DEV_POOL: Final = "sim_dev_pool_plug"
DEV_ROD: Final = "sim_dev_heating_rod"
DEV_WATER_HEATER: Final = "sim_dev_water_heater"
DEV_WATER_HEATER_RELAYS: Final = "sim_dev_water_heater_relays"
DEV_TIBBER: Final = "sim_dev_tibber"
DEV_SOLCAST: Final = "sim_dev_solcast"
DEV_OPEN_METEO: Final = "sim_dev_open_meteo"
DEV_FORECAST_SOLAR: Final = "sim_dev_forecast_solar"


@dataclass(frozen=True, slots=True)
class RegEntity:
    """One entity registry entry."""

    entity_id: str
    platform: str
    name: str | None
    device_id: str | None = None
    area_id: str | None = None
    translation_key: str | None = None


def _sim(entity_id: str, name: str) -> RegEntity:
    return RegEntity(entity_id, SIM_PLATFORM, name, DEV_HOUSE)


ENTITIES: Final[tuple[RegEntity, ...]] = (
    _sim("sensor.sim_grid_power", "Grid power"),
    _sim("sensor.sim_pv_power", "PV power"),
    _sim("sensor.sim_battery_power", "Battery power"),
    _sim("sensor.sim_battery_soc", "Battery state of charge"),
    _sim("sensor.sim_house_power", "House power"),
    _sim(d.GRID_IMPORT_KWH_ID, "Grid energy import"),
    _sim(d.GRID_EXPORT_KWH_ID, "Grid energy export"),
    _sim(d.PV_KWH_ID, "PV energy"),
    _sim(d.BATTERY_IN_KWH_ID, "Battery energy in"),
    _sim(d.BATTERY_OUT_KWH_ID, "Battery energy out"),
    # The LG climate entity is the device's main feature: name None (research), so no `en` key.
    RegEntity(d.AC_ID, "lg_thinq", None, DEV_AC, AREA_LIVING, "climate_air_conditioner"),
    RegEntity(
        d.AC_ENERGY_TODAY_ID, "lg_thinq", "Energy today", DEV_AC, AREA_LIVING, "energy_usage_today"
    ),
    RegEntity(
        d.AC_ENERGY_YESTERDAY_ID,
        "lg_thinq",
        "Energy yesterday",
        DEV_AC,
        AREA_LIVING,
        "energy_usage_yesterday",
    ),
    # The same unit through the HACS integration `smartthinq_sensors`: its own registry device
    # (same device name), a second climate entity and a polled power sensor.
    RegEntity(d.AC_B_ID, "smartthinq_sensors", None, DEV_AC_B, AREA_LIVING),
    RegEntity(d.AC_B_POWER_ID, "smartthinq_sensors", "Energy current", DEV_AC_B, AREA_LIVING),
    RegEntity(d.POOL_SWITCH_ID, SIM_PLATFORM, "Pool pump", DEV_POOL, AREA_GARDEN),
    RegEntity(d.POOL_POWER_ID, SIM_PLATFORM, "Pool pump power", DEV_POOL, AREA_GARDEN),
    RegEntity(d.POOL_ENERGY_ID, SIM_PLATFORM, "Pool pump energy", DEV_POOL, AREA_GARDEN),
    RegEntity(d.ROD_LIMIT_ID, SIM_PLATFORM, "Heating rod limit", DEV_ROD, AREA_GARDEN),
    RegEntity(
        d.WATER_HEATER_LEVEL_ID, SIM_PLATFORM, "Water heater level", DEV_WATER_HEATER, AREA_GARDEN
    ),
    RegEntity(
        d.WATER_HEATER_TEMP_ID,
        SIM_PLATFORM,
        "Water heater temperature",
        DEV_WATER_HEATER,
        AREA_GARDEN,
    ),
    *(
        RegEntity(
            relay, SIM_PLATFORM, f"Water heater relay {index}", DEV_WATER_HEATER_RELAYS, AREA_GARDEN
        )
        for index, relay in enumerate(d.WATER_HEATER_RELAY_IDS, start=1)
    ),
    RegEntity(d.PRICE_ID, "tibber", "Electricity price", DEV_TIBBER, None, "electricity_price"),
    RegEntity(
        d.FORECAST_TODAY_ID,
        "solcast_solar",
        "Forecast Today",
        DEV_SOLCAST,
        None,
        "total_kwh_forecast_today",
    ),
    RegEntity(
        d.FORECAST_TOMORROW_ID,
        "solcast_solar",
        "Forecast Tomorrow",
        DEV_SOLCAST,
        None,
        "total_kwh_forecast_tomorrow",
    ),
    # Open-Meteo Solar Forecast: translation key assumed to equal the sensor key (UNVERIFIED).
    RegEntity(
        d.OPEN_METEO_TODAY_ID,
        "open_meteo_solar_forecast",
        "Energy production - today",
        DEV_OPEN_METEO,
        None,
        "energy_production_today",
    ),
    RegEntity(
        d.OPEN_METEO_TOMORROW_ID,
        "open_meteo_solar_forecast",
        "Energy production - tomorrow",
        DEV_OPEN_METEO,
        None,
        "energy_production_tomorrow",
    ),
    # A user's Home Assistant template sensor that exposes the generic forecast attribute.
    RegEntity(d.TEMPLATE_FORECAST_ID, "template", "PV forecast", DEV_HOUSE),
    RegEntity(d.WORKDAY_ID, "workday", "Workday sensor", DEV_HOUSE),
    RegEntity(d.WEATHER_ID, "met", "Home", DEV_HOUSE),
)

AREAS: Final[tuple[tuple[str, str], ...]] = ((AREA_LIVING, "Living room"), (AREA_GARDEN, "Garden"))

# (device id, name, manufacturer, model, area id, entry_type)
_DEVICES: Final[tuple[tuple[str, str, str, str, str | None, str | None], ...]] = (
    (DEV_HOUSE, "SunCore demo house", "SunCore", "Virtual house", None, "service"),
    (DEV_AC, "Living room AC", "LGE", "SIM-AC (DEVICE_AIR_CONDITIONER)", AREA_LIVING, None),
    (
        DEV_AC_B,
        "Living room AC",
        "LGE",
        "SIM-AC-RAC (AC)",
        AREA_LIVING,
        None,
    ),
    (DEV_POOL, "Pool pump plug", "SunCore Sim", "Smart plug", AREA_GARDEN, None),
    (DEV_ROD, "Heating rod controller", "SunCore Sim", "Power limiter", AREA_GARDEN, None),
    (DEV_WATER_HEATER, "Water heater", "SunCore Sim", "Stepped water heater", AREA_GARDEN, None),
    (
        DEV_WATER_HEATER_RELAYS,
        "Water heater relays",
        "SunCore Sim",
        "3-channel relay",
        AREA_GARDEN,
        None,
    ),
    (DEV_TIBBER, "Home", "Tibber", "Price Sensor", None, "service"),
    (DEV_SOLCAST, "Solcast PV Forecast", "BJReplay", "Solcast PV Forecast", None, "service"),
    (
        DEV_OPEN_METEO,
        "Solar production forecast",
        "Open-Meteo",
        "Solar production forecast",
        None,
        "service",
    ),
    (DEV_FORECAST_SOLAR, "Home forecast", "Forecast.Solar", "Estimate", None, "service"),
)
_DEVICE_DOMAIN: Final[dict[str, str]] = {
    DEV_AC: "lg_thinq",
    DEV_AC_B: "smartthinq_sensors",
    DEV_TIBBER: "tibber",
    DEV_SOLCAST: "solcast_solar",
    DEV_OPEN_METEO: "open_meteo_solar_forecast",
    DEV_FORECAST_SOLAR: "forecast_solar",
}


def entity_registry_list_for_display(*, include: set[str] | None = None) -> dict[str, Any]:
    """Payload of ``config/entity_registry/list_for_display`` (compact, optional keys absent)."""
    rows: list[dict[str, Any]] = []
    for e in ENTITIES:
        if include is not None and e.entity_id not in include:
            continue
        row: dict[str, Any] = {"ei": e.entity_id, "pl": e.platform}
        if e.device_id is not None:
            row["di"] = e.device_id
        if e.area_id is not None:
            row["ai"] = e.area_id
        if e.translation_key is not None:
            row["tk"] = e.translation_key
        row["hn"] = True
        if e.name is not None:
            row["en"] = e.name
        rows.append(row)
    return {"entity_categories": dict(ENTITY_CATEGORIES), "entities": rows}


def entity_registry_list(*, include: set[str] | None = None) -> list[dict[str, Any]]:
    """Payload of ``config/entity_registry/list`` (all keys present, ``null`` when unset)."""
    rows: list[dict[str, Any]] = []
    for e in ENTITIES:
        if include is not None and e.entity_id not in include:
            continue
        rows.append(
            {
                "area_id": e.area_id,
                "categories": {},
                "config_entry_id": f"sim_entry_{e.platform}",
                "config_subentry_id": None,
                "created_at": 0.0,
                "device_id": e.device_id,
                "disabled_by": None,
                "entity_category": None,
                "entity_id": e.entity_id,
                "has_entity_name": True,
                "hidden_by": None,
                "icon": None,
                "id": f"sim_reg_{e.entity_id.replace('.', '_')}",
                "labels": [],
                "modified_at": 0.0,
                "name": None,
                "next_name_part": None,
                "options": {},
                "original_name": e.name,
                "platform": e.platform,
                "translation_key": e.translation_key,
                "unique_id": f"sim_{e.entity_id.split('.', 1)[1]}",
            }
        )
    return rows


def device_registry_list() -> list[dict[str, Any]]:
    """Payload of ``config/device_registry/list``."""
    rows: list[dict[str, Any]] = []
    for dev_id, name, manufacturer, model, area_id, entry_type in _DEVICES:
        domain = _DEVICE_DOMAIN.get(dev_id, SIM_PLATFORM)
        rows.append(
            {
                "area_id": area_id,
                "configuration_url": None,
                "config_entries": [f"sim_entry_{domain}"],
                "config_entries_subentries": {},
                "config_entry_id": f"sim_entry_{domain}",
                "config_subentry_id": None,
                "connections": [],
                "created_at": 0.0,
                "disabled_by": None,
                "entry_type": entry_type,
                "hw_version": None,
                "id": dev_id,
                "identifiers": [[domain, dev_id]],
                "labels": [],
                "manufacturer": manufacturer,
                "model": model,
                "model_id": None,
                "modified_at": 0.0,
                "name_by_user": None,
                "name": name,
                "next_name_part": None,
                "parent_device_id": None,
                "serial_number": None,
                "sw_version": None,
                "via_device_id": None,
            }
        )
    return rows


def area_registry_list() -> list[dict[str, Any]]:
    """Payload of ``config/area_registry/list``."""
    return [
        {
            "aliases": [],
            "area_id": area_id,
            "floor_id": None,
            "humidity_entity_id": None,
            "icon": None,
            "labels": [],
            "name": name,
            "picture": None,
            "temperature_entity_id": None,
            "created_at": 0.0,
            "modified_at": 0.0,
        }
        for area_id, name in AREAS
    ]


def energy_prefs(battery_capacity_kwh: float) -> dict[str, Any]:
    """Payload of ``energy/get_prefs`` for the demo installation.

    Grid power is positive when importing (like SunCore); the battery sensor is positive when
    *charging*, the opposite of HA's convention, so the battery uses ``stat_rate_inverted``
    and the top-level ``stat_rate`` is the id HA would generate for the inverted sensor
    (``sensor.<source>_inverted``, see the research note). That generated sensor is not an
    entity of the fake.
    """
    return {
        "energy_sources": [
            {
                "type": "grid",
                "stat_energy_from": d.GRID_IMPORT_KWH_ID,
                "stat_energy_to": d.GRID_EXPORT_KWH_ID,
                "stat_cost": None,
                "entity_energy_price": d.PRICE_ID,
                "number_energy_price": None,
                "stat_compensation": None,
                "entity_energy_price_export": None,
                "number_energy_price_export": None,
                "cost_adjustment_day": 0.0,
                "stat_rate": "sensor.sim_grid_power",
                "power_config": {"stat_rate": "sensor.sim_grid_power"},
            },
            {
                "type": "solar",
                "stat_energy_from": d.PV_KWH_ID,
                "stat_rate": "sensor.sim_pv_power",
                "config_entry_solar_forecast": [
                    "sim_entry_solcast_solar",
                    "sim_entry_forecast_solar",
                ],
            },
            {
                "type": "battery",
                "stat_energy_from": d.BATTERY_OUT_KWH_ID,
                "stat_energy_to": d.BATTERY_IN_KWH_ID,
                "stat_rate": "sensor.sim_battery_power_inverted",
                "power_config": {"stat_rate_inverted": "sensor.sim_battery_power"},
                "stat_soc": "sensor.sim_battery_soc",
                "capacity": battery_capacity_kwh,
            },
        ],
        "device_consumption": [
            {
                "stat_consumption": d.POOL_ENERGY_ID,
                "stat_rate": d.POOL_POWER_ID,
                "name": "Pool pump",
            },
            {"stat_consumption": d.AC_ENERGY_TODAY_ID, "name": "Living room AC"},
        ],
    }
