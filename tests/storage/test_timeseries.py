# SPDX-License-Identifier: Apache-2.0
"""``timeseries.db``: migrations, repository round trips, downsampling and retention."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from suncore.core.measurements import SITE_GRID_W, SITE_SOC_PCT, device_power_key
from suncore.core.series import (
    PriceSeries,
    QuantileSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
    floor_to_step,
)
from suncore.storage import Database, SchemaTooNewError
from suncore.storage.migrations import latest_version
from suncore.storage.timeseries import (
    MINUTE_KEEP,
    RAW_KEEP,
    TimeseriesStore,
    open_timeseries,
    open_timeseries_or_recover,
    timeseries_migrations,
)

T0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
Q = timedelta(minutes=15)


@pytest.fixture
async def tsdb(tmp_path: Path) -> AsyncIterator[Database]:
    db = await open_timeseries(tmp_path / "timeseries.db")
    yield db
    await db.close()


@pytest.fixture
def store(tsdb: Database) -> TimeseriesStore:
    return TimeseriesStore(tsdb)


def _series(values: list[float | None], start: datetime = T0, step: timedelta = Q) -> TimeSeries:
    return TimeSeries(start=start, step=step, values=tuple(values), unit=SeriesUnit.W)


def _quantiles(
    p10: list[float | None], p50: list[float | None], p90: list[float | None], start: datetime = T0
) -> QuantileSeries:
    return QuantileSeries(p10=_series(p10, start), p50=_series(p50, start), p90=_series(p90, start))


# --- migrations -----------------------------------------------------------------------


async def test_fresh_timeseries_db_is_migrated_wal(tmp_path: Path) -> None:
    async with await open_timeseries(tmp_path / "t.db") as db:
        assert await db.user_version() == len(timeseries_migrations()) >= 1
        assert await db.fetchone("PRAGMA journal_mode") == ("wal",)
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
    assert {"measurement", "measurement_1m", "measurement_15m", "forecast", "price"} <= tables


async def test_timeseries_migrations_are_independent_of_suncore_db(tmp_path: Path) -> None:
    async with await open_timeseries(tmp_path / "t.db") as db:
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
    assert "config_doc" not in tables
    assert "plan_snapshot" not in tables
    assert latest_version() >= 4  # suncore.db has its own, longer history


async def test_reopen_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    async with await open_timeseries(path) as db:
        await TimeseriesStore(db).append_measurements([("k", T0, 1.0)])
    async with await open_timeseries(path) as db:
        assert await TimeseriesStore(db).read_raw("k", TimeRange(start=T0, end=T0 + Q)) == [
            (T0, 1.0)
        ]


async def test_newer_timeseries_db_is_refused_and_untouched(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 99")
    con.close()
    with pytest.raises(SchemaTooNewError):
        await open_timeseries(path)
    assert await open_timeseries_or_recover(path) is None
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone() == (99,)
    con.close()
    assert not (tmp_path / "t.db.corrupt").exists()


async def test_corrupt_timeseries_db_is_moved_aside_with_its_wal(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    path.write_bytes(b"this is not a database" * 100)
    db = await open_timeseries_or_recover(path, now=lambda: 1_800_000_000.0)
    assert db is not None
    try:
        assert await db.user_version() >= 1
    finally:
        await db.close()
    moved = tmp_path / "t.db.corrupt-20270115T080000Z"
    assert moved.read_bytes().startswith(b"this is not")


def test_move_aside_takes_wal_and_shm_along_and_never_unlinks_them(tmp_path: Path) -> None:
    from suncore.storage.timeseries import _move_aside  # noqa: PLC0415

    path = tmp_path / "t.db"
    for suffix, data in (("", b"db"), ("-wal", b"wal"), ("-shm", b"shm")):
        (tmp_path / f"t.db{suffix}").write_bytes(data)
    _move_aside(path, 1_800_000_000.0)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "t.db.corrupt-20270115T080000Z",
        "t.db.corrupt-20270115T080000Z-shm",
        "t.db.corrupt-20270115T080000Z-wal",
    ]
    assert (tmp_path / "t.db.corrupt-20270115T080000Z-wal").read_bytes() == b"wal"


async def test_failing_quick_check_counts_as_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import suncore.storage.timeseries as module  # noqa: PLC0415

    path = tmp_path / "t.db"
    await (await open_timeseries(path)).close()  # a valid file first
    calls = {"n": 0}

    async def fake_check(_db: Database) -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # the first (damaged) file fails, the fresh one passes

    monkeypatch.setattr(module, "_quick_check", fake_check)
    db = await open_timeseries_or_recover(path, now=lambda: 1_800_000_000.0)
    assert db is not None
    await db.close()
    assert (tmp_path / "t.db.corrupt-20270115T080000Z").exists()


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("disk I/O error"),
        sqlite3.OperationalError("database or disk is full"),
        sqlite3.OperationalError("unable to open database file"),
        PermissionError("denied"),
    ],
)
async def test_transient_errors_never_move_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    import suncore.storage.timeseries as module  # noqa: PLC0415

    path = tmp_path / "t.db"
    async with await open_timeseries(path) as db:
        await TimeseriesStore(db).append_measurements([("k", T0, 1.0)])

    async def failing(_path: Path | str) -> Database:
        raise error

    monkeypatch.setattr(module, "open_timeseries", failing)
    assert await open_timeseries_or_recover(path) is None
    monkeypatch.undo()
    assert not await asyncio.to_thread(lambda: list(tmp_path.glob("*.corrupt*")))
    async with await open_timeseries(path) as db:  # still there, data intact, next start works
        assert await TimeseriesStore(db).read_raw("k", TimeRange(start=T0, end=T0 + Q)) == [
            (T0, 1.0)
        ]


async def test_corrupt_files_are_rotated_keeping_two(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    for i in range(4):
        path.write_bytes(b"garbage" * 100)
        (tmp_path / "t.db-wal").write_bytes(b"w")
        db = await open_timeseries_or_recover(path, now=lambda i=i: 1_800_000_000.0 + i * 60)
        assert db is not None
        await db.close()
        path.unlink()
        for suffix in ("-wal", "-shm"):
            (tmp_path / f"t.db{suffix}").unlink(missing_ok=True)
    kept = await asyncio.to_thread(lambda: sorted(p.name for p in tmp_path.glob("t.db.corrupt-*")))
    assert kept == ["t.db.corrupt-20270115T080200Z", "t.db.corrupt-20270115T080300Z"]


async def test_unopenable_path_gives_none(tmp_path: Path) -> None:
    assert await open_timeseries_or_recover(tmp_path / "missing" / "dir" / "t.db") is None
    assert not await asyncio.to_thread(lambda: list(tmp_path.glob("**/*.corrupt*")))


# --- measurements ---------------------------------------------------------------------


async def test_append_skips_gaps_and_replaces_duplicates(store: TimeseriesStore) -> None:
    written = await store.append_measurements(
        [
            ("k", T0, 1.0),
            ("k", T0 + timedelta(seconds=5), None),
            ("k", T0 + timedelta(seconds=10), float("nan")),
            ("k", T0 + timedelta(seconds=15), float("inf")),
            ("k", T0 + timedelta(seconds=20), 2.0),
            ("k", T0 + timedelta(seconds=20, milliseconds=400), 3.0),  # same second: replaces
        ]
    )
    assert written == 3
    rows = await store.read_raw("k", TimeRange(start=T0, end=T0 + timedelta(minutes=1)))
    assert rows == [(T0, 1.0), (T0 + timedelta(seconds=20), 3.0)]


async def test_naive_timestamps_are_rejected(store: TimeseriesStore) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        await store.append_measurements([("k", datetime(2026, 1, 1), 1.0)])  # noqa: DTZ001


async def test_downsample_is_sample_weighted_and_energy_preserving(store: TimeseriesStore) -> None:
    # 5 s ticks for 30 minutes; a ramp, so every minute has a different mean
    rows = [("k", T0 + timedelta(seconds=5 * i), float(i % 97)) for i in range(360)]
    await store.append_measurements(rows)
    now = T0 + timedelta(minutes=45)
    report = await store.downsample(now)
    assert report.minute_rows == 30
    assert report.quarter_rows == 2
    raw_mean = sum(v for _, _, v in rows[:180]) / 180
    q = await store.read_series(
        "k", TimeRange(start=T0, end=T0 + timedelta(minutes=30)), "15m", include_recent=False
    )
    assert q.values[0] == pytest.approx(raw_mean)
    # energy of the first quarter hour: mean W x 0.25 h equals the sum of the samples x 5 s
    sample_wh = sum(v for _, _, v in rows[:180]) * 5 / 3600
    assert q.values[0] * 0.25 == pytest.approx(sample_wh)
    row = await store.database.fetchone(
        "SELECT min, max, n FROM measurement_15m WHERE ts = ?", (int(T0.timestamp()),)
    )
    assert row == (0.0, 96.0, 180)


async def test_downsample_is_idempotent(store: TimeseriesStore) -> None:
    await store.append_measurements(
        [("k", T0 + timedelta(seconds=10 * i), float(i)) for i in range(200)]
    )
    now = T0 + timedelta(hours=1)
    await store.downsample(now)
    first = await store.database.fetchall("SELECT * FROM measurement_15m ORDER BY key, ts")
    first_1m = await store.database.fetchall("SELECT * FROM measurement_1m ORDER BY key, ts")
    await store.downsample(now)
    await store.downsample(now + timedelta(hours=1))
    assert await store.database.fetchall("SELECT * FROM measurement_15m ORDER BY key, ts") == first
    assert (
        await store.database.fetchall("SELECT * FROM measurement_1m ORDER BY key, ts") == first_1m
    )


async def test_incomplete_buckets_are_not_aggregated(store: TimeseriesStore) -> None:
    await store.append_measurements(
        [("k", T0 + timedelta(seconds=s), 1.0) for s in range(0, 100, 10)]
    )
    # now = 00:01:40: the quarter hour 00:00 is incomplete, so not even its minutes are written
    report = await store.downsample(T0 + timedelta(seconds=100))
    assert (report.minute_rows, report.quarter_rows) == (0, 0)


async def test_a_job_inside_a_quarter_hour_does_not_skew_the_quarter(
    store: TimeseriesStore,
) -> None:
    """Review example: job at 03:35, 5 min x 100 W before it, 10 min x 1000 W after it."""
    quarter = T0 + timedelta(hours=3, minutes=30)
    window = TimeRange(start=quarter, end=quarter + Q)
    low = [("k", quarter + timedelta(seconds=10 * i), 100.0) for i in range(30)]
    high = [("k", quarter + timedelta(minutes=5, seconds=10 * i), 1000.0) for i in range(60)]
    await store.append_measurements(low)
    await store.downsample(quarter + timedelta(minutes=5))  # the 03:35 job
    await store.append_measurements(high)
    expected = (30 * 100 + 60 * 1000) / 90  # 700
    assert (await store.read_series("k", window)).values == (pytest.approx(expected),)
    await store.downsample(quarter + timedelta(minutes=20))  # a later job completes it
    assert (await store.read_series("k", window, include_recent=False)).values == (
        pytest.approx(expected),
    )
    assert (await store.read_series("k", window)).values == (pytest.approx(expected),)


async def test_primary_keys_are_time_first_and_range_reads_use_them(store: TimeseriesStore) -> None:
    """(ts, key) keeps one flush on consecutive pages (SD wear); reads still seek on ts."""
    db = store.database
    for table in ("measurement", "measurement_1m"):
        columns = await db.fetchall(f"PRAGMA table_info({table})")
        order = sorted((int(str(c[5])), str(c[1])) for c in columns if int(str(c[5])) > 0)
        assert [name for _, name in order] == ["ts", "key"]
    plan = await db.fetchall(
        "EXPLAIN QUERY PLAN SELECT ts, value FROM measurement WHERE key = ? AND ts >= ? AND ts < ?",
        ("k", 0, 10),
    )
    assert any("PRIMARY KEY" in str(r[3]) and "ts>" in str(r[3]) for r in plan)


async def test_measured_beats_derived_when_merging_recent_data(store: TimeseriesStore) -> None:
    window = TimeRange(start=T0, end=T0 + Q)
    await store.store_quarter_hours("k", _series([99.0]), derived=True)
    await store.append_measurements([("k", T0 + timedelta(seconds=10 * i), 5.0) for i in range(30)])
    # not yet aggregated: the live data wins over the rebuilt value
    assert (await store.read_series("k", window)).values == (5.0,)
    assert (await store.read_series("k", window, include_recent=False)).values == (99.0,)
    # a derived row alone is still used, and an aggregated measured row beats live data
    other = TimeRange(start=T0 + Q, end=T0 + 2 * Q)
    await store.store_quarter_hours("k", _series([7.0], start=T0 + Q), derived=True)
    assert (await store.read_series("k", other)).values == (7.0,)


async def test_15m_weights_minutes_by_sample_count(store: TimeseriesStore) -> None:
    # minute 0: 6 samples of 10; minute 1: 1 sample of 70 -> sample mean (60 + 70) / 7
    rows = [("k", T0 + timedelta(seconds=10 * i), 10.0) for i in range(6)]
    rows.append(("k", T0 + timedelta(seconds=60), 70.0))
    await store.append_measurements(rows)
    await store.downsample(T0 + timedelta(minutes=30))
    series = await store.read_series("k", TimeRange(start=T0, end=T0 + Q), include_recent=False)
    assert series.values == (pytest.approx(130 / 7),)


async def test_retention_downsamples_before_it_deletes(store: TimeseriesStore) -> None:
    old = T0
    await store.append_measurements(
        [("k", old + timedelta(seconds=30 * i), 5.0) for i in range(60)]
    )
    later = old + RAW_KEEP + timedelta(days=3)
    report = await store.maintain(later)
    assert report.retention.raw == 60
    assert await store.database.fetchone("SELECT COUNT(*) FROM measurement") == (0,)
    # the aggregates survived the deletion of the raw rows
    series = await store.read_series("k", TimeRange(start=old, end=old + timedelta(minutes=30)))
    assert series.values[:2] == (5.0, 5.0)


async def test_retention_cutoffs_per_table(store: TimeseriesStore) -> None:
    now = T0 + timedelta(days=100)
    db = store.database
    for table in ("measurement_1m", "measurement_15m"):
        await db.execute(
            f"INSERT INTO {table} (key, ts, mean, min, max, n) VALUES ('k', ?, 1, 1, 1, 1)",
            (int((now - MINUTE_KEEP - timedelta(days=1)).timestamp()) // 900 * 900,),
        )
        await db.execute(
            f"INSERT INTO {table} (key, ts, mean, min, max, n) VALUES ('k', ?, 1, 1, 1, 1)",
            (int((now - timedelta(days=1)).timestamp()) // 900 * 900,),
        )
    report = await store.apply_retention(now)
    assert report.minute == 1
    assert await db.fetchone("SELECT COUNT(*) FROM measurement_1m") == (1,)
    assert await db.fetchone("SELECT COUNT(*) FROM measurement_15m") == (2,)  # unlimited


async def test_maintain_on_an_empty_database_and_size_report(store: TimeseriesStore) -> None:
    report = await store.maintain(T0)
    assert report.retention.total == 0
    assert report.vacuumed is False
    size = await store.size_report()
    assert size.file_bytes > 0
    assert size.total_bytes >= size.file_bytes
    assert set(size.rows) == {
        "measurement",
        "measurement_1m",
        "measurement_15m",
        "forecast",
        "price",
    }
    assert all(count == 0 for count in size.rows.values())


async def test_vacuum_runs_only_when_worth_it(
    store: TimeseriesStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import suncore.storage.timeseries as module  # noqa: PLC0415

    monkeypatch.setattr(module, "VACUUM_MIN_FREE_BYTES", 4096)
    await store.append_measurements(
        [(f"key{i % 50}", T0 + timedelta(seconds=i), float(i)) for i in range(20_000)]
    )
    assert (await store.maintain(T0 + timedelta(hours=1))).vacuumed is False
    before = (await store.size_report()).total_bytes
    report = await store.maintain(T0 + RAW_KEEP + timedelta(days=10))
    assert report.retention.raw == 20_000
    assert report.vacuumed is True
    assert (await store.size_report()).total_bytes < before


# --- read_series ----------------------------------------------------------------------


async def test_read_series_gaps_are_none_and_units_follow_the_key(store: TimeseriesStore) -> None:
    await store.store_quarter_hours(SITE_GRID_W, _series([1.0, None, 3.0]))
    series = await store.read_series(SITE_GRID_W, TimeRange(start=T0, end=T0 + 4 * Q))
    assert series.values == (1.0, None, 3.0, None)
    assert series.unit is SeriesUnit.W
    assert series.step == Q
    soc = await store.read_series(SITE_SOC_PCT, TimeRange(start=T0, end=T0 + Q))
    assert soc.unit is SeriesUnit.PERCENT


async def test_read_series_includes_the_not_yet_aggregated_recent_data(
    store: TimeseriesStore,
) -> None:
    await store.append_measurements([("k", T0 + timedelta(seconds=30 * i), 2.0) for i in range(30)])
    window = TimeRange(start=T0, end=T0 + Q)
    assert (await store.read_series("k", window, include_recent=False)).values == (None,)
    assert (await store.read_series("k", window)).values == (2.0,)
    await store.downsample(T0 + timedelta(hours=1))
    await store.apply_retention(T0 + timedelta(hours=1))
    assert (await store.read_series("k", window)).values == (2.0,)


async def test_read_series_one_minute_resolution(store: TimeseriesStore) -> None:
    await store.append_measurements(
        [("k", T0 + timedelta(seconds=10 * i), float(i)) for i in range(12)]
    )
    await store.downsample(T0 + timedelta(hours=1))
    series = await store.read_series("k", TimeRange(start=T0, end=T0 + timedelta(minutes=2)), "1m")
    assert series.step == timedelta(minutes=1)
    assert series.values == (2.5, 8.5)


async def test_read_series_refuses_a_huge_window(store: TimeseriesStore) -> None:
    with pytest.raises(ValueError, match="exceed"):
        await store.read_series("k", TimeRange(start=T0, end=T0 + timedelta(days=30)), "1m")


async def test_derived_rows_never_overwrite_measured_ones(store: TimeseriesStore) -> None:
    await store.store_quarter_hours("k", _series([10.0, None]), derived=False)
    await store.store_quarter_hours("k", _series([99.0, 20.0, 30.0]), derived=True)
    series = await store.read_series("k", TimeRange(start=T0, end=T0 + 3 * Q))
    assert series.values == (10.0, 20.0, 30.0)
    await store.store_quarter_hours(
        "k", _series([1.0, 2.0, 3.0]), derived=True
    )  # derived on derived
    series = await store.read_series("k", TimeRange(start=T0, end=T0 + 3 * Q))
    assert series.values == (10.0, 2.0, 3.0)
    assert await store.database.fetchall("SELECT derived FROM measurement_15m ORDER BY ts") == [
        (0,),
        (1,),
        (1,),
    ]


async def test_real_aggregates_replace_derived_rows(store: TimeseriesStore) -> None:
    await store.store_quarter_hours("k", _series([99.0]), derived=True)
    assert await store.has_quarter_history()
    await store.append_measurements([("k", T0 + timedelta(seconds=10 * i), 4.0) for i in range(90)])
    await store.downsample(T0 + timedelta(hours=1))
    assert (await store.read_series("k", TimeRange(start=T0, end=T0 + Q))).values == (4.0,)
    assert await store.database.fetchone("SELECT derived, n FROM measurement_15m") == (0, 90)


async def test_store_quarter_hours_needs_a_15_minute_series(store: TimeseriesStore) -> None:
    with pytest.raises(ValueError, match="15-minute"):
        await store.store_quarter_hours("k", _series([1.0], step=timedelta(hours=1)))


async def test_has_quarter_history(store: TimeseriesStore) -> None:
    assert not await store.has_quarter_history()
    await store.store_quarter_hours("k", _series([1.0]))
    assert await store.has_quarter_history()


async def test_device_keys_are_plain_measurements(store: TimeseriesStore) -> None:
    key = device_power_key("pool")
    await store.append_measurements([(key, T0, 1500.0)])
    assert (await store.read_raw(key, TimeRange(start=T0, end=T0 + Q))) == [(T0, 1500.0)]


# --- forecasts ------------------------------------------------------------------------


async def test_forecast_round_trip_with_gaps(store: TimeseriesStore) -> None:
    issued = T0 - timedelta(hours=2)
    forecast = _quantiles(
        [None, 1.0, 2.0, None], [None, 2.0, 3.0, 4.0], [None, 3.0, 4.0, None], start=T0
    )
    assert await store.store_forecast("solcast", "pv", issued, forecast) == 4
    assert await store.read_forecast("solcast", "pv", issued) == forecast  # leading None kept
    assert await store.read_forecast("solcast", "load", issued) is None
    assert await store.read_forecast("other", "pv", issued) is None


async def test_forecast_restore_replaces_the_same_issue(store: TimeseriesStore) -> None:
    issued = T0
    await store.store_forecast("s", "pv", issued, QuantileSeries.deterministic(_series([1.0, 2.0])))
    await store.store_forecast("s", "pv", issued, QuantileSeries.deterministic(_series([5.0])))
    got = await store.read_forecast("s", "pv", issued)
    assert got is not None
    assert got.p50.values == (5.0,)


async def test_forecast_window_clip_and_plain_time_series(store: TimeseriesStore) -> None:
    await store.store_forecast("s", "load", T0, _series([1.0, 2.0, 3.0, 4.0]))
    got = await store.read_forecast("s", "load", T0, TimeRange(start=T0 + Q, end=T0 + 3 * Q))
    assert got is not None
    assert got.p50.values == (2.0, 3.0)
    assert got.p10.values == (None, None)  # a plain series fills p50 only
    assert got.start == T0 + Q


async def test_latest_forecast_before_semantics(store: TimeseriesStore) -> None:
    # issue A (00:00) covers 00:00..00:45 with 1; issue B (00:30) covers 00:30..01:15 with 2
    await store.store_forecast("s", "pv", T0, QuantileSeries.deterministic(_series([1.0] * 4)))
    await store.store_forecast(
        "s", "pv", T0 + 2 * Q, QuantileSeries.deterministic(_series([2.0] * 4, start=T0 + 2 * Q))
    )
    before_b = await store.latest_forecast_before("s", "pv", T0 + Q)
    assert before_b is not None
    assert before_b.p50.values == (1.0, 1.0, 1.0, 1.0)
    after_b = await store.latest_forecast_before("s", "pv", T0 + 2 * Q)
    assert after_b is not None
    assert after_b.p50.values == (1.0, 1.0, 2.0, 2.0, 2.0, 2.0)
    assert await store.latest_forecast_before("s", "pv", T0 - Q) is None
    assert await store.forecast_issues("s", "pv") == [T0, T0 + 2 * Q]
    assert await store.forecast_issues("s", "pv", since=T0 + Q) == [T0 + 2 * Q]


async def test_forecast_retention_by_slot(store: TimeseriesStore) -> None:
    await store.store_forecast("s", "pv", T0, QuantileSeries.deterministic(_series([1.0])))
    report = await store.apply_retention(T0 + timedelta(days=60))
    assert report.forecast == 1


# --- prices ---------------------------------------------------------------------------


def _prices(
    imp: list[float | None],
    exp: list[float | None] | None,
    start: datetime = T0,
    step: timedelta = Q,
) -> PriceSeries:
    def make(values: list[float | None]) -> TimeSeries:
        return TimeSeries(start=start, step=step, values=tuple(values), unit=SeriesUnit.PRICE)

    return PriceSeries(
        source_id="tibber",
        price_includes="all_in",
        import_price=make(imp),
        export_price=None if exp is None else make(exp),
    )


async def test_prices_round_trip_with_negative_values_and_gaps(store: TimeseriesStore) -> None:
    prices = _prices([0.30, -0.05, None, 0.28], [0.08, 0.0, None, 0.08])
    await store.store_prices(prices)
    got = await store.read_prices("tibber", TimeRange(start=T0, end=T0 + 4 * Q))
    assert got == [prices]


async def test_prices_without_export_series(store: TimeseriesStore) -> None:
    prices = _prices([0.3, 0.4], None)
    await store.store_prices(prices)
    got = await store.read_prices("tibber", TimeRange(start=T0, end=T0 + 2 * Q))
    assert got == [prices]
    assert got[0].export_price is None


async def test_prices_mixed_resolution_segments_and_replacement(store: TimeseriesStore) -> None:
    quarters = _prices([0.1, 0.2, 0.3, 0.4], None)
    hourly = _prices([0.5, 0.6], None, start=T0 + timedelta(hours=1), step=timedelta(hours=1))
    await store.store_prices(quarters)
    await store.store_prices(hourly)
    got = await store.read_prices("tibber", TimeRange(start=T0, end=T0 + timedelta(hours=3)))
    assert [s.step for s in got] == [Q, timedelta(hours=1)]
    assert got[1].import_price.values == (0.5, 0.6)
    # an hourly update of the first hour replaces the four quarter-hour rows it overlaps
    await store.store_prices(_prices([0.9], None, step=timedelta(hours=1)))
    got = await store.read_prices("tibber", TimeRange(start=T0, end=T0 + timedelta(hours=3)))
    assert [s.step for s in got] == [timedelta(hours=1)]
    assert got[0].import_price.values == (0.9, 0.5, 0.6)


async def test_prices_are_per_source_and_retained_long(store: TimeseriesStore) -> None:
    await store.store_prices(_prices([0.3], None))
    assert await store.read_prices("nordpool", TimeRange(start=T0, end=T0 + Q)) == []
    assert (await store.apply_retention(T0 + timedelta(days=300))).price == 0
    assert (await store.apply_retention(T0 + timedelta(days=500))).price == 1


async def test_price_gap_that_is_not_a_whole_slot_starts_a_new_segment(
    store: TimeseriesStore,
) -> None:
    await store.store_prices(_prices([0.1], None))
    await store.store_prices(_prices([0.2], None, start=T0 + timedelta(minutes=20)))
    got = await store.read_prices("tibber", TimeRange(start=T0, end=T0 + timedelta(hours=1)))
    assert len(got) == 2


# --- DST ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "hours"),
    [("2026-03-29", 23), ("2026-10-25", 25), ("2026-06-01", 24)],
)
async def test_series_round_trip_on_dst_days(store: TimeseriesStore, day: str, hours: int) -> None:
    berlin = ZoneInfo("Europe/Berlin")
    start = datetime.fromisoformat(day).replace(tzinfo=berlin).astimezone(UTC)
    end = (datetime.fromisoformat(day) + timedelta(days=1)).replace(tzinfo=berlin).astimezone(UTC)
    assert (end - start) == timedelta(hours=hours)
    count = hours * 4
    values: list[float | None] = [float(i) if i % 7 else None for i in range(count)]
    series = _series(values, start=start)
    await store.store_quarter_hours("k", series)
    got = await store.read_series("k", TimeRange(start=start, end=end))
    assert got.values == series.values
    assert got.start == start
    assert len(got) == count
    # forecast issued the day before round-trips as well
    forecast = QuantileSeries.deterministic(series)
    await store.store_forecast("s", "pv", start - timedelta(hours=6), forecast)
    assert await store.read_forecast("s", "pv", start - timedelta(hours=6)) == forecast


async def test_timestamps_are_stored_in_utc_whatever_the_input_zone(store: TimeseriesStore) -> None:
    berlin = ZoneInfo("Europe/Berlin")
    local = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=1)  # the second 02:30
    await store.append_measurements([("k", local, 5.0)])
    utc = local.astimezone(UTC)  # local arithmetic would reset `fold`: windows are UTC
    rows = await store.read_raw("k", TimeRange(start=utc, end=utc + Q))
    assert rows == [(utc, 5.0)]
    assert utc == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert floor_to_step(utc) == utc


@pytest.mark.parametrize("app", ["suncore", "suncore_beta"])
def test_backup_excludes_the_database_its_wal_and_the_corrupt_copies(app: str) -> None:
    config = (Path(__file__).resolve().parents[2] / "addon" / app / "config.yaml").read_text(
        "utf-8"
    )
    for entry in (
        "timeseries.db",
        "timeseries.db-wal",
        "timeseries.db-shm",
        "timeseries.db.corrupt*",
    ):
        assert f"  - {entry}\n" in config
