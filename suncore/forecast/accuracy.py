# SPDX-License-Identifier: Apache-2.0
"""Accuracy bookkeeping of the PV forecast sources (Phase 4 WP5).

For every source the forecast that was *issued* ahead of time is compared with the measured PV
power (``site.pv_w``, 15-minute means in ``timeseries.db``), per horizon bucket:

========  ==============  ==================================================================
bucket    lead time       forecast used for a slot
========  ==============  ==================================================================
0-6 h     0 to < 6 h      the newest issue at or before the slot start
6-24 h    6 to < 24 h     the newest issue at or before ``slot start - 6 h``
24-48 h   24 to < 48 h    the newest issue at or before ``slot start - 24 h``
========  ==============  ==================================================================

An issue only counts for a bucket if its lead time lies inside it (an issue that is older than
the bucket's upper edge is skipped, not blamed). The forecast is brought to the 15-minute grid
by a time-weighted mean of the source's power (energy preserving); intervals the forecast does
not cover completely are skipped.

**Sign convention (one for all of SunCore):** ``error = actual - forecast``; ``bias_w`` is the
mean error, so a positive bias means the source under-forecasts. ``mae_w`` is the mean absolute
error, ``n`` the number of 15-minute intervals.

Only **daylight** intervals count: the forecast or the measurement is above 0 W. A night of
exact zeros would otherwise dilute the MAE and the bias by about half and make every source
look better than it is during the day. Also excluded: intervals without a measurement, and
intervals flagged by the ``is_clipped`` hook (an
inverter or export limit makes the measured PV smaller than what the sun gave, which would
read as a forecast error; :mod:`suncore.forecast.pv.clipping` detects them and the nightly job
:func:`suncore.forecast.pv.learning.run_nightly` passes the hook - the default flags nothing).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal, Protocol

import structlog

from suncore.core.forecast import ACCURACY_MIN_SAMPLES, AccuracyRecord, HorizonBucket
from suncore.core.measurements import SITE_PV_W
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
from suncore.forecast.views import AccuracySourceView, ForecastAccuracyView

log = structlog.get_logger(__name__)

WINDOW_DAYS: Final = 14
"""Look-back of the nightly statistics (matches the 14-day inverse-MAE blending weights)."""

BUCKETS: Final[tuple[tuple[HorizonBucket, timedelta, timedelta], ...]] = (
    (HorizonBucket.H0_6, timedelta(0), timedelta(hours=6)),
    (HorizonBucket.H6_24, timedelta(hours=6), timedelta(hours=24)),
    (HorizonBucket.H24_48, timedelta(hours=24), timedelta(hours=48)),
)
"""``(bucket, lower edge, upper edge)`` of the lead time."""

ClipFilter = Callable[[datetime], bool]
"""``is_clipped(slot_start)``: whether the measured PV of that quarter hour was limited."""

ForecastKind = Literal["pv", "load", "price"]
ActualReader = Callable[[TimeRange], Awaitable[TimeSeries]]
"""Reads the measured quantity (15-minute means, W) a forecast is scored against."""

SCHEMA_VERSION: Final = 1
NS_FORECAST_ACCURACY: Final = "forecast_accuracy"
"""``learned_param`` namespace (equal to ``suncore.storage.learned.NS_FORECAST_ACCURACY``; the
forecast package does not import the storage package)."""


def no_clipping(_slot_start: datetime) -> bool:
    """The default hook: nothing is flagged."""
    return False


class AccuracyData(Protocol):
    """The part of ``TimeseriesStore`` the statistics read."""

    async def read_series(
        self,
        key: str,
        window: TimeRange,
        resolution: Literal["1m", "15m"] = "15m",
        unit: SeriesUnit | None = None,
        *,
        include_recent: bool = True,
    ) -> TimeSeries: ...

    async def forecast_issues(
        self, source_id: str, kind: Literal["pv", "load", "price"], since: datetime | None = None
    ) -> list[datetime]: ...

    async def read_forecast(
        self,
        source_id: str,
        kind: Literal["pv", "load", "price"],
        issued_at: datetime,
        window: TimeRange | None = None,
    ) -> QuantileSeries | None: ...


class LearnedEntry(Protocol):
    """A row of ``learned_param`` as ``LearnedParamStore`` returns it."""

    @property
    def key(self) -> str: ...

    @property
    def payload(self) -> dict[str, Any]: ...

    @property
    def schema_version(self) -> int: ...


class LearnedParams(Protocol):
    """The part of ``LearnedParamStore`` the bookkeeping uses."""

    async def put(
        self, namespace: str, key: str, payload: Mapping[str, Any], *, schema_version: int = 1
    ) -> None: ...

    async def in_namespace(self, namespace: str) -> Sequence[LearnedEntry]: ...


@dataclass(slots=True)
class _Sums:
    abs_error: float = 0.0
    error: float = 0.0
    n: int = 0

    def add(self, error: float) -> None:
        self.abs_error += abs(error)
        self.error += error
        self.n += 1


def quarter_errors(
    forecast_p50: TimeSeries,
    actual: TimeSeries,
    *,
    is_clipped: ClipFilter = no_clipping,
    daylight_only: bool = True,
) -> list[float]:
    """``actual - forecast`` per 15-minute daylight interval (forecast or measurement above 0 W)
    both series know and that is not clipped.

    ``forecast_p50`` is resampled onto the grid of ``actual`` (must be the 15-minute grid).
    ``daylight_only=False`` (the load forecast) keeps the intervals where both are zero too.
    """
    if not len(actual) or not len(forecast_p50):
        return []
    on_grid = resample_power(
        forecast_p50, Grid(start=actual.start, step=actual.step, count=len(actual))
    )
    errors: list[float] = []
    for (begin, _, measured), (_, _, predicted) in zip(
        actual.slots(), on_grid.slots(), strict=True
    ):
        if measured is None or predicted is None or is_clipped(begin):
            continue
        if daylight_only and measured <= 0 and predicted <= 0:
            continue  # night: both zero says nothing about the forecast
        errors.append(measured - predicted)
    return errors


async def compute_accuracy(
    data: AccuracyData,
    source_id: str,
    now: datetime,
    *,
    window_days: int = WINDOW_DAYS,
    is_clipped: ClipFilter = no_clipping,
    kind: ForecastKind = "pv",
    actual_reader: ActualReader | None = None,
) -> list[AccuracyRecord]:
    """The records of one source for the last ``window_days`` complete days (buckets with data).

    ``kind`` and ``actual_reader`` select what is scored: by default the PV forecast against
    ``site.pv_w`` on daylight intervals; the load forecast passes ``kind="load"`` and a reader
    of the measured base load, and then every interval counts (a night is load too).
    """
    end = floor_to_step(now, PLANNING_STEP)
    start = end - timedelta(days=window_days)
    window = TimeRange(start=start, end=end)
    if actual_reader is None:
        actual = await data.read_series(SITE_PV_W, window, "15m", SeriesUnit.W)
    else:
        actual = await actual_reader(window)
    daylight_only = kind == "pv"
    issues = await data.forecast_issues(source_id, kind, start - BUCKETS[-1][2])
    sums = {bucket: _Sums() for bucket, _, _ in BUCKETS}
    for issued, ranges, read in _issue_ranges(issues, start, end):
        forecast = await data.read_forecast(source_id, kind, issued, read)
        if forecast is None:
            continue
        for bucket, lo, hi in ranges:
            measured = _slice(actual, lo, hi)
            for error in quarter_errors(
                forecast.p50, measured, is_clipped=is_clipped, daylight_only=daylight_only
            ):
                sums[bucket].add(error)
    return [
        AccuracyRecord(
            source_id=source_id,
            horizon=bucket,
            mae_w=total.abs_error / total.n,
            bias_w=total.error / total.n,
            n=total.n,
        )
        for bucket, _, _ in BUCKETS
        if (total := sums[bucket]).n > 0
    ]


def _issue_ranges(
    issues: Sequence[datetime], start: datetime, end: datetime
) -> list[tuple[datetime, list[tuple[HorizonBucket, datetime, datetime]], TimeRange]]:
    """Per issue the ``(bucket, from, to)`` ranges it is judged on (module doc) and the window
    to read: an issue counts for a bucket only inside its lead time and only until a newer
    issue takes over; one read per issue over the union of its buckets (a forecast slot may
    start before the first quarter hour and still cover it)."""
    out: list[tuple[datetime, list[tuple[HorizonBucket, datetime, datetime]], TimeRange]] = []
    for index, issued in enumerate(issues):
        following = issues[index + 1] if index + 1 < len(issues) else None
        ranges: list[tuple[HorizonBucket, datetime, datetime]] = []
        for bucket, lower, upper in BUCKETS:
            lo = _ceil(max(start, issued + lower))
            upper_edge = min(end, issued + upper)
            if following is not None:
                upper_edge = min(upper_edge, following + lower)  # then a newer issue is judged
            hi = _ceil(upper_edge)
            if hi > lo:
                ranges.append((bucket, lo, hi))
        if not ranges:
            continue
        first = min(lo for _, lo, _ in ranges)
        last = max(hi for _, _, hi in ranges)
        out.append((issued, ranges, TimeRange(start=first - timedelta(hours=1), end=last)))
    return out


@dataclass(frozen=True, slots=True)
class PairedAccuracy:
    """Two sources scored on exactly the same quarter hours (WP12 model choice).

    Only issues both sources stored at the same time count, and in them only the quarter hours
    where both forecasts and the measurement are known, pooled over the horizon buckets; so the
    two MAEs compare like with like. ``computed_at`` is when the nightly job computed it."""

    source_a: str
    source_b: str
    mae_a_w: float
    mae_b_w: float
    n: int
    computed_at: datetime


async def compute_paired_accuracy(
    data: AccuracyData,
    source_a: str,
    source_b: str,
    now: datetime,
    *,
    kind: ForecastKind,
    actual_reader: ActualReader,
    window_days: int = WINDOW_DAYS,
) -> PairedAccuracy:
    """MAE of ``source_a`` and ``source_b`` on their common issues and quarter hours (every
    quarter hour counts, as for the load forecast)."""
    end = floor_to_step(now, PLANNING_STEP)
    start = end - timedelta(days=window_days)
    actual = await actual_reader(TimeRange(start=start, end=end))
    since = start - BUCKETS[-1][2]
    common = sorted(
        set(await data.forecast_issues(source_a, kind, since))
        & set(await data.forecast_issues(source_b, kind, since))
    )
    abs_a = abs_b = 0.0
    n = 0
    for issued, ranges, read in _issue_ranges(common, start, end):
        fa = await data.read_forecast(source_a, kind, issued, read)
        fb = await data.read_forecast(source_b, kind, issued, read)
        if fa is None or fb is None:
            continue
        for _, lo, hi in ranges:
            measured = _slice(actual, lo, hi)
            if not len(measured):
                continue
            grid = Grid(start=measured.start, step=measured.step, count=len(measured))
            pa, pb = resample_power(fa.p50, grid), resample_power(fb.p50, grid)
            for value, a, b in zip(measured.values, pa.values, pb.values, strict=True):
                if value is None or a is None or b is None:
                    continue
                abs_a += abs(value - a)
                abs_b += abs(value - b)
                n += 1
    return PairedAccuracy(
        source_a=source_a,
        source_b=source_b,
        mae_a_w=abs_a / n if n else 0.0,
        mae_b_w=abs_b / n if n else 0.0,
        n=n,
        computed_at=now,
    )


def _ceil(moment: datetime) -> datetime:
    """``moment`` rounded up to the 15-minute grid."""
    return floor_to_step(moment + PLANNING_STEP - timedelta(microseconds=1), PLANNING_STEP)


def _slice(series: TimeSeries, begin: datetime, end: datetime) -> TimeSeries:
    """The part of a series between two grid-aligned instants (clamped to its extent)."""
    step = series.step
    first = max(0, (begin - series.start) // step)
    last = min(len(series), -(-(end - series.start) // step))
    last = max(first, last)
    return TimeSeries(
        start=series.start + step * first,
        step=step,
        values=series.values[first:last],
        unit=series.unit,
    )


# --- persistence -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredAccuracy:
    """The nightly result of one source as kept in ``learned_param``."""

    source_id: str
    computed_at: datetime
    window_days: int
    records: tuple[AccuracyRecord, ...]


async def save_accuracy(
    learned: LearnedParams,
    source_id: str,
    records: Sequence[AccuracyRecord],
    *,
    now: datetime,
    window_days: int = WINDOW_DAYS,
) -> None:
    """Store the records of one source (replacing the previous night's)."""
    payload = {
        "computed_at": now.isoformat(),
        "window_days": window_days,
        "records": [json.loads(r.model_dump_json()) for r in records],
    }
    await learned.put(NS_FORECAST_ACCURACY, source_id, payload, schema_version=SCHEMA_VERSION)


async def load_accuracy(
    learned: LearnedParams, source_ids: Iterable[str] | None = None
) -> dict[str, StoredAccuracy]:
    """The stored results (all, or only ``source_ids``); an unreadable entry is skipped."""
    wanted = None if source_ids is None else set(source_ids)
    out: dict[str, StoredAccuracy] = {}
    for param in await learned.in_namespace(NS_FORECAST_ACCURACY):
        if wanted is not None and param.key not in wanted:
            continue
        stored = _decode(param.key, param.payload, param.schema_version)
        if stored is not None:
            out[param.key] = stored
    return out


def _decode(source_id: str, payload: Mapping[str, object], version: int) -> StoredAccuracy | None:
    if version != SCHEMA_VERSION:
        log.warning("forecast.accuracy.unknown_schema", source=source_id, version=version)
        return None
    try:
        raw_records = payload["records"]
        assert isinstance(raw_records, list)
        records = tuple(AccuracyRecord.model_validate(r) for r in raw_records)
        computed = datetime.fromisoformat(str(payload["computed_at"]))
        window = int(str(payload["window_days"]))
    except (KeyError, ValueError, TypeError, AssertionError):
        log.warning("forecast.accuracy.unreadable", source=source_id)
        return None
    return StoredAccuracy(source_id, computed, window, records)


def accuracy_view(
    source_ids: Iterable[str], stored: Mapping[str, StoredAccuracy]
) -> ForecastAccuracyView:
    """The API read model: every configured source, with its stored records if there are any."""
    sources = []
    for source_id in source_ids:
        item = stored.get(source_id)
        sources.append(
            AccuracySourceView(
                source_id=source_id,
                computed_at=None if item is None else item.computed_at,
                window_days=None if item is None else item.window_days,
                records=[] if item is None else list(item.records),
            )
        )
    return ForecastAccuracyView(min_samples=ACCURACY_MIN_SAMPLES, sources=sources)


async def update_accuracy(
    data: AccuracyData,
    learned: LearnedParams,
    source_ids: Iterable[str],
    now: datetime,
    *,
    window_days: int = WINDOW_DAYS,
    is_clipped: ClipFilter = no_clipping,
    kind: ForecastKind = "pv",
    actual_reader: ActualReader | None = None,
) -> dict[str, list[AccuracyRecord]]:
    """The nightly job: recompute and store every source; one failing source does not stop
    the others (it is logged and keeps last night's result)."""
    results: dict[str, list[AccuracyRecord]] = {}
    for source_id in source_ids:
        began = time.perf_counter()  # runtime for the log only, never for the statistics
        try:
            records = await compute_accuracy(
                data,
                source_id,
                now,
                window_days=window_days,
                is_clipped=is_clipped,
                kind=kind,
                actual_reader=actual_reader,
            )
            await save_accuracy(learned, source_id, records, now=now, window_days=window_days)
        except Exception:
            log.exception("forecast.accuracy.failed", source=source_id)
            continue
        log.info(
            "forecast.accuracy.source",
            source=source_id,
            records=len(records),
            runtime_ms=round((time.perf_counter() - began) * 1000, 1),
        )
        results[source_id] = records
    return results


__all__ = [
    "ACCURACY_MIN_SAMPLES",
    "BUCKETS",
    "NS_FORECAST_ACCURACY",
    "WINDOW_DAYS",
    "AccuracyData",
    "ActualReader",
    "ClipFilter",
    "ForecastKind",
    "LearnedParams",
    "PairedAccuracy",
    "StoredAccuracy",
    "accuracy_view",
    "compute_accuracy",
    "compute_paired_accuracy",
    "load_accuracy",
    "no_clipping",
    "quarter_errors",
    "save_accuracy",
    "update_accuracy",
]
