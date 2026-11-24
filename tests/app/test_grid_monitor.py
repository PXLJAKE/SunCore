# SPDX-License-Identifier: Apache-2.0
"""Monthly peak and grid events of the control stack (PR 6-F)."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from suncore.app.grid import GridMonitor, quarter_start
from suncore.controller.grid import GridInputs
from suncore.core.balance import Flow
from suncore.core.grid import GridState, Para14aBudget, Para14aStatus
from suncore.core.series import TimeRange
from suncore.core.signals import Quality
from suncore.storage.db import Database
from suncore.storage.grid import GridStore
from suncore.storage.timeseries import timeseries_migrations
from tests.safety.helpers import flows

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 7, 31, 23, 0, tzinfo=TZ)


def feed(monitor: GridMonitor, start: datetime, seconds: int, grid_w: float, **kw: object) -> None:
    grid = GridInputs(**kw)  # type: ignore[arg-type]
    for s in range(0, seconds, 5):
        monitor.observe(start + timedelta(seconds=s), flows(grid_w), grid)


def test_quarter_means_set_the_peak_and_reset_at_the_month() -> None:
    monitor = GridMonitor(TZ)
    feed(monitor, T0, 900, 1000.0)
    feed(monitor, T0 + timedelta(seconds=900), 900, 3000.0)
    feed(monitor, T0 + timedelta(seconds=1800), 905, 500.0)
    assert monitor.month_peak_w(T0) == 3000.0
    assert [e.kind for e in monitor.pending] == ["peak_new"]
    # After local midnight the August quarters start a fresh peak.
    august = datetime(2026, 8, 1, 0, 0, tzinfo=TZ)
    assert monitor.month_peak_w(august) is None
    feed(monitor, august, 1805, 400.0)
    assert monitor.month_peak_w(august) == 400.0


def test_export_and_gaps_do_not_count() -> None:
    monitor = GridMonitor(TZ)
    feed(monitor, T0, 900, -2000.0)
    feed(monitor, T0 + timedelta(seconds=900), 5, 0.0)
    assert monitor.month_peak_w(T0) == 0.0
    stale = GridMonitor(TZ)
    bad = dataclasses.replace(flows(), grid_w=Flow(9000.0, Quality.STALE))
    for s in range(0, 1805, 5):
        stale.observe(T0 + timedelta(seconds=s), bad, GridInputs())
    assert stale.month_peak_w(T0) is None  # nothing measured: no peak


def test_events() -> None:
    monitor = GridMonitor(TZ)
    island = GridInputs(grid_state=GridState.ISLAND, fuse_limit_w=1000.0)
    unknown = GridInputs(grid_state=GridState.UNKNOWN)
    back = GridInputs(grid_state=GridState.CONNECTED)
    active = GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, 4200))
    para_unknown = GridInputs(para14a=Para14aBudget(Para14aStatus.UNKNOWN))
    for i, grid in enumerate((back, island, unknown, back, active, para_unknown, back)):
        monitor.observe(T0 + timedelta(seconds=5 * i), flows(1500.0), grid)
    kinds = [e.kind for e in monitor.pending]
    assert kinds == [
        "island_start",
        "import_limit_exceeded",
        "island_end",
        "para14a_start",
        "para14a_unknown",
        "para14a_end",
    ]


def test_flush_persists_and_load_seeds(tmp_path: Path) -> None:
    async def go() -> None:
        db = await Database.open(tmp_path / "ts.db", migrations=timeseries_migrations())
        try:
            store = GridStore(db)
            monitor = GridMonitor(TZ, store=store)
            feed(monitor, T0, 905, 2000.0, grid_state=GridState.ISLAND)
            await monitor.flush()
            assert monitor.pending == []
            window = TimeRange(start=T0 - timedelta(hours=1), end=T0 + timedelta(hours=1))
            events = await store.events(window)
            assert [e.kind for e in events] == ["island_start"]
            fresh = GridMonitor(TZ, store=store)
            await fresh.load(T0)
            assert fresh.month_peak_w(T0) == 2000.0
        finally:
            await db.close()

    asyncio.run(go())


def test_quarter_start() -> None:
    assert quarter_start(T0 + timedelta(minutes=7, seconds=3)) == T0
