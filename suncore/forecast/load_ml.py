# SPDX-License-Identifier: Apache-2.0
"""Optional machine-learning load model: k nearest neighbours on analogue days (Phase 4 WP12).

NumPy only (no scikit-learn: its musl/aarch64 wheels are ``UNVERIFIED``, plan §0). Pure
functions; the service (:mod:`suncore.forecast.load_service`) reads the history, scores the
model next to the profiles and decides which one the planner gets (:func:`choose_load_model`).

**Samples** (the "training"): every quarter hour of every complete local day of the base load
history (the same base load as the profiles: house minus the controlled devices, see
:mod:`suncore.forecast.load`) with at least ``DAY_COVERAGE`` of its quarter hours known, at most
``ML_HISTORY_DAYS`` back (or ``min_history_days`` if that is longer). Each sample carries the
features of its day and slot:

* **day type** (:class:`~suncore.forecast.load.DayType`, from the same ``workday`` sensor /
  ``check_date`` / weekday rule as the profiles); a holiday is close to a weekend day and far
  from a workday - the profiles' "a holiday looks like a Sunday" rule as a distance,
* **slot of day** (wall-clock quarter hour, circular), only neighbours within
  ``SLOT_WINDOW`` slots are candidates,
* **temperature**: the day's mean outside temperature (recorded ``weather.temperature_c``) - only
  used when the forecast day has one,
* **recent load level**: the mean base load of the ``LEVEL_DAYS`` days before the day (a whole
  week, so the level does not depend on the weekday pattern),
* **age** in days, a weak term that prefers recent analogues and makes ties deterministic.

**Prediction** of a forecast quarter hour: the ``K_NEIGHBOURS`` samples with the smallest
distance (Euclidean over the scaled features, :func:`_distances`), sorted stably (equal distances
keep the sample order: fully deterministic), weighted ``1 / (WEIGHT_EPS + distance)``. P50 is the
weighted median of their values, P10/P90 the weighted 10 %/90 % quantiles (the same
interpolation as :func:`suncore.forecast.stats.weighted_quantile`), clipped at 0 W - so
``0 <= p10 <= p50 <= p90`` always holds, on the same 15-minute grid and unit (W) as the profile
forecast.

**Model choice** (:func:`choose_load_model`, ``forecast.learning.load_ml_mode``): ``off`` - not
computed; ``shadow`` (default) - computed, stored and scored as source ``load_ml``, never used;
``auto`` - used only while it has at least ``min_history_days`` days of history **and** its
rolling 14-day MAE (the same nightly statistic as the profiles', pooled over the horizon buckets)
is at least ``ML_MARGIN`` better than the profiles' on a comparable number of scored quarter hours
(plan §0: "only if measurably better"); otherwise the profiles. Every outcome carries a
:class:`~suncore.core.decisions.Reason`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

from suncore.core.decisions import Reason
from suncore.core.forecast import ACCURACY_MIN_SAMPLES, AccuracyRecord
from suncore.core.series import Grid, QuantileSeries, SeriesUnit, TimeSeries
from suncore.forecast.days import local_midnight
from suncore.forecast.load import (
    DAY_COVERAGE,
    MIN_CLEAN_DAYS,
    SLOTS_PER_DAY,
    TEMPERATURE_DAY_COVERAGE,
    DayType,
    DayValues,
    day_values,
    local_slot,
    weekday_rule,
)

LOAD_ML_SOURCE_ID: Final = "load_ml"
"""Source id of the KNN forecast in ``timeseries.db`` and the accuracy statistics."""

ML_HISTORY_DAYS: Final = 91
"""Default look-back of the samples (13 weeks: every weekday 13 times, a season's change)."""
KNN_MIN_DAYS: Final = 7
"""Usable days before the model forecasts at all (a week: every weekday once)."""
K_NEIGHBOURS: Final = 24
SLOT_WINDOW: Final = 2
"""Candidates lie within +-2 quarter hours of the forecast slot (wall clock)."""
SLOT_SCALE: Final = 2.0
"""Quarter hours per distance unit (one slot off = 0.5)."""
TEMPERATURE_SCALE_K: Final = 4.0
LEVEL_SCALE: Final = 0.15
"""Relative level difference per distance unit (``|ln(a / b)|``, 15 %)."""
LEVEL_DAYS: Final = 7
LEVEL_MIN_SLOTS: Final = 2 * SLOTS_PER_DAY
"""Known quarter hours the level window needs (else the level is unknown)."""
LEVEL_FLOOR_W: Final = 1.0
AGE_SCALE_DAYS: Final = 120.0
MISSING_FEATURE: Final = 1.0
"""Distance of a sample that lacks a feature the forecast day has (temperature, level)."""
WEIGHT_EPS: Final = 0.25
LOW_QUANTILE: Final = 0.1
HIGH_QUANTILE: Final = 0.9
ML_MARGIN: Final = 0.02
"""``auto`` needs an MAE at least 2 % below the profiles' (measurably better, plan §0)."""
ML_MIN_SCORED_SHARE: Final = 0.9
"""``auto`` needs the ML forecast scored on at least 90 % as many quarter hours as the profiles
(else it was not stored for most of the 14 days and the two MAEs are not comparable)."""

_TYPE_INDEX: Final = {DayType.WORKDAY: 0, DayType.WEEKEND: 1, DayType.HOLIDAY: 2}
_TYPE_DISTANCE: Final = np.array(
    [
        [0.0, 3.0, 3.0],  # workday
        [3.0, 0.0, 0.5],  # weekend
        [3.0, 0.5, 0.0],  # holiday: like a weekend day (the profiles' rule), not a workday
    ]
)

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

LoadMlMode = Literal["off", "shadow", "auto"]
ModelInUse = Literal["profile", "ml"]


@dataclass(frozen=True, slots=True, eq=False)
class KnnLoadModel:
    """The samples of the KNN model (column arrays, one entry per known quarter hour)."""

    days: int
    """Usable complete local days among the samples."""
    fitted_for: date
    """The local date the model was fitted on (ages count from it)."""
    recent_level_w: float | None
    """Mean base load of the ``LEVEL_DAYS`` days before the fit (the forecast's level)."""
    slot: IntArray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    day_type: IntArray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    temperature: FloatArray = field(default_factory=lambda: np.zeros(0))
    """Daily mean °C of the sample's day (NaN: unknown)."""
    level: FloatArray = field(default_factory=lambda: np.zeros(0))
    """Level (W) of the sample's day (NaN: unknown)."""
    age_days: FloatArray = field(default_factory=lambda: np.zeros(0))
    value: FloatArray = field(default_factory=lambda: np.zeros(0))
    candidates: tuple[IntArray, ...] = ()
    """Per slot of the day: the sample indices within ``SLOT_WINDOW`` (ascending)."""

    @property
    def ready(self) -> bool:
        return self.days >= KNN_MIN_DAYS

    @property
    def samples(self) -> int:
        return int(self.value.size)


# --- fit -----------------------------------------------------------------------------------


def _usable_days(
    base: TimeSeries,
    *,
    tz: tzinfo,
    until: datetime,
    unmeasured: Sequence[bool],
    oldest: date,
) -> list[DayValues]:
    """Complete days with ``DAY_COVERAGE`` from ``oldest`` on; quarter hours with an unmeasured
    device are left out once ``MIN_CLEAN_DAYS`` days are fully measured (else kept, as the
    profiles do before they have clean days)."""
    days = [
        d for d in day_values(base, tz, until) if d.coverage >= DAY_COVERAGE and d.day >= oldest
    ]
    if not any(unmeasured):
        return days
    masked_values = tuple(
        None if i < len(unmeasured) and unmeasured[i] else v for i, v in enumerate(base.values)
    )
    masked = {
        d.day: d for d in day_values(base.model_copy(update={"values": masked_values}), tz, until)
    }
    clean = sum(1 for d in days if masked[d.day].slots == d.slots)
    if clean < MIN_CLEAN_DAYS:
        return days
    return [masked[d.day] for d in days if masked[d.day].known()]


def daily_temperatures(temperature: TimeSeries, tz: tzinfo, until: datetime) -> dict[date, float]:
    """Mean temperature of every complete local day with ``TEMPERATURE_DAY_COVERAGE`` known."""
    out: dict[date, float] = {}
    for d in day_values(temperature, tz, until):
        known = [v for _, v in d.known()]
        if known and d.coverage >= TEMPERATURE_DAY_COVERAGE:
            out[d.day] = sum(known) / len(known)
    return out


def _as_array(series: TimeSeries) -> FloatArray:
    return np.array([math.nan if v is None else v for v in series.values], dtype=np.float64)


def _window_mean(values: FloatArray, first: int, last: int) -> float | None:
    """Mean of the known values in ``values[first:last]`` (clamped) with ``LEVEL_MIN_SLOTS``."""
    first, last = max(0, first), min(values.size, last)
    if last <= first:
        return None
    window = values[first:last]
    known = window[~np.isnan(window)]
    if known.size < LEVEL_MIN_SLOTS:
        return None
    return float(known.mean())


def fit_knn_model(
    base: TimeSeries,
    *,
    now: datetime,
    tz: tzinfo,
    day_types: Mapping[date, DayType],
    temperature: TimeSeries | None = None,
    unmeasured: Sequence[bool] = (),
    history_days: int = ML_HISTORY_DAYS,
) -> KnnLoadModel:
    """The samples (module doc) from the base load history up to the local midnight of ``now``.

    ``day_types`` maps history dates to their type (missing: weekday rule); ``temperature`` is
    the recorded outside temperature (°C, 15 minutes); ``unmeasured`` flags slots of ``base``
    with an unmeasured device (:attr:`~suncore.forecast.load.BaseLoad.unmeasured`).
    """
    today = now.astimezone(tz).date()
    midnight = local_midnight(now, tz).astimezone(UTC)
    days = _usable_days(
        base,
        tz=tz,
        until=midnight,
        unmeasured=unmeasured,
        oldest=today - timedelta(days=history_days),
    )
    values = _as_array(base)
    step = base.step
    level_span = (timedelta(days=LEVEL_DAYS)) // step

    def index_of(moment: datetime) -> int:
        return (moment - base.start) // step

    now_index = index_of(min(now, base.end)) if len(base) else 0
    recent = _window_mean(values, now_index - level_span, now_index) if len(base) else None
    temps = {} if temperature is None else daily_temperatures(temperature, tz, midnight)

    slots: list[int] = []
    kinds: list[int] = []
    temp_col: list[float] = []
    level_col: list[float] = []
    ages: list[float] = []
    vals: list[float] = []
    for d in days:
        start = datetime.combine(d.day, time(), tzinfo=tz).astimezone(UTC)
        level = _window_mean(values, index_of(start) - level_span, index_of(start))
        kind = _TYPE_INDEX[day_types.get(d.day, weekday_rule(d.day))]
        day_temp = temps.get(d.day, math.nan)
        age = float((today - d.day).days)
        for slot, value in d.known():
            slots.append(slot)
            kinds.append(kind)
            temp_col.append(day_temp)
            level_col.append(math.nan if level is None else level)
            ages.append(age)
            vals.append(max(0.0, value))
    slot_arr = np.array(slots, dtype=np.int64)
    by_slot = [np.flatnonzero(slot_arr == s) for s in range(SLOTS_PER_DAY)]
    candidates = tuple(
        np.sort(
            np.concatenate(
                [by_slot[(s + o) % SLOTS_PER_DAY] for o in range(-SLOT_WINDOW, SLOT_WINDOW + 1)]
            )
        ).astype(np.int64)
        for s in range(SLOTS_PER_DAY)
    )
    return KnnLoadModel(
        days=len(days),
        fitted_for=today,
        recent_level_w=recent,
        slot=slot_arr,
        day_type=np.array(kinds, dtype=np.int64),
        temperature=np.array(temp_col, dtype=np.float64),
        level=np.array(level_col, dtype=np.float64),
        age_days=np.array(ages, dtype=np.float64),
        value=np.array(vals, dtype=np.float64),
        candidates=candidates,
    )


# --- predict -------------------------------------------------------------------------------


def _distances(
    model: KnnLoadModel,
    idx: IntArray,
    *,
    slot: int,
    kind: int,
    temperature_c: float | None,
    level_w: float | None,
) -> FloatArray:
    """Squared scaled distance of the candidates ``idx`` to one forecast quarter hour."""
    offset = np.abs(model.slot[idx] - slot)
    offset = np.minimum(offset, SLOTS_PER_DAY - offset)
    d2: FloatArray = (offset / SLOT_SCALE) ** 2 + _TYPE_DISTANCE[kind, model.day_type[idx]] ** 2
    d2 = d2 + (model.age_days[idx] / AGE_SCALE_DAYS) ** 2
    if temperature_c is not None:
        temps = model.temperature[idx]
        term = np.where(
            np.isnan(temps), MISSING_FEATURE, np.abs(temps - temperature_c) / TEMPERATURE_SCALE_K
        )
        d2 = d2 + term**2
    if level_w is not None:
        levels = model.level[idx]
        ratio = np.log(np.maximum(levels, LEVEL_FLOOR_W) / max(level_w, LEVEL_FLOOR_W))
        term = np.where(np.isnan(levels), MISSING_FEATURE, np.abs(ratio) / LEVEL_SCALE)
        d2 = d2 + term**2
    return d2


def weighted_quantiles(
    values: FloatArray, weights: FloatArray, qs: Sequence[float]
) -> tuple[float, ...]:
    """Weighted quantiles with the midpoint interpolation of
    :func:`suncore.forecast.stats.weighted_quantile` (no extrapolation; monotone in ``q``)."""
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    total = float(w.sum())
    positions = (np.cumsum(w) - w / 2) / total
    return tuple(float(np.interp(q, positions, v)) for q in qs)


def predict_slot(
    model: KnnLoadModel,
    *,
    slot: int,
    day_type: DayType,
    temperature_c: float | None,
    level_w: float | None,
) -> tuple[float, float, float] | None:
    """``(p10, p50, p90)`` W of one wall-clock slot; ``None`` without a ready model."""
    if not model.ready or not model.candidates:
        return None
    idx = model.candidates[slot]
    if idx.size == 0:
        return None
    d2 = _distances(
        model,
        idx,
        slot=slot,
        kind=_TYPE_INDEX[day_type],
        temperature_c=temperature_c,
        level_w=level_w,
    )
    nearest = np.argsort(d2, kind="stable")[:K_NEIGHBOURS]
    weights = 1.0 / (WEIGHT_EPS + np.sqrt(d2[nearest]))
    p10, p50, p90 = weighted_quantiles(
        model.value[idx[nearest]], weights, (LOW_QUANTILE, 0.5, HIGH_QUANTILE)
    )
    p10, p50, p90 = max(0.0, p10), max(0.0, p50), max(0.0, p90)
    return min(p10, p50), p50, max(p50, p90)


def forecast_knn(
    model: KnnLoadModel,
    grid: Grid,
    tz: tzinfo,
    day_types: Mapping[date, DayType],
    daily_temperature: Mapping[date, float] | None = None,
) -> QuantileSeries | None:
    """P10/P50/P90 of the base load (W) on ``grid``; ``None`` while the model is not ready."""
    if not model.ready:
        return None
    temps = daily_temperature or {}
    lows: list[float | None] = []
    mids: list[float | None] = []
    highs: list[float | None] = []
    cache: dict[tuple[int, DayType, float | None], tuple[float, float, float] | None] = {}
    for begin in grid.starts():
        day, slot = local_slot(begin, tz)
        kind = day_types.get(day, weekday_rule(day))
        temp = temps.get(day)
        key = (slot, kind, temp)
        if key not in cache:  # a repeated wall-clock hour (DST) asks the same slot twice
            cache[key] = predict_slot(
                model, slot=slot, day_type=kind, temperature_c=temp, level_w=model.recent_level_w
            )
        q = cache[key]
        if q is None:
            return None
        lows.append(q[0])
        mids.append(q[1])
        highs.append(q[2])

    def series(values: list[float | None]) -> TimeSeries:
        return TimeSeries(start=grid.start, step=grid.step, values=tuple(values), unit=SeriesUnit.W)

    return QuantileSeries(p10=series(lows), p50=series(mids), p90=series(highs))


# --- model choice --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PooledError:
    """MAE over the horizon buckets of one source (weighted by their quarter hours)."""

    mae_w: float
    n: int


def pooled_error(records: Sequence[AccuracyRecord]) -> PooledError | None:
    n = sum(r.n for r in records)
    if n <= 0:
        return None
    return PooledError(mae_w=sum(r.mae_w * r.n for r in records) / n, n=n)


@dataclass(frozen=True, slots=True)
class LoadModelChoice:
    """Which load model the planner gets, and why."""

    mode: LoadMlMode
    in_use: ModelInUse
    reason: Reason
    ml_days: int
    min_history_days: int
    profile: PooledError | None
    ml: PooledError | None


def choose_load_model(
    mode: LoadMlMode,
    *,
    ml_days: int,
    ml_available: bool,
    min_history_days: int,
    profile_records: Sequence[AccuracyRecord],
    ml_records: Sequence[AccuracyRecord],
) -> LoadModelChoice:
    """The model the planner uses (module doc). Pure and deterministic.

    ``ml_days``: usable history days of the KNN model; ``ml_available``: it produced a forecast
    now; the records are the stored rolling 14-day accuracy of ``load_profile`` and ``load_ml``.
    """
    profile = pooled_error(profile_records)
    ml = pooled_error(ml_records)
    if mode == "off":
        in_use: ModelInUse = "profile"
        reason = Reason(key="forecast.load_model.ml_off")
    elif mode == "shadow":
        in_use = "profile"
        reason = Reason(
            key="forecast.load_model.ml_shadow",
            args={"days": ml_days, "min_days": min_history_days},
        )
    else:
        in_use, reason = _auto(
            ml_days=ml_days,
            ml_available=ml_available,
            min_history_days=min_history_days,
            profile=profile,
            ml=ml,
        )
    return LoadModelChoice(
        mode=mode,
        in_use=in_use,
        reason=reason,
        ml_days=ml_days,
        min_history_days=min_history_days,
        profile=profile,
        ml=ml,
    )


def _auto(
    *,
    ml_days: int,
    ml_available: bool,
    min_history_days: int,
    profile: PooledError | None,
    ml: PooledError | None,
) -> tuple[ModelInUse, Reason]:
    """Mode ``auto``: the KNN model only with enough history, a forecast now, a comparable
    score and an MAE ``ML_MARGIN`` below the profiles'."""
    if ml_days < min_history_days:
        return "profile", Reason(
            key="forecast.load_model.ml_collecting",
            args={"days": ml_days, "min_days": min_history_days},
        )
    if not ml_available:
        return "profile", Reason(key="forecast.load_model.ml_unavailable")
    needed = max(
        ACCURACY_MIN_SAMPLES,
        0 if profile is None else math.ceil(ML_MIN_SCORED_SHARE * profile.n),
    )
    if profile is None or ml is None or ml.n < needed:
        return "profile", Reason(
            key="forecast.load_model.ml_not_scored",
            args={"n": 0 if ml is None else ml.n, "needed": needed},
        )
    args = {"ml_mae_w": round(ml.mae_w), "profile_mae_w": round(profile.mae_w)}  # whole W
    if ml.mae_w < (1.0 - ML_MARGIN) * profile.mae_w:
        return "ml", Reason(key="forecast.load_model.ml_in_use", args=args)
    return "profile", Reason(key="forecast.load_model.ml_not_better", args=args)


__all__ = [
    "KNN_MIN_DAYS",
    "K_NEIGHBOURS",
    "LOAD_ML_SOURCE_ID",
    "ML_HISTORY_DAYS",
    "ML_MARGIN",
    "ML_MIN_SCORED_SHARE",
    "KnnLoadModel",
    "LoadMlMode",
    "LoadModelChoice",
    "ModelInUse",
    "PooledError",
    "choose_load_model",
    "daily_temperatures",
    "fit_knn_model",
    "forecast_knn",
    "pooled_error",
    "predict_slot",
    "weighted_quantiles",
]
