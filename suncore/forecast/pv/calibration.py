# SPDX-License-Identifier: Apache-2.0
"""Bias calibration of a PV forecast source (Phase 4 WP5).

Every source gets a table of **multiplicative factors** ``actual / forecast`` per cell
``hour of day x season x weather class``:

* *hour* - the local hour (site time zone) of the slot start;
* *season* - meteorological month groups ``djf``, ``mam``, ``jja``, ``son`` (the key is the
  month group, so it works on both hemispheres);
* *weather class* - the clear-sky index of the source's own P50
  (:mod:`suncore.forecast.pv.clearsky`).

**Learning** (once a night, one complete local day at a time): the source's issue that was
current at the start of the day (lead 0-24 h; what the plan for that day relied on) is put on the
15-minute grid and compared with the measured PV. A quarter hour counts when the forecast is at
least ``MIN_SAMPLE_W``, the measurement is known and the slot is **not clipped**. A cell keeps
energy sums (``forecast_wh``, ``actual_wh``) and a sample count ``n``; before a day's samples are
added, the cell's sums are multiplied by ``DECAY`` (an older season fades slowly; a cell that is
not visited keeps its values, so last year's winter is still there next winter).

**Factor** of a cell: ``raw = actual_wh / forecast_wh`` bounded to ``FACTOR_BOUNDS``, shrunk to 1
with few samples: ``f = 1 + n / (n + SHRINK_K) * (raw - 1)``. A cell without samples has
``f = 1``. The same factor scales P10, P50 and P90, so their order is kept.

**Activation**: the table is learnt (and stored) from the first day on, but applied only when
``days_with_data >= min_history_days`` (``forecast.learning.min_history_days``, default 30, the
maintainer's answer). Before that the API shows the calibrated forecast next to the uncalibrated
one for comparison, and the planner gets the uncalibrated one.

The table is stored in ``learned_param`` namespace ``forecast_bias``, key = source id, through
the :class:`~suncore.forecast.accuracy.LearnedParams` protocol.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from typing import Any, Final, Literal

import structlog

from suncore.core.series import Grid, QuantileSeries, TimeSeries, resample_power
from suncore.forecast.accuracy import LearnedParams
from suncore.forecast.pv.clearsky import (
    ClassifierMode,
    PlantGeometry,
    WeatherClass,
    classifier_mode,
    classify_slots,
    daily_peaks,
)

log = structlog.get_logger(__name__)

NS_FORECAST_BIAS: Final = "forecast_bias"
"""``learned_param`` namespace (equal to ``suncore.storage.learned.NS_FORECAST_BIAS``)."""
SCHEMA_VERSION: Final = 1

FACTOR_BOUNDS: Final = (0.5, 1.5)
SHRINK_K: Final = 16.0
"""Quarter hours at which a cell's factor goes half-way from 1 to its raw ratio."""
DECAY: Final = 0.98
"""Per learned day with samples in the cell (half-life about 34 such days)."""
MIN_SAMPLE_W: Final = 50.0
"""Slots with a smaller forecast (dawn, dusk) carry no usable ratio."""

SEASONS: Final[Mapping[int, str]] = {
    12: "djf",
    1: "djf",
    2: "djf",
    3: "mam",
    4: "mam",
    5: "mam",
    6: "jja",
    7: "jja",
    8: "jja",
    9: "son",
    10: "son",
    11: "son",
}

CalibrationReason = Literal["active", "collecting", "no_history", "classifier_changed"]
"""``active``: applied. ``collecting``: fewer than ``min_history_days`` days of data.
``no_history``: nothing learnt yet. ``classifier_changed``: the table was learnt with the other
weather classifier (the location became unknown); it waits instead of being misapplied."""


def season(local: datetime) -> str:
    return SEASONS[local.month]


def cell_key(slot_start: datetime, weather: WeatherClass, tz: tzinfo) -> str:
    local = slot_start.astimezone(tz)
    return f"{local.hour:02d}.{season(local)}.{weather.value}"


@dataclass(frozen=True, slots=True)
class CellStats:
    n: float = 0.0
    forecast_wh: float = 0.0
    actual_wh: float = 0.0


def _bounded(value: float, bounds: tuple[float, float] = FACTOR_BOUNDS) -> float:
    return min(bounds[1], max(bounds[0], value))


def cell_factor(
    stats: CellStats | None,
    *,
    shrink_k: float = SHRINK_K,
    bounds: tuple[float, float] = FACTOR_BOUNDS,
) -> float:
    """The cell's factor: bounded raw ratio, shrunk to 1 by ``n / (n + k)`` (module doc)."""
    if stats is None or stats.n <= 0 or stats.forecast_wh <= 0:
        return 1.0
    raw = _bounded(stats.actual_wh / stats.forecast_wh, bounds)
    return _bounded(1.0 + stats.n / (stats.n + shrink_k) * (raw - 1.0), bounds)


@dataclass(frozen=True, slots=True)
class BiasTable:
    """The learnt state of one source."""

    source_id: str
    mode: ClassifierMode
    cells: Mapping[str, CellStats] = field(default_factory=dict)
    days_with_data: int = 0
    learned_until: datetime | None = None
    """End of the last day that was processed (with or without samples)."""
    peak_reference_w: float | None = None
    """Largest daily forecast peak seen (reference of the ``daily_peak`` classifier)."""

    @property
    def samples(self) -> float:
        return sum(c.n for c in self.cells.values())


def new_table(source_id: str, plant: PlantGeometry | None) -> BiasTable:
    return BiasTable(source_id=source_id, mode=classifier_mode(plant))


@dataclass(frozen=True, slots=True)
class CalibrationState:
    """Whether a source's calibration is applied and why (API read model input)."""

    source_id: str
    active: bool
    reason: CalibrationReason
    days_with_data: int
    min_history_days: int
    cells: int
    factor_min: float | None
    factor_max: float | None


def calibration_state(
    source_id: str,
    table: BiasTable | None,
    *,
    plant: PlantGeometry | None,
    min_history_days: int,
) -> CalibrationState:
    if table is None or not table.cells:
        return CalibrationState(source_id, False, "no_history", 0, min_history_days, 0, None, None)
    factors = [cell_factor(c) for c in table.cells.values() if c.n > 0]
    reason: CalibrationReason
    if table.mode != classifier_mode(plant):
        reason = "classifier_changed"
    elif table.days_with_data < min_history_days:
        reason = "collecting"
    else:
        reason = "active"
    return CalibrationState(
        source_id=source_id,
        active=reason == "active",
        reason=reason,
        days_with_data=table.days_with_data,
        min_history_days=min_history_days,
        cells=len(factors),
        factor_min=min(factors) if factors else None,
        factor_max=max(factors) if factors else None,
    )


def slot_factors(
    forecast_p50: TimeSeries, table: BiasTable, *, plant: PlantGeometry | None, tz: tzinfo
) -> list[float]:
    """The factor of every slot of ``forecast_p50`` (1 where there is no class or no data)."""
    if table.mode != classifier_mode(plant):
        return [1.0] * len(forecast_p50)
    classes = classify_slots(
        forecast_p50, plant=plant, tz=tz, peak_reference_w=table.peak_reference_w
    )
    out: list[float] = []
    for (begin, _, _), weather in zip(forecast_p50.slots(), classes, strict=True):
        out.append(
            1.0 if weather is None else cell_factor(table.cells.get(cell_key(begin, weather, tz)))
        )
    return out


def calibrate(
    series: QuantileSeries, table: BiasTable, *, plant: PlantGeometry | None, tz: tzinfo
) -> QuantileSeries:
    """``series`` with every quantile scaled by its slot's factor (order preserved)."""
    factors = slot_factors(series.p50, table, plant=plant, tz=tz)

    def scaled(part: TimeSeries) -> TimeSeries:
        values = tuple(
            None if v is None else v * f for v, f in zip(part.values, factors, strict=True)
        )
        return TimeSeries(start=part.start, step=part.step, values=values, unit=part.unit)

    return QuantileSeries(p10=scaled(series.p10), p50=scaled(series.p50), p90=scaled(series.p90))


def learn_day(
    table: BiasTable,
    forecast_p50: TimeSeries,
    actual: TimeSeries,
    *,
    plant: PlantGeometry | None,
    tz: tzinfo,
    day_end: datetime,
    is_clipped: Callable[[datetime], bool] = lambda _slot: False,
    decay: float = DECAY,
    min_sample_w: float = MIN_SAMPLE_W,
) -> BiasTable:
    """``table`` with one more day learnt (``actual`` on the 15-minute grid of that day).

    A table learnt with the other classifier is reset first when the current one is
    ``clear_sky`` (the location became known: the better classifier wins). The other way round
    (the location is unknown tonight) the table is returned unchanged, so a night without Home
    Assistant does not throw away what was learnt.
    """
    mode = classifier_mode(plant)
    if table.mode != mode:
        if mode == "daily_peak":
            return table
        log.info("forecast.bias.classifier_reset", source=table.source_id)
        table = BiasTable(source_id=table.source_id, mode=mode)
    if not len(actual) or not len(forecast_p50):
        return replace(table, learned_until=day_end)
    grid = Grid(start=actual.start, step=actual.step, count=len(actual))
    forecast = resample_power(forecast_p50, grid)
    classes = classify_slots(forecast, plant=plant, tz=tz, peak_reference_w=table.peak_reference_w)
    hours = actual.step / timedelta(hours=1)
    day: dict[str, CellStats] = {}
    for (begin, _, measured), predicted, weather in zip(
        actual.slots(), forecast.values, classes, strict=True
    ):
        if (
            measured is None
            or predicted is None
            or weather is None
            or predicted < min_sample_w
            or is_clipped(begin)
        ):
            continue
        key = cell_key(begin, weather, tz)
        old = day.get(key, CellStats())
        day[key] = CellStats(
            n=old.n + 1,
            forecast_wh=old.forecast_wh + predicted * hours,
            actual_wh=old.actual_wh + max(0.0, measured) * hours,
        )
    cells = dict(table.cells)
    for key, add in day.items():
        old = cells.get(key, CellStats())
        cells[key] = CellStats(
            n=old.n * decay + add.n,
            forecast_wh=old.forecast_wh * decay + add.forecast_wh,
            actual_wh=old.actual_wh * decay + add.actual_wh,
        )
    peaks = daily_peaks(forecast, tz).values()
    peak = max([table.peak_reference_w or 0.0, *peaks])
    return BiasTable(
        source_id=table.source_id,
        mode=mode,
        cells=cells,
        days_with_data=table.days_with_data + (1 if day else 0),
        learned_until=day_end,
        peak_reference_w=peak if peak > 0 else None,
    )


# --- persistence -------------------------------------------------------------------------


def encode(table: BiasTable) -> dict[str, Any]:
    return {
        "mode": table.mode,
        "days_with_data": table.days_with_data,
        "learned_until": None if table.learned_until is None else table.learned_until.isoformat(),
        "peak_reference_w": table.peak_reference_w,
        "cells": {k: [c.n, c.forecast_wh, c.actual_wh] for k, c in sorted(table.cells.items())},
    }


def decode(source_id: str, payload: Mapping[str, Any], version: int) -> BiasTable | None:
    if version != SCHEMA_VERSION:
        log.warning("forecast.bias.unknown_schema", source=source_id, version=version)
        return None
    try:
        mode = payload["mode"]
        if mode not in ("clear_sky", "daily_peak"):
            raise ValueError(mode)
        until_raw = payload.get("learned_until")
        until = None if until_raw is None else datetime.fromisoformat(str(until_raw))
        peak_raw = payload.get("peak_reference_w")
        peak = None if peak_raw is None else float(peak_raw)
        raw_cells = payload["cells"]
        if not isinstance(raw_cells, Mapping):
            raise TypeError("cells")
        cells: dict[str, CellStats] = {}
        for key, value in raw_cells.items():
            n, f_wh, a_wh = (float(v) for v in value)
            cells[str(key)] = CellStats(n=n, forecast_wh=f_wh, actual_wh=a_wh)
        days = int(payload["days_with_data"])
    except (KeyError, ValueError, TypeError):
        log.warning("forecast.bias.unreadable", source=source_id)
        return None
    return BiasTable(source_id, mode, cells, days, until, peak)


async def save_bias(learned: LearnedParams, table: BiasTable) -> None:
    await learned.put(
        NS_FORECAST_BIAS, table.source_id, encode(table), schema_version=SCHEMA_VERSION
    )


async def load_bias(learned: LearnedParams) -> dict[str, BiasTable]:
    out: dict[str, BiasTable] = {}
    for param in await learned.in_namespace(NS_FORECAST_BIAS):
        table = decode(param.key, param.payload, param.schema_version)
        if table is not None:
            out[param.key] = table
    return out


__all__ = [
    "DECAY",
    "FACTOR_BOUNDS",
    "MIN_SAMPLE_W",
    "NS_FORECAST_BIAS",
    "SHRINK_K",
    "BiasTable",
    "CalibrationReason",
    "CalibrationState",
    "CellStats",
    "calibrate",
    "calibration_state",
    "cell_factor",
    "cell_key",
    "decode",
    "encode",
    "learn_day",
    "load_bias",
    "new_table",
    "save_bias",
    "season",
    "slot_factors",
]
