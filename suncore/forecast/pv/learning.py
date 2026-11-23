# SPDX-License-Identifier: Apache-2.0
"""The nightly learning job of the PV forecast (Phase 4 WP5).

Order, once a night after the time-series maintenance:

1. **Clipping**: read the measured PV and grid power (15-minute means) and the sources' P50 as
   they stood at the start of every local day (their mean is the reference forecast), and flag
   the limited quarter hours (:mod:`suncore.forecast.pv.clipping`).
2. **Accuracy** of every source and of the ``blended`` forecast (the one the planner uses) over
   the last 14 days, without the clipped quarter hours (:mod:`suncore.forecast.accuracy`).
3. **Bias tables**: every complete local day after a source's ``learned_until`` (at most
   ``BACKFILL_DAYS`` back) is learnt from that day-start issue, without the clipped quarter
   hours (:mod:`suncore.forecast.pv.calibration`).

Storage is reached only through protocols (the forecast package never imports storage). A
failing source is logged and keeps last night's state; the job never raises.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Final, Literal, Protocol

import structlog

from suncore.core.forecast import AccuracyRecord
from suncore.core.measurements import SITE_GRID_W, SITE_PV_W
from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    QuantileSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
    floor_to_step,
    resample_power,
)
from suncore.forecast.accuracy import (
    WINDOW_DAYS,
    AccuracyData,
    LearnedParams,
    update_accuracy,
)
from suncore.forecast.days import LocalDay, local_days, local_midnight, slice_series
from suncore.forecast.pv.calibration import (
    BiasTable,
    learn_day,
    load_bias,
    new_table,
    save_bias,
)
from suncore.forecast.pv.clearsky import PlantGeometry
from suncore.forecast.pv.clipping import clip_filter, clipped_slots

log = structlog.get_logger(__name__)

BLENDED_SOURCE_ID: Final = "blended"
"""Source id under which the blended forecast is stored and scored (template ids always
contain a dot, so it cannot collide with a configured source)."""
BACKFILL_DAYS: Final = 60
"""How far back a source's first learning run (or one after a long pause) reaches."""


class LearningData(AccuracyData, Protocol):
    """The part of ``TimeseriesStore`` the nightly job reads."""

    async def latest_forecast_before(
        self,
        source_id: str,
        kind: Literal["pv", "load", "price"],
        before: datetime,
        window: TimeRange | None = None,
    ) -> QuantileSeries | None: ...


@dataclass(frozen=True, slots=True)
class LearningContext:
    tz: tzinfo
    plant: PlantGeometry | None
    export_limit_w: float | None


@dataclass(frozen=True, slots=True)
class NightlyResult:
    accuracy: Mapping[str, Sequence[AccuracyRecord]]
    bias: Mapping[str, BiasTable]
    clipped_slots: int
    clipped: frozenset[datetime] = frozenset()
    """The flagged quarter hours (slot starts), for the risk model's learning."""


async def day_start_forecast(
    data: LearningData, source_id: str, day: LocalDay
) -> TimeSeries | None:
    """The source's P50 for ``day`` as it stood at the day's start, on the 15-minute grid."""
    issue = await data.latest_forecast_before(
        source_id, "pv", day.start, TimeRange(start=day.start - timedelta(hours=1), end=day.end)
    )
    if issue is None or not len(issue):
        return None
    grid = Grid(start=day.start, step=PLANNING_STEP, count=(day.end - day.start) // PLANNING_STEP)
    series = resample_power(issue.p50, grid)
    return series if any(v is not None for v in series.values) else None


def mean_reference(window: TimeSeries, forecasts: Sequence[TimeSeries]) -> TimeSeries | None:
    """Per slot of ``window`` the mean of the forecasts that know it (``None``: none knows any)."""
    sums = [0.0] * len(window)
    counts = [0] * len(window)
    for forecast in forecasts:
        offset = (forecast.start - window.start) // window.step
        for i, value in enumerate(forecast.values):
            j = offset + i
            if value is not None and 0 <= j < len(window):
                sums[j] += value
                counts[j] += 1
    if not any(counts):
        return None
    values = tuple(s / c if c else None for s, c in zip(sums, counts, strict=True))
    return TimeSeries(start=window.start, step=window.step, values=values, unit=SeriesUnit.W)


async def run_nightly(
    data: LearningData,
    learned: LearnedParams,
    source_ids: Sequence[str],
    now: datetime,
    context: LearningContext,
    *,
    window_days: int = WINDOW_DAYS,
    backfill_days: int = BACKFILL_DAYS,
) -> NightlyResult:
    """Clipping, accuracy (sources + ``blended``) and bias tables; never raises."""
    tz = context.tz
    today = local_midnight(now, tz)
    tables: dict[str, BiasTable] = {}
    try:
        tables = await load_bias(learned)
    except Exception:
        log.exception("forecast.bias.load_failed")
    oldest = today - timedelta(days=backfill_days)
    learn_from = {
        s: max(oldest, tables[s].learned_until or oldest) if s in tables else oldest
        for s in source_ids
    }
    earliest = min([today - timedelta(days=window_days), *learn_from.values()])
    days = local_days(earliest, today, tz)
    end = floor_to_step(now, PLANNING_STEP)
    clipped: frozenset[datetime] = frozenset()
    day_forecasts: dict[tuple[str, date], TimeSeries] = {}
    pv: TimeSeries | None = None
    if days:
        window = TimeRange(start=days[0].start, end=max(end, days[-1].end))
        try:
            pv = await data.read_series(SITE_PV_W, window, "15m", SeriesUnit.W)
            grid = (
                None
                if context.export_limit_w is None
                else await data.read_series(SITE_GRID_W, window, "15m", SeriesUnit.W)
            )
            for source_id in source_ids:
                for day in days:
                    forecast = await day_start_forecast(data, source_id, day)
                    if forecast is not None:
                        day_forecasts[(source_id, day.day)] = forecast
            reference = mean_reference(pv, list(day_forecasts.values()))
            clipped = clipped_slots(
                pv,
                tz=tz,
                grid=grid,
                export_limit=context.export_limit_w,
                reference=reference,
            )
        except Exception:
            log.exception("forecast.clipping.failed")
    is_clipped = clip_filter(clipped)
    accuracy = await update_accuracy(
        data,
        learned,
        [*source_ids, BLENDED_SOURCE_ID],
        now,
        window_days=window_days,
        is_clipped=is_clipped,
    )
    learnt: dict[str, BiasTable] = {}
    for source_id in source_ids:
        table = tables.get(source_id) or new_table(source_id, context.plant)
        if pv is None:
            learnt[source_id] = table
            continue
        try:
            for day in days:
                if day.start < learn_from[source_id]:
                    continue
                forecast = day_forecasts.get((source_id, day.day))
                actual = slice_series(pv, day.start, day.end)
                table = learn_day(
                    table,
                    forecast if forecast is not None else slice_series(actual, day.end, day.end),
                    actual,
                    plant=context.plant,
                    tz=tz,
                    day_end=day.end,
                    is_clipped=is_clipped,
                )
            if table != tables.get(source_id):
                await save_bias(learned, table)
        except Exception:
            log.exception("forecast.bias.failed", source=source_id)
            table = tables.get(source_id) or new_table(source_id, context.plant)
        learnt[source_id] = table
    log.info(
        "forecast.nightly_learning",
        sources=len(source_ids),
        days=len(days),
        clipped_slots=len(clipped),
    )
    return NightlyResult(
        accuracy=accuracy, bias=learnt, clipped_slots=len(clipped), clipped=clipped
    )


__all__ = [
    "BACKFILL_DAYS",
    "BLENDED_SOURCE_ID",
    "LearningContext",
    "LearningData",
    "LocalDay",
    "NightlyResult",
    "day_start_forecast",
    "local_days",
    "local_midnight",
    "mean_reference",
    "run_nightly",
    "slice_series",
]
