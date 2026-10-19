# SPDX-License-Identifier: Apache-2.0
"""The demo's forecast shapes: deterministic, derived from the PV model, documented shapes."""

from __future__ import annotations

from datetime import datetime, timedelta

from suncore.sim.forecasts import SimForecastModel
from suncore.sim.scenario import load_scenario

SCENARIO = load_scenario("summer_day")
NOW = SCENARIO.start.replace(hour=9, minute=0)
MIDNIGHT = NOW.replace(hour=0)


def model(seed: int = 1) -> SimForecastModel:
    return SimForecastModel(SCENARIO, lambda _: 0.9, seed=seed)


def test_same_seed_same_numbers_other_seed_other_numbers() -> None:
    a, b = model(1), model(1)
    assert a.solcast_day(MIDNIGHT, NOW) == b.solcast_day(MIDNIGHT, NOW)
    assert a.open_meteo_day(MIDNIGHT, NOW) == b.open_meteo_day(MIDNIGHT, NOW)
    assert a.solcast_day(MIDNIGHT, NOW) != model(2).solcast_day(MIDNIGHT, NOW)


def test_solcast_shape_and_quantile_order() -> None:
    slots = model().solcast_day(MIDNIGHT, NOW)
    assert len(slots) == 48
    assert set(slots[0]) == {"period_start", "pv_estimate", "pv_estimate10", "pv_estimate90"}
    assert datetime.fromisoformat(slots[1]["period_start"]) - datetime.fromisoformat(
        slots[0]["period_start"]
    ) == timedelta(minutes=30)
    assert all(s["pv_estimate10"] <= s["pv_estimate"] <= s["pv_estimate90"] for s in slots)
    assert slots[0]["pv_estimate"] == 0
    assert max(s["pv_estimate"] for s in slots) > 1


def test_open_meteo_and_forecast_solar_shapes() -> None:
    wh = model().open_meteo_day(MIDNIGHT, NOW)
    assert len(wh) == 96
    assert all(v >= 0 for v in wh.values())
    hours = model().forecast_solar_wh_hours(NOW)
    assert all(v != 0 or k.endswith("T00:00:00" + k[-6:]) for k, v in hours.items())
    half = model().solcast_wh_hours(NOW, 48, NOW)
    assert len(half) == 96
    points = model().template_points(MIDNIGHT, 2, NOW)
    assert len(points) == 96
    assert set(points[0]) == {"start", "power_w"}
