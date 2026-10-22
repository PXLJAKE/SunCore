# SPDX-License-Identifier: Apache-2.0
"""Nightly learning of the net energy errors and the risk read model (WP7)."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from suncore.core.battery import BatteryModel, BatterySettings
from suncore.core.measurements import SITE_PV_W
from suncore.core.series import QuantileSeries, SeriesUnit, TimeRange, TimeSeries
from suncore.forecast.load import LOAD_SOURCE_ID
from suncore.forecast.risk import ErrorModel, RiskWindow
from suncore.forecast.risk_learning import (
    NS_NET_ERROR,
    SAMPLES_KEY,
    learn_net_errors,
    load_error_model,
    risk_view,
)
from suncore.storage import Database, LearnedParamStore
from suncore.storage.learned import NS_NET_ERROR as STORAGE_NS
from suncore.storage.timeseries import TimeseriesStore, open_timeseries

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)
FIRST = date(2026, 6, 1)
START = datetime(2026, 6, 1, tzinfo=BERLIN).astimezone(UTC)
DAYS = 6
NOW = datetime(2026, 6, 7, 3, 30, tzinfo=BERLIN).astimezone(UTC)
BATTERY = BatteryModel(capacity_wh=10_000, max_charge_w=5_000, max_discharge_w=5_000)


def pv_at(moment: datetime, peak: float) -> float:
    t = moment.astimezone(BERLIN)
    hour = t.hour + t.minute / 60 + 0.125
    return peak * math.sin(math.pi * (hour - 6) / 15) if 6 <= hour < 21 else 0.0


def series(values: list[float | None], start: datetime = START) -> TimeSeries:
    return TimeSeries(start=start, step=Q, values=tuple(values), unit=SeriesUnit.W)


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


async def fill(store: TimeseriesStore, *, optimistic: float = 1.0) -> None:
    """Measured PV (peak 4 kW) and a 400 W base load; forecasts issued daily at 00:00 and
    17:00 local saying ``optimistic`` times the PV and the same load."""
    count = 96 * DAYS
    starts = [START + Q * i for i in range(count)]
    await store.store_quarter_hours(SITE_PV_W, series([pv_at(t, 4000) for t in starts]))
    for day in range(DAYS):
        for hour in (0, 17):
            issued = datetime.combine(
                FIRST + timedelta(days=day), datetime.min.time(), tzinfo=BERLIN
            ).astimezone(UTC) + timedelta(hours=hour)
            grid = [issued + Q * i for i in range(48 * 4)]
            pv = series([pv_at(t, 4000 * optimistic) for t in grid], issued)
            await store.store_forecast(
                "blended", "pv", issued, QuantileSeries(p10=pv, p50=pv, p90=pv)
            )
            load = series([400.0] * len(grid), issued)
            await store.store_forecast(
                LOAD_SOURCE_ID, "load", issued, QuantileSeries.deterministic(load)
            )


async def flat_base(window: TimeRange) -> TimeSeries:
    count = (window.end - window.start) // Q
    return series([400.0] * count, window.start)


async def test_learning_measures_the_net_error_per_window(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill(store, optimistic=1.3)
    model = await learn_net_errors(
        store, learned, NOW, tz=BERLIN, plant=None, base_reader=flat_base, backfill_days=10
    )
    days = {s.day for s in model.samples}
    assert min(days) == FIRST  # forecasts exist from 1 June on
    assert max(days) == date(2026, 6, 5)  # today - 2: the bridge of 5 June ended yesterday
    by_kind = {s.window: s for s in model.samples if s.day == date(2026, 6, 2)}
    assert set(by_kind) == set(RiskWindow)
    # the PV forecast was 30 % too high: daytime windows come in lower, the night is exact
    assert by_kind[RiskWindow.NEXT_DAY].error_wh < 0
    assert by_kind[RiskWindow.REST_OF_TODAY].error_wh < 0
    assert by_kind[RiskWindow.NIGHT].error_wh == pytest.approx(0, abs=1)
    # stored and idempotent
    stored = await load_error_model(learned)
    assert stored.learned_until == date(2026, 6, 5)
    key = [(s.day, s.window, s.weather) for s in model.samples]
    assert [(s.day, s.window, s.weather) for s in stored.model.samples] == key
    for a, b in zip(stored.model.samples, model.samples, strict=True):
        assert a.error_wh == pytest.approx(b.error_wh, abs=0.001)  # stored rounded to mWh
    again = await learn_net_errors(
        store, learned, NOW, tz=BERLIN, plant=None, base_reader=flat_base, backfill_days=10
    )
    assert [(s.day, s.window, s.weather) for s in again.samples] == key
    assert STORAGE_NS == NS_NET_ERROR


async def test_no_load_forecast_means_no_sample(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await store.store_quarter_hours(SITE_PV_W, series([1.0] * 96 * DAYS))
    model = await learn_net_errors(
        store, learned, NOW, tz=BERLIN, plant=None, base_reader=flat_base, backfill_days=10
    )
    assert model.samples == ()


async def test_unreadable_samples_start_again(learned: LearnedParamStore) -> None:
    await learned.put(NS_NET_ERROR, SAMPLES_KEY, {"samples": "x"})
    assert (await load_error_model(learned)).model == ErrorModel()
    await learned.put(NS_NET_ERROR, SAMPLES_KEY, {"samples": []}, schema_version=99)
    assert (await load_error_model(learned)).learned_until is None


# --- the read model ----------------------------------------------------------------------------


def forecast(level: float, now: datetime) -> QuantileSeries:
    s = series([level] * 192, now)
    return QuantileSeries(p10=s, p50=s, p90=s)


def test_view_without_a_load_forecast() -> None:
    view = risk_view(
        NOW,
        pv=None,
        load=None,
        settings=BatterySettings(),
        user_target_pct=None,
        model=ErrorModel(),
        min_history_days=30,
        tz=BERLIN,
        plant=None,
    )
    assert view.status == "no_forecast"
    assert view.windows == []


def test_view_without_a_battery_model_shows_the_windows() -> None:
    view = risk_view(
        NOW,
        pv=None,
        load=forecast(400, NOW),
        settings=BatterySettings(),
        user_target_pct=None,
        model=ErrorModel(),
        min_history_days=30,
        tz=BERLIN,
        plant=None,
    )
    assert view.status == "no_battery_model"
    assert [w.window for w in view.windows] == list(RiskWindow)
    assert view.morning_buffer is None
    assert not view.pv_forecast
    assert view.distribution_source == "source_quantiles"


def test_view_with_a_battery_model() -> None:
    view = risk_view(
        NOW,
        pv=None,
        load=forecast(400, NOW),
        settings=BatterySettings(model=BATTERY),
        user_target_pct=50.0,
        model=ErrorModel(),
        min_history_days=30,
        tz=BERLIN,
        plant=None,
    )
    assert view.status == "ok"
    assert view.evening_target is not None
    assert view.morning_buffer is not None
    assert view.evening_target.soc_pct >= 50
    assert view.evening_target.reason.key == "planner.battery.evening_target"
    assert view.evening_at == view.windows[1].start
