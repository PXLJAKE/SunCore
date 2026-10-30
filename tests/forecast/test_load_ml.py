# SPDX-License-Identifier: Apache-2.0
"""The optional KNN load model (WP12): features, quantiles, determinism and the model choice."""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.forecast import ACCURACY_MIN_SAMPLES, AccuracyRecord, HorizonBucket
from suncore.core.series import SeriesUnit, TimeSeries, planning_grid
from suncore.forecast.load import DayType
from suncore.forecast.load_ml import (
    KNN_MIN_DAYS,
    ML_MARGIN,
    LoadMlMode,
    choose_load_model,
    fit_knn_model,
    forecast_knn,
    pooled_error,
    predict_slot,
    weighted_quantiles,
)
from suncore.forecast.stats import weighted_quantile

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)
START = datetime(2026, 7, 6, tzinfo=BERLIN).astimezone(UTC)  # a Monday


def series(values: list[float | None], start: datetime = START) -> TimeSeries:
    return TimeSeries(start=start, step=Q, values=tuple(values), unit=SeriesUnit.W)


def history(days: int, shape: Callable[[datetime], float], start: datetime = START) -> TimeSeries:
    return series([shape(start + Q * i) for i in range(96 * days)], start)


def weekly(moment: datetime) -> float:
    """300 W, +400 W in the evening, weekends +200 W around noon."""
    t = moment.astimezone(BERLIN)
    level = 300.0 + (400.0 if 18 <= t.hour < 21 else 0.0)
    if t.weekday() >= 5 and 10 <= t.hour < 14:
        level += 200.0
    return level


def at(days: int, hour: int = 10) -> datetime:
    """``hour`` local on day ``days`` after START."""
    d = START.astimezone(BERLIN).date() + timedelta(days=days)
    return datetime(d.year, d.month, d.day, hour, 5, tzinfo=BERLIN).astimezone(UTC)


def p50_at(forecast_values: tuple[float | None, ...], start: datetime, moment: datetime) -> float:
    value = forecast_values[(moment - start) // Q]
    assert value is not None
    return value


# --- fit and predict -----------------------------------------------------------------------


def test_learns_day_type_and_time_of_day() -> None:
    base = history(35, weekly)
    now = at(35)  # Monday
    model = fit_knn_model(base, now=now, tz=BERLIN, day_types={})
    assert model.ready
    assert model.days == 35
    assert model.samples == 35 * 96
    assert model.recent_level_w is not None
    forecast = forecast_knn(model, planning_grid(now, timedelta(hours=48 * 3)), BERLIN, {})
    assert forecast is not None
    values = forecast.p50.values
    assert p50_at(values, forecast.start, at(35, 19)) == pytest.approx(700, abs=1)
    assert p50_at(values, forecast.start, at(35, 11)) == pytest.approx(300, abs=1)
    saturday = at(40, 11)  # Saturday noon
    assert p50_at(values, forecast.start, saturday) == pytest.approx(500, abs=1)


def test_a_holiday_is_predicted_like_a_weekend_day() -> None:
    base = history(35, weekly)
    now = at(35)
    tuesday = (now + timedelta(days=1)).astimezone(BERLIN).date()
    model = fit_knn_model(base, now=now, tz=BERLIN, day_types={})
    grid = planning_grid(now, timedelta(hours=48))
    holiday = forecast_knn(model, grid, BERLIN, {tuesday: DayType.HOLIDAY})
    workday = forecast_knn(model, grid, BERLIN, {})
    assert holiday is not None
    assert workday is not None
    noon = at(36, 11)
    assert p50_at(holiday.p50.values, grid.start, noon) == pytest.approx(500, abs=1)
    assert p50_at(workday.p50.values, grid.start, noon) == pytest.approx(300, abs=1)


def test_temperature_selects_analogue_days() -> None:
    """Cold days draw 600 W more; the forecast of a cold day follows the cold analogues."""
    rng = random.Random(3)
    temps = {
        START.astimezone(BERLIN).date() + timedelta(days=i): rng.choice([0.0, 20.0])
        for i in range(42)
    }

    def shape(moment: datetime) -> float:
        return 300.0 + (600.0 if temps[moment.astimezone(BERLIN).date()] < 10 else 0.0)

    base = history(42, shape)
    temperature = series([temps[(START + Q * i).astimezone(BERLIN).date()] for i in range(96 * 42)])
    temperature = temperature.model_copy(update={"unit": SeriesUnit.CELSIUS})
    now = at(42)
    model = fit_knn_model(base, now=now, tz=BERLIN, day_types={}, temperature=temperature)
    tomorrow = (now + timedelta(days=1)).astimezone(BERLIN).date()
    grid = planning_grid(now, timedelta(hours=40))
    cold = forecast_knn(model, grid, BERLIN, {}, {tomorrow: 0.0})
    warm = forecast_knn(model, grid, BERLIN, {}, {tomorrow: 20.0})
    assert cold is not None
    assert warm is not None
    noon = at(43, 12)
    assert p50_at(cold.p50.values, grid.start, noon) == pytest.approx(900, abs=1)
    assert p50_at(warm.p50.values, grid.start, noon) == pytest.approx(300, abs=1)


def test_recent_level_selects_analogue_days() -> None:
    """The house drew 800 W for three weeks, then 300 W for three weeks, then 800 W again:
    with the recent level at 800 W the 800 W weeks are the analogues."""

    def shape(moment: datetime) -> float:
        week = (moment - START) // timedelta(days=7)
        return 800.0 if week < 3 or week >= 6 else 300.0

    base = history(56, shape)
    now = at(56)
    model = fit_knn_model(base, now=now, tz=BERLIN, day_types={})
    assert model.recent_level_w == pytest.approx(800)
    forecast = forecast_knn(model, planning_grid(now, timedelta(hours=24)), BERLIN, {})
    assert forecast is not None
    assert all(v == pytest.approx(800, abs=1) for v in forecast.p50.values)


def test_too_little_history_gives_no_forecast() -> None:
    base = history(KNN_MIN_DAYS - 1, weekly)
    now = at(KNN_MIN_DAYS - 1)
    model = fit_knn_model(base, now=now, tz=BERLIN, day_types={})
    assert not model.ready
    assert forecast_knn(model, planning_grid(now, timedelta(hours=48)), BERLIN, {}) is None
    assert (
        predict_slot(model, slot=40, day_type=DayType.WORKDAY, temperature_c=None, level_w=None)
        is None
    )


def test_empty_history() -> None:
    empty = series([])
    model = fit_knn_model(empty, now=START, tz=BERLIN, day_types={})
    assert model.days == 0
    assert model.recent_level_w is None
    assert forecast_knn(model, planning_grid(START, timedelta(hours=4)), BERLIN, {}) is None


def test_days_with_little_coverage_and_unmeasured_quarter_hours_are_left_out() -> None:
    values: list[float | None] = [weekly(START + Q * i) for i in range(96 * 21)]
    for i in range(96 * 3, 96 * 3 + 40):  # day 3: only 56 of 96 quarter hours known
        values[i] = None
    unmeasured = [False] * len(values)
    for i in range(96 * 10, 96 * 10 + 8):  # day 10: a device without a measurement
        unmeasured[i] = True
    model = fit_knn_model(
        series(values), now=at(21), tz=BERLIN, day_types={}, unmeasured=unmeasured
    )
    assert model.days == 20
    assert model.samples == 20 * 96 - 8


def test_history_window_is_respected() -> None:
    base = history(60, weekly)
    model = fit_knn_model(base, now=at(60), tz=BERLIN, day_types={}, history_days=30)
    assert model.days == 30


def test_spring_forward_day_has_a_forecast_for_every_slot() -> None:
    start = datetime(2026, 2, 23, tzinfo=BERLIN).astimezone(UTC)
    base = history(35, weekly, start)
    now = datetime(2026, 3, 28, 10, tzinfo=BERLIN).astimezone(UTC)
    model = fit_knn_model(base, now=now, tz=BERLIN, day_types={})
    grid = planning_grid(now, timedelta(hours=48))  # contains the 23-hour day
    forecast = forecast_knn(model, grid, BERLIN, {})
    assert forecast is not None
    assert len(forecast) == grid.count
    assert all(v is not None for v in forecast.p50.values)


# --- properties --------------------------------------------------------------------------


def noisy(seed: int, scale: float) -> Callable[[datetime], float]:
    rng = random.Random(seed)
    cache: dict[datetime, float] = {}

    def shape(moment: datetime) -> float:
        if moment not in cache:
            cache[moment] = max(0.0, weekly(moment) * scale + rng.gauss(0, 150))
        return cache[moment]

    return shape


@settings(max_examples=25, deadline=None)
@given(
    days=st.integers(KNN_MIN_DAYS, 40),
    seed=st.integers(0, 10_000),
    scale=st.floats(0.0, 5.0),
    hours=st.integers(1, 48),
    temp=st.one_of(st.none(), st.floats(-20, 40)),
)
def test_quantiles_are_monotone_non_negative_and_on_the_grid(
    days: int, seed: int, scale: float, hours: int, temp: float | None
) -> None:
    base = history(days, noisy(seed, scale))
    now = at(days, 7)
    model = fit_knn_model(base, now=now, tz=BERLIN, day_types={})
    grid = planning_grid(now, timedelta(hours=hours))
    temps = {} if temp is None else {now.astimezone(BERLIN).date(): temp}
    forecast = forecast_knn(model, grid, BERLIN, {}, temps)
    assert forecast is not None
    assert forecast.start == grid.start
    assert len(forecast) == grid.count
    assert forecast.unit is SeriesUnit.W
    for lo, mid, hi in zip(
        forecast.p10.values, forecast.p50.values, forecast.p90.values, strict=True
    ):
        assert lo is not None
        assert mid is not None
        assert hi is not None
        assert 0 <= lo <= mid <= hi


@settings(max_examples=10, deadline=None)
@given(days=st.integers(KNN_MIN_DAYS, 30), seed=st.integers(0, 10_000))
def test_fit_and_forecast_are_deterministic(days: int, seed: int) -> None:
    now = at(days, 13)
    grid = planning_grid(now, timedelta(hours=48))
    results = []
    for _ in range(2):
        base = history(days, noisy(seed, 1.0))
        model = fit_knn_model(base, now=now, tz=BERLIN, day_types={})
        results.append(forecast_knn(model, grid, BERLIN, {}))
    assert results[0] == results[1]


def test_ties_are_broken_deterministically() -> None:
    """Every day identical: all distances tie apart from the age; the forecast is the value."""
    base = history(28, lambda _m: 400.0)
    now = at(28)
    model = fit_knn_model(base, now=now, tz=BERLIN, day_types={})
    first = forecast_knn(model, planning_grid(now, timedelta(hours=48)), BERLIN, {})
    second = forecast_knn(model, planning_grid(now, timedelta(hours=48)), BERLIN, {})
    assert first == second
    assert first is not None
    assert set(first.p50.values) == {400.0}


@given(
    st.lists(st.tuples(st.floats(-1e5, 1e5), st.floats(0.01, 10)), min_size=1, max_size=40),
    st.floats(0, 1),
)
def test_weighted_quantiles_match_the_reference(pairs: list[tuple[float, float]], q: float) -> None:
    values = [v for v, _ in pairs]
    weights = [w for _, w in pairs]
    (ours,) = weighted_quantiles(np.array(values), np.array(weights), (q,))
    assert ours == pytest.approx(weighted_quantile(values, weights, q), rel=1e-9, abs=1e-6)


# --- model choice ------------------------------------------------------------------------


def records(source: str, mae: float, n: int = 1344) -> list[AccuracyRecord]:
    return [
        AccuracyRecord(source_id=source, horizon=h, mae_w=mae, bias_w=0.0, n=n)
        for h in HorizonBucket
    ]


def choose(
    mode: LoadMlMode = "auto",
    *,
    ml_days: int = 45,
    ml_available: bool = True,
    min_history_days: int = 30,
    profile: list[AccuracyRecord] | None = None,
    ml: list[AccuracyRecord] | None = None,
) -> tuple[str, str]:
    choice = choose_load_model(
        mode,
        ml_days=ml_days,
        ml_available=ml_available,
        min_history_days=min_history_days,
        profile_records=records("load_profile", 100.0) if profile is None else profile,
        ml_records=records("load_ml", 80.0) if ml is None else ml,
    )
    return choice.in_use, choice.reason.key


def test_off_and_shadow_never_use_the_model() -> None:
    assert choose("off") == ("profile", "forecast.load_model.ml_off")
    assert choose("shadow") == ("profile", "forecast.load_model.ml_shadow")


def test_auto_uses_the_model_only_when_it_is_measurably_better() -> None:
    assert choose() == ("ml", "forecast.load_model.ml_in_use")
    better_by_less_than_the_margin = 100.0 * (1 - ML_MARGIN / 2)
    assert choose(ml=records("load_ml", better_by_less_than_the_margin)) == (
        "profile",
        "forecast.load_model.ml_not_better",
    )
    assert choose(ml=records("load_ml", 120.0)) == ("profile", "forecast.load_model.ml_not_better")


def test_auto_needs_enough_history() -> None:
    assert choose(ml_days=29) == ("profile", "forecast.load_model.ml_collecting")
    assert choose(ml_days=30) == ("ml", "forecast.load_model.ml_in_use")
    assert choose(ml_days=10, min_history_days=10) == ("ml", "forecast.load_model.ml_in_use")


def test_auto_needs_a_forecast_and_a_comparable_score() -> None:
    assert choose(ml_available=False) == ("profile", "forecast.load_model.ml_unavailable")
    assert choose(ml=[]) == ("profile", "forecast.load_model.ml_not_scored")
    assert choose(profile=[]) == ("profile", "forecast.load_model.ml_not_scored")
    # scored on 3 of the profiles' 14 days (stored only for the last few days)
    assert choose(ml=records("load_ml", 50.0, n=288)) == (
        "profile",
        "forecast.load_model.ml_not_scored",
    )
    thin = records("load_ml", 50.0, n=ACCURACY_MIN_SAMPLES // 6)
    assert choose(profile=records("load_profile", 100.0, n=10), ml=thin) == (
        "profile",
        "forecast.load_model.ml_not_scored",
    )


@given(
    mode=st.sampled_from(["off", "shadow", "auto"]),
    ml_days=st.integers(0, 400),
    min_days=st.integers(1, 365),
    available=st.booleans(),
    profile_mae=st.floats(0, 5000),
    ml_mae=st.floats(0, 5000),
    profile_n=st.integers(0, 5000),
    ml_n=st.integers(0, 5000),
)
def test_the_model_is_used_only_under_every_rule(
    *,
    mode: LoadMlMode,
    ml_days: int,
    min_days: int,
    available: bool,
    profile_mae: float,
    ml_mae: float,
    profile_n: int,
    ml_n: int,
) -> None:
    choice = choose_load_model(
        mode,
        ml_days=ml_days,
        ml_available=available,
        min_history_days=min_days,
        profile_records=records("load_profile", profile_mae, profile_n) if profile_n else [],
        ml_records=records("load_ml", ml_mae, ml_n) if ml_n else [],
    )
    assert choice.reason.key.startswith("forecast.load_model.")
    if choice.in_use == "ml":
        assert mode == "auto"
        assert available
        assert ml_days >= min_days
        assert ml_n >= profile_n * 0.9
        assert 3 * ml_n >= ACCURACY_MIN_SAMPLES
        assert ml_mae < profile_mae
    assert choice.ml_days == ml_days
    assert choice.min_history_days == min_days


def test_pooled_error_weights_the_buckets_by_their_quarter_hours() -> None:
    pooled = pooled_error(
        [
            AccuracyRecord(source_id="x", horizon=HorizonBucket.H0_6, mae_w=10, bias_w=0, n=1),
            AccuracyRecord(source_id="x", horizon=HorizonBucket.H6_24, mae_w=40, bias_w=0, n=3),
        ]
    )
    assert pooled is not None
    assert pooled.mae_w == pytest.approx(32.5)
    assert pooled.n == 4
    assert pooled_error([]) is None
