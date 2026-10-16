# SPDX-License-Identifier: Apache-2.0
"""Demo devices, price and forecast sensors and the fake registry data (Phase 2 WP3)."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from suncore.core.units import parse_unit
from suncore.ha.connection import NotConnectedError
from suncore.sim import demo_devices as d
from suncore.sim import registry
from suncore.sim.demo_devices import DemoDevices, sim_price
from suncore.sim.fake_ha import ALL_SIM_ENTITY_IDS, FakeHAConnection
from suncore.sim.scenario import Scenario, parse_scenario

pytestmark = pytest.mark.simulator

TWO_DAYS = """
name: two_days
start: "2026-06-21T00:00:00+02:00"
duration_s: 172800
step_s: 60
house: {seed: 1, pv: {kwp: 8.0, latitude: 48.0, longitude: 11.0}}
outdoor: {mean_c: 27.0, amplitude_c: 7.0}
clouds:
  - {at_s: 0, factor: 1.0}
"""


@pytest.fixture
def scenario() -> Scenario:
    return parse_scenario(TWO_DAYS)


def _run(scenario: Scenario, steps: int) -> tuple[DemoDevices, list[dict[str, d.DemoEntity]]]:
    house = scenario.build_house()
    devices = DemoDevices(scenario)
    history = []
    for _ in range(steps):
        devices.step(house.step())
        history.append({e.entity_id: e for e in devices.entities()})
    return devices, history


def test_counter_is_hourly_quantised_integer_wh_and_resets_at_midnight(scenario: Scenario) -> None:
    _, history = _run(scenario, 2 * 24 * 60)
    values = [int(h[d.AC_ENERGY_TODAY_ID].state) for h in history]
    changes = [i for i in range(1, len(values)) if values[i] != values[i - 1]]
    # The published value only changes on a full hour (minute 0) or at midnight.
    assert changes
    assert all(i % 60 == 0 for i in changes)
    assert max(values[: 24 * 60]) > 0  # the AC ran on the sunny first day
    assert values[24 * 60] == 0  # reset at local midnight
    # Yesterday's total equals the last hourly value of day one plus the final partial hour.
    assert int(history[24 * 60][d.AC_ENERGY_YESTERDAY_ID].state) >= values[24 * 60 - 1]
    attrs = history[100][d.AC_ENERGY_TODAY_ID].attributes
    assert (attrs["unit_of_measurement"], attrs["device_class"], attrs["state_class"]) == (
        "Wh",
        "energy",
        "total",
    )


def test_ac_and_pool_follow_the_clock(scenario: Scenario) -> None:
    _, history = _run(scenario, 24 * 60)
    at = lambda hour: history[hour * 60 + 30]  # noqa: E731
    assert at(12)[d.AC_ID].state == "cool"
    assert at(3)[d.AC_ID].state == "off"
    assert at(11)[d.POOL_SWITCH_ID].state == "on"
    assert float(at(11)[d.POOL_POWER_ID].state) == d.POOL_PUMP_POWER_W
    assert at(15)[d.POOL_SWITCH_ID].state == "off"
    assert float(at(15)[d.POOL_POWER_ID].state) == 0.0


def test_energy_counters_never_decrease(scenario: Scenario) -> None:
    _, history = _run(scenario, 24 * 60)
    for entity_id in (d.PV_KWH_ID, d.GRID_IMPORT_KWH_ID, d.GRID_EXPORT_KWH_ID, d.POOL_ENERGY_ID):
        series = [float(h[entity_id].state) for h in history]
        assert series == sorted(series)
        assert history[0][entity_id].attributes["state_class"] == "total_increasing"
    assert float(history[-1][d.PV_KWH_ID].state) > 10  # 8 kWp on a clear June day


def test_price_sensor_is_tibber_like(scenario: Scenario) -> None:
    _, history = _run(scenario, 60)
    price = history[0][d.PRICE_ID]
    assert price.attributes["unit_of_measurement"] == "EUR/kWh"
    assert "device_class" not in price.attributes  # Tibber sets none
    assert len(price.state.split(".")[1]) == 3
    t = datetime(2026, 6, 21, 19, 10, tzinfo=UTC)
    assert sim_price(t) == sim_price(t.replace(minute=14))  # constant within a 15 min slot
    assert sim_price(t) > sim_price(t.replace(hour=13))


def test_forecast_sensors_follow_the_solcast_shape(scenario: Scenario) -> None:
    _, history = _run(scenario, 61)
    for entity_id in (d.FORECAST_TODAY_ID, d.FORECAST_TOMORROW_ID):
        entity = history[-1][entity_id]
        attrs = entity.attributes
        assert attrs["unit_of_measurement"] == "kWh"
        assert attrs["device_class"] == "energy"
        slots = attrs["detailedForecast"]
        assert len(slots) == 48
        assert set(slots[0]) == {"period_start", "pv_estimate", "pv_estimate10", "pv_estimate90"}
        starts = [datetime.fromisoformat(s["period_start"]) for s in slots]
        assert all((b - a).total_seconds() == 1800 for a, b in itertools.pairwise(starts))
        assert all(s["pv_estimate10"] <= s["pv_estimate"] <= s["pv_estimate90"] for s in slots)
        # kW average power per 30 min slot: kWh = sum(kW) * 0.5
        assert float(entity.state) == pytest.approx(
            sum(s["pv_estimate"] for s in slots) * 0.5, abs=0.01
        )
        assert float(entity.state) > 5
    today = history[-1][d.FORECAST_TODAY_ID].attributes["detailedForecast"][0]["period_start"]
    tomorrow = history[-1][d.FORECAST_TOMORROW_ID].attributes["detailedForecast"][0]["period_start"]
    assert today.startswith("2026-06-21T00:00")
    assert tomorrow.startswith("2026-06-22T00:00")


def test_no_entities_before_the_first_step(scenario: Scenario) -> None:
    assert DemoDevices(scenario).entities() == []


# --- registry payloads -----------------------------------------------------------------


def test_every_entity_has_a_registry_entry_and_a_known_device_and_area() -> None:
    assert {e.entity_id for e in registry.ENTITIES} == set(ALL_SIM_ENTITY_IDS)
    devices = {row["id"] for row in registry.device_registry_list()}
    areas = {row["area_id"] for row in registry.area_registry_list()}
    for entity in registry.ENTITIES:
        assert entity.device_id in devices
        assert entity.area_id is None or entity.area_id in areas


def test_list_for_display_uses_short_keys_and_omits_empty_ones() -> None:
    payload = registry.entity_registry_list_for_display()
    assert set(payload) == {"entity_categories", "entities"}
    rows = {r["ei"]: r for r in payload["entities"]}
    ac = rows[d.AC_ID]
    assert (ac["pl"], ac["tk"], ac["di"], ac["ai"]) == (
        "lg_thinq",
        "climate_air_conditioner",
        registry.DEV_AC,
        registry.AREA_LIVING,
    )
    assert "ai" not in rows[d.PRICE_ID]  # no area: key absent
    assert "tk" not in rows["sensor.sim_grid_power"]
    assert rows[d.PRICE_ID]["pl"] == "tibber"


def test_registry_list_has_every_key_always() -> None:
    rows = registry.entity_registry_list()
    assert {frozenset(r) for r in rows} == {frozenset(rows[0])}
    price = next(r for r in rows if r["entity_id"] == d.PRICE_ID)
    assert price["area_id"] is None
    assert price["platform"] == "tibber"
    assert price["translation_key"] == "electricity_price"
    assert price["disabled_by"] is None


def test_energy_prefs_reference_existing_statistics() -> None:
    prefs = registry.energy_prefs(10.0)
    assert set(prefs) == {"energy_sources", "device_consumption"}
    known = set(ALL_SIM_ENTITY_IDS)
    stats: list[str] = []
    for source in prefs["energy_sources"]:
        for key in ("stat_energy_from", "stat_energy_to", "stat_soc", "entity_energy_price"):
            if source.get(key):
                stats.append(source[key])
    for dev in prefs["device_consumption"]:
        stats.append(dev["stat_consumption"])
        if "stat_rate" in dev:
            stats.append(dev["stat_rate"])
    assert set(stats) <= known
    battery = next(s for s in prefs["energy_sources"] if s["type"] == "battery")
    assert battery["power_config"] == {"stat_rate_inverted": "sensor.sim_battery_power"}
    assert battery["stat_rate"] == "sensor.sim_battery_power_inverted"
    assert battery["capacity"] == 10.0


# --- connection accessors --------------------------------------------------------------


@pytest.fixture
async def conn(scenario: Scenario) -> AsyncIterator[FakeHAConnection]:
    never = asyncio.Event()

    async def sleep(_: float) -> None:
        await never.wait()

    c = FakeHAConnection(scenario, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC), sleep=sleep)
    yield c
    await c.close()


async def test_accessors_need_a_connection(conn: FakeHAConnection) -> None:
    for call in (
        conn.entity_registry_list_for_display,
        conn.entity_registry_list,
        conn.device_registry_list,
        conn.area_registry_list,
        conn.energy_get_prefs,
    ):
        with pytest.raises(NotConnectedError):
            await call()
    with pytest.raises(NotConnectedError):
        conn.entity_metas()


async def test_entity_metas_join_registry_and_state(conn: FakeHAConnection) -> None:
    await conn.connect()
    metas = {m.entity_id: m for m in conn.entity_metas()}
    assert set(metas) == set(ALL_SIM_ENTITY_IDS)
    energy = metas[d.AC_ENERGY_TODAY_ID]
    assert (energy.platform, energy.unit, energy.device_class, energy.state_class) == (
        "lg_thinq",
        "Wh",
        "energy",
        "total",
    )
    assert energy.translation_key == "energy_usage_today"
    assert energy.device_id == metas[d.AC_ID].device_id
    assert all(parse_unit(m.unit) is not None for m in metas.values() if m.device_class == "power")
    assert metas[d.PRICE_ID].unit == "EUR/kWh"


async def test_energy_prefs_not_configured_is_none(scenario: Scenario) -> None:
    c = FakeHAConnection(
        scenario, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC), energy_prefs_configured=False
    )
    await c.connect()
    try:
        assert await c.energy_get_prefs() is None
        assert await c.device_registry_list()
    finally:
        await c.close()


def test_second_integration_power_sensor_is_polled_every_five_minutes(scenario: Scenario) -> None:
    _, history = _run(scenario, 24 * 60)
    values = [float(h[d.AC_B_POWER_ID].state) for h in history]
    changes = [i for i in range(1, len(values)) if values[i] != values[i - 1]]
    assert changes
    assert all(i % 5 == 0 for i in changes)  # sample and hold: changes only on 5 min slots
    # the unit really cools a hot afternoon: its power is physical, not a constant
    assert d.AC_STANDBY_W < max(values) <= d.AC_POWER_W
    assert min(values) == d.AC_STANDBY_W
    attrs = history[0][d.AC_B_POWER_ID].attributes
    assert (attrs["unit_of_measurement"], attrs["device_class"], attrs["state_class"]) == (
        "W",
        "power",
        "measurement",
    )


def test_second_integration_is_another_device_with_the_same_name() -> None:
    rows = {r["id"]: r for r in registry.device_registry_list()}
    assert registry.DEV_AC != registry.DEV_AC_B
    assert rows[registry.DEV_AC]["name"] == rows[registry.DEV_AC_B]["name"]
    by_id = {e.entity_id: e for e in registry.ENTITIES}
    assert by_id[d.AC_B_ID].device_id == by_id[d.AC_B_POWER_ID].device_id == registry.DEV_AC_B
    assert by_id[d.AC_B_POWER_ID].platform == "smartthinq_sensors"
    assert by_id[d.AC_ID].device_id == registry.DEV_AC  # the lg_thinq ids stay as they were
