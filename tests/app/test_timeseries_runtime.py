# SPDX-License-Identifier: Apache-2.0
"""Wiring of ``timeseries.db`` into the runtime: recording, flushing, nightly job, rebuild."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import structlog.testing

from suncore.app.control import ControlStack
from suncore.app.history import device_statistic_specs, site_statistic_specs
from suncore.app.main import build_runtime
from suncore.app.recorder import MeasurementRecorder
from suncore.app.runtime import Runtime
from suncore.app.settings import Settings
from suncore.core.measurements import (
    SITE_HOUSE_W,
    SITE_KEYS,
    SITE_SOC_PCT,
    device_power_key,
)
from suncore.core.series import TimeRange
from suncore.ha.connection import CommandError, HAError
from suncore.storage.timeseries import META_HISTORY_REBUILT, TS_FILENAME, TimeseriesStore
from tests.app.conftest import RecordingConnection, demo_settings

T0 = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)


def prod_settings(tmp_path: Path) -> Settings:
    return Settings(demo_mode=False, data_dir=tmp_path / "data", static_dir=tmp_path / "no-dist")


class GatedSleep:
    """Sleep that records the requested delays and returns only when released."""

    def __init__(self) -> None:
        self.requested: list[float] = []
        self._events: list[asyncio.Event] = []

    async def __call__(self, delay: float) -> None:
        self.requested.append(delay)
        event = asyncio.Event()
        self._events.append(event)
        await event.wait()

    async def release(self, delay: float) -> None:
        """Let the oldest pending sleep of ``delay`` return."""
        for event, requested in zip(self._events, self.requested, strict=False):
            if requested == delay and not event.is_set():
                event.set()
                break
        for _ in range(10):
            await asyncio.sleep(0)


async def until(predicate: Callable[[], bool], timeout_s: float = 5.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate
            await asyncio.sleep(0.01)


async def idle(runtime: Runtime) -> ControlStack:
    """Stop the background tick of the current stack (after a possible rebuild settled)."""
    await until(lambda: runtime.stack is not None and runtime._tz_learnt)
    stack = runtime.stack
    assert stack is not None
    await stack._stop_tasks()
    return stack


def store_of(runtime: Runtime) -> TimeseriesStore:
    assert runtime.timeseries is not None
    return runtime.timeseries


@pytest.fixture
async def gated(tmp_path: Path) -> AsyncIterator[tuple[Runtime, GatedSleep, RecordingConnection]]:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    sleep = GatedSleep()
    runtime.sleep = sleep
    runtime.clock = lambda: T0 + timedelta(hours=10)
    await runtime.start()
    try:
        yield runtime, sleep, conn
    finally:
        await runtime.stop()


# --- opening and closing --------------------------------------------------------------------


async def test_production_path_creates_timeseries_next_to_suncore_db(tmp_path: Path) -> None:
    settings = prod_settings(tmp_path)
    runtime = await build_runtime(settings, env={}, connection=RecordingConnection(6000.0))
    try:
        assert (settings.data_dir / "suncore.db").is_file()
        assert (settings.data_dir / TS_FILENAME).is_file()
        assert runtime.timeseries is not None
        assert runtime.timeseries_db is not None
        assert await runtime.timeseries_db.user_version() >= 1
        assert runtime.plans is not None
        assert runtime.learned is not None
        assert runtime.recorder.enabled
    finally:
        await runtime.stop()
    assert not await runtime.timeseries_db.check()  # closed with the runtime
    assert not await runtime.db.check()


async def test_demo_keeps_timeseries_in_its_throw_away_dir(tmp_path: Path) -> None:
    settings = demo_settings(tmp_path)
    runtime = await build_runtime(settings, env={}, connection=RecordingConnection(6000.0))
    try:
        assert runtime.timeseries_db is not None
        assert not Path(runtime.timeseries_db.path).is_relative_to(settings.data_dir)
        assert Path(runtime.timeseries_db.path).name == TS_FILENAME
    finally:
        await runtime.stop()
    assert not settings.data_dir.exists()


async def test_too_new_timeseries_db_disables_recording_but_not_the_app(tmp_path: Path) -> None:
    import sqlite3  # noqa: PLC0415

    settings = prod_settings(tmp_path)
    settings.data_dir.mkdir(parents=True)
    con = sqlite3.connect(settings.data_dir / TS_FILENAME)
    con.execute("PRAGMA user_version = 99")
    con.close()
    runtime = await build_runtime(settings, env={}, connection=RecordingConnection(6000.0))
    try:
        assert runtime.timeseries is None
        assert not runtime.recorder.enabled
        await runtime.start()  # control and API come up without it
        stack = await idle(runtime)
        assert await stack.tick() is not None
        assert runtime.recorder.pending == 0
        assert await runtime.maintain_timeseries() is False
    finally:
        await runtime.stop()
    con = sqlite3.connect(settings.data_dir / TS_FILENAME)
    assert con.execute("PRAGMA user_version").fetchone() == (99,)
    con.close()


# --- recording ---------------------------------------------------------------------------------


async def test_ticks_are_buffered_and_not_written_per_tick(
    gated: tuple[Runtime, GatedSleep, RecordingConnection], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _sleep, _conn = gated
    store = store_of(runtime)
    writes: list[int] = []
    original = store.append_measurements

    async def spy(rows: Any) -> int:
        batch = list(rows)
        writes.append(len(batch))
        return await original(batch)

    monkeypatch.setattr(store, "append_measurements", spy)
    stack = await idle(runtime)
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)
    for _ in range(5):
        await stack.tick()
    assert writes == []  # nothing written per tick
    pending = runtime.recorder.pending
    assert pending >= 5 * len(SITE_KEYS)  # five site values per tick (+ the devices)
    assert await runtime.recorder.flush() == pending
    assert writes == [pending]  # one batch
    assert runtime.recorder.pending == 0
    assert await runtime.recorder.flush() == 0
    assert writes == [pending]


async def test_recorded_keys_cover_site_and_devices(
    gated: tuple[Runtime, GatedSleep, RecordingConnection],
) -> None:
    runtime, _sleep, _conn = gated
    stack = await idle(runtime)
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)
    await stack.tick()
    await runtime.recorder.flush()
    rows = await store_of(runtime).database.fetchall("SELECT DISTINCT key FROM measurement")
    keys = {str(r[0]) for r in rows}
    assert set(SITE_KEYS) <= keys
    assert {device_power_key(d) for d in stack.models} <= keys
    soc = await store_of(runtime).database.fetchone(
        "SELECT value FROM measurement WHERE key = ?", (SITE_SOC_PCT,)
    )
    assert soc is not None
    assert 0.0 <= float(str(soc[0])) <= 100.0


async def test_flush_cadence_is_60_seconds_and_a_stop_flushes_the_rest(
    gated: tuple[Runtime, GatedSleep, RecordingConnection],
) -> None:
    runtime, sleep, _conn = gated
    assert runtime.flush_interval_s == 60.0
    await until(lambda: 60.0 in sleep.requested)  # the flush loop is waiting
    stack = await idle(runtime)
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)
    await stack.tick()
    assert runtime.recorder.pending > 0
    await sleep.release(60.0)  # one flush interval elapses
    await until(lambda: runtime.recorder.pending == 0)
    count = await store_of(runtime).database.fetchone("SELECT COUNT(*) FROM measurement")
    assert count is not None
    assert int(str(count[0])) > 0
    await stack.tick()
    assert runtime.recorder.pending > 0
    pending = runtime.recorder.pending
    written: list[int] = []
    store = store_of(runtime)
    original = store.append_measurements

    async def spy(rows: Any) -> int:
        batch = list(rows)
        written.append(len(batch))
        return await original(batch)

    store.append_measurements = spy  # type: ignore[method-assign]
    await runtime.stop()  # the shutdown writes what is left, before the database is closed
    assert written == [pending]
    assert runtime.recorder.pending == 0


async def test_a_database_error_never_reaches_the_controller(
    gated: tuple[Runtime, GatedSleep, RecordingConnection], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _sleep, _conn = gated
    store = store_of(runtime)

    async def broken(_rows: Any) -> int:
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(store, "append_measurements", broken)
    stack = await idle(runtime)
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)
    assert await stack.tick() is not None
    pending = runtime.recorder.pending
    with structlog.testing.capture_logs() as logs:
        assert await runtime.recorder.flush() == 0  # logged, not raised
    assert any(e["event"] == "timeseries.flush_failed" for e in logs)
    assert runtime.recorder.pending == pending  # kept for the next flush
    assert await stack.tick() is not None  # control is unaffected
    monkeypatch.undo()
    assert await runtime.recorder.flush() > 0  # the database is back: the kept rows are written


async def test_a_failing_sink_does_not_break_the_tick(
    gated: tuple[Runtime, GatedSleep, RecordingConnection],
) -> None:
    runtime, _sleep, _conn = gated
    stack = await idle(runtime)
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)

    def boom(_ts: datetime, _values: Any) -> None:
        msg = "sink exploded"
        raise RuntimeError(msg)

    stack._on_measurements = boom
    with structlog.testing.capture_logs() as logs:
        assert await stack.tick() is not None
    assert any(e["event"] == "control.measurement_sink_failed" for e in logs)


async def test_only_ok_quality_values_are_recorded(
    gated: tuple[Runtime, GatedSleep, RecordingConnection],
) -> None:
    runtime, _sleep, conn = gated
    stack = await idle(runtime)
    seen: list[dict[str, float | None]] = []
    stack._on_measurements = lambda _ts, values: seen.append(dict(values))
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)
    await stack.tick()
    assert seen[0][SITE_HOUSE_W] is not None
    # a dead house sensor is a gap (None), never a zero
    from suncore.ha.connection import EntityState, EntityUpdate  # noqa: PLC0415

    stack.cache.ingest(
        EntityUpdate(
            changed={
                "sensor.sim_house_power": EntityState(
                    "sensor.sim_house_power",
                    "unavailable",
                    {"unit_of_measurement": "W"},
                    last_changed=1.0,
                    last_updated=1.0,
                )
            }
        )
    )
    await stack.tick()
    assert seen[1][SITE_HOUSE_W] is None
    assert conn is not None


# --- the recorder itself -----------------------------------------------------------------------


async def test_recorder_skips_gaps_and_is_a_noop_without_store() -> None:
    off = MeasurementRecorder(None)
    off.record(T0, {"k": 1.0})
    assert off.pending == 0
    assert await off.flush() == 0


async def test_recorder_buffer_is_bounded_and_drops_the_oldest(tmp_path: Path) -> None:
    from suncore.storage import open_timeseries  # noqa: PLC0415

    async with await open_timeseries(tmp_path / "t.db") as db:
        recorder = MeasurementRecorder(TimeseriesStore(db), max_buffered=5)
        for i in range(8):
            recorder.record(T0 + timedelta(seconds=i), {"k": float(i), "gap": None})
        assert recorder.pending == 5
        assert recorder.dropped == 3
        assert await recorder.flush() == 5
        store = TimeseriesStore(db)
        rows = await store.read_raw("k", TimeRange(start=T0, end=T0 + timedelta(minutes=1)))
        assert [v for _, v in rows] == [3.0, 4.0, 5.0, 6.0, 7.0]  # the oldest three are gone


async def test_recorder_retries_a_failed_batch_within_the_bound(tmp_path: Path) -> None:
    from suncore.storage import open_timeseries  # noqa: PLC0415

    async with await open_timeseries(tmp_path / "t.db") as db:
        store = TimeseriesStore(db)
        recorder = MeasurementRecorder(store, max_buffered=4)
        recorder.record(T0, {"k": 1.0})
        recorder.record(T0 + timedelta(seconds=1), {"k": 2.0})
        original = store.append_measurements
        fail = True

        async def flaky(rows: Any) -> int:
            if fail:
                msg = "locked"
                raise OSError(msg)
            return await original(rows)

        store.append_measurements = flaky  # type: ignore[method-assign]
        assert await recorder.flush() == 0
        recorder.record(T0 + timedelta(seconds=2), {"k": 3.0})
        recorder.record(T0 + timedelta(seconds=3), {"k": 4.0})
        recorder.record(T0 + timedelta(seconds=4), {"k": 5.0})
        assert recorder.pending == 4
        assert recorder.dropped == 1
        fail = False
        assert await recorder.flush() == 4
        rows = await original.__self__.read_raw(  # type: ignore[attr-defined]
            "k", TimeRange(start=T0, end=T0 + timedelta(minutes=1))
        )
        assert [v for _, v in rows] == [2.0, 3.0, 4.0, 5.0]


# --- the nightly job ---------------------------------------------------------------------------


async def test_nightly_job_runs_at_start_then_wakes_at_most_hourly(
    gated: tuple[Runtime, GatedSleep, RecordingConnection],
) -> None:
    runtime, sleep, _conn = gated
    # the clock is 10:00 UTC = 12:00 CEST (Europe/Berlin, the demo region): 15.5 h to 03:30
    await until(lambda: 3600.0 in sleep.requested)
    assert runtime.seconds_until_maintenance() == 15.5 * 3600


async def test_nightly_job_runs_once_per_night_and_follows_the_time_zone(
    tmp_path: Path,
) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    sleep = GatedSleep()
    runtime.sleep = sleep
    now = [datetime(2026, 6, 21, 10, 0, tzinfo=UTC)]  # 12:00 CEST
    runtime.clock = lambda: now[0]
    runs: list[datetime] = []

    async def spy(at: datetime) -> None:
        runs.append(at)

    store_of(runtime).maintain = spy  # type: ignore[method-assign,assignment]
    try:
        await runtime.start()
        await until(lambda: len(runs) == 1)  # at start
        await until(lambda: 3600.0 in sleep.requested)
        # six hourly wakes later it is 18:00 CEST: no run
        for hour in range(1, 7):
            now[0] += timedelta(hours=1)
            await sleep.release(3600.0)
            await until(lambda n=hour: len(sleep.requested) > n)
        assert len(runs) == 1
        # 03:30 CEST the next day: exactly one run, and waking again does not repeat it
        now[0] = datetime(2026, 6, 22, 1, 31, tzinfo=UTC)
        await sleep.release(3600.0)
        await until(lambda: len(runs) == 2)
        await sleep.release(3600.0)
        await sleep.release(3600.0)
        await asyncio.sleep(0.05)
        assert len(runs) == 2
        # the zone changes (e.g. Home Assistant's zone becomes known): the target moves with it
        from suncore.core.config import RegionConfig  # noqa: PLC0415

        before = runtime.next_maintenance_after(now[0])
        runtime.region = RegionConfig(region_id="de", currency="EUR", timezone="Asia/Tokyo")
        after = runtime.next_maintenance_after(now[0])
        assert before != after
        assert after == datetime(2026, 6, 22, 18, 30, tzinfo=UTC)  # 03:30 JST
    finally:
        await runtime.stop()


@pytest.mark.parametrize(
    ("now", "expected_h"),
    [
        (datetime(2026, 6, 21, 1, 29, tzinfo=UTC), 0.0 + 1 / 60),  # 03:29 CEST -> 1 min
        (datetime(2026, 6, 21, 1, 30, tzinfo=UTC), 24.0),  # exactly 03:30: tomorrow's
        (datetime(2026, 3, 28, 12, 0, tzinfo=UTC), 13.5),  # DST night: 23 h day (03:30 CEST)
        (datetime(2026, 10, 24, 12, 0, tzinfo=UTC), 14.5),  # DST night: 25 h day (03:30 CET)
    ],
)
async def test_seconds_until_maintenance_is_dst_safe(
    gated: tuple[Runtime, GatedSleep, RecordingConnection], now: datetime, expected_h: float
) -> None:
    runtime, _sleep, _conn = gated
    runtime.clock = lambda: now
    assert runtime.seconds_until_maintenance() == pytest.approx(expected_h * 3600, abs=1)


async def test_maintenance_downsamples_and_survives_errors(
    gated: tuple[Runtime, GatedSleep, RecordingConnection], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _sleep, _conn = gated
    store = store_of(runtime)
    stack = await idle(runtime)
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)
    await stack.tick()
    later = datetime.now(UTC) + timedelta(days=5)  # the stack stamps with the real clock
    runtime.clock = lambda: later
    assert await runtime.maintain_timeseries() is True
    assert await store.database.fetchone("SELECT COUNT(*) FROM measurement") == (0,)
    assert await store.database.fetchone("SELECT COUNT(*) FROM measurement_1m") != (0,)

    async def broken(_now: datetime) -> Any:
        msg = "boom"
        raise OSError(msg)

    monkeypatch.setattr(store, "maintain", broken)
    with structlog.testing.capture_logs() as logs:
        assert await runtime.maintain_timeseries() is False
    assert any(e["event"] == "timeseries.maintenance_failed" for e in logs)


async def test_plan_snapshots_are_pruned_hourly(
    gated: tuple[Runtime, GatedSleep, RecordingConnection],
) -> None:
    runtime, _sleep, _conn = gated
    assert runtime.plans is not None
    await runtime.plans.save(
        {"x": 1}, source="t", schema_version=1, created_at=runtime.clock() - timedelta(days=30)
    )
    await runtime.prune_plans()  # the hourly loop may have been first: either way it is gone
    assert await runtime.plans.count() == 0


# --- the statistics rebuild ---------------------------------------------------------------------


@pytest.fixture
async def rebuild_env(tmp_path: Path) -> AsyncIterator[tuple[Runtime, RecordingConnection]]:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    # the fake house's recorder history ends at the scenario start
    runtime.clock = lambda: conn._scenario.start
    try:
        yield runtime, conn
    finally:
        await runtime.stop()


async def test_rebuild_runs_once_after_connect_when_the_15_minute_table_is_empty(
    rebuild_env: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, conn = rebuild_env
    store = store_of(runtime)
    assert not await store.has_quarter_history()
    await runtime.start()
    await until(lambda: runtime._history_done)
    assert await store.has_quarter_history()
    keys = {
        str(r[0]) for r in await store.database.fetchall("SELECT DISTINCT key FROM measurement_15m")
    }
    assert keys == set(SITE_KEYS)
    derived = await store.database.fetchone("SELECT MIN(derived) FROM measurement_15m")
    assert derived == (1,)
    # 72 h of simulated history, all hours present
    count = await store.database.fetchone(
        "SELECT COUNT(*) FROM measurement_15m WHERE key = ?", (SITE_HOUSE_W,)
    )
    assert count == (72 * 4,)
    assert all(c["period"] == "hour" for c in conn.recorder.calls)
    asked = {i for c in conn.recorder.calls for i in c["ids"]}
    devices = runtime.devices.devices  # type: ignore[union-attr]
    expected = [*site_statistic_specs(runtime.site), *device_statistic_specs(devices)]  # type: ignore[arg-type]
    assert asked == {s.statistic_id for s in expected}  # the devices' power sensors too (M2)
    calls = len(conn.recorder.calls)
    runtime._start_history_rebuild()  # a reconnect or a site edit does not rebuild again
    await asyncio.sleep(0.05)
    assert len(conn.recorder.calls) == calls


async def test_rebuild_is_skipped_once_the_marker_is_set(
    rebuild_env: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, conn = rebuild_env
    await store_of(runtime).set_meta(META_HISTORY_REBUILT, "2026-06-01T00:00:00+00:00")
    await runtime.start()
    await until(lambda: runtime._history_done)
    assert conn.recorder.calls == []


async def test_existing_quarter_history_does_not_suppress_the_rebuild(
    rebuild_env: tuple[Runtime, RecordingConnection],
) -> None:
    """Only a successful rebuild sets the marker: data (or a maintenance run) is no substitute."""
    runtime, conn = rebuild_env
    from suncore.core.series import SeriesUnit, TimeSeries  # noqa: PLC0415

    store = store_of(runtime)
    await store.store_quarter_hours(
        SITE_HOUSE_W,
        TimeSeries(start=T0, step=timedelta(minutes=15), values=(1.0,), unit=SeriesUnit.W),
    )
    assert await store.has_quarter_history()
    await runtime.start()  # the maintenance job runs at start, too
    await until(lambda: runtime._history_done)
    assert conn.recorder.calls
    assert await store.get_meta(META_HISTORY_REBUILT) is not None


async def test_rebuild_retries_with_backoff_until_it_succeeded_once(
    rebuild_env: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, conn = rebuild_env
    sleep = GatedSleep()
    runtime.sleep = sleep
    runtime.flush_interval_s = 61.0  # keep the flush loop out of the 60 s back-off
    conn.statistics_error = TimeoutError("slow")
    store = store_of(runtime)
    await runtime.start()
    await until(lambda: 60.0 in sleep.requested)  # first retry after a minute
    assert not runtime._history_done
    assert await store.get_meta(META_HISTORY_REBUILT) is None
    conn.statistics_error = HAError("down")
    await sleep.release(60.0)
    await until(lambda: 300.0 in sleep.requested)
    conn.statistics_error = None  # Home Assistant is back
    await sleep.release(300.0)
    await until(lambda: runtime._history_done)
    assert await store.get_meta(META_HISTORY_REBUILT) is not None
    assert await store.has_quarter_history()


async def test_rebuild_failure_is_logged_and_does_not_stop_the_app(
    rebuild_env: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, conn = rebuild_env
    conn.statistics_error = CommandError("unknown_command", "Unknown command.")
    with structlog.testing.capture_logs() as logs:
        await runtime.start()
        await until(lambda: runtime._history_done)
    warning = next(e for e in logs if e["event"] == "history.rebuild_failed")
    assert warning["reason"] == "unknown_command"
    assert not await store_of(runtime).has_quarter_history()
    assert await store_of(runtime).get_meta(META_HISTORY_REBUILT) is None  # retried next start
    stack = await idle(runtime)
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)
    assert await stack.tick() is not None  # control is unaffected


async def test_rebuild_waits_for_the_site(
    tmp_path: Path,
) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    runtime.clock = lambda: conn._scenario.start
    site = runtime.site
    runtime.site = None
    try:
        await runtime.start()
        await asyncio.sleep(0.1)
        assert not runtime._history_done
        assert conn.recorder.calls == []
        runtime.site = site
        runtime._start_history_rebuild()
        await until(lambda: runtime._history_done)
        assert conn.recorder.calls
    finally:
        await runtime.stop()


async def test_a_cancelled_flush_puts_the_batch_back(tmp_path: Path) -> None:
    from suncore.storage import open_timeseries  # noqa: PLC0415

    async with await open_timeseries(tmp_path / "t.db") as db:
        store = TimeseriesStore(db)
        recorder = MeasurementRecorder(store)
        recorder.record(T0, {"a": 1.0, "b": 2.0})
        entered = asyncio.Event()

        async def hang(_rows: Any) -> int:
            entered.set()
            await asyncio.Event().wait()
            return 0

        store.append_measurements = hang  # type: ignore[method-assign]
        task = asyncio.create_task(recorder.flush())
        await entered.wait()
        assert recorder.pending == 0  # in flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert recorder.pending == 2  # back in the buffer, to be written by the final flush
        del store.append_measurements
        assert await recorder.flush() == 2


async def test_devices_are_released_before_storage_tasks_are_awaited(
    gated: tuple[Runtime, GatedSleep, RecordingConnection],
) -> None:
    """A storage task that is slow to cancel (its rollback waits behind a long SQL statement)
    must not delay the hardware release of the control stack."""
    runtime, _sleep, _conn = gated
    stack = await idle(runtime)
    hold = asyncio.Event()
    release = asyncio.Event()
    in_maintain = asyncio.Event()
    shut_down = asyncio.Event()

    async def stubborn(_now: datetime) -> None:
        in_maintain.set()
        try:
            await hold.wait()
        except asyncio.CancelledError:
            await release.wait()  # the shielded rollback behind the running statement
            raise

    store_of(runtime).maintain = stubborn  # type: ignore[method-assign,assignment]
    runtime._maintenance_task.cancel()  # type: ignore[union-attr]
    with contextlib.suppress(asyncio.CancelledError):
        await runtime._maintenance_task  # type: ignore[misc]
    runtime._maintenance_task = asyncio.create_task(runtime._maintenance_loop())
    await in_maintain.wait()
    original = stack.shutdown

    async def shutdown() -> None:
        await original()
        shut_down.set()

    stack.shutdown = shutdown  # type: ignore[method-assign]
    stopping = asyncio.create_task(runtime.stop())
    async with asyncio.timeout(5):
        await shut_down.wait()  # the release ran although the storage task is still stuck
    assert not stopping.done()
    release.set()
    await stopping


async def test_a_failure_while_building_the_values_does_not_break_the_tick(
    gated: tuple[Runtime, GatedSleep, RecordingConnection],
) -> None:
    runtime, _sleep, _conn = gated
    stack = await idle(runtime)
    await until(lambda: stack.cache.get("sensor.sim_house_power") is not None)
    stack._on_measurements = lambda _t, _v: None
    stack.estimates = None  # type: ignore[assignment]  # not a mapping: .items() would raise
    with structlog.testing.capture_logs() as logs:
        stack._record_measurements(runtime.clock(), None, None)  # type: ignore[arg-type]
    assert any(e["event"] == "control.measurement_sink_failed" for e in logs)
