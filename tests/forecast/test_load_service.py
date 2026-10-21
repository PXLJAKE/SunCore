# SPDX-License-Identifier: Apache-2.0
"""The load forecast service: fit from storage, day types, temperature, store, accuracy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from suncore.core.forecast import HorizonBucket
from suncore.core.measurements import SITE_HOUSE_W, WEATHER_TEMPERATURE_C, device_power_key
from suncore.core.series import SeriesUnit, TimeRange, TimeSeries
from suncore.forecast.accuracy import load_accuracy
from suncore.forecast.load import LOAD_SOURCE_ID, DayType, DayTypeSource, LoadQuality
from suncore.forecast.load_service import (
    MAX_CHECKS_PER_RUN,
    LoadForecastService,
    LoadInputs,
)
from suncore.storage import Database, LearnedParamStore
from suncore.storage.timeseries import TimeseriesStore, open_timeseries

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)
START = datetime(2026, 9, 7, tzinfo=BERLIN).astimezone(UTC)  # a Monday
NOW = START + timedelta(days=14, hours=10, minutes=5)  # Monday 21 Sep, 10:05 local


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeReader:
    def __init__(self) -> None:
        self.today: bool | None = None
        self.answers: dict[date, bool] = {}
        self.fail_after: int | None = None
        self.checks: list[date] = []
        self.temperature: float | None = None
        self.points: list[tuple[datetime, float]] = []
        self.weather_calls = 0

    def workday_today(self, entity_id: str) -> bool | None:
        assert entity_id == "binary_sensor.workday"
        return self.today

    async def check_workday(self, entity_id: str, day: date, *, min_interval_s: float) -> bool:
        assert entity_id == "binary_sensor.workday"
        assert min_interval_s > 0
        if self.fail_after is not None and len(self.checks) >= self.fail_after:
            msg = "rate limited"
            raise RuntimeError(msg)
        self.checks.append(day)
        return self.answers.get(day, day.weekday() < 5)

    def outside_temperature(self, entity_id: str) -> float | None:
        assert entity_id == "weather.home"
        return self.temperature

    async def hourly_temperatures(
        self, entity_id: str, *, min_interval_s: float
    ) -> list[tuple[datetime, float]]:
        assert entity_id == "weather.home"
        assert min_interval_s > 0
        self.weather_calls += 1
        return self.points


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[TimeseriesStore]:
    db = await open_timeseries(tmp_path / "timeseries.db")
    yield TimeseriesStore(db)
    await db.close()


@pytest.fixture
async def learned(tmp_path: Path) -> AsyncIterator[LearnedParamStore]:
    db = await Database.open(tmp_path / "suncore.db")
    yield LearnedParamStore(db, clock=lambda: NOW)
    await db.close()


def ts(values: list[float | None], start: datetime = START) -> TimeSeries:
    return TimeSeries(start=start, step=Q, values=tuple(values), unit=SeriesUnit.W)


async def fill_history(store: TimeseriesStore, days: int = 14) -> None:
    """House 500 W, of which the AC draws 200 W from 12:00 to 16:00 local."""
    house: list[float | None] = []
    ac: list[float | None] = []
    for i in range(96 * days):
        hour = (START + Q * i).astimezone(BERLIN).hour
        draw = 200.0 if 12 <= hour < 16 else 0.0
        house.append(300.0 + draw)
        ac.append(draw)
    await store.store_quarter_hours(SITE_HOUSE_W, ts(house))
    await store.store_quarter_hours(device_power_key("ac"), ts(ac))


def service(
    store: TimeseriesStore | None,
    learned: LearnedParamStore | None,
    clock: Clock,
    reader: FakeReader | None = None,
    **inputs: object,
) -> LoadForecastService:
    return LoadForecastService(
        store,
        learned,
        clock=clock,
        tz=lambda: BERLIN,
        inputs=LoadInputs(**inputs),  # type: ignore[arg-type]
        reader=reader,
    )


async def test_refresh_forecasts_the_base_load_without_devices(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store)
    clock = Clock(NOW)
    svc = service(store, learned, clock, device_ids=("ac",))
    forecast = await svc.refresh()
    assert forecast is not None
    assert len(forecast) == 48 * 4 + 1  # the current slot and 48 h
    assert all(v == pytest.approx(300, abs=1) for v in forecast.p50.values)
    assert set(svc.device_histories) == {"ac"}
    issues = await store.forecast_issues(LOAD_SOURCE_ID, "load")
    assert issues == [NOW]
    clock.now = NOW + timedelta(minutes=30)
    await svc.refresh()
    assert len(await store.forecast_issues(LOAD_SOURCE_ID, "load")) == 1  # hourly
    clock.now = NOW + timedelta(hours=1)
    await svc.refresh()
    assert len(await store.forecast_issues(LOAD_SOURCE_ID, "load")) == 2
    view = svc.view(24)
    assert view.quality is LoadQuality.OK
    assert view.days_of_history == 14
    assert len(view.p50_w) == 96
    assert view.controlled_devices == ["ac"]
    assert view.devices_unmeasured == {}
    assert view.temperature.reason == "not_configured"


async def test_unmeasured_device_is_counted_not_guessed(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store)
    svc = service(store, learned, Clock(NOW), device_ids=("ac", "pool"))
    await svc.refresh()
    view = svc.view()
    assert view.devices_unmeasured["pool"] > 0
    assert "ac" not in view.devices_unmeasured


async def test_without_history_there_is_no_forecast(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    svc = service(store, learned, Clock(NOW))
    assert await svc.refresh() is None
    view = svc.view()
    assert view.quality is LoadQuality.NO_HISTORY
    assert view.p50_w == []
    assert await store.forecast_issues(LOAD_SOURCE_ID, "load") == []


async def test_no_store_means_no_forecast() -> None:
    svc = service(None, None, Clock(NOW))
    assert await svc.refresh() is None
    assert await svc.refit() is None
    assert await svc.nightly() == []


async def test_cold_start(store: TimeseriesStore, learned: LearnedParamStore) -> None:
    await store.store_quarter_hours(
        SITE_HOUSE_W, ts([400.0] * 96 * 2, NOW.replace(minute=0) - timedelta(days=2))
    )
    svc = service(store, learned, Clock(NOW))
    forecast = await svc.refresh()
    assert forecast is not None
    assert svc.view().quality is LoadQuality.COLD_START
    assert forecast.p90.values[0] == pytest.approx(800)


async def test_day_types_from_the_workday_sensor_and_check_date(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store)
    reader = FakeReader()
    reader.today = False  # Monday 21 Sep: a holiday
    tomorrow = date(2026, 9, 22)
    reader.answers[tomorrow] = False
    svc = service(store, learned, Clock(NOW), reader, workday_entity="binary_sensor.workday")
    await svc.refresh()
    today = date(2026, 9, 21)
    assert svc.day_info(today, today).day_type is DayType.HOLIDAY
    assert svc.day_info(today, today).source is DayTypeSource.WORKDAY_SENSOR
    assert svc.day_info(tomorrow, today).day_type is DayType.HOLIDAY
    assert svc.day_info(tomorrow, today).source is DayTypeSource.CHECK_DATE
    assert reader.checks[0] == tomorrow  # forecast days first
    assert len(reader.checks) == MAX_CHECKS_PER_RUN
    view = svc.view(48)
    assert [d.day_type for d in view.days][:2] == [DayType.HOLIDAY, DayType.HOLIDAY]
    # the answers are cached: a new service asks only for dates it has not seen
    again = FakeReader()
    svc2 = service(store, learned, Clock(NOW), again, workday_entity="binary_sensor.workday")
    await svc2.refresh()
    assert tomorrow not in again.checks
    assert svc2.day_info(tomorrow, today).day_type is DayType.HOLIDAY


async def test_a_failing_check_date_falls_back_to_the_weekday_rule(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store)
    reader = FakeReader()
    reader.fail_after = 0
    svc = service(store, learned, Clock(NOW), reader, workday_entity="binary_sensor.workday")
    await svc.refresh()
    tomorrow, today = date(2026, 9, 22), date(2026, 9, 21)
    info = svc.day_info(tomorrow, today)
    assert info.source is DayTypeSource.WEEKDAY_RULE
    assert info.day_type is DayType.WORKDAY
    assert svc.day_info(today, today).source is DayTypeSource.WEEKDAY_RULE  # state unknown


async def test_temperature_is_recorded_and_the_forecast_days_get_a_mean(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store)
    reader = FakeReader()
    reader.temperature = 12.5
    reader.points = [(NOW + timedelta(hours=h), 8.0) for h in range(48)]
    clock = Clock(NOW)
    svc = service(store, learned, clock, reader, weather_entity="weather.home")
    await svc.refresh()
    recorded = await store.read_raw(WEATHER_TEMPERATURE_C, TimeRange(start=NOW - Q, end=NOW + Q))
    assert recorded == [(NOW, 12.5)]
    temps = svc.daily_temperatures(NOW)
    assert temps[date(2026, 9, 22)] == pytest.approx(8.0)
    assert 8.0 < temps[date(2026, 9, 21)] < 12.5  # recorded and forecast hours of today
    view = svc.view()
    assert view.temperature.reason == "too_few_days"
    assert view.days[1].temperature_c == pytest.approx(8.0)
    clock.now = NOW + timedelta(minutes=15)
    await svc.refresh()
    assert reader.weather_calls == 1  # hourly


async def test_nightly_scores_the_load_forecast(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, days=16)  # measured until 23 Sep 00:00 local
    clock = Clock(NOW)
    svc = service(store, learned, clock, device_ids=("ac",))
    await svc.refresh()  # issued Monday 10:05, forecasts 300 W
    clock.now = START + timedelta(days=16, hours=3)
    records = await svc.nightly()
    by = {r.horizon: r for r in records}
    assert by[HorizonBucket.H0_6].n > 0
    assert by[HorizonBucket.H0_6].mae_w == pytest.approx(0, abs=1)
    stored = await load_accuracy(learned, [LOAD_SOURCE_ID])
    assert LOAD_SOURCE_ID in stored
