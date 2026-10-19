# SPDX-License-Identifier: Apache-2.0
"""Bias calibration: clear-sky classes, bounded shrinking factors, activation, clipping."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.core.series import QuantileSeries, SeriesUnit, TimeSeries
from suncore.core.site import PvArray
from suncore.forecast.pv.calibration import (
    FACTOR_BOUNDS,
    NS_FORECAST_BIAS,
    BiasTable,
    CellStats,
    calibrate,
    calibration_state,
    cell_factor,
    decode,
    encode,
    learn_day,
    load_bias,
    new_table,
    save_bias,
)
from suncore.forecast.pv.clearsky import (
    ArrayGeometry,
    PlantGeometry,
    WeatherClass,
    classify_slots,
    clear_sky_slot_w,
    plant_geometry,
)
from suncore.storage.learned import NS_FORECAST_BIAS as STORAGE_NS

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)
SOUTH = PlantGeometry(48.0, 11.0, (ArrayGeometry(8.0, 180.0, 30.0),))
EAST_WEST = PlantGeometry(
    48.0, 11.0, (ArrayGeometry(4.0, 90.0, 20.0), ArrayGeometry(4.0, 270.0, 20.0))
)


def day_start(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=BERLIN).astimezone(UTC)


def day_series(day: date, fn: Any) -> TimeSeries:
    start = day_start(day)
    end = day_start(day + timedelta(days=1))
    count = (end - start) // Q
    values = tuple(fn(start + Q * i) for i in range(count))
    return TimeSeries(start=start, step=Q, values=values, unit=SeriesUnit.W)


def clear(plant: PlantGeometry, kt: float = 0.85) -> Any:
    return lambda begin: kt * clear_sky_slot_w(plant, begin, Q)


# --- clear-sky classes ------------------------------------------------------------------


def test_plant_geometry_needs_location_and_every_kwp() -> None:
    arrays = [PvArray(name="S", kwp=8.0), PvArray(name="N", kwp=2.0, azimuth_deg=0, tilt_deg=10)]
    plant = plant_geometry(48.0, 11.0, arrays)
    assert plant is not None
    assert plant.arrays[0].azimuth_deg == 180.0
    assert plant.arrays[0].tilt_deg == 30.0
    assert plant_geometry(None, 11.0, arrays) is None
    assert plant_geometry(48.0, 11.0, [PvArray(name="x")]) is None
    assert plant_geometry(48.0, 11.0, []) is None
    southern = plant_geometry(-33.0, 151.0, [PvArray(name="S", kwp=5.0)])
    assert southern is not None and southern.arrays[0].azimuth_deg == 0.0


@pytest.mark.parametrize("plant", [SOUTH, EAST_WEST], ids=["south", "east_west"])
def test_a_clear_forecast_is_clear_all_day_also_on_an_east_west_roof(plant: PlantGeometry) -> None:
    day = date(2026, 6, 21)
    classes = classify_slots(day_series(day, clear(plant)), plant=plant, tz=BERLIN)
    daytime = [c for c in classes if c is not None]
    assert len(daytime) > 50
    assert set(daytime) == {WeatherClass.CLEAR}


def test_an_east_west_profile_is_not_clear_against_a_south_reference() -> None:
    """The class depends on the geometry: a south model reads an east-west morning as clear
    sky only because the morning is strong - consistent, not necessarily equal."""
    day = date(2026, 6, 21)
    forecast = day_series(day, clear(EAST_WEST, 0.85))
    south = classify_slots(forecast, plant=SOUTH, tz=BERLIN)
    ew = classify_slots(forecast, plant=EAST_WEST, tz=BERLIN)
    assert south != ew


def test_overcast_and_mixed_classes() -> None:
    day = date(2026, 6, 21)
    assert {
        c for c in classify_slots(day_series(day, clear(SOUTH, 0.2)), plant=SOUTH, tz=BERLIN) if c
    } == {WeatherClass.OVERCAST}
    assert {
        c for c in classify_slots(day_series(day, clear(SOUTH, 0.5)), plant=SOUTH, tz=BERLIN) if c
    } == {WeatherClass.MIXED}


def test_without_a_location_the_daily_peak_is_the_reference() -> None:
    day = date(2026, 6, 21)
    sunny = day_series(day, clear(SOUTH, 0.85))
    dull = day_series(day + timedelta(days=1), clear(SOUTH, 0.2))
    both = TimeSeries(
        start=sunny.start, step=Q, values=sunny.values + dull.values, unit=SeriesUnit.W
    )
    classes = classify_slots(both, plant=None, tz=BERLIN)
    first = {c for c in classes[: len(sunny)] if c}
    second = {c for c in classes[len(sunny) :] if c}
    assert first == {WeatherClass.CLEAR}
    assert second == {WeatherClass.OVERCAST}
    # a remembered peak makes a dull day alone overcast too
    assert {c for c in classify_slots(dull, plant=None, tz=BERLIN, peak_reference_w=7000) if c} == {
        WeatherClass.OVERCAST
    }


# --- factors ----------------------------------------------------------------------------


@given(st.floats(0.0, 1e6), st.floats(1e-3, 1e6), st.floats(0.0, 1e5), st.floats(0.0, 1e5))
def test_factors_are_bounded_and_shrink_monotonically(
    actual: float, forecast: float, n: float, more: float
) -> None:
    few = cell_factor(CellStats(n=n, forecast_wh=forecast, actual_wh=actual))
    many = cell_factor(CellStats(n=n + more, forecast_wh=forecast, actual_wh=actual))
    lo, hi = FACTOR_BOUNDS
    assert lo <= few <= hi
    assert lo <= many <= hi
    assert abs(many - 1.0) >= abs(few - 1.0) - 1e-12  # more samples never pull it nearer to 1
    raw = min(hi, max(lo, actual / forecast))
    assert min(1.0, raw) - 1e-12 <= few <= max(1.0, raw) + 1e-12


def test_no_samples_means_factor_one() -> None:
    assert cell_factor(None) == 1.0
    assert cell_factor(CellStats()) == 1.0


# --- learning ---------------------------------------------------------------------------


def learn_days(
    table: BiasTable,
    days: int,
    *,
    plant: PlantGeometry | None,
    forecast_kt: float = 0.85,
    truth_kt: float = 0.85 / 1.3,
    first: date = date(2026, 6, 1),
    clipped: Any = None,
) -> BiasTable:
    for i in range(days):
        day = first + timedelta(days=i)
        geometry = plant or SOUTH
        forecast = day_series(day, clear(geometry, forecast_kt))
        actual = day_series(day, clear(geometry, truth_kt))
        table = learn_day(
            table,
            forecast,
            actual,
            plant=plant,
            tz=BERLIN,
            day_end=day_start(day + timedelta(days=1)),
            is_clipped=clipped or (lambda _s: False),
        )
    return table


def test_a_source_30_percent_too_high_is_corrected_after_30_days_only() -> None:
    table = learn_days(new_table("b", SOUTH), 29, plant=SOUTH)
    state = calibration_state("b", table, plant=SOUTH, min_history_days=30)
    assert not state.active
    assert state.reason == "collecting"
    assert state.days_with_data == 29
    assert state.factor_max is not None and state.factor_max < 0.9  # learnt, not applied
    table = learn_days(table, 1, plant=SOUTH, first=date(2026, 6, 30))
    state = calibration_state("b", table, plant=SOUTH, min_history_days=30)
    assert state.active and state.reason == "active"

    day = date(2026, 7, 1)
    forecast = day_series(day, clear(SOUTH, 0.85))
    truth = day_series(day, clear(SOUTH, 0.85 / 1.3))
    fixed = calibrate(QuantileSeries.deterministic(forecast), table, plant=SOUTH, tz=BERLIN)
    raw_error = sum(abs((f or 0) - (t or 0)) for f, t in zip(forecast.values, truth.values))
    fixed_error = sum(abs((f or 0) - (t or 0)) for f, t in zip(fixed.p50.values, truth.values))
    assert fixed_error < 0.25 * raw_error


def test_an_east_west_roof_forecast_as_south_learns_hourly_factors() -> None:
    """A source that models the roof as south-facing is too low in the morning and evening and
    too high at noon; the hour-of-day cells learn exactly that."""
    table = new_table("s", EAST_WEST)
    for i in range(30):
        day = date(2026, 6, 1) + timedelta(days=i)
        # the source scales a south profile to the east-west plant's daily energy
        ew = day_series(day, clear(EAST_WEST, 0.85))
        south = day_series(day, clear(SOUTH, 0.85))
        scale = sum(v or 0 for v in ew.values) / sum(v or 0 for v in south.values)
        forecast = TimeSeries(
            start=south.start,
            step=Q,
            values=tuple((v or 0) * scale for v in south.values),
            unit=SeriesUnit.W,
        )
        table = learn_day(
            table,
            forecast,
            ew,
            plant=EAST_WEST,
            tz=BERLIN,
            day_end=day_start(day + timedelta(days=1)),
        )
    morning = [c for k, c in table.cells.items() if k.startswith("08.jja")]
    noon = [c for k, c in table.cells.items() if k.startswith("13.jja")]
    assert morning and noon
    assert all(cell_factor(c) > 1.05 for c in morning)
    assert all(cell_factor(c) < 0.97 for c in noon)


def test_clipped_slots_are_not_learnt() -> None:
    everything = learn_days(new_table("b", SOUTH), 3, plant=SOUTH)
    nothing = learn_days(new_table("b", SOUTH), 3, plant=SOUTH, clipped=lambda _s: True)
    assert everything.samples > 0
    assert nothing.samples == 0
    assert nothing.days_with_data == 0
    assert nothing.learned_until == everything.learned_until


@given(st.integers(1, 40), st.integers(1, 60))
def test_inactive_before_min_history_days(days: int, minimum: int) -> None:
    table = BiasTable(
        "s", "clear_sky", {"12.jja.clear": CellStats(50, 1000, 700)}, days_with_data=days
    )
    state = calibration_state("s", table, plant=SOUTH, min_history_days=minimum)
    assert state.active is (days >= minimum)


def test_an_inactive_classifier_is_never_applied_and_never_learnt_over() -> None:
    table = learn_days(new_table("b", SOUTH), 30, plant=SOUTH)
    state = calibration_state("b", table, plant=None, min_history_days=30)
    assert state.reason == "classifier_changed" and not state.active
    kept = learn_days(table, 2, plant=None, first=date(2026, 7, 1))
    assert kept == table  # a night without the location keeps the table
    reset = learn_days(new_table("b", None), 2, plant=None)
    assert reset.mode == "daily_peak"
    upgraded = learn_days(reset, 1, plant=SOUTH, first=date(2026, 7, 1))
    assert upgraded.mode == "clear_sky" and upgraded.days_with_data == 1


def test_calibrate_keeps_quantile_order_and_unknown_slots() -> None:
    table = learn_days(new_table("b", SOUTH), 30, plant=SOUTH)
    day = date(2026, 7, 2)
    p50 = day_series(day, clear(SOUTH, 0.85))
    values = list(p50.values)
    values[50] = None
    p50 = TimeSeries(start=p50.start, step=Q, values=tuple(values), unit=SeriesUnit.W)
    p10 = TimeSeries(
        start=p50.start,
        step=Q,
        values=tuple(None if v is None else 0.7 * v for v in values),
        unit=SeriesUnit.W,
    )
    out = calibrate(QuantileSeries(p10=p10, p50=p50, p90=p50), table, plant=SOUTH, tz=BERLIN)
    assert out.p50.values[50] is None
    assert out.p50.values[52] is not None and values[52] is not None
    assert out.p50.values[52] < values[52]


# --- persistence ------------------------------------------------------------------------


class FakeLearned:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}

    async def put(self, namespace: str, key: str, payload: Any, *, schema_version: int = 1) -> None:
        self.rows[(namespace, key)] = (dict(payload), schema_version)

    async def in_namespace(self, namespace: str) -> list[Any]:
        class Row:
            def __init__(self, key: str, payload: dict[str, Any], version: int) -> None:
                self.key, self.payload, self.schema_version = key, payload, version

        return [Row(k, p, v) for (ns, k), (p, v) in self.rows.items() if ns == namespace]


async def test_tables_round_trip_through_learned_params() -> None:
    assert NS_FORECAST_BIAS == STORAGE_NS
    table = learn_days(new_table("solcast.pv", SOUTH), 2, plant=SOUTH)
    learned = FakeLearned()
    await save_bias(learned, table)
    loaded = await load_bias(learned)
    assert loaded["solcast.pv"] == table


def test_unreadable_payloads_are_skipped() -> None:
    assert decode("s", {"mode": "nope"}, 1) is None
    assert decode("s", encode(new_table("s", SOUTH)), 2) is None
    assert decode("s", {"mode": "clear_sky", "cells": [], "days_with_data": 1}, 1) is None
