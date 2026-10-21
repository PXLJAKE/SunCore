# SPDX-License-Identifier: Apache-2.0
"""The load forecast in the runtime: wiring, demo workday/weather fakes, rebuild (Phase 4 WP6)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from suncore.app.adapters import HaLoadReader
from suncore.app.main import build_runtime
from suncore.app.runtime import Runtime
from suncore.core.config import DevicesConfig, ForecastConfig
from suncore.forecast.load import DayTypeSource, LoadQuality
from suncore.sim.demo_devices import WEATHER_ID, WORKDAY_ID
from tests.app.conftest import RecordingConnection, demo_settings

SCENARIO_DAY = datetime(2026, 6, 21, tzinfo=UTC)


async def until(predicate: Callable[[], bool], timeout_s: float = 10.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate
            await asyncio.sleep(0.01)


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[Runtime]:
    conn = RecordingConnection(speed=6000.0)

    def clock() -> datetime:
        return conn.sim_time or SCENARIO_DAY

    rt = await build_runtime(demo_settings(tmp_path), env={}, connection=conn, clock=clock)
    await rt.start()
    try:
        yield rt
    finally:
        await rt.stop()


async def test_the_demo_load_forecast_uses_its_workday_and_weather(runtime: Runtime) -> None:
    service = runtime.load_service
    assert service is not None
    assert service.inputs.workday_entity == WORKDAY_ID
    assert service.inputs.weather_entity == WEATHER_ID
    assert set(service.inputs.device_ids) == {d.id for d in runtime.devices.devices}  # type: ignore[union-attr]
    await until(lambda: service.model is not None)
    await until(lambda: runtime.cache.get(WORKDAY_ID) is not None)
    await service.refresh()
    view = await runtime.load_forecast(24)
    assert view.quality in (LoadQuality.OK, LoadQuality.COLD_START)
    assert view.p50_w
    assert view.days[0].source is DayTypeSource.WORKDAY_SENSOR
    assert {d.source for d in view.days[1:]} <= {DayTypeSource.CHECK_DATE}
    assert view.days[1].temperature_c is not None  # from weather.get_forecasts
    accuracy = await runtime.forecast_accuracy()
    assert "load_profile" in [s.source_id for s in accuracy.sources]


async def test_the_reader_reads_the_fake_home_assistant(runtime: Runtime) -> None:
    await until(lambda: runtime.cache.get(WEATHER_ID) is not None)
    reader = HaLoadReader(runtime.connection, runtime.cache)
    assert reader.outside_temperature(WEATHER_ID) is not None
    assert reader.workday_today(WORKDAY_ID) is (SCENARIO_DAY.weekday() < 5)
    monday = date(2026, 6, 22)
    assert await reader.check_workday(WORKDAY_ID, monday, min_interval_s=1.0) is True
    assert (
        await reader.check_workday(WORKDAY_ID, monday + timedelta(days=5), min_interval_s=1.0)
        is False
    )
    points = await reader.hourly_temperatures(WEATHER_ID, min_interval_s=1.0)
    assert len(points) == 48
    assert reader.outside_temperature("weather.unknown") is None
    assert reader.workday_today("binary_sensor.unknown") is None


async def test_a_devices_or_forecast_change_rebuilds_the_load_forecast(runtime: Runtime) -> None:
    old = runtime.load_service
    assert old is not None
    await runtime.put_section("devices", DevicesConfig())
    assert runtime.load_service is not old
    assert runtime.load_service is not None
    assert runtime.load_service.inputs.device_ids == ()
    await runtime.put_section("forecast", ForecastConfig())
    assert runtime.load_service.inputs.workday_entity is None
