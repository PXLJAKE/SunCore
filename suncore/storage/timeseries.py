# SPDX-License-Identifier: Apache-2.0
"""``timeseries.db``: measurements, forecasts and prices (ADR-0004, Phase 4 WP3).

A separate SQLite file next to ``suncore.db``, excluded from Home Assistant backups
(``backup_exclude``) and rebuildable: the 15-minute history comes back from HA's long-term
statistics (``suncore.app.history``). It has its own migrations (``ts_migrations``) and its own
``PRAGMA user_version``; a file that is newer than the code is refused (and left untouched).

Tables and retention (all times injected, nothing reads the wall clock):

========================  ======================================  =============
table                     content                                 kept
========================  ======================================  =============
``measurement``           controller resolution, one row per tick 48 h
``measurement_1m``        mean/min/max/n per minute               30 days
``measurement_15m``       mean/min/max/n per quarter hour         unlimited
``forecast``              P10/P50/P90 per slot, per issue time    45 days (slots)
``price``                 import/export price per slot            400 days
========================  ======================================  =============

Conventions: timestamps are integer seconds since the UNIX epoch (UTC); a gap in a measurement
is a missing row (never a stored zero) and reads back as ``None``; the aggregates are
sample-weighted, so ``mean * n`` adds up exactly (a 15-minute mean is the sample mean over its
minutes) and the power-times-time energy of an interval survives the downsampling.
``downsample`` is idempotent: it recomputes every complete bucket from the finer table, and
the retention cut-offs are aligned to the bucket size, so a bucket is either fully present in
its source table or gone - a re-run never aggregates half a bucket.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Final, Literal

import structlog

from suncore.core.measurements import is_percent_key
from suncore.core.series import (
    MAX_POINTS,
    PriceIncludes,
    PriceSeries,
    QuantileSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
)
from suncore.storage.db import Database, Row, SqlParam
from suncore.storage.errors import MigrationError, SchemaTooNewError
from suncore.storage.migrations import Migration, load_migrations

log = structlog.get_logger(__name__)

TS_FILENAME: Final = "timeseries.db"

RAW_KEEP: Final = timedelta(hours=48)
MINUTE_KEEP: Final = timedelta(days=30)
FORECAST_KEEP: Final = timedelta(days=45)
PRICE_KEEP: Final = timedelta(days=400)

VACUUM_MIN_FREE_BYTES: Final = 8 * 1024 * 1024
VACUUM_MIN_FREE_FRACTION: Final = 0.25
"""``VACUUM`` rewrites the file; it runs only if at least this much (and this share) is free."""

MINUTE_S: Final = 60
QUARTER_S: Final = 900

Resolution = Literal["1m", "15m"]
ForecastKind = Literal["pv", "load", "price"]
_STEP_OF: Final[Mapping[str, int]] = {"1m": MINUTE_S, "15m": QUARTER_S}
_TABLE_OF: Final[Mapping[str, str]] = {"1m": "measurement_1m", "15m": "measurement_15m"}

MeasurementRow = tuple[str, datetime, float | None]
"""``(key, timestamp, value)``; a ``None`` value is a gap and is not stored."""


# --- opening ------------------------------------------------------------------------


def timeseries_migrations() -> list[Migration]:
    """The migrations of ``timeseries.db``."""
    return load_migrations(resources.files("suncore.storage.ts_migrations"))


async def open_timeseries(path: Path | str) -> Database:
    """Open (creating, migrating) ``timeseries.db``; raises ``SchemaTooNewError`` if too new."""
    return await Database.open(path, migrations=timeseries_migrations())


async def open_timeseries_or_recover(path: Path | str) -> Database | None:
    """Open ``timeseries.db`` for the application; ``None`` = run without time series.

    The file is rebuildable, so a corrupt one (not a database, a migration that fails) is
    moved aside as ``timeseries.db.corrupt`` and a fresh one is created. A file that is
    *newer* than this code is never touched: the application runs without time series and
    says so (the newer version may still need the data). Control never depends on this file.
    """
    target = Path(path)
    for attempt in (1, 2):
        try:
            return await open_timeseries(target)
        except SchemaTooNewError as exc:
            log.error("timeseries.schema_too_new", path=str(target), error=str(exc))
            return None
        except (sqlite3.DatabaseError, MigrationError, OSError) as exc:
            log.error("timeseries.open_failed", path=str(target), attempt=attempt, error=str(exc))
            if attempt == 2:
                return None
            try:
                _move_aside(target)
            except OSError:
                log.exception("timeseries.move_aside_failed", path=str(target))
                return None
    return None


def _move_aside(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    if path.exists():
        path.replace(path.with_name(path.name + ".corrupt"))


# --- helpers ------------------------------------------------------------------------


def _epoch(moment: datetime) -> int:
    if moment.tzinfo is None or moment.utcoffset() is None:
        msg = "timestamps must be timezone-aware"
        raise ValueError(msg)
    return math.floor(moment.timestamp())


def _from_epoch(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _floor(seconds: int, step: int) -> int:
    return seconds - seconds % step


def _num(value: object) -> float:
    assert isinstance(value, int | float)
    return float(value)


def _int(value: object) -> int:
    assert isinstance(value, int | float)
    return int(value)


def _opt(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else value


def _opt_num(value: object) -> float | None:
    return None if value is None else _num(value)


def _default_unit(key: str) -> SeriesUnit:
    return SeriesUnit.PERCENT if is_percent_key(key) else SeriesUnit.W


# --- reports ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DownsampleReport:
    minute_rows: int
    quarter_rows: int


@dataclass(frozen=True, slots=True)
class RetentionReport:
    raw: int
    minute: int
    forecast: int
    price: int

    @property
    def total(self) -> int:
        return self.raw + self.minute + self.forecast + self.price


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    downsample: DownsampleReport
    retention: RetentionReport
    vacuumed: bool
    size_bytes: int


@dataclass(frozen=True, slots=True)
class TimeseriesSize:
    """Size of ``timeseries.db`` for the diagnostics page."""

    file_bytes: int
    wal_bytes: int
    free_bytes: int
    """Pages inside the file that hold no data (reclaimed by ``VACUUM``)."""
    rows: Mapping[str, int]

    @property
    def total_bytes(self) -> int:
        return self.file_bytes + self.wal_bytes


# --- the repository -----------------------------------------------------------------


class TimeseriesStore:
    """Repository over ``timeseries.db`` with typed methods and core series as results."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def database(self) -> Database:
        return self._db

    # --- measurements -----------------------------------------------------------

    async def append_measurements(self, rows: Iterable[MeasurementRow]) -> int:
        """Store measurements in one batch; gaps (``None``, NaN, inf) are skipped.

        A second value for the same ``(key, second)`` replaces the first. Returns the number
        of rows written.
        """
        batch: list[Sequence[SqlParam]] = []
        for key, moment, value in rows:
            checked = _opt(value)
            if checked is not None:
                batch.append((key, _epoch(moment), checked))
        if batch:
            await self._db.executemany(
                "INSERT OR REPLACE INTO measurement (key, ts, value) VALUES (?, ?, ?)", batch
            )
        return len(batch)

    async def read_raw(self, key: str, window: TimeRange) -> list[tuple[datetime, float]]:
        """Controller-resolution samples in ``window`` (oldest first)."""
        rows = await self._db.fetchall(
            "SELECT ts, value FROM measurement WHERE key = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (key, _epoch(window.start), _ceil_epoch(window.end)),
        )
        return [(_from_epoch(_int(r[0])), _num(r[1])) for r in rows]

    async def read_series(
        self,
        key: str,
        window: TimeRange,
        resolution: Resolution = "15m",
        unit: SeriesUnit | None = None,
        *,
        include_recent: bool = True,
    ) -> TimeSeries:
        """The mean of ``key`` per minute or quarter hour as a regular series.

        The grid starts at the bucket containing ``window.start`` and ends at the bucket
        containing the last instant of the window. A bucket without data is ``None``.
        With ``include_recent`` (15 minutes only), buckets that the nightly job has not
        aggregated yet are computed on the fly from the 1-minute and then the raw rows, so
        yesterday's data is available at once; a bucket still in progress is included with
        what has been measured so far (end the window at the last complete bucket if that
        matters).
        """
        step = _STEP_OF[resolution]
        first = _floor(_epoch(window.start), step)
        end = _ceil_epoch(window.end)
        count = max(1, -(-(end - first) // step))
        if count > MAX_POINTS:
            msg = f"read_series: {count} points exceed the limit of {MAX_POINTS}"
            raise ValueError(msg)
        last = first + count * step
        means: dict[int, float] = {}
        if include_recent and resolution == "15m":
            means.update(await self._recent_quarters(key, first, last))
        table = _TABLE_OF[resolution]
        rows = await self._db.fetchall(
            f"SELECT ts, mean FROM {table} WHERE key = ? AND ts >= ? AND ts < ?",
            (key, first, last),
        )
        means.update({_int(r[0]): _num(r[1]) for r in rows})
        values = tuple(means.get(first + i * step) for i in range(count))
        return TimeSeries(
            start=_from_epoch(first),
            step=timedelta(seconds=step),
            values=values,
            unit=unit or _default_unit(key),
        )

    async def _recent_quarters(self, key: str, first: int, last: int) -> dict[int, float]:
        """Quarter-hour means from the finer tables (1 minute wins over raw per bucket)."""
        out: dict[int, float] = {}
        raw = await self._db.fetchall(
            "SELECT (ts / 900) * 900 AS b, AVG(value) FROM measurement "
            "WHERE key = ? AND ts >= ? AND ts < ? GROUP BY b",
            (key, first, last),
        )
        out.update({_int(r[0]): _num(r[1]) for r in raw})
        minutes = await self._db.fetchall(
            "SELECT (ts / 900) * 900 AS b, SUM(mean * n) / SUM(n) FROM measurement_1m "
            "WHERE key = ? AND ts >= ? AND ts < ? GROUP BY b",
            (key, first, last),
        )
        out.update({_int(r[0]): _num(r[1]) for r in minutes})
        return out

    async def store_quarter_hours(
        self, key: str, series: TimeSeries, *, derived: bool = False
    ) -> int:
        """Write a 15-minute series into ``measurement_15m`` (``None`` slots are skipped).

        ``derived=True`` marks values rebuilt from Home Assistant's statistics: they fill
        free slots and replace earlier derived rows but never overwrite measured ones.
        Returns the number of slots offered.
        """
        if series.step != timedelta(seconds=QUARTER_S):
            msg = "store_quarter_hours: needs a 15-minute series"
            raise ValueError(msg)
        batch: list[Sequence[SqlParam]] = []
        for begin, _end, value in series.slots():
            checked = _opt(value)
            if checked is not None:
                batch.append((key, _epoch(begin), checked, checked, checked, 1 if derived else 0))
        if not batch:
            return 0
        guard = "WHERE measurement_15m.derived = 1" if derived else ""
        await self._db.executemany(
            "INSERT INTO measurement_15m (key, ts, mean, min, max, n, derived) "
            "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT(key, ts) DO UPDATE SET "
            "mean = excluded.mean, min = excluded.min, max = excluded.max, "
            f"n = excluded.n, derived = excluded.derived {guard}",
            batch,
        )
        return len(batch)

    async def has_quarter_history(self) -> bool:
        """Whether ``measurement_15m`` holds any row (the rebuild runs when it does not)."""
        return await self._db.fetchone("SELECT 1 FROM measurement_15m LIMIT 1") is not None

    # --- forecasts ----------------------------------------------------------------

    async def store_forecast(
        self,
        source_id: str,
        kind: ForecastKind,
        issued_at: datetime,
        forecast: QuantileSeries | TimeSeries,
    ) -> int:
        """Store one issue of a forecast; an earlier store of the same issue is replaced.

        Every slot is stored, also an unknown one (all NULL), so the series reads back with
        the same start and length. A plain ``TimeSeries`` fills ``p50`` only.
        """
        p10: TimeSeries | None
        p90: TimeSeries | None
        if isinstance(forecast, QuantileSeries):
            p10, p50, p90 = forecast.p10, forecast.p50, forecast.p90
        else:
            p10 = p90 = None
            p50 = forecast
        step_s = int(p50.step.total_seconds())
        issued = _epoch(issued_at)
        rows: list[Sequence[SqlParam]] = []
        for i, begin in enumerate(p50.slot_start(j) for j in range(len(p50))):
            rows.append(
                (
                    source_id,
                    kind,
                    issued,
                    _epoch(begin),
                    step_s,
                    p50.unit.value,
                    None if p10 is None else p10.values[i],
                    p50.values[i],
                    None if p90 is None else p90.values[i],
                )
            )
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM forecast WHERE source_id = ? AND kind = ? AND issued_at = ?",
                (source_id, kind, issued),
            )
            await tx.executemany(
                "INSERT INTO forecast (source_id, kind, issued_at, slot_start, step_s, unit, "
                "p10, p50, p90) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    async def read_forecast(
        self,
        source_id: str,
        kind: ForecastKind,
        issued_at: datetime,
        window: TimeRange | None = None,
    ) -> QuantileSeries | None:
        """One issue of a forecast (optionally clipped to ``window``); ``None`` if absent."""
        lo, hi = _bounds(window)
        rows = await self._db.fetchall(
            "SELECT slot_start, step_s, unit, p10, p50, p90 FROM forecast "
            "WHERE source_id = ? AND kind = ? AND issued_at = ? AND slot_start >= ? "
            "AND slot_start < ? ORDER BY slot_start",
            (source_id, kind, _epoch(issued_at), lo, hi),
        )
        return _quantile_series(rows)

    async def latest_forecast_before(
        self,
        source_id: str,
        kind: ForecastKind,
        before: datetime,
        window: TimeRange | None = None,
    ) -> QuantileSeries | None:
        """Per slot the newest value issued at or before ``before`` (for the accuracy).

        To judge a forecast at lead time L, ask for ``before = slot_start - L``: every slot
        then carries what was known L earlier, whichever issue that was. The result spans
        the first to the last slot found; slots no issue covered are ``None``.
        """
        lo, hi = _bounds(window)
        limit = _epoch(before)
        rows = await self._db.fetchall(
            "SELECT f.slot_start, f.step_s, f.unit, f.p10, f.p50, f.p90 FROM forecast f "
            "JOIN (SELECT slot_start, MAX(issued_at) AS issued FROM forecast "
            "WHERE source_id = ? AND kind = ? AND issued_at <= ? AND slot_start >= ? "
            "AND slot_start < ? GROUP BY slot_start) m "
            "ON f.slot_start = m.slot_start AND f.issued_at = m.issued "
            "WHERE f.source_id = ? AND f.kind = ? ORDER BY f.slot_start",
            (source_id, kind, limit, lo, hi, source_id, kind),
        )
        return _quantile_series(rows)

    async def forecast_issues(
        self, source_id: str, kind: ForecastKind, since: datetime | None = None
    ) -> list[datetime]:
        """Issue times stored for a source, oldest first."""
        rows = await self._db.fetchall(
            "SELECT DISTINCT issued_at FROM forecast WHERE source_id = ? AND kind = ? "
            "AND issued_at >= ? ORDER BY issued_at",
            (source_id, kind, 0 if since is None else _epoch(since)),
        )
        return [_from_epoch(_int(r[0])) for r in rows]

    # --- prices -------------------------------------------------------------------

    async def store_prices(self, prices: PriceSeries) -> int:
        """Store prices; rows of the same source that overlap their time span are replaced."""
        step_s = int(prices.step.total_seconds())
        start = _epoch(prices.start)
        end = start + step_s * len(prices)
        export = prices.export_price
        rows: list[Sequence[SqlParam]] = [
            (
                prices.source_id,
                _epoch(prices.import_price.slot_start(i)),
                step_s,
                prices.price_includes,
                prices.import_price.values[i],
                None if export is None else export.values[i],
            )
            for i in range(len(prices))
        ]
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM price WHERE source_id = ? AND slot_start < ? "
                "AND slot_start + step_s > ?",
                (prices.source_id, end, start),
            )
            await tx.executemany(
                "INSERT INTO price (source_id, slot_start, step_s, includes, import_price, "
                "export_price) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    async def read_prices(self, source_id: str, window: TimeRange) -> list[PriceSeries]:
        """Stored prices overlapping ``window`` as regular segments (a change of step,
        ``price_includes`` or a gap that is not a whole number of slots starts a new one).

        A segment whose export prices are all unknown has ``export_price = None``.
        """
        rows = await self._db.fetchall(
            "SELECT slot_start, step_s, includes, import_price, export_price FROM price "
            "WHERE source_id = ? AND slot_start < ? AND slot_start + step_s > ? "
            "ORDER BY slot_start",
            (source_id, _ceil_epoch(window.end), _epoch(window.start)),
        )
        segments: list[list[Row]] = []
        for row in rows:
            if segments and _continues(segments[-1], row):
                segments[-1].append(row)
            else:
                segments.append([row])
        return [_price_segment(source_id, segment) for segment in segments]

    # --- maintenance --------------------------------------------------------------

    async def downsample(self, now: datetime) -> DownsampleReport:
        """Aggregate every complete minute and quarter hour (idempotent, see module doc)."""
        now_s = _epoch(now)
        async with self._db.transaction() as tx:
            minutes = await tx.execute(
                "INSERT INTO measurement_1m (key, ts, mean, min, max, n) "
                "SELECT key, (ts / 60) * 60 AS b, AVG(value), MIN(value), MAX(value), COUNT(*) "
                "FROM measurement WHERE ts < ? GROUP BY key, b "
                "ON CONFLICT(key, ts) DO UPDATE SET mean = excluded.mean, min = excluded.min, "
                "max = excluded.max, n = excluded.n",
                (_floor(now_s, MINUTE_S),),
            )
            quarters = await tx.execute(
                "INSERT INTO measurement_15m (key, ts, mean, min, max, n, derived) "
                "SELECT key, (ts / 900) * 900 AS b, SUM(mean * n) / SUM(n), MIN(min), MAX(max), "
                "SUM(n), 0 FROM measurement_1m WHERE ts < ? GROUP BY key, b "
                "ON CONFLICT(key, ts) DO UPDATE SET mean = excluded.mean, min = excluded.min, "
                "max = excluded.max, n = excluded.n, derived = 0",
                (_floor(now_s, QUARTER_S),),
            )
        return DownsampleReport(minute_rows=minutes, quarter_rows=quarters)

    async def apply_retention(self, now: datetime) -> RetentionReport:
        """Delete what is older than the retention of its table (cut-offs bucket-aligned)."""
        now_s = _epoch(now)
        raw_cut = _floor(now_s - int(RAW_KEEP.total_seconds()), MINUTE_S)
        minute_cut = _floor(now_s - int(MINUTE_KEEP.total_seconds()), QUARTER_S)
        forecast_cut = now_s - int(FORECAST_KEEP.total_seconds())
        price_cut = now_s - int(PRICE_KEEP.total_seconds())
        async with self._db.transaction() as tx:
            raw = await tx.execute("DELETE FROM measurement WHERE ts < ?", (raw_cut,))
            minute = await tx.execute("DELETE FROM measurement_1m WHERE ts < ?", (minute_cut,))
            forecast = await tx.execute(
                "DELETE FROM forecast WHERE slot_start < ?", (forecast_cut,)
            )
            price = await tx.execute("DELETE FROM price WHERE slot_start < ?", (price_cut,))
        return RetentionReport(raw=raw, minute=minute, forecast=forecast, price=price)

    async def maintain(self, now: datetime) -> MaintenanceReport:
        """The nightly job: downsample first (nothing is deleted before it is aggregated),
        then retention, then ``VACUUM`` if enough of the file is free."""
        downsample = await self.downsample(now)
        retention = await self.apply_retention(now)
        vacuumed = await self._vacuum_if_worth_it()
        size = await self.size_report()
        report = MaintenanceReport(
            downsample=downsample,
            retention=retention,
            vacuumed=vacuumed,
            size_bytes=size.total_bytes,
        )
        log.info(
            "timeseries.maintained",
            minute_rows=downsample.minute_rows,
            quarter_rows=downsample.quarter_rows,
            deleted=retention.total,
            vacuumed=vacuumed,
            size_bytes=report.size_bytes,
        )
        return report

    async def _free_and_total(self) -> tuple[int, int]:
        page_size = await self._pragma("page_size")
        return (
            await self._pragma("freelist_count") * page_size,
            await self._pragma("page_count") * page_size,
        )

    async def _pragma(self, name: str) -> int:
        row = await self._db.fetchone(f"PRAGMA {name}")
        return _int(row[0]) if row else 0

    async def _vacuum_if_worth_it(self) -> bool:
        free, total = await self._free_and_total()
        if free < VACUUM_MIN_FREE_BYTES or total == 0 or free / total < VACUUM_MIN_FREE_FRACTION:
            return False
        await self._db.execute("VACUUM")
        await self._db.fetchone("PRAGMA wal_checkpoint(TRUNCATE)")
        return True

    async def size_report(self) -> TimeseriesSize:
        """File, WAL and free bytes plus the row count per table."""
        path = Path(self._db.path)
        free, _total = await self._free_and_total()
        rows: dict[str, int] = {}
        for table in ("measurement", "measurement_1m", "measurement_15m", "forecast", "price"):
            row = await self._db.fetchone(f"SELECT COUNT(*) FROM {table}")
            rows[table] = _int(row[0]) if row else 0
        return TimeseriesSize(
            file_bytes=_size(path),
            wal_bytes=_size(path.with_name(path.name + "-wal")),
            free_bytes=free,
            rows=rows,
        )


# --- row conversion -----------------------------------------------------------------


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _ceil_epoch(moment: datetime) -> int:
    seconds = moment.timestamp()
    _epoch(moment)  # validates awareness
    return math.ceil(seconds)


def _bounds(window: TimeRange | None) -> tuple[int, int]:
    if window is None:
        return 0, 2**62
    return _epoch(window.start), _ceil_epoch(window.end)


def _quantile_series(rows: Sequence[Row]) -> QuantileSeries | None:
    """Rebuild a series from slot rows; the most frequent step wins, gaps become ``None``."""
    if not rows:
        return None
    steps = Counter(_int(r[1]) for r in rows)
    step_s = steps.most_common(1)[0][0]
    if len(steps) > 1:
        log.warning("timeseries.mixed_forecast_steps", steps=sorted(steps), used=step_s)
    kept = [r for r in rows if _int(r[1]) == step_s]
    units = Counter(str(r[2]) for r in kept)
    unit = SeriesUnit(units.most_common(1)[0][0])
    kept = [r for r in kept if SeriesUnit(str(r[2])) is unit]
    first = _int(kept[0][0])
    count = (_int(kept[-1][0]) - first) // step_s + 1
    grids: tuple[list[float | None], list[float | None], list[float | None]] = (
        [None] * count,
        [None] * count,
        [None] * count,
    )
    for row in kept:
        offset = _int(row[0]) - first
        if offset % step_s:  # off the grid of the chosen step: ignore the stray row
            continue
        index = offset // step_s
        for column in range(3):
            grids[column][index] = _opt_num(row[3 + column])
    start = _from_epoch(first)
    step = timedelta(seconds=step_s)
    p10, p50, p90 = (TimeSeries(start=start, step=step, values=tuple(g), unit=unit) for g in grids)
    return QuantileSeries(p10=p10, p50=p50, p90=p90)


def _continues(segment: Sequence[Row], row: Row) -> bool:
    last = segment[-1]
    if _int(row[1]) != _int(last[1]) or str(row[2]) != str(last[2]):
        return False
    gap = _int(row[0]) - (_int(last[0]) + _int(last[1]))
    return gap >= 0 and gap % _int(last[1]) == 0


def _price_segment(source_id: str, segment: Sequence[Row]) -> PriceSeries:
    step_s = _int(segment[0][1])
    first = _int(segment[0][0])
    count = (_int(segment[-1][0]) - first) // step_s + 1
    imports: list[float | None] = [None] * count
    exports: list[float | None] = [None] * count
    for row in segment:
        index = (_int(row[0]) - first) // step_s
        imports[index] = _opt_num(row[3])
        exports[index] = _opt_num(row[4])
    start = _from_epoch(first)
    step = timedelta(seconds=step_s)
    includes: PriceIncludes = "all_in" if str(segment[0][2]) == "all_in" else "spot_only"
    export = (
        None
        if all(v is None for v in exports)
        else TimeSeries(start=start, step=step, values=tuple(exports), unit=SeriesUnit.PRICE)
    )
    return PriceSeries(
        source_id=source_id,
        price_includes=includes,
        import_price=TimeSeries(
            start=start, step=step, values=tuple(imports), unit=SeriesUnit.PRICE
        ),
        export_price=export,
    )


__all__ = [
    "FORECAST_KEEP",
    "MINUTE_KEEP",
    "PRICE_KEEP",
    "RAW_KEEP",
    "TS_FILENAME",
    "VACUUM_MIN_FREE_BYTES",
    "VACUUM_MIN_FREE_FRACTION",
    "DownsampleReport",
    "ForecastKind",
    "MaintenanceReport",
    "MeasurementRow",
    "Resolution",
    "RetentionReport",
    "TimeseriesSize",
    "TimeseriesStore",
    "open_timeseries",
    "open_timeseries_or_recover",
    "timeseries_migrations",
]
