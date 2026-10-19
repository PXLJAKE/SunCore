# SPDX-License-Identifier: Apache-2.0
"""Accuracy bookkeeping: MAE, bias (actual - forecast) and n per source and horizon bucket."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from suncore.core.forecast import ACCURACY_MIN_SAMPLES, AccuracyRecord, HorizonBucket
from suncore.core.measurements import SITE_PV_W
from suncore.core.series import QuantileSeries, SeriesUnit, TimeSeries
from suncore.forecast.accuracy import (
    NS_FORECAST_ACCURACY,
    accuracy_view,
    compute_accuracy,
    load_accuracy,
    no_clipping,
    quarter_errors,
    save_accuracy,
    update_accuracy,
)
from suncore.storage import Database, LearnedParamStore
from suncore.storage.learned import NS_FORECAST_ACCURACY as STORAGE_NS
from suncore.storage.timeseries import TimeseriesStore, open_timeseries

D = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)  # the day measured
NOW = D + timedelta(days=1)
Q = timedelta(minutes=15)


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


def ts(values: list[float | None], start: datetime, step: timedelta = Q) -> TimeSeries:
    return TimeSeries(start=start, step=step, values=tuple(values), unit=SeriesUnit.W)


async def measure(store: TimeseriesStore, values: list[float | None], start: datetime = D) -> None:
    await store.store_quarter_hours(SITE_PV_W, ts(values, start))


async def issue(
    store: TimeseriesStore,
    issued: datetime,
    level: float,
    *,
    start: datetime | None = None,
    hours: int = 72,
    step: timedelta = Q,
    source: str = "solcast.pv",
) -> None:
    first = start or issued
    count = int(timedelta(hours=hours) / step)
    p50 = ts([level] * count, first, step)
    await store.store_forecast(source, "pv", issued, QuantileSeries.deterministic(p50))


def by_bucket(records: list[AccuracyRecord]) -> dict[HorizonBucket, AccuracyRecord]:
    return {r.horizon: r for r in records}


# --- the maths -----------------------------------------------------------------------


def test_error_is_actual_minus_forecast() -> None:
    actual = ts([1000.0, 1000.0, 500.0], D)
    forecast = ts([800.0, 1200.0, 500.0], D)
    assert quarter_errors(forecast, actual) == [200.0, -200.0, 0.0]  # + = under-forecast


def test_hourly_forecast_is_averaged_onto_quarter_hours_and_gaps_are_skipped() -> None:
    actual = ts([100.0, 100.0, 100.0, 100.0, 100.0, 100.0], D)
    forecast = ts([400.0, None], D, timedelta(hours=1))  # covers 4 quarters, then unknown
    assert quarter_errors(forecast, actual) == [-300.0] * 4
    assert quarter_errors(ts([], D), actual) == []
    gappy = ts([100.0, None, 100.0, 100.0], D)
    assert quarter_errors(ts([50.0] * 4, D), gappy) == [50.0, 50.0, 50.0]


async def test_under_forecast_gives_positive_bias_and_over_forecast_negative(
    store: TimeseriesStore,
) -> None:
    await measure(store, [1000.0] * 96)
    await issue(store, D - timedelta(hours=6), 800.0, source="under")
    await issue(store, D - timedelta(hours=6), 1300.0, source="over")
    under = by_bucket(await compute_accuracy(store, "under", NOW))
    over = by_bucket(await compute_accuracy(store, "over", NOW))
    rec = under[HorizonBucket.H6_24]
    assert (rec.bias_w, rec.mae_w, rec.horizon) == (200.0, 200.0, HorizonBucket.H6_24)
    assert over[HorizonBucket.H6_24].bias_w == -300.0
    assert over[HorizonBucket.H6_24].mae_w == 300.0


async def test_each_bucket_judges_the_issue_with_the_matching_lead(store: TimeseriesStore) -> None:
    await measure(store, [1000.0] * 96)
    await issue(store, D - timedelta(hours=6), 800.0)  # D-1 18:00, 200 W too low
    await issue(store, D + timedelta(hours=4), 1000.0)  # D 04:00, perfect
    records = by_bucket(await compute_accuracy(store, "solcast.pv", NOW))
    # 0-6 h: the newest issue not after the slot, lead < 6 h -> issue 2 for D 04:00-10:00
    near = records[HorizonBucket.H0_6]
    assert (near.n, near.mae_w, near.bias_w) == (24, 0.0, 0.0)
    # 6-24 h: issue 1 for D 00:00-10:00 (40 quarters, +200), issue 2 for D 10:00-24:00 (56, 0)
    mid = records[HorizonBucket.H6_24]
    assert mid.n == 96
    assert mid.bias_w == pytest.approx(200.0 * 40 / 96)
    assert mid.mae_w == pytest.approx(200.0 * 40 / 96)
    # 24-48 h: issue 1 for D 18:00-24:00
    far = records[HorizonBucket.H24_48]
    assert (far.n, far.bias_w) == (24, 200.0)


async def test_an_issue_older_than_the_bucket_is_skipped_not_blamed(store: TimeseriesStore) -> None:
    await measure(store, [1000.0] * 96)
    await issue(store, D - timedelta(days=3), 100.0, hours=96)  # 3 days stale: lead >= 48 h
    assert await compute_accuracy(store, "solcast.pv", NOW) == []


async def test_clipped_and_unmeasured_intervals_are_excluded(store: TimeseriesStore) -> None:
    actual: list[float | None] = [1000.0] * 96
    for i in range(48, 56):  # 12:00-14:00 the inverter clipped: measured far below the sun
        actual[i] = 300.0
    for i in range(8):  # no measurement 00:00-02:00
        actual[i] = None
    await measure(store, actual)
    await issue(store, D - timedelta(hours=6), 800.0)
    plain = by_bucket(await compute_accuracy(store, "solcast.pv", NOW))[HorizonBucket.H6_24]
    assert plain.n == 64  # D 00:00-18:00 = 72 quarters, 8 unmeasured ones never count as 0
    assert plain.bias_w != 200.0

    def clipped(slot_start: datetime) -> bool:
        return D + timedelta(hours=12) <= slot_start < D + timedelta(hours=14)

    filtered = by_bucket(await compute_accuracy(store, "solcast.pv", NOW, is_clipped=clipped))[
        HorizonBucket.H6_24
    ]
    assert filtered.n == 56
    assert (filtered.bias_w, filtered.mae_w) == (200.0, 200.0)
    assert no_clipping(D) is False  # the default hook flags nothing (detection is part B)


async def test_half_hour_and_hourly_forecasts_are_compared_on_the_quarter_grid(
    store: TimeseriesStore,
) -> None:
    await measure(store, [1000.0] * 96)
    await issue(store, D - timedelta(hours=6), 700.0, step=timedelta(minutes=30), source="half")
    await issue(store, D - timedelta(hours=6), 900.0, step=timedelta(hours=1), source="hour")
    half = by_bucket(await compute_accuracy(store, "half", NOW))[HorizonBucket.H6_24]
    hour = by_bucket(await compute_accuracy(store, "hour", NOW))[HorizonBucket.H6_24]
    assert (half.n, half.bias_w) == (72, 300.0)
    assert (hour.n, hour.bias_w) == (72, 100.0)


async def test_a_source_without_data_has_no_records(store: TimeseriesStore) -> None:
    await measure(store, [1000.0] * 96)
    assert await compute_accuracy(store, "nothing.pv", NOW) == []
    empty = await compute_accuracy(store, "nothing.pv", NOW + timedelta(days=40))
    assert empty == []


# --- persistence -------------------------------------------------------------------------


async def test_records_round_trip_through_learned_param(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    assert NS_FORECAST_ACCURACY == STORAGE_NS
    await measure(store, [1000.0] * 96)
    await issue(store, D - timedelta(hours=6), 800.0)
    results = await update_accuracy(store, learned, ["solcast.pv", "other.pv"], NOW)
    assert set(results) == {"solcast.pv", "other.pv"}
    assert results["other.pv"] == []
    stored = await load_accuracy(learned, ["solcast.pv", "other.pv"])
    assert stored["solcast.pv"].records == tuple(results["solcast.pv"])
    assert stored["solcast.pv"].computed_at == NOW
    assert stored["solcast.pv"].window_days == 14
    view = accuracy_view(["solcast.pv", "other.pv", "new.pv"], stored)
    assert view.min_samples == ACCURACY_MIN_SAMPLES
    assert [s.source_id for s in view.sources] == ["solcast.pv", "other.pv", "new.pv"]
    assert view.sources[0].records[0].bias_w == 200.0
    assert view.sources[2].computed_at is None
    assert view.sources[2].records == []
    # a later night replaces the earlier result
    await save_accuracy(learned, "solcast.pv", [], now=NOW + timedelta(days=1))
    assert (await load_accuracy(learned, ["solcast.pv"]))["solcast.pv"].records == ()


async def test_unreadable_or_foreign_payloads_are_skipped(learned: LearnedParamStore) -> None:
    await learned.put(NS_FORECAST_ACCURACY, "bad.pv", {"records": "nope"}, schema_version=1)
    await learned.put(NS_FORECAST_ACCURACY, "future.pv", {"records": []}, schema_version=9)
    assert await load_accuracy(learned) == {}


async def test_one_failing_source_does_not_stop_the_others(learned: LearnedParamStore) -> None:
    class Broken:
        async def read_series(self, *_args: object, **_kw: object) -> TimeSeries:
            msg = "db gone"
            raise OSError(msg)

        async def forecast_issues(self, *_args: object, **_kw: object) -> list[datetime]:
            return []

        async def read_forecast(self, *_args: object, **_kw: object) -> None:
            return None

    assert await update_accuracy(Broken(), learned, ["a.pv", "b.pv"], NOW) == {}  # type: ignore[arg-type]
