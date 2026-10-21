# SPDX-License-Identifier: Apache-2.0
"""Parsing of the documented PV forecast shapes into W quantile series (Phase 4 WP5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from suncore.core.series import Grid, SeriesUnit, resample_power
from suncore.core.units import Unit
from suncore.forecast.pv.parsing import (
    RawPoint,
    build_quantiles,
    crop,
    dig,
    dig_response,
    empty_series,
    has_known,
    infer_step,
    list_points,
    mapping_points,
    number,
    parse_timestamp,
    regularize_energy,
)
from suncore.templates.models import SeriesFields
from tests.forecast.conftest import BERLIN, local_day_slots, solcast_entries

HALF_HOUR = timedelta(minutes=30)
QUARTER = timedelta(minutes=15)
HOUR = timedelta(hours=1)
SOLCAST_FIELDS = SeriesFields(
    timestamp_field="period_start",
    value_field="pv_estimate",
    p10_field="pv_estimate10",
    p90_field="pv_estimate90",
    value_unit="kW",
)


def solcast_series(*days: date, kw=None):  # type: ignore[no-untyped-def]
    entries = [e for day in days for e in solcast_entries(day, kw)]
    parsed = list_points(entries, SOLCAST_FIELDS)
    assert parsed.skipped == 0
    return build_quantiles(parsed.points, Unit.KW, HALF_HOUR)


def test_solcast_day_kw_becomes_w_on_the_native_grid() -> None:
    series = solcast_series(date(2026, 6, 21))
    assert series is not None
    assert series.unit is SeriesUnit.W
    assert series.step == HALF_HOUR
    assert len(series) == 48
    assert series.start == datetime(2026, 6, 20, 22, 0, tzinfo=UTC)  # local midnight, UTC+2
    noon = series.p50.values[2 * 12 + 4]  # local 12:00 -> 2.0 kW
    assert noon == 2000.0
    assert series.p10.values[2 * 12 + 4] == pytest.approx(1200.0)
    assert series.p90.values[2 * 12 + 4] == pytest.approx(2400.0)
    assert series.p50.values[0] == 0.0  # night is a known zero, not a gap


def test_solcast_energy_is_the_sum_of_kw_times_half_an_hour() -> None:
    series = solcast_series(date(2026, 6, 21))
    assert series is not None
    kw_sum = 20 * 2.0  # 10 h at 2 kW = 20 slots of 2 kW
    assert series.p50.total() == pytest.approx(kw_sum * 0.5 * 1000)  # Wh


def test_spring_forward_day_has_46_slots_and_no_gap() -> None:
    day = date(2026, 3, 29)  # Europe/Berlin: 02:00 -> 03:00
    assert len(local_day_slots(day, 30)) == 46
    series = solcast_series(day, kw=lambda _: 1.0)
    assert series is not None
    assert len(series) == 46
    assert all(v == 1000.0 for v in series.p50.values)  # no None: the missing hour does not exist


def test_fall_back_day_has_50_slots_with_distinct_instants() -> None:
    day = date(2026, 10, 25)  # Europe/Berlin: 03:00 -> 02:00
    entries = solcast_entries(day, kw=lambda _: 1.0)
    assert len(entries) == 50
    offsets = {e["period_start"][-6:] for e in entries}
    assert offsets == {"+02:00", "+01:00"}  # the repeated hour carries both offsets
    series = solcast_series(day, kw=lambda _: 1.0)
    assert series is not None
    assert len(series) == 50
    assert all(v == 1000.0 for v in series.p50.values)


def test_day_sensors_join_into_one_series_and_a_missing_day_is_a_gap() -> None:
    # today, tomorrow and d4 (d3's sensor is unavailable): 48 slots of None in between
    series = solcast_series(date(2026, 6, 21), date(2026, 6, 22), date(2026, 6, 24))
    assert series is not None
    assert len(series) == 4 * 48
    assert series.p50.values[48 + 24] == 2000.0  # tomorrow noon
    gap = series.p50.values[2 * 48 : 3 * 48]
    assert gap == (None,) * 48  # never a copy of the neighbour's value
    assert series.p50.values[3 * 48 + 24] == 2000.0


def test_dst_day_joins_without_a_gap_to_the_next_day() -> None:
    series = solcast_series(
        date(2026, 3, 28), date(2026, 3, 29), date(2026, 3, 30), kw=lambda _: 1.0
    )
    assert series is not None
    assert len(series) == 48 + 46 + 48
    assert None not in series.p50.values


def test_irregular_timestamps_are_refused_not_guessed() -> None:
    entries = solcast_entries(date(2026, 6, 21))
    entries[5]["period_start"] = "2026-06-21T02:40:00+02:00"  # off the 30-minute grid
    parsed = list_points(entries, SOLCAST_FIELDS)
    with pytest.raises(ValueError, match="neither"):
        build_quantiles(parsed.points, Unit.KW, HALF_HOUR)


def test_partial_entries_keep_their_slot_and_unknown_quantiles() -> None:
    entries = solcast_entries(date(2026, 6, 21))
    del entries[24]["pv_estimate10"]  # no P10 for this slot
    entries[25]["pv_estimate"] = "unavailable"  # unparsable value
    entries[26]["pv_estimate"] = -3.0  # a negative forecast is invalid data
    entries[27] = "garbage"  # not an object
    entries[28]["period_start"] = "2026-06-21 12:00"  # no offset: refused
    parsed = list_points(entries, SOLCAST_FIELDS)
    assert parsed.skipped == 2
    series = build_quantiles(parsed.points, Unit.KW, HALF_HOUR)
    assert series is not None
    assert series.p50.values[24] == 2000.0
    assert series.p10.values[24] is None
    assert series.p50.values[25] is None
    assert series.p10.values[25] is None
    assert series.p50.values[26] is None
    assert series.p50.values[27] is None
    assert series.p50.values[28] is None
    assert series.p50.values[29] == 2000.0


def test_inverted_quantiles_are_clamped_to_p50() -> None:
    points = [RawPoint(datetime(2026, 6, 21, 10, tzinfo=UTC), 2.0, 2.5, 1.5)]
    series = build_quantiles(points, Unit.KW, HALF_HOUR)
    assert series is not None
    assert (series.p10.values[0], series.p50.values[0], series.p90.values[0]) == (2000, 2000, 2000)


def test_a_source_without_quantile_fields_has_equal_quantiles() -> None:
    points = [RawPoint(datetime(2026, 6, 21, 10, tzinfo=UTC), 400.0)]
    series = build_quantiles(points, Unit.WH, QUARTER)
    assert series is not None
    assert series.p10 == series.p50 == series.p90


def test_energy_per_slot_converts_to_power_and_preserves_the_energy() -> None:
    start = datetime(2026, 6, 21, 10, tzinfo=UTC)
    wh = [0.0, 125.0, 250.0, 500.0]  # Wh per 15 minutes (Open-Meteo wh_period_15m)
    points = [RawPoint(start + QUARTER * i, v) for i, v in enumerate(wh)]
    series = build_quantiles(points, Unit.WH, QUARTER)
    assert series is not None
    assert series.p50.values == (0.0, 500.0, 1000.0, 2000.0)  # W = Wh * 4
    assert series.p50.total() == pytest.approx(sum(wh))
    hourly = [RawPoint(start + HOUR * i, v) for i, v in enumerate([1500.0, 3000.0])]  # Wh per hour
    h = build_quantiles(hourly, Unit.WH, HOUR)
    assert h is not None
    assert h.p50.values == (1500.0, 3000.0)
    k = build_quantiles([RawPoint(start, 1.5)], Unit.KWH, HOUR)  # 1.5 kWh in an hour
    assert k is not None
    assert k.p50.values == (1500.0,)


def test_refining_the_hourly_forecast_onto_the_planning_grid_keeps_the_energy() -> None:
    start = datetime(2026, 6, 21, 10, tzinfo=UTC)
    points = [RawPoint(start + HOUR * i, v) for i, v in enumerate([1000.0, 2000.0, 500.0])]
    series = build_quantiles(points, Unit.WH, HOUR)
    assert series is not None
    fine = resample_power(series.p50, Grid(start=start, step=QUARTER, count=12))
    assert fine.total() == pytest.approx(series.p50.total())
    assert fine.values[:4] == (1000.0,) * 4


def test_missing_is_zero_fills_between_known_periods_only() -> None:
    start = datetime(2026, 6, 21, 4, tzinfo=UTC)
    # two missing periods between known ones are read as 0 (the general rule; Forecast.Solar
    # itself only ever drops the zero at 00:00:00)
    points = [RawPoint(start, 100.0), RawPoint(start + 3 * HOUR, 300.0)]
    filled = build_quantiles(points, Unit.WH, HOUR, missing_is_zero=True)
    assert filled is not None
    assert filled.p50.values == (100.0, 0.0, 0.0, 300.0)
    plain = build_quantiles(points, Unit.WH, HOUR)
    assert plain is not None
    assert plain.p50.values == (100.0, None, None, 300.0)
    assert has_known(filled)
    assert not has_known(empty_series(start, HOUR))


def test_infer_step_from_spacing_never_assumes() -> None:
    start = datetime(2026, 6, 21, 4, tzinfo=UTC)
    hourly = [RawPoint(start + HOUR * i, 1.0) for i in (0, 1, 3, 4)]  # one missing hour
    assert infer_step(hourly, QUARTER) == HOUR
    half = [RawPoint(start + HALF_HOUR * i, 1.0) for i in range(3)]
    assert infer_step(half, HOUR) == HALF_HOUR
    assert infer_step([RawPoint(start, 1.0)], HOUR) == HOUR  # one point: the default


def test_mapping_points_and_timestamp_rules() -> None:
    parsed = mapping_points(
        {
            "2026-06-21T10:00:00+02:00": 120.5,
            "2026-06-21T10:15:00+02:00": "130",
            "2026-06-21T10:30:00": 5,  # naive
            "nonsense": 1,
            "2026-06-21T10:45:00+02:00": None,
        }
    )
    assert parsed.skipped == 2
    assert [p.value for p in parsed.points] == [120.5, 130.0, None]
    assert mapping_points(None).points == ()
    assert parse_timestamp(datetime(2026, 1, 1)) is None  # noqa: DTZ001
    assert number(True) is None
    assert number(float("nan")) is None
    assert number("x") is None


def test_dig_and_response_paths() -> None:
    assert dig({"a": {"b": [1]}}, "a.b") == [1]
    assert dig({"a": 1}, "a.b") is None
    assert dig("x", None) == "x"
    response = {"prices": {"Home": [{"s": 1}], "Cabin": [{"s": 2}]}, "data": [{"s": 3}]}
    assert dig_response(response, "prices.*") == [{"s": 1}, {"s": 2}]
    assert dig_response(response, "data") == [{"s": 3}]
    assert dig_response(response, "nothing") == []


def test_crop_selects_overlapping_slots_and_never_pads() -> None:
    series = solcast_series(date(2026, 6, 21))
    assert series is not None
    start = series.start + timedelta(hours=10, minutes=10)  # inside slot 20
    cut = crop(series, start, start + timedelta(hours=1))
    assert cut.start == series.start + timedelta(hours=10)
    assert len(cut) == 3  # 10:10-11:10 overlaps the slots 10:00, 10:30 and 11:00
    before = crop(series, series.start - timedelta(days=1), series.start + HALF_HOUR)
    assert len(before) == 1
    assert before.start == series.start
    outside = crop(series, series.p50.end + HOUR, series.p50.end + 2 * HOUR)
    assert len(outside) == 0


def test_local_timestamps_keep_their_offsets_in_utc() -> None:
    slots = local_day_slots(date(2026, 6, 21), 30)
    assert slots[0].utcoffset() == timedelta(hours=2)
    assert slots[0].tzinfo == BERLIN


# --- off-grid points of an energy mapping (energy/solar_forecast) -------------------------


def test_an_off_grid_sunrise_key_never_makes_a_one_minute_grid() -> None:
    start = datetime(2026, 6, 21, 4, tzinfo=UTC)
    points = [RawPoint(start + HOUR * i, 100.0 * i) for i in range(6)]
    sunrise = RawPoint(datetime(2026, 6, 21, 3, 41, 13, tzinfo=UTC), 0.0)
    sunset = RawPoint(datetime(2026, 6, 21, 8, 12, 5, tzinfo=UTC), 40.0)
    assert infer_step([*points, sunrise, sunset], HOUR) < HOUR  # the old behaviour
    regular = regularize_energy([*points, sunrise, sunset], HOUR)
    assert regular.step == HOUR
    assert regular.dropped == 1
    assert regular.merged == 1
    series = build_quantiles(regular.points, Unit.WH, regular.step)
    assert series is not None
    assert series.p50.values == (0.0, 100.0, 200.0, 300.0, 440.0, 500.0)
    total_in = sum(p.value or 0.0 for p in [*points, sunrise, sunset])
    assert series.p50.total() == pytest.approx(total_in)


def test_an_off_grid_point_outside_the_series_opens_its_period() -> None:
    start = datetime(2026, 6, 21, 4, tzinfo=UTC)
    points = [RawPoint(start + HOUR * i, 10.0) for i in range(3)]
    late = RawPoint(datetime(2026, 6, 21, 7, 30, tzinfo=UTC), 5.0)
    regular = regularize_energy([*points, late], HOUR)
    assert regular.step == HOUR  # 07:30 lies on the 15-min grid but the hours dominate
    assert [p.start.hour for p in regular.points] == [4, 5, 6, 7]
    assert regular.points[-1].value == 5.0


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_regular_mappings_keep_their_period(minutes: int) -> None:
    step = timedelta(minutes=minutes)
    start = datetime(2026, 6, 21, 4, tzinfo=UTC)
    points = [RawPoint(start + step * i, 1.0) for i in range(8)]
    regular = regularize_energy(points, HOUR)
    assert regular.step == step
    assert (regular.dropped, regular.merged) == (0, 0)
    assert regular.points == tuple(points)
