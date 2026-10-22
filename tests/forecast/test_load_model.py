# SPDX-License-Identifier: Apache-2.0
"""The load profile model: base load, day types, smoothing, robustness, temperature, quantiles."""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.series import SeriesUnit, TimeSeries, planning_grid
from suncore.forecast.load import (
    COLD_START_HIGH,
    COLD_START_LOW,
    DayType,
    LoadQuality,
    SlotBlock,
    base_load,
    daily_mean_temperatures,
    day_type_from_workday,
    day_values,
    degree_days,
    fit_load_model,
    fit_temperature,
    forecast_load,
    parse_check_date,
    parse_hourly_temperatures,
    parse_workday_sensor,
    slot_block,
    temperature_c,
    weekday_rule,
)
from suncore.forecast.stats import clip_outliers, median, weighted_quantile

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)
START = datetime(2026, 9, 1, tzinfo=BERLIN).astimezone(UTC)  # a Tuesday


def series(values: list[float | None], start: datetime = START) -> TimeSeries:
    return TimeSeries(start=start, step=Q, values=tuple(values), unit=SeriesUnit.W)


def history(days: int, shape: Callable[[datetime], float], start: datetime = START) -> TimeSeries:
    return series([shape(start + Q * i) for i in range(96 * days)], start)


def local(moment: datetime) -> datetime:
    return moment.astimezone(BERLIN)


def evening_peak(moment: datetime) -> float:
    t = local(moment)
    level = 300.0 + (500.0 if 17 <= t.hour < 21 else 0.0)
    if t.weekday() >= 5 and 9 <= t.hour < 13:
        level += 400.0
    return level


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BERLIN).astimezone(UTC)


# --- base load ---------------------------------------------------------------------------


def test_base_load_subtracts_each_device_once() -> None:
    house = series([1000.0, 1000.0, None, 500.0])
    ac = series([400.0, None, 100.0, 800.0])
    pool = series([100.0, 100.0, 100.0, 0.0])
    result = base_load(house, {"ac": ac, "pool": pool})
    assert result.series.values == (500.0, 900.0, None, 0.0)
    assert result.unmeasured_slots == {"ac": 1}  # the None house slot is not counted
    assert result.clamped_slots == 1


def test_base_load_without_devices_is_the_house() -> None:
    house = series([1.0, 2.0])
    assert base_load(house, {}).series.values == (1.0, 2.0)


def test_base_load_needs_one_grid() -> None:
    with pytest.raises(ValueError, match="grid"):
        base_load(series([1.0]), {"x": series([1.0], START + Q)})


@given(
    st.lists(st.one_of(st.none(), st.floats(0, 10_000)), min_size=1, max_size=40),
    st.lists(st.one_of(st.none(), st.floats(-100, 5_000)), min_size=40, max_size=40),
)
def test_base_load_is_never_negative_nor_above_the_house(
    house: list[float | None], device: list[float | None]
) -> None:
    result = base_load(series(house), {"d": series(device[: len(house)])})
    for total, rest in zip(house, result.series.values, strict=True):
        if total is None:
            assert rest is None
        else:
            assert rest is not None
            assert 0 <= rest <= total + 1e-9


# --- day types -----------------------------------------------------------------------------


def test_weekday_rule_and_workday_mapping() -> None:
    monday, saturday = date(2026, 10, 19), date(2026, 10, 24)
    assert weekday_rule(monday) is DayType.WORKDAY
    assert weekday_rule(saturday) is DayType.WEEKEND
    assert day_type_from_workday(monday, True) is DayType.WORKDAY
    assert day_type_from_workday(monday, False) is DayType.HOLIDAY
    assert day_type_from_workday(saturday, False) is DayType.WEEKEND
    assert day_type_from_workday(saturday, True) is DayType.WORKDAY  # a working Saturday


def test_workday_state_and_check_date_shapes() -> None:
    entity_id = "binary_sensor.workday_sensor"
    on = parse_workday_sensor(entity_id, "on", {"days_offset": 0, "workdays": ["mon"]})
    assert on is not None
    assert (on.workday, on.offset) == (True, 0)
    off = parse_workday_sensor(entity_id, "off", {})
    assert off is not None
    assert not off.workday
    assert parse_workday_sensor(entity_id, "unavailable", {}) is None
    shifted = parse_workday_sensor(entity_id, "on", {"days_offset": 1})
    assert shifted is not None
    assert shifted.offset == 1  # describes tomorrow; compensated by the service
    assert shifted.fingerprint != on.fingerprint
    assert parse_workday_sensor(entity_id, "on", {"days_offset": "1"}) is None
    entity = "binary_sensor.workday_sensor"
    assert parse_check_date({entity: {"workday": False}}, entity) is False
    assert parse_check_date({entity: {"workday": "yes"}}, entity) is None
    assert parse_check_date({}, entity) is None


def test_slot_blocks() -> None:
    assert slot_block(0) is SlotBlock.NIGHT
    assert slot_block(24) is SlotBlock.MORNING
    assert slot_block(40) is SlotBlock.MIDDAY
    assert slot_block(64) is SlotBlock.EVENING
    assert slot_block(95) is SlotBlock.LATE


# --- day grid (DST) -------------------------------------------------------------------------


def test_day_values_on_dst_days() -> None:
    fall = datetime(2026, 10, 25, tzinfo=BERLIN).astimezone(UTC)  # 25 h
    spring = datetime(2026, 3, 29, tzinfo=BERLIN).astimezone(UTC)  # 23 h
    for start, slots_known in ((fall, 96), (spring, 92)):
        data = series([100.0] * 100, start)
        (day,) = day_values(data, BERLIN, start + timedelta(hours=26))[:1]
        assert sum(v is not None for v in day.slots) == slots_known
        assert day.coverage == 1.0


# --- the profile ------------------------------------------------------------------------------


def test_profile_learns_day_types() -> None:
    data = history(42, evening_peak)
    now = START + timedelta(days=42)
    model = fit_load_model(data, now=now, tz=BERLIN, day_types={})
    assert model.quality is LoadQuality.OK
    assert model.days == 42
    workday, weekend = model.profiles[DayType.WORKDAY], model.profiles[DayType.WEEKEND]
    assert workday[19 * 4] == pytest.approx(800, abs=5)  # 19:00
    assert workday[3 * 4] == pytest.approx(300, abs=5)
    assert weekend[11 * 4] > 600  # weekend late-morning load
    assert workday[11 * 4] < 320
    # no holidays seen: a holiday looks like a weekend day
    assert model.profiles[DayType.HOLIDAY] == pytest.approx(weekend)


def test_holiday_profile_from_the_workday_sensor() -> None:
    def shape(moment: datetime) -> float:
        t = local(moment)
        return 900.0 if t.date() in holidays and 12 <= t.hour < 14 else 300.0

    holidays = {date(2026, 9, d) for d in (7, 14, 21, 28)}  # Mondays off
    data = history(35, shape)
    types = dict.fromkeys(holidays, DayType.HOLIDAY)
    model = fit_load_model(data, now=START + timedelta(days=35), tz=BERLIN, day_types=types)
    assert model.day_counts[DayType.HOLIDAY] == 4
    assert model.profiles[DayType.HOLIDAY][13 * 4] > 600  # learnt, shrunk 4/(4+3)
    assert model.profiles[DayType.WORKDAY][13 * 4] == pytest.approx(300, abs=1)


def test_one_outlier_day_barely_moves_the_profile() -> None:
    def shape(moment: datetime) -> float:
        t = local(moment)
        if t.date() == date(2026, 10, 6) and 10 <= t.hour < 12:
            return 9000.0  # a sauna afternoon
        return 400.0

    model = fit_load_model(
        history(42, shape), now=START + timedelta(days=42), tz=BERLIN, day_types={}
    )
    assert model.profiles[DayType.WORKDAY][11 * 4] < 450


def test_exponential_smoothing_follows_a_level_change() -> None:
    change = START + timedelta(days=35)
    data = history(42, lambda m: 300.0 if m < change else 600.0)
    model = fit_load_model(data, now=START + timedelta(days=42), tz=BERLIN, day_types={})
    level = model.profiles[DayType.WORKDAY][12 * 4]
    # 7 recent days at 600 against 28 older days at 300: with a 21-day half-life the recent
    # week weighs about 40 %, a plain mean would give 20 %
    assert 380 < level < 500


def test_cold_start_is_a_flat_wide_band() -> None:
    data = history(2, lambda _: 500.0)
    now = START + timedelta(days=2, hours=5)
    model = fit_load_model(data, now=now, tz=BERLIN, day_types={})
    assert model.quality is LoadQuality.COLD_START
    forecast = forecast_load(model, planning_grid(now, timedelta(hours=48)), BERLIN, {})
    assert forecast is not None
    assert set(forecast.p50.values) == {500.0}
    assert set(forecast.p10.values) == {COLD_START_LOW * 500}
    assert set(forecast.p90.values) == {COLD_START_HIGH * 500}


def test_no_history_gives_no_forecast() -> None:
    data = series([None] * 96)
    model = fit_load_model(data, now=START + timedelta(days=3), tz=BERLIN, day_types={})
    assert model.quality is LoadQuality.NO_HISTORY
    grid = planning_grid(START + timedelta(days=3), timedelta(hours=1))
    assert forecast_load(model, grid, BERLIN, {}) is None


def test_residual_quantiles_cover_the_noise() -> None:
    rng = random.Random(7)
    data = history(42, lambda _: 500.0 + rng.gauss(0, 100))
    now = START + timedelta(days=42)
    model = fit_load_model(data, now=now, tz=BERLIN, day_types={})
    q10, q90 = model.residuals[SlotBlock.MIDDAY]
    assert -170 < q10 < -90
    assert 90 < q90 < 170
    forecast = forecast_load(model, planning_grid(now, timedelta(hours=24)), BERLIN, {})
    assert forecast is not None
    assert all(
        lo is not None and mid is not None and hi is not None and lo <= mid <= hi
        for lo, mid, hi in zip(
            forecast.p10.values, forecast.p50.values, forecast.p90.values, strict=True
        )
    )


def test_forecast_uses_the_day_type_of_each_date() -> None:
    data = history(42, evening_peak)
    now = START + timedelta(days=42)  # Tue 13 Oct 2026
    model = fit_load_model(data, now=now, tz=BERLIN, day_types={})
    grid = planning_grid(now, timedelta(hours=48))
    tomorrow = (now.astimezone(BERLIN) + timedelta(days=1)).date()
    workday = forecast_load(model, grid, BERLIN, {})
    holiday = forecast_load(model, grid, BERLIN, {tomorrow: DayType.HOLIDAY})
    assert workday is not None
    assert holiday is not None
    index = (at(tomorrow, 11) - grid.start) // Q
    assert holiday.p50.values[index] > workday.p50.values[index]  # type: ignore[operator]


@settings(max_examples=30, deadline=None)
@given(
    st.lists(st.one_of(st.none(), st.floats(0, 5000)), min_size=96 * 4, max_size=96 * 6),
    st.integers(0, 10_000),
)
def test_forecast_quantiles_are_ordered_and_non_negative(
    values: list[float | None], seed: int
) -> None:
    data = series(values)
    now = START + timedelta(days=len(values) // 96, minutes=15 * (seed % 96))
    model = fit_load_model(data, now=now, tz=BERLIN, day_types={})
    forecast = forecast_load(model, planning_grid(now, timedelta(hours=48)), BERLIN, {})
    if forecast is None:
        return
    for lo, mid, hi in zip(
        forecast.p10.values, forecast.p50.values, forecast.p90.values, strict=True
    ):
        assert lo is not None
        assert mid is not None
        assert hi is not None
        assert 0 <= lo <= mid <= hi


# --- temperature ---------------------------------------------------------------------------------


def temperatures(days: int, daily: Callable[[int], float]) -> TimeSeries:
    values: list[float | None] = [daily(i // 96) for i in range(96 * days)]
    return TimeSeries(start=START, step=Q, values=tuple(values), unit=SeriesUnit.CELSIUS)


def test_temperature_term_learns_heating() -> None:
    def temp_of(day: int) -> float:
        return 4.0 + (day * 7) % 13  # 4..16 °C, spread over the days

    def shape(moment: datetime) -> float:
        day = (moment - START) // timedelta(days=1)
        return 400.0 + 30.0 * degree_days(temp_of(day))[0]

    now = START + timedelta(days=42)
    model = fit_load_model(
        history(42, shape),
        now=now,
        tz=BERLIN,
        day_types={},
        temperature=temperatures(42, temp_of),
        temperature_configured=True,
    )
    term = model.temperature
    assert term.active
    assert term.heating_w_per_k == pytest.approx(30, rel=0.1)
    grid = planning_grid(now, timedelta(hours=24))
    day = now.astimezone(BERLIN).date()
    cold = forecast_load(model, grid, BERLIN, {}, {day: 0.0})
    mild = forecast_load(model, grid, BERLIN, {}, {day: 14.0})
    assert cold is not None
    assert mild is not None
    assert cold.p50.values[0] > mild.p50.values[0] + 300  # type: ignore[operator]


def test_temperature_term_needs_days_and_spread() -> None:
    assert fit_temperature([(0.0, 10.0, 1.0)] * 5).reason == "too_few_days"
    flat = fit_temperature([(float(i), 18.0, 1.0) for i in range(20)])
    assert flat.reason == "no_spread"
    assert not flat.active
    assert flat.offset_w(-10.0) == 0.0


def test_temperature_term_is_bounded() -> None:
    samples = [(5000.0 * (i % 2), 5.0 + 10 * (i % 2), 1.0) for i in range(20)]
    term = fit_temperature(samples)
    assert 0 <= term.heating_w_per_k <= 300
    # negative dependence (load falls when it gets colder) is not a heating term
    assert term.heating_w_per_k == 0.0


def test_temperature_not_configured_is_reported() -> None:
    model = fit_load_model(
        history(10, lambda _: 1.0), now=START + timedelta(days=10), tz=BERLIN, day_types={}
    )
    assert model.temperature.reason == "not_configured"


# --- Home Assistant shapes -----------------------------------------------------------------------


def test_parse_hourly_temperatures() -> None:
    entity = "weather.home"
    response = {
        entity: {
            "forecast": [
                {"datetime": "2026-10-21T10:00:00+00:00", "temperature": 50.0},
                {"datetime": "2026-10-21T11:00:00", "temperature": 51.0},  # no zone
                {"datetime": "2026-10-21T12:00:00+00:00", "temperature": None},
                {"datetime": "nonsense", "temperature": 1.0},
                {"condition": "sunny"},
                "junk",
            ]
        }
    }
    points = parse_hourly_temperatures(response, entity, "°F")
    assert points == [(datetime(2026, 10, 21, 10, tzinfo=UTC), pytest.approx(10.0))]
    assert parse_hourly_temperatures({entity: {"forecast": "x"}}, entity, None) == []
    assert parse_hourly_temperatures(response, entity, "K") == []


def test_temperature_units() -> None:
    assert temperature_c("21.5", "°C") == 21.5
    assert temperature_c(32, "°F") == 0.0
    assert temperature_c(True, None) is None
    assert temperature_c("nan", None) is None
    assert temperature_c("x", None) is None


def test_daily_mean_temperatures_weigh_hours_alike() -> None:
    day = date(2026, 10, 21)
    points = [(at(day, h, m), 10.0) for h in range(6) for m in (0, 15, 30, 45)]
    points += [(at(day, h), 20.0) for h in range(6, 12)]
    points += [(at(day + timedelta(days=1), h), 5.0) for h in range(3)]
    means = daily_mean_temperatures(points, BERLIN)
    assert means == {day: 15.0}


# --- statistics helpers --------------------------------------------------------------------------


def test_weighted_quantile_and_clipping() -> None:
    values = [float(i) for i in range(1, 11)]
    assert weighted_quantile(values, [1.0] * 10, 0.5) == pytest.approx(5.5)
    assert weighted_quantile(values, [1.0] * 10, 0.0) == 1.0
    assert weighted_quantile(values, [1.0] * 10, 1.0) == 10.0
    assert weighted_quantile([1.0, 2.0], [0.0, 1.0], 0.1) == 2.0
    with pytest.raises(ValueError, match="empty"):
        weighted_quantile([], [], 0.5)
    with pytest.raises(ValueError, match="positive"):
        weighted_quantile([1.0], [0.0], 0.5)
    with pytest.raises(ValueError, match="q must"):
        weighted_quantile([1.0], [1.0], 2.0)
    assert median([3.0, 1.0, 2.0]) == 2.0
    clipped = clip_outliers([10.0, 11.0, 9.0, 10.0, 1000.0], 3.0, 1.0)
    assert max(clipped) < 20
    assert clip_outliers([], 3.0, 1.0) == []
    with pytest.raises(ValueError, match="empty"):
        median([])


@given(st.lists(st.floats(-1e6, 1e6), min_size=1, max_size=50), st.floats(0, 1))
def test_weighted_quantile_lies_in_the_sample_range(values: list[float], q: float) -> None:
    result = weighted_quantile(values, [1.0] * len(values), q)
    assert min(values) - 1e-6 <= result <= max(values) + 1e-6
    assert not math.isnan(result)


def test_temperature_term_learns_heating_and_cooling_together() -> None:
    def temp_of(day: int) -> float:
        return 4.0 + (day * 11) % 27  # 4..30 °C: heating and cooling days

    def shape(moment: datetime) -> float:
        hdd, cdd = degree_days(temp_of((moment - START) // timedelta(days=1)))
        return 400.0 + 25.0 * hdd + 40.0 * cdd

    model = fit_load_model(
        history(56, shape),
        now=START + timedelta(days=56),
        tz=BERLIN,
        day_types={},
        temperature=temperatures(56, temp_of),
        temperature_configured=True,
    )
    term = model.temperature
    assert term.active
    assert term.heating_w_per_k == pytest.approx(25, rel=0.15)
    assert term.cooling_w_per_k == pytest.approx(40, rel=0.15)


def test_a_negative_joint_coefficient_falls_back_to_one_regressor() -> None:
    temps = [4.0 + (i * 11) % 27 for i in range(20)]
    samples = [
        (30.0 * degree_days(t)[0] - 20.0 * degree_days(t)[1], t, 1.0)  # cooling lowers the load
        for t in temps
    ]
    term = fit_temperature(samples)
    assert term.active
    assert term.cooling_w_per_k == 0.0
    assert term.heating_w_per_k > 0


def test_the_forecast_follows_the_wall_clock_over_a_dst_day() -> None:
    data = history(42, evening_peak, datetime(2026, 9, 12, tzinfo=BERLIN).astimezone(UTC))
    now = datetime(2026, 10, 24, 12, 0, tzinfo=BERLIN).astimezone(UTC)  # day before fall-back
    model = fit_load_model(data, now=now, tz=BERLIN, day_types={})
    grid = planning_grid(now, timedelta(hours=48))
    forecast = forecast_load(model, grid, BERLIN, {})
    assert forecast is not None
    assert len(forecast) == 48 * 4  # absolute time: the 25-hour day adds no slot
    for begin, _, value in forecast.p50.slots():
        local_hour = begin.astimezone(BERLIN).hour
        assert value is not None
        if 17 <= local_hour < 21:
            assert value > 600  # the evening peak stays at local 17-21 h after the switch
        elif 1 <= local_hour < 6:
            assert value < 400
