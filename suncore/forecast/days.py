# SPDX-License-Identifier: Apache-2.0
"""Local days and grid slices shared by the forecast statistics (PV, load, risk).

A local day is ``[local midnight, next local midnight)`` in absolute time, so a DST day has 23
or 25 hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from suncore.core.series import TimeSeries


@dataclass(frozen=True, slots=True)
class LocalDay:
    day: date
    start: datetime
    """Local midnight, in UTC."""
    end: datetime


def local_midnight(moment: datetime, tz: tzinfo) -> datetime:
    local = moment.astimezone(tz)
    return datetime.combine(local.date(), time(), tzinfo=tz)


def local_days(start: datetime, end: datetime, tz: tzinfo) -> list[LocalDay]:
    """The complete local days inside ``[start, end)`` (DST days have 23 or 25 hours)."""
    first = start.astimezone(tz).date()
    if datetime.combine(first, time(), tzinfo=tz) < start:
        first += timedelta(days=1)
    days: list[LocalDay] = []
    current = first
    while True:
        # in UTC: Python subtracts two datetimes of the same zone on the wall clock (24 h even
        # on a DST day), so the bounds are kept in absolute time
        begin = datetime.combine(current, time(), tzinfo=tz).astimezone(UTC)
        finish = datetime.combine(current + timedelta(days=1), time(), tzinfo=tz).astimezone(UTC)
        if finish > end:
            return days
        days.append(LocalDay(current, begin, finish))
        current += timedelta(days=1)


def slice_series(series: TimeSeries, begin: datetime, end: datetime) -> TimeSeries:
    """The grid-aligned part of ``series`` in ``[begin, end)`` (clamped to its extent)."""
    step = series.step
    first = max(0, (begin - series.start) // step)
    last = max(first, min(len(series), -(-(end - series.start) // step)))
    return TimeSeries(
        start=series.start + step * first,
        step=step,
        values=series.values[first:last],
        unit=series.unit,
    )


__all__ = ["LocalDay", "local_days", "local_midnight", "slice_series"]
