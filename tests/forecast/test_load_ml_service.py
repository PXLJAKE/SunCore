# SPDX-License-Identifier: Apache-2.0
"""The KNN load model inside the load forecast service (WP12): shadow, off, auto, bookkeeping."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from suncore.core.forecast import HorizonBucket
from suncore.core.measurements import SITE_HOUSE_W, device_power_key
from suncore.core.series import SeriesUnit, TimeSeries
from suncore.forecast import accuracy as accuracy_module
from suncore.forecast.accuracy import load_accuracy
from suncore.forecast.load import LOAD_SOURCE_ID
from suncore.forecast.load_ml import (
    LOAD_ML_SOURCE_ID,
    ML_MIN_PAIRED_SAMPLES,
    LoadMlMode,
    PairedScore,
)
from suncore.forecast.load_service import (
    COMPARISON_KEY,
    NS_ML_STATE,
    LoadForecastService,
    LoadInputs,
)
from suncore.storage import Database, LearnedParamStore
from suncore.storage.timeseries import TimeseriesStore, open_timeseries

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)
START = datetime(2026, 8, 3, tzinfo=BERLIN).astimezone(UTC)  # a Monday


def moment(days: int, hour: int = 10, minute: int = 5) -> datetime:
    d = START.astimezone(BERLIN).date() + timedelta(days=days)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=BERLIN).astimezone(UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[TimeseriesStore]:
    db = await open_timeseries(tmp_path / "timeseries.db")
    yield TimeseriesStore(db)
    await db.close()


@pytest.fixture
async def learned(tmp_path: Path) -> AsyncIterator[LearnedParamStore]:
    db = await Database.open(tmp_path / "suncore.db")
    yield LearnedParamStore(db, clock=lambda: START)
    await db.close()


def ts(values: list[float | None], start: datetime = START) -> TimeSeries:
    return TimeSeries(start=start, step=Q, values=tuple(values), unit=SeriesUnit.W)


async def fill_history(store: TimeseriesStore, days: int) -> None:
    """House 300 W plus 400 W from 18:00 to 21:00 local; the AC draws 200 W of it at noon."""
    house: list[float | None] = []
    ac: list[float | None] = []
    for i in range(96 * days):
        hour = (START + Q * i).astimezone(BERLIN).hour
        draw = 200.0 if 12 <= hour < 14 else 0.0
        house.append(300.0 + (400.0 if 18 <= hour < 21 else 0.0) + draw)
        ac.append(draw)
    await store.store_quarter_hours(SITE_HOUSE_W, ts(house))
    await store.store_quarter_hours(device_power_key("ac"), ts(ac))


def service(
    store: TimeseriesStore | None,
    learned: LearnedParamStore | None,
    clock: Clock,
    mode: LoadMlMode,
    min_history_days: int = 30,
) -> LoadForecastService:
    return LoadForecastService(
        store,
        learned,
        clock=clock,
        tz=lambda: BERLIN,
        inputs=LoadInputs(device_ids=("ac",), ml_mode=mode, min_history_days=min_history_days),
    )


def paired(ml: float, profile: float = 100.0, at: datetime | None = None) -> PairedScore:
    return PairedScore(
        profile_mae_w=profile,
        ml_mae_w=ml,
        n=ML_MIN_PAIRED_SAMPLES * 2,
        computed_at=at or moment(35, 3),
    )


async def test_shadow_is_stored_with_the_profiles_and_never_used(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, 35)
    clock = Clock(moment(35))
    svc = service(store, learned, clock, "shadow")
    svc.set_comparison(paired(10))
    forecast = await svc.refresh()
    assert forecast is not None
    assert forecast == svc.profile_forecast()  # even though the ML model scored better
    ml = svc.ml_forecast()
    assert ml is not None
    assert ml.start == forecast.start
    assert len(ml) == len(forecast)
    clock.now = moment(35, 10, 35)
    await svc.refresh()  # not due
    clock.now = moment(35, 11, 5)
    await svc.refresh()
    profile_issues = await store.forecast_issues(LOAD_SOURCE_ID, "load")
    assert profile_issues == [moment(35), moment(35, 11, 5)]
    assert await store.forecast_issues(LOAD_ML_SOURCE_ID, "load") == profile_issues  # paired
    assert svc.source_ids == (LOAD_SOURCE_ID, LOAD_ML_SOURCE_ID)
    assert svc.planning_reasons() == ()
    view = svc.view()
    assert view.source_id == LOAD_SOURCE_ID
    assert view.model is not None
    assert view.model.mode == "shadow"
    assert view.model.in_use == "profile"
    assert view.model.ml_ready
    assert view.model.ml_days == 35
    assert view.model.reason.key == "forecast.load_model.ml_shadow"
    assert view.model.reason.args == {"days": 35, "min_days": 30}
    assert view.model.ml_mae_w == 10
    assert view.model.profile_mae_w == 100
    assert view.model.compared_n == ML_MIN_PAIRED_SAMPLES * 2


async def test_off_computes_nothing(store: TimeseriesStore, learned: LearnedParamStore) -> None:
    await fill_history(store, 35)
    now = moment(35)
    svc = service(store, learned, Clock(now), "off")
    assert await svc.refresh() is not None
    assert svc.ml_model is None
    assert svc.ml_forecast(now) is None
    assert svc.source_ids == (LOAD_SOURCE_ID,)
    assert await store.forecast_issues(LOAD_ML_SOURCE_ID, "load") == []
    view = svc.view()
    assert view.model is not None
    assert view.model.reason.key == "forecast.load_model.ml_off"
    assert not view.model.ml_ready


async def test_auto_with_too_little_history_keeps_the_profiles(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, 20)
    now = moment(20)
    svc = service(store, learned, Clock(now), "auto")
    svc.set_comparison(paired(10, at=now))
    forecast = await svc.refresh()
    assert forecast == svc.profile_forecast(now)
    (reason,) = svc.planning_reasons()
    assert reason.key == "forecast.load_model.ml_collecting"
    assert reason.args == {"days": 20, "min_days": 30}


async def test_auto_uses_the_model_while_it_is_better_with_hysteresis(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, 35)
    now = moment(35)
    svc = service(store, learned, Clock(now), "auto")
    await svc.refresh()
    assert svc.choice().reason.key == "forecast.load_model.ml_not_scored"  # nothing scored yet
    assert svc.forecast(now) == svc.profile_forecast(now)

    svc.set_comparison(paired(99))  # better, but by less than the margin
    assert svc.forecast(now) == svc.profile_forecast(now)
    svc.set_comparison(paired(60))
    ml = svc.ml_forecast(now)
    assert ml is not None
    load, choice = svc.selected(now)
    assert load == ml
    assert svc.planning_reasons(choice)[0].key == "forecast.load_model.ml_in_use"
    view = svc.view()
    assert view.source_id == LOAD_ML_SOURCE_ID
    assert view.p50_w == list(ml.p50.values[: 48 * 4])

    svc.set_comparison(paired(99))  # still better: it stays in use (hysteresis)
    assert svc.forecast(now) == ml
    svc.set_comparison(paired(100))  # no longer better
    assert svc.forecast(now) == svc.profile_forecast(now)
    assert svc.choice().reason.key == "forecast.load_model.ml_not_better"
    svc.set_comparison(paired(99))  # entering again needs the margin
    assert svc.forecast(now) == svc.profile_forecast(now)


async def test_auto_ignores_a_stale_comparison(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, 35)
    now = moment(35)
    svc = service(store, learned, Clock(now), "auto")
    await svc.refresh()
    svc.set_comparison(paired(10, at=now - timedelta(hours=37)))  # last night's job failed
    load, choice = svc.selected(now)
    assert load == svc.profile_forecast(now)
    assert choice.reason.key == "forecast.load_model.ml_not_scored"
    assert svc.model_view().compared_n == 0


async def test_auto_falls_back_when_the_model_has_no_forecast(
    store: TimeseriesStore, learned: LearnedParamStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fill_history(store, 35)
    now = moment(35)
    svc = service(store, learned, Clock(now), "auto")
    await svc.refresh()
    svc.set_comparison(paired(60))

    def broken(*_args: object, **_kwargs: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("suncore.forecast.load_service.forecast_knn", broken)
    load, choice = svc.selected(now)
    assert load == svc.profile_forecast(now)
    assert choice.in_use == "profile"
    assert choice.reason.key == "forecast.load_model.ml_unavailable"


async def test_a_stale_model_is_not_used(
    store: TimeseriesStore, learned: LearnedParamStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The KNN fit keeps failing: yesterday's model still forecasts, an older one does not."""
    await fill_history(store, 35)
    clock = Clock(moment(35))
    svc = service(store, learned, clock, "auto")
    await svc.refresh()
    fitted = svc.ml_model
    assert fitted is not None

    def broken(*_args: object, **_kwargs: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("suncore.forecast.load_service.fit_knn_model", broken)
    clock.now = moment(36)
    svc.set_comparison(paired(60, at=clock.now))
    await svc.refresh()
    assert svc.ml_model is fitted  # kept
    assert svc.view().fitted_at == clock.now  # the profiles refit regardless
    assert svc.choice().in_use == "ml"
    clock.now = moment(37)
    svc.set_comparison(paired(60, at=clock.now))
    await svc.refresh()
    load, choice = svc.selected()
    assert choice.reason.key == "forecast.load_model.ml_unavailable"
    assert load == svc.profile_forecast()
    assert svc.ml_forecast() is None
    assert not svc.model_view().ml_ready


async def test_the_model_is_fitted_once_per_day_unless_asked(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, 35)
    now = moment(35)
    svc = service(store, learned, Clock(now), "shadow")
    await svc.refit(now)
    first = svc.ml_model
    await svc.refit(now + timedelta(hours=1))
    assert svc.ml_model is first
    svc.request_refit()
    await svc.refit(now + timedelta(hours=2))
    assert svc.ml_model is not first
    second = svc.ml_model
    await svc.refit(moment(36, 0, 30))
    assert svc.ml_model is not second
    assert svc.ml_model is not None
    assert svc.ml_model.fitted_for == moment(36).astimezone(BERLIN).date()


async def test_the_profiles_keep_their_own_window(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, 70)
    now = moment(70)
    with_ml = service(store, learned, Clock(now), "shadow")
    without = service(store, learned, Clock(now), "off")
    await with_ml.refit(now)
    await without.refit(now)
    assert with_ml.model is not None
    assert without.model is not None
    assert with_ml.model.days == without.model.days == 56
    assert with_ml.profile_forecast(now) == without.profile_forecast(now)
    assert with_ml.view().devices_unmeasured == without.view().devices_unmeasured
    assert with_ml.ml_model is not None
    assert with_ml.ml_model.days == 70
    assert with_ml.model_view().ml_days == 70


async def test_autumn_dst_inside_the_window_keeps_56_profile_days(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    """3 Aug + 100 days = 11 Nov: 25 Oct (25 hours) lies inside both windows."""
    await fill_history(store, 100)
    now = moment(100)
    svc = service(store, learned, Clock(now), "shadow")
    await svc.refit(now)
    assert svc.model is not None
    assert svc.model.days == 56
    assert svc.ml_model is not None
    assert svc.ml_model.days == 91


async def test_long_min_history_widens_the_window(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, 100)
    now = moment(100)
    svc = service(store, learned, Clock(now), "auto", min_history_days=95)
    await svc.refit(now)
    assert svc.ml_model is not None
    assert svc.ml_model.days == 95


async def test_nightly_scores_both_models_on_the_same_quarter_hours(
    store: TimeseriesStore, learned: LearnedParamStore
) -> None:
    await fill_history(store, 16)
    clock = Clock(moment(14))
    svc = service(store, learned, clock, "shadow")
    await svc.refresh()  # issues of both models, same time
    clock.now = moment(16, 3, 0)
    records_ = await svc.nightly()
    assert {r.source_id for r in records_} == {LOAD_SOURCE_ID}
    stored = await load_accuracy(learned, [LOAD_SOURCE_ID, LOAD_ML_SOURCE_ID])
    assert set(stored) == {LOAD_SOURCE_ID, LOAD_ML_SOURCE_ID}
    ml = {r.horizon: r for r in stored[LOAD_ML_SOURCE_ID].records}
    profile = {r.horizon: r for r in stored[LOAD_SOURCE_ID].records}
    assert ml[HorizonBucket.H0_6].n == profile[HorizonBucket.H0_6].n > 0
    view = svc.model_view()
    assert view.compared_n == sum(r.n for r in profile.values())  # every quarter hour paired
    assert view.compared_at == clock.now
    assert view.ml_mae_w == pytest.approx(0, abs=1)  # the house is the same every day
    assert view.profile_mae_w is not None
    assert view.profile_mae_w < 20  # the profiles' kernel smoothing blurs the evening step
    rows = await learned.in_namespace(NS_ML_STATE)
    assert [r.key for r in rows] == [COMPARISON_KEY]

    # a later service (restart) reads the comparison back
    again = service(store, learned, clock, "shadow")
    await again.refit()
    assert again.model_view().compared_n == view.compared_n


async def test_a_failed_comparison_or_ml_score_leaves_nothing_stale(
    store: TimeseriesStore, learned: LearnedParamStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fill_history(store, 16)
    clock = Clock(moment(14))
    svc = service(store, learned, clock, "auto", min_history_days=7)
    await svc.refresh()
    svc.set_comparison(paired(10, at=moment(16, 2)))

    async def broken(*_args: object, **_kwargs: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("suncore.forecast.load_service.compute_paired_accuracy", broken)

    async def only_profile(data, learned_, source_ids, now, **kwargs):  # type: ignore[no-untyped-def]
        return await accuracy_module.update_accuracy(
            data, learned_, [LOAD_SOURCE_ID], now, **kwargs
        )

    monkeypatch.setattr("suncore.forecast.load_service.update_accuracy", only_profile)
    svc.set_scores({LOAD_ML_SOURCE_ID: []})
    clock.now = moment(16, 3)
    await svc.nightly()
    assert svc.choice().reason.key == "forecast.load_model.ml_not_scored"
    assert svc.model_view().compared_n == 0
    assert LOAD_ML_SOURCE_ID not in svc._scores


async def test_without_storage_nothing_breaks() -> None:
    svc = service(None, None, Clock(moment(1)), "auto")
    assert await svc.refresh() is None
    assert svc.forecast() is None
    assert svc.choice().reason.key == "forecast.load_model.ml_collecting"
    assert await svc.nightly() == []
