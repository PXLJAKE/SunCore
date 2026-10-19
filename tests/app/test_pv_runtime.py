# SPDX-License-Identifier: Apache-2.0
"""The PV forecast service in the runtime: wiring, demo forecasts, accuracy job (Phase 4 WP5)."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from suncore.app.main import build_runtime
from suncore.app.runtime import Runtime
from suncore.core.config import ForecastConfig, ForecastSourceConfig
from suncore.core.forecast import HorizonBucket
from suncore.core.measurements import SITE_PV_W
from suncore.core.series import QuantileSeries, SeriesUnit, TimeSeries
from suncore.core.site import EntityRef
from tests.app.conftest import RecordingConnection, demo_settings

SCENARIO_DAY = datetime(2026, 6, 21, tzinfo=UTC)


async def until(predicate: Callable[[], bool], timeout_s: float = 10.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate
            await asyncio.sleep(0.01)


async def fast_sleep(delay: float) -> None:
    """The refresh interval stays long; the debounce after an entity change is short."""
    await asyncio.sleep(0.01 if delay < 100 else delay)


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[Runtime]:
    conn = RecordingConnection(speed=6000.0)

    def clock() -> datetime:
        return conn.sim_time or SCENARIO_DAY

    rt = await build_runtime(demo_settings(tmp_path), env={}, connection=conn, clock=clock)
    rt.control_sleep = fast_sleep
    await rt._rebuild_pv_service()  # the service takes the sleep it is built with
    await rt.start()
    try:
        yield rt
    finally:
        await rt.stop()


async def test_the_demo_shows_forecasts_of_all_three_sources(runtime: Runtime) -> None:
    assert runtime.pv_service is not None
    assert runtime.pv_service.source_ids == [
        "solcast.pv",
        "open_meteo_solar_forecast.pv",
        "forecast_solar.pv",
    ]
    await until(
        lambda: all(
            (s := runtime.pv_service.snapshot(i)) is not None and s.series is not None  # type: ignore[union-attr]
            for i in runtime.pv_service.source_ids  # type: ignore[union-attr]
        )
    )
    view = await runtime.pv_forecast(48)
    assert [s.status for s in view.sources] == ["ok", "ok", "ok"]
    steps = {s.source_id: s.series.step_s for s in view.sources if s.series is not None}
    assert steps == {
        "solcast.pv": 1800,
        "open_meteo_solar_forecast.pv": 900,
        "forecast_solar.pv": 3600,
    }
    assert all(not s.stale for s in view.sources)
    assert view.blended is not None
    assert view.blended.source_id == "solcast.pv"
    solcast = view.sources[0].series
    assert solcast is not None
    known = [v for v in solcast.p50_w if v is not None]
    assert known
    assert max(known) > 1000
    # P10 <= P50 <= P90 where the source has quantiles
    for lo, mid, hi in zip(solcast.p10_w, solcast.p50_w, solcast.p90_w, strict=True):
        if lo is not None and mid is not None and hi is not None:
            assert lo <= mid <= hi


async def test_forecasts_are_stored_in_timeseries_db_at_most_hourly(runtime: Runtime) -> None:
    store = runtime.timeseries
    assert store is not None
    assert runtime.pv_service is not None
    await until(lambda: runtime.pv_service.snapshot("solcast.pv").last_stored_at is not None)  # type: ignore[union-attr]
    issues = await store.forecast_issues("solcast.pv", "pv")
    assert issues
    gaps = [b - a for a, b in itertools.pairwise(issues)]
    assert all(g >= timedelta(minutes=15) for g in gaps)  # never faster than the minimum gap
    back = await store.read_forecast("solcast.pv", "pv", issues[0])
    assert back is not None
    assert back.step == timedelta(minutes=30)


async def test_changing_the_forecast_section_rebuilds_the_service(runtime: Runtime) -> None:
    conn = runtime.connection
    assert isinstance(conn, RecordingConnection)
    old = runtime.pv_service
    only_open_meteo = ForecastConfig(
        sources=[
            ForecastSourceConfig(
                template_id="open_meteo_solar_forecast.pv",
                entities={
                    "energy_today": EntityRef(
                        entity_id="sensor.sim_open_meteo_energy_production_today"
                    ),
                    "energy_tomorrow": EntityRef(
                        entity_id="sensor.sim_open_meteo_energy_production_tomorrow"
                    ),
                },
            )
        ]
    )
    await runtime.put_section("forecast", only_open_meteo)
    assert runtime.pv_service is not old
    assert runtime.pv_service is not None
    assert runtime.pv_service.source_ids == ["open_meteo_solar_forecast.pv"]
    assert "sensor.sim_solcast_forecast_today" not in conn.subscriptions[-1]
    await runtime.put_section("forecast", ForecastConfig())
    assert runtime.pv_service is None
    view = await runtime.pv_forecast(24)
    assert view.sources == []
    assert view.blended is None
    accuracy = await runtime.forecast_accuracy()
    assert accuracy.sources == []


async def test_unusable_templates_are_skipped_not_fatal(runtime: Runtime) -> None:
    config = ForecastConfig(
        sources=[
            ForecastSourceConfig(template_id="no.such_template"),
            ForecastSourceConfig(template_id="tibber.price"),  # a tariff, not a forecast
        ]
    )
    await runtime.put_section("forecast", config)
    assert runtime.pv_service is None


async def test_the_nightly_job_stores_accuracy_that_the_api_reads(runtime: Runtime) -> None:
    store, learned = runtime.timeseries, runtime.learned
    assert store is not None
    assert learned is not None
    now = runtime.clock()
    day = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    quarters = 96
    await store.store_quarter_hours(
        SITE_PV_W,
        TimeSeries(
            start=day, step=timedelta(minutes=15), values=(1000.0,) * quarters, unit=SeriesUnit.W
        ),
    )
    issued = day - timedelta(hours=6)
    forecast = TimeSeries(
        start=issued, step=timedelta(minutes=30), values=(700.0,) * 144, unit=SeriesUnit.W
    )
    await store.store_forecast("solcast.pv", "pv", issued, QuantileSeries.deterministic(forecast))
    assert await runtime.maintain_timeseries() is True  # starts the accuracy job in the background
    assert runtime._accuracy_task is not None
    await runtime._accuracy_task
    accuracy = await runtime.forecast_accuracy()
    by_id = {s.source_id: s for s in accuracy.sources}
    assert set(by_id) == {"solcast.pv", "open_meteo_solar_forecast.pv", "forecast_solar.pv"}
    record = next(r for r in by_id["solcast.pv"].records if r.horizon is HorizonBucket.H6_24)
    assert record.bias_w == 300.0
    assert record.mae_w == 300.0
    assert record.n > 0
    assert by_id["open_meteo_solar_forecast.pv"].records == []


async def test_the_accuracy_job_never_raises(runtime: Runtime) -> None:
    assert runtime.learned is not None

    async def boom(*_args: object, **_kw: object) -> None:
        msg = "learned store gone"
        raise OSError(msg)

    runtime.learned.put = boom  # type: ignore[method-assign]
    await runtime.update_forecast_accuracy()  # logged, not raised
