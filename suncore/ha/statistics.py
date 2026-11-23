# SPDX-License-Identifier: Apache-2.0
"""Quarter-hour history from Home Assistant's long-term statistics (Phase 4 WP3).

When ``timeseries.db`` is missing or empty (a restore without it, a fresh install) the
15-minute history is rebuilt from ``recorder/statistics_during_period``. The finest period
that outlives the recorder's purge is ``hour`` (``5minute`` is purged after ``purge_keep_days``,
default 10; verified in ``docs/research/ha-websocket-api.md`` "Long-term statistics"), so the
rebuilt history is a **step-hold of the hourly values**:

* a power or state sensor (``mean``): each of the four quarter hours of an hour gets the hour's
  mean, which preserves the energy of the hour (4 x W x 0.25 h = W x 1 h);
* an energy counter (``change`` = the hour's delta of ``sum``, in Wh): each quarter hour gets
  ``change / 1 h`` as mean power, which preserves the energy as well.

These values are *derived*: the intra-hour shape is lost. They are stored with
``derived = 1`` and are replaced as soon as the controller measured the same slot itself.

Request details (all verified): ``start_time``/``end_time`` are ISO 8601 **with** a UTC offset
(a value without one is read in HA's time zone), the response has ``start``/``end`` in integer
epoch **milliseconds** and no unit, so ``units`` is always sent. A statistic id without data is
absent from the result; a value can be ``null``. ``UNVERIFIED``: that the Supervisor proxy
forwards ``recorder/*`` (first run on Home Assistant OS); an error is the caller's to log.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import structlog

from suncore.core.series import MAX_POINTS, PLANNING_STEP, SeriesUnit, TimeSeries
from suncore.ha.connection import HAConnection

log = structlog.get_logger(__name__)

STATISTICS_UNITS: Final[Mapping[str, str]] = {"power": "W", "energy": "Wh"}
"""Target units, always sent so a display-unit change in HA does not change the numbers."""

DEFAULT_CHUNK_DAYS: Final = 7
DEFAULT_BATCH_SIZE: Final = 20
HOUR_MS: Final = 3_600_000
QUARTER_MS: Final = 900_000

StatKind = Literal["mean", "energy"]
"""``mean``: power or state sensor (column ``mean``); ``energy``: counter (``change``/``sum``)."""

_TYPES: Final[Mapping[str, tuple[str, ...]]] = {
    "mean": ("mean",),
    "energy": ("change", "sum"),
}


@dataclass(frozen=True, slots=True)
class StatisticSpec:
    """One statistic to fetch: ``key`` names it in the result, ``statistic_id`` is the
    sensor's entity id. ``invert`` flips the sign (a source with opposite polarity)."""

    key: str
    statistic_id: str
    kind: StatKind = "mean"
    unit: SeriesUnit = SeriesUnit.W
    invert: bool = False


@dataclass(frozen=True, slots=True)
class StatisticsHistory:
    """Result of a fetch: 15-minute series per spec key and the ids Home Assistant had no
    data for in the whole range."""

    series: Mapping[str, TimeSeries]
    missing: tuple[str, ...]


def _hour_start(row: Mapping[str, Any]) -> int | None:
    start = row.get("start")
    if isinstance(start, bool) or not isinstance(start, int | float):
        return None
    start_ms = int(start)
    return start_ms if start_ms % QUARTER_MS == 0 else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def hourly_values(rows: Iterable[Mapping[str, Any]], kind: StatKind) -> dict[int, float]:
    """``{hour start (epoch ms): value}`` of the usable rows.

    ``mean`` rows give the mean as is; ``energy`` rows give the hour's energy in Wh from
    ``change``, else from the difference of ``sum`` to the previous row *if that row is the
    previous hour*. A row with a missing value, a start off the quarter-hour grid or a
    negative energy (a counter reset HA did not compensate) is skipped - a gap, never a zero.
    """
    values: dict[int, float] = {}
    previous_sum: tuple[int, float] | None = None
    for row in sorted(rows, key=lambda r: _hour_start(r) or 0):
        start = _hour_start(row)
        if start is None:
            continue
        end = row.get("end")
        if (
            isinstance(end, int | float)
            and not isinstance(end, bool)
            and int(end) - start != HOUR_MS
        ):
            continue  # not an hour row: the request asked for period "hour"
        if kind == "mean":
            mean = _number(row.get("mean"))
            if mean is not None:
                values[start] = mean
            continue
        total = _number(row.get("sum"))
        change = _number(row.get("change"))
        if (
            change is None
            and total is not None
            and previous_sum is not None
            and previous_sum[0] + HOUR_MS == start
        ):
            change = total - previous_sum[1]
        if total is not None:
            previous_sum = (start, total)
        if change is not None and change >= 0:
            values[start] = change  # Wh in the hour == mean W over one hour
    return values


def quarter_hours(
    hourly: Mapping[int, float], *, unit: SeriesUnit, invert: bool = False
) -> TimeSeries | None:
    """Step-hold the hourly values onto a 15-minute grid (``None`` where an hour is missing).

    The grid runs from the first to the last known hour. ``None`` if there is no value.
    """
    if not hourly:
        return None
    first = min(hourly)
    last = max(hourly) + HOUR_MS
    count = (last - first) // QUARTER_MS
    if count > MAX_POINTS:
        msg = f"statistics range too long: {count} points exceed {MAX_POINTS}"
        raise ValueError(msg)
    sign = -1.0 if invert else 1.0
    slots: list[float | None] = [None] * count
    for start_ms, value in hourly.items():
        offset = (start_ms - first) // QUARTER_MS
        for i in range(HOUR_MS // QUARTER_MS):
            slots[offset + i] = sign * value + 0.0  # + 0.0: no negative zero
    return TimeSeries(
        start=datetime.fromtimestamp(first / 1000, UTC),
        step=PLANNING_STEP,
        values=tuple(slots),
        unit=unit,
    )


def _hour_floor(moment: datetime) -> datetime:
    utc = moment.astimezone(UTC)
    return utc.replace(minute=0, second=0, microsecond=0)


def _hour_ceil(moment: datetime) -> datetime:
    floor = _hour_floor(moment)
    return floor if floor == moment.astimezone(UTC) else floor + timedelta(hours=1)


def _windows(start: datetime, end: datetime, chunk: timedelta) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        out.append((cursor, min(cursor + chunk, end)))
        cursor += chunk
    return out


async def fetch_quarter_history(
    connection: HAConnection,
    specs: Sequence[StatisticSpec],
    start: datetime,
    end: datetime,
    *,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> StatisticsHistory:
    """Fetch hourly statistics in chunks and return them as 15-minute series per spec.

    Requests are split by time (``chunk_days``) and by statistic id (``batch_size``) so one
    answer stays small, and by kind because the requested ``types`` differ. Errors of
    Home Assistant (:class:`~suncore.ha.connection.HAError`) propagate: a rebuild is
    all-or-nothing and the caller decides whether to retry.
    """
    if not start < end:
        msg = "statistics range: start must be before end"
        raise ValueError(msg)
    if chunk_days < 1 or batch_size < 1:
        msg = "chunk_days and batch_size must be positive"
        raise ValueError(msg)
    start, end = _hour_floor(start), _hour_ceil(end)
    rows: dict[str, list[Mapping[str, Any]]] = {}
    by_id = {s.statistic_id: s for s in specs}
    for kind in ("mean", "energy"):
        ids = sorted({s.statistic_id for s in specs if s.kind == kind})
        for begin, stop in _windows(start, end, timedelta(days=chunk_days)):
            for i in range(0, len(ids), batch_size):
                batch = ids[i : i + batch_size]
                result = await connection.statistics_during_period(
                    batch,
                    begin,
                    stop,
                    period="hour",
                    types=_TYPES[kind],
                    units=STATISTICS_UNITS,
                )
                for statistic_id, statistic_rows in result.items():
                    if statistic_id in by_id and statistic_rows:
                        rows.setdefault(statistic_id, []).extend(statistic_rows)
    series: dict[str, TimeSeries] = {}
    missing: list[str] = []
    for spec in specs:
        built = quarter_hours(
            hourly_values(rows.get(spec.statistic_id, ()), spec.kind),
            unit=spec.unit,
            invert=spec.invert,
        )
        if built is None:
            missing.append(spec.statistic_id)
        else:
            series[spec.key] = built
    if missing:
        log.info("statistics.no_data", statistic_ids=sorted(set(missing)))
    return StatisticsHistory(series=series, missing=tuple(dict.fromkeys(missing)))


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CHUNK_DAYS",
    "STATISTICS_UNITS",
    "StatKind",
    "StatisticSpec",
    "StatisticsHistory",
    "fetch_quarter_history",
    "hourly_values",
    "quarter_hours",
]
