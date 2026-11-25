# SPDX-License-Identifier: Apache-2.0
"""Backfill of up to 365 days from HA's hourly statistics (Phase 5 WP3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from suncore.app.history import BACKFILL_DAYS, REBUILD_DAYS, backfill_history, rebuild_history
from suncore.core.measurements import SITE_GRID_W, SITE_HOUSE_W
from suncore.core.series import SeriesUnit, TimeRange, TimeSeries
from suncore.core.site import EntityRef, GridConfig, SiteConfig
from suncore.ha.connection import HAConnection
from suncore.storage import open_timeseries
from suncore.storage.timeseries import TimeseriesStore

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
HOUR_MS = 3_600_000
BACKFILLED_QUARTERS = (BACKFILL_DAYS - REBUILD_DAYS) * 24 * 4


class HourlyStats:
    """Fake ``recorder/statistics_during_period``: one row per hour of the asked range, for
    the grid sensor only (the house sensor has no statistics)."""

    def __init__(self, *, oldest: datetime | None = None) -> None:
        self.calls: list[tuple[datetime, datetime]] = []
        self.oldest = oldest

    async def statistics_during_period(
        self, ids: list[str], start: datetime, end: datetime, **_kw: Any
    ) -> dict[str, list[dict[str, Any]]]:
        self.calls.append((start, end))
        rows = []
        cursor = start
        while cursor < end:
            if self.oldest is None or cursor >= self.oldest:
                ms = int(cursor.timestamp() * 1000)
                rows.append({"start": ms, "end": ms + HOUR_MS, "mean": 400.0})
            cursor += timedelta(hours=1)
        return {i: rows for i in ids if i == "sensor.grid"}


def site() -> SiteConfig:
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id="sensor.grid")),
        house=EntityRef(entity_id="sensor.house"),
    )


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[TimeseriesStore]:
    async with await open_timeseries(tmp_path / "t.db") as db:
        yield TimeseriesStore(db)


def as_connection(stats: HourlyStats) -> HAConnection:
    return stats  # type: ignore[return-value]


async def test_backfill_reaches_365_days_back_in_windows_and_marks_rows_derived(
    store: TimeseriesStore,
) -> None:
    stats = HourlyStats()
    result = await backfill_history(as_connection(stats), store, site(), NOW)
    end = NOW.replace(minute=0)
    first_wanted = end - timedelta(days=BACKFILL_DAYS)
    last_wanted = end - timedelta(days=REBUILD_DAYS)
    assert min(s for s, _ in stats.calls) == first_wanted
    assert max(e for _, e in stats.calls) == last_wanted  # where the 28-day rebuild starts
    # 337 days of hours: more than one series may carry (MAX_POINTS), so it ran in windows
    assert result.stored[SITE_GRID_W] == BACKFILLED_QUARTERS
    assert result.missing == ("sensor.house",)
    grid = await store.read_series(
        SITE_GRID_W, TimeRange(start=first_wanted, end=first_wanted + timedelta(hours=2))
    )
    assert grid.values == (400.0,) * 8
    flags = await store.database.fetchone("SELECT MIN(derived), MAX(derived) FROM measurement_15m")
    assert flags == (1, 1)
    assert await store.first_quarter(SITE_GRID_W) == first_wanted
    # the window of the first rebuild stays untouched
    after = await store.read_series(
        SITE_GRID_W, TimeRange(start=last_wanted, end=last_wanted + timedelta(hours=1))
    )
    assert after.values == (None,) * 4


async def test_backfill_never_overwrites_measured_quarters_and_is_idempotent(
    store: TimeseriesStore,
) -> None:
    measured_at = NOW.replace(minute=0) - timedelta(days=100)
    await store.store_quarter_hours(
        SITE_GRID_W,
        TimeSeries(
            start=measured_at,
            step=timedelta(minutes=15),
            values=(111.0, 222.0),
            unit=SeriesUnit.W,
        ),
    )
    stats = HourlyStats()
    first = await backfill_history(as_connection(stats), store, site(), NOW)
    second = await backfill_history(as_connection(stats), store, site(), NOW)
    assert first.stored == second.stored
    got = await store.read_series(
        SITE_GRID_W, TimeRange(start=measured_at, end=measured_at + timedelta(hours=1))
    )
    assert got.values == (111.0, 222.0, 400.0, 400.0)  # measured wins, the rest is derived
    count = await store.database.fetchone(
        "SELECT COUNT(*) FROM measurement_15m WHERE key = ?", (SITE_GRID_W,)
    )
    assert count == (BACKFILLED_QUARTERS,)


async def test_days_home_assistant_has_no_statistics_for_stay_gaps(store: TimeseriesStore) -> None:
    stats = HourlyStats(oldest=NOW - timedelta(days=60))  # the recorder kept 60 days only
    result = await backfill_history(as_connection(stats), store, site(), NOW)
    assert result.stored[SITE_GRID_W] == (60 - REBUILD_DAYS) * 24 * 4
    oldest = await store.first_quarter(SITE_GRID_W)
    assert oldest is not None
    assert oldest >= NOW - timedelta(days=60, hours=1)


async def test_the_28_day_rebuild_is_still_one_range_up_to_now(store: TimeseriesStore) -> None:
    stats = HourlyStats()
    await rebuild_history(as_connection(stats), store, site(), NOW)
    assert min(s for s, _ in stats.calls) == NOW.replace(minute=0) - timedelta(days=REBUILD_DAYS)
    assert max(e for _, e in stats.calls) == NOW.replace(minute=0)
    keys = {
        str(r[0]) for r in await store.database.fetchall("SELECT DISTINCT key FROM measurement_15m")
    }
    assert SITE_HOUSE_W not in keys


async def test_a_failed_window_keeps_what_the_earlier_windows_stored(
    store: TimeseriesStore,
) -> None:
    class Flaky(HourlyStats):
        async def statistics_during_period(  # type: ignore[no-untyped-def]
            self, ids, start, end, **kw
        ):
            if len(self.calls) >= 10:  # the first window (9 weekly requests) went through
                msg = "recorder busy"
                raise TimeoutError(msg)
            return await super().statistics_during_period(ids, start, end, **kw)

    with pytest.raises(TimeoutError):
        await backfill_history(as_connection(Flaky()), store, site(), NOW)
    assert await store.has_quarter_history()  # the first window is in; a second run completes
    done = await backfill_history(as_connection(HourlyStats()), store, site(), NOW)
    assert done.stored[SITE_GRID_W] == BACKFILLED_QUARTERS
