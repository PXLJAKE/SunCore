# SPDX-License-Identifier: Apache-2.0
"""The nightly PV learning job and the service's calibrated blend (WP5 part B)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from suncore.core.forecast import HorizonBucket
from suncore.core.measurements import SITE_GRID_W, SITE_PV_W
from suncore.core.series import QuantileSeries, SeriesUnit, TimeRange, TimeSeries
from suncore.forecast.accuracy import load_accuracy
from suncore.forecast.pv.adapter import FetchReport, FetchStatus
from suncore.forecast.pv.calibration import BiasTable, CellStats, load_bias
from suncore.forecast.pv.clearsky import ArrayGeometry, PlantGeometry, clear_sky_slot_w
from suncore.forecast.pv.learning import (
    BLENDED_SOURCE_ID,
    LearningContext,
    local_days,
    mean_reference,
    run_nightly,
)
from suncore.forecast.pv.service import BlendContext, PvForecastService, SourceSettings
from suncore.storage import Database, LearnedParamStore
from suncore.storage.timeseries import TimeseriesStore, open_timeseries

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)
PLANT = PlantGeometry(48.0, 11.0, (ArrayGeometry(8.0, 180.0, 0.0),))
FIRST = date(2026, 6, 18)
NOW = datetime.combine(date(2026, 6, 21), time(1, 0), tzinfo=BERLIN)


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=BERLIN).astimezone(UTC)


def days_series(first: date, days: int, fn: Any, step: timedelta = Q) -> TimeSeries:
    start = midnight(first).astimezone(UTC)
    count = (midnight(first + timedelta(days=days)) - midnight(first)) // step
    return TimeSeries(
        start=start,
        step=step,
        values=tuple(fn(start + step * i) for i in range(count)),
        unit=SeriesUnit.W,
    )


def sun(kt: float) -> Any:
    return lambda begin: kt * clear_sky_slot_w(PLANT, begin, Q)


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


def test_local_days_are_complete_and_dst_aware() -> None:
    start = datetime(2026, 3, 28, 12, tzinfo=BERLIN)
    days = local_days(start, datetime(2026, 3, 31, tzinfo=BERLIN), BERLIN)
    assert [d.day for d in days] == [date(2026, 3, 29), date(2026, 3, 30)]
    assert days[0].end - days[0].start == timedelta(hours=23)  # spring forward


def test_mean_reference_averages_what_is_known() -> None:
    window = days_series(FIRST, 1, lambda _b: 0.0)
    a = TimeSeries(start=window.start, step=Q, values=(100.0, None, 300.0), unit=SeriesUnit.W)
    b = TimeSeries(start=window.start + Q, step=Q, values=(200.0, 500.0), unit=SeriesUnit.W)
    ref = mean_reference(window, [a, b])
    assert ref is not None
    assert ref.values[:4] == (100.0, 200.0, 400.0, None)
    assert mean_reference(window, []) is None


async def test_the_nightly_job_skips_clipping_scores_the_blend_and_learns_bias(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    limit = 3000.0
    potential = days_series(FIRST, 3, sun(0.8))
    pv = TimeSeries(
        start=potential.start,
        step=Q,
        values=tuple(min(v or 0.0, limit) for v in potential.values),
        unit=SeriesUnit.W,
    )
    grid = TimeSeries(
        start=pv.start, step=Q, values=tuple(-(v or 0.0) for v in pv.values), unit=SeriesUnit.W
    )
    await store.store_quarter_hours(SITE_PV_W, pv)
    await store.store_quarter_hours(SITE_GRID_W, grid)
    # a source that is right (where nothing is clipped) issues every day at 23:00 for 48 h
    for i in range(-1, 3):
        issued = midnight(FIRST + timedelta(days=i)) - timedelta(hours=1)
        series = days_series(FIRST + timedelta(days=i), 2, sun(0.8))
        await store.store_forecast("exact.pv", "pv", issued, QuantileSeries.deterministic(series))
        await store.store_forecast(
            BLENDED_SOURCE_ID, "pv", issued, QuantileSeries.deterministic(series)
        )

    result = await run_nightly(
        store,
        learned,
        ["exact.pv"],
        NOW,
        LearningContext(tz=BERLIN, plant=PLANT, export_limit_w=limit),
    )
    assert result.clipped_slots > 0
    stored = await load_accuracy(learned)
    assert set(stored) == {"exact.pv", BLENDED_SOURCE_ID}
    for item in stored.values():
        assert item.records
        for record in item.records:
            assert abs(record.bias_w) < 1e-6  # the clipped quarter hours were left out
    table = (await load_bias(learned))["exact.pv"]
    assert table.days_with_data == 3
    assert table.learned_until == midnight(date(2026, 6, 21))
    assert table.mode == "clear_sky"
    assert table.cells
    for cell in table.cells.values():
        assert cell.actual_wh == pytest.approx(cell.forecast_wh)

    again = await run_nightly(
        store, learned, ["exact.pv"], NOW, LearningContext(BERLIN, PLANT, limit)
    )
    assert again.bias["exact.pv"].days_with_data == 3  # nothing is learnt twice


async def test_the_nightly_job_never_raises(store: TimeseriesStore) -> None:
    class Broken:
        async def put(self, *_a: object, **_k: object) -> None:
            raise OSError

        async def in_namespace(self, _ns: str) -> list[Any]:
            raise OSError

    result = await run_nightly(store, Broken(), ["a.pv"], NOW, LearningContext(BERLIN, None, None))
    assert result.accuracy == {}


# --- the service ------------------------------------------------------------------------


class Source:
    def __init__(self, source_id: str, series: QuantileSeries) -> None:
        self.source_id = source_id
        self.watched_entities: frozenset[str] = frozenset()
        self.last_report = FetchReport(FetchStatus.OK)
        self._series = series

    async def fetch(self, _window: TimeRange) -> QuantileSeries:
        return self._series


class Store:
    def __init__(self) -> None:
        self.ids: list[str] = []

    async def store_forecast(self, source_id: str, *_a: object) -> int:
        self.ids.append(source_id)
        return 1


def biased_table(days: int) -> BiasTable:
    cells = {
        f"{h:02d}.jja.{w}": CellStats(n=1000, forecast_wh=1000, actual_wh=770)
        for h in range(24)
        for w in ("clear", "mixed", "overcast")
    }
    return BiasTable("high.pv", "clear_sky", cells, days_with_data=days)


async def test_the_service_blends_calibrates_and_stores_the_blend() -> None:
    now = datetime(2026, 6, 21, 6, tzinfo=UTC)
    day = days_series(date(2026, 6, 21), 2, sun(0.8))
    high = TimeSeries(
        start=day.start, step=Q, values=tuple((v or 0) * 1.3 for v in day.values), unit=SeriesUnit.W
    )
    store = Store()
    service = PvForecastService(
        [
            Source("high.pv", QuantileSeries.deterministic(high)),
            Source("ok.pv", QuantileSeries.deterministic(day)),
        ],
        clock=lambda: now,
        store=store,
        settings={"ok.pv": SourceSettings("manual", 0.5)},
        min_history_days=30,
        context=lambda: BlendContext(tz=BERLIN, plant=PLANT),
    )
    await service.refresh()
    assert BLENDED_SOURCE_ID in store.ids

    service.set_learning({}, {"high.pv": biased_table(29)})
    collecting = service.blended(now)
    assert collecting is not None
    assert not collecting.calibration_active
    assert collecting.series == collecting.result.series
    assert collecting.series.p50 == collecting.uncalibrated.p50
    assert collecting.calibrated is not None
    weights = collecting.result.weights[0]
    assert weights.horizon is HorizonBucket.H0_6
    assert weights.weights == {"ok.pv": pytest.approx(0.5), "high.pv": pytest.approx(0.5)}

    service.set_learning({}, {"high.pv": biased_table(30)})
    active = service.blended(now)
    assert active is not None
    assert active.calibration_active
    assert active.series.p50 == active.calibrated.p50 if active.calibrated else False
    noon = (datetime(2026, 6, 21, 11, tzinfo=UTC) - active.series.start) // Q
    truth = day.values[(datetime(2026, 6, 21, 11, tzinfo=UTC) - day.start) // Q]
    raw = active.uncalibrated.p50.values[noon]
    fixed = active.series.p50.values[noon]
    assert truth is not None
    assert raw is not None
    assert fixed is not None
    assert abs(fixed - truth) < abs(raw - truth) / 5

    view = service.view(48)
    assert view.blended is not None
    assert view.blended.calibration_active
    assert [c.reason for c in view.blended.calibration] == ["active", "no_history"]
    assert view.blended.uncalibrated_p50_w != view.blended.p50_w
