# SPDX-License-Identifier: Apache-2.0
"""Forecast/price source protocols and accuracy records (Phase 4 WP2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from suncore.core.forecast import AccuracyRecord, ForecastSource, HorizonBucket, PriceSource
from suncore.core.series import (
    PriceSeries,
    QuantileSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
)

T0 = datetime(2026, 6, 1, tzinfo=UTC)


class FlatPv:
    source_id = "test.flat"

    async def fetch(self, window: TimeRange) -> QuantileSeries:
        n = int(window.duration / timedelta(minutes=15))
        series = TimeSeries(
            start=window.start, step=timedelta(minutes=15), values=(1000.0,) * n, unit=SeriesUnit.W
        )
        return QuantileSeries.deterministic(series)


class FixedPrice:
    source_id = "test.fixed"

    async def fetch(self, window: TimeRange) -> PriceSeries:
        series = TimeSeries(
            start=window.start, step=window.duration, values=(0.3,), unit=SeriesUnit.PRICE
        )
        return PriceSeries(source_id=self.source_id, price_includes="all_in", import_price=series)


def test_adapters_satisfy_the_protocols() -> None:
    pv: ForecastSource = FlatPv()
    price: PriceSource = FixedPrice()
    assert isinstance(pv, ForecastSource)
    assert isinstance(price, PriceSource)
    window = TimeRange(start=T0, end=T0 + timedelta(hours=1))
    assert len(asyncio.run(pv.fetch(window))) == 4
    assert asyncio.run(price.fetch(window)).import_price.values == (0.3,)


@pytest.mark.parametrize(
    ("hours", "bucket"),
    [
        (-1, HorizonBucket.H0_6),
        (0, HorizonBucket.H0_6),
        (5.99, HorizonBucket.H0_6),
        (6, HorizonBucket.H6_24),
        (24, HorizonBucket.H24_48),
        (47.9, HorizonBucket.H24_48),
        (48, HorizonBucket.H48_PLUS),
    ],
)
def test_horizon_buckets(hours: float, bucket: HorizonBucket) -> None:
    assert HorizonBucket.of(timedelta(hours=hours)) is bucket


def test_accuracy_record() -> None:
    r = AccuracyRecord(
        source_id="solcast.pv", horizon=HorizonBucket.H6_24, mae_w=120, bias_w=-30, n=96
    )
    assert AccuracyRecord.model_validate_json(r.model_dump_json()) == r
    for bad in ({"mae_w": -1}, {"n": -1}, {"source_id": "Bad Id"}, {"bias_w": float("nan")}):
        with pytest.raises(ValidationError):
            AccuracyRecord.model_validate(r.model_dump() | bad)
