# SPDX-License-Identifier: Apache-2.0
"""The PV forecast service: keep the last good data, gap filling, storage cadence (WP5)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from suncore.core.series import QuantileSeries, SeriesUnit, TimeRange, TimeSeries
from suncore.forecast.pv.adapter import FetchReport, FetchStatus
from suncore.forecast.pv.service import (
    PvForecastService,
    fill_from_previous,
    materially_changed,
)
from suncore.storage import Database
from suncore.storage.timeseries import TimeseriesStore, open_timeseries

T0 = datetime(2026, 6, 21, 6, 0, tzinfo=UTC)
HALF = timedelta(minutes=30)


def series(
    values: list[float | None], start: datetime = T0, step: timedelta = HALF
) -> QuantileSeries:
    ts = TimeSeries(start=start, step=step, values=tuple(values), unit=SeriesUnit.W)
    return QuantileSeries.deterministic(ts)


def day(level: float, start: datetime = T0, slots: int = 96) -> QuantileSeries:
    return series([level] * slots, start)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


class ScriptedSource:
    """A source that returns what the test scripts (a series, or an exception)."""

    def __init__(
        self, source_id: str = "solcast.pv", entities: frozenset[str] = frozenset({"sensor.a"})
    ) -> None:
        self._id = source_id
        self.watched_entities = entities
        self.next: QuantileSeries | Exception = series([])
        self.last_report = FetchReport(FetchStatus.OK)
        self.windows: list[TimeRange] = []

    @property
    def source_id(self) -> str:
        return self._id

    async def fetch(self, window: TimeRange) -> QuantileSeries:
        self.windows.append(window)
        if isinstance(self.next, Exception):
            raise self.next
        return self.next


class RecordingStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, datetime, QuantileSeries]] = []
        self.fail = False

    async def store_forecast(self, source_id, kind, issued_at, forecast):  # type: ignore[no-untyped-def]
        if self.fail:
            msg = "disk full"
            raise OSError(msg)
        self.calls.append((source_id, kind, issued_at, forecast))
        return len(forecast)


def make(
    source: ScriptedSource, clock: Clock, store: RecordingStore | None = None, **kw: object
) -> PvForecastService:
    return PvForecastService(
        [source],
        clock=clock,
        store=store,
        sleep=kw.pop("sleep", asyncio.sleep),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


# --- keep the last good data -----------------------------------------------------------


async def test_first_good_fetch_is_exposed_with_its_freshness() -> None:
    clock, source = Clock(), ScriptedSource()
    service = make(source, clock)
    source.next = day(1000.0)
    await service.refresh()
    snap = service.snapshot("solcast.pv")
    assert snap is not None
    assert snap.series is not None
    assert snap.fetched_at == T0
    assert snap.status == "ok"
    view = service.view(24)
    assert view.sources[0].stale is False
    assert view.sources[0].age_s == 0
    assert view.sources[0].series is not None
    assert view.sources[0].series.p50_w[0] == 1000.0
    assert source.windows[0].start == T0
    assert source.windows[0].end == T0 + timedelta(hours=48)


@pytest.mark.parametrize("bad", ["empty", "all_none", "past_only", "error"])
async def test_a_bad_fetch_never_replaces_good_data(bad: str) -> None:
    clock, source = Clock(), ScriptedSource()
    service = make(source, clock)
    source.next = day(1000.0)
    await service.refresh()
    good = service.snapshot("solcast.pv")
    assert good is not None
    kept = good.series
    clock.advance(minutes=30)
    if bad == "empty":
        source.next = series([])
        source.last_report = FetchReport(FetchStatus.NO_DATA)
    elif bad == "all_none":
        source.next = series([None] * 10, clock.now)
        source.last_report = FetchReport(FetchStatus.NO_DATA)
    elif bad == "past_only":  # the integration did not roll over: only slots that already ended
        source.next = series([1.0] * 4, T0 - timedelta(hours=10))
    else:
        source.next = RuntimeError("boom")
    await service.refresh()
    snap = service.snapshot("solcast.pv")
    assert snap is not None
    assert snap.series is kept  # untouched
    assert snap.fetched_at == T0  # the age keeps growing
    assert snap.status in {"no_data", "error"}
    assert service.view(24).sources[0].series is not None


async def test_stale_flag_after_three_missed_refreshes() -> None:
    clock, source = Clock(), ScriptedSource()
    service = make(source, clock, refresh_interval_s=1800.0)
    assert service.view().sources[0].stale is True  # never fetched
    source.next = day(1000.0)
    await service.refresh()
    clock.advance(minutes=89)
    assert service.view().sources[0].stale is False
    clock.advance(minutes=2)
    assert service.view().sources[0].stale is True


async def test_gap_filling_from_the_last_good_fetch_is_bounded() -> None:
    clock, source = Clock(), ScriptedSource()
    service = make(source, clock, gap_fill_max_age=timedelta(hours=2))
    source.next = day(1000.0)  # 48 h
    await service.refresh()
    clock.advance(minutes=30)
    # tomorrow's sensor flaps: the new fetch only knows the next 6 hours
    source.next = series([2000.0] * 12, clock.now)
    await service.refresh()
    snap = service.snapshot("solcast.pv")
    assert snap is not None
    assert snap.series is not None
    assert snap.filled_slots > 0
    assert snap.series.p50.values[0] == 2000.0
    assert snap.series.p50.values[12] == 1000.0
    assert len(snap.series) == 95  # extended to where the previous good fetch reached
    # an hour later: past the bound after the last complete fetch -> no more filling
    clock.advance(hours=2, minutes=1)
    source.next = series([3000.0] * 12, T0 + timedelta(hours=3))
    await service.refresh()
    again = service.snapshot("solcast.pv")
    assert again is not None
    assert again.series is not None
    assert again.filled_slots == 0
    assert len(again.series) == 12


def test_fill_from_previous_only_fills_unknown_future_slots_on_the_same_grid() -> None:
    previous = series([1.0, 2.0, 3.0, 4.0])
    new = series([None, 9.0, None], T0 + HALF)  # slots 1..3 of previous
    now = T0 + HALF + timedelta(minutes=10)
    filled, count = fill_from_previous(new, previous, now)
    assert count == 2
    # slot 0 of new (T0+30 min .. 1 h) is current: filled from previous slot 1 = 2.0
    assert filled.p50.values == (2.0, 9.0, 4.0)
    other_grid = series([1.0, 2.0], T0, timedelta(hours=1))
    assert fill_from_previous(new, other_grid, now) == (new, 0)
    ended = fill_from_previous(series([None], T0), series([5.0]), T0 + timedelta(hours=1))
    assert ended[1] == 0  # a slot that already ended is not worth filling


# --- storage cadence ------------------------------------------------------------------


async def test_storage_is_at_most_hourly_unless_the_forecast_changed_materially() -> None:
    clock, source, store = Clock(), ScriptedSource(), RecordingStore()
    service = make(source, clock, store=store)
    source.next = day(1000.0)
    await service.refresh()
    assert len(store.calls) == 1
    name, kind, issued, issue = store.calls[0]
    assert (name, kind, issued) == ("solcast.pv", "pv", T0)
    assert issue.start == T0
    assert issue.p50.end <= T0 + timedelta(hours=48)

    for _ in range(5):  # unchanged refreshes every 10 minutes
        clock.advance(minutes=10)
        await service.refresh()
    assert len(store.calls) == 1  # 50 minutes, nothing stored

    clock.advance(minutes=10)  # one hour since the first store
    await service.refresh()
    assert len(store.calls) == 2

    clock.advance(minutes=5)
    source.next = day(
        400.0, T0 + timedelta(hours=1)
    )  # a big change, but only 5 minutes after the last store
    await service.refresh()
    assert len(store.calls) == 2  # the minimum gap holds
    clock.advance(minutes=10)
    await service.refresh()
    assert len(store.calls) == 3  # changed materially and past the gap
    assert store.calls[-1][3].p50.values[0] == 400.0

    clock.advance(minutes=20)
    source.next = day(410.0, T0 + timedelta(hours=1))  # 2.5 %: not material
    await service.refresh()
    assert len(store.calls) == 3


async def test_a_day_of_five_minute_refreshes_stores_at_most_an_hourly_series() -> None:
    clock, source, store = Clock(), ScriptedSource(), RecordingStore()
    service = make(source, clock, store=store)
    source.next = day(1000.0, slots=200)
    for _ in range(24 * 12):
        await service.refresh()
        clock.advance(minutes=5)
    assert len(store.calls) <= 25
    assert sum(len(call[3]) for call in store.calls) <= 25 * 96


async def test_storage_failure_is_retried_and_never_raised() -> None:
    clock, source, store = Clock(), ScriptedSource(), RecordingStore()
    store.fail = True
    service = make(source, clock, store=store)
    source.next = day(1000.0)
    await service.refresh()  # does not raise
    assert store.calls == []
    store.fail = False
    clock.advance(minutes=1)
    await service.refresh()
    assert len(store.calls) == 1  # last_stored_at was not set, so it is retried


async def test_what_is_stored_is_what_the_source_said_not_the_filled_series() -> None:
    clock, source, store = Clock(), ScriptedSource(), RecordingStore()
    service = make(source, clock, store=store, store_interval_s=60.0, min_store_gap_s=0.0)
    source.next = day(1000.0)
    await service.refresh()
    clock.advance(minutes=2)
    source.next = series([2000.0] * 4, T0 + HALF)
    await service.refresh()
    assert len(store.calls[-1][3]) == 4  # not extended by the previous fetch's tail


def test_materially_changed_ignores_unknown_slots_and_night_noise() -> None:
    a, b = day(1000.0), day(1100.0)
    assert not materially_changed(a, b, T0)
    assert materially_changed(a, day(300.0), T0)
    assert not materially_changed(day(0.0), day(1.0), T0)  # tiny absolute change at night
    assert not materially_changed(series([None] * 10), day(500.0), T0)  # nothing comparable


# --- real store round trip -------------------------------------------------------------


@pytest.fixture
async def tsdb(tmp_path: Path) -> AsyncIterator[Database]:
    db = await open_timeseries(tmp_path / "timeseries.db")
    yield db
    await db.close()


async def test_the_issue_is_readable_from_timeseries_db(tsdb: Database) -> None:
    clock, source = Clock(), ScriptedSource()
    store = TimeseriesStore(tsdb)
    service = make(source, clock, store=store)
    source.next = day(1234.0)
    await service.refresh()
    issues = await store.forecast_issues("solcast.pv", "pv")
    assert issues == [T0]
    back = await store.read_forecast("solcast.pv", "pv", T0)
    assert back is not None
    assert back.step == HALF
    assert back.p50.values[0] == 1234.0


# --- loop and wake ------------------------------------------------------------------


async def test_entity_changes_wake_the_loop_and_unrelated_ones_do_not() -> None:
    clock, source = Clock(), ScriptedSource()
    source.next = day(1000.0)
    sleeps: list[float] = []
    gate = asyncio.Event()

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        if delay > 100:
            await gate.wait()  # the refresh interval: never ends by itself

    service = make(source, clock, sleep=sleep, refresh_interval_s=1800.0)
    task = asyncio.create_task(service.run())
    try:
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(source.windows) == 1
        service.on_entities(["sensor.unrelated"])
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(source.windows) == 1
        service.on_entities(["sensor.a"])
        for _ in range(30):
            await asyncio.sleep(0)
        assert len(source.windows) == 2  # woken, debounced by a short sleep
        assert 30.0 in sleeps
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_blended_is_the_first_source_with_data() -> None:
    clock = Clock()
    first, second = ScriptedSource("a.pv"), ScriptedSource("b.pv", frozenset({"sensor.b"}))
    service = PvForecastService([first, second], clock=clock)
    assert service.blended() is None
    assert service.watched_entities == {"sensor.a", "sensor.b"}
