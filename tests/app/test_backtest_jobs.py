# SPDX-License-Identifier: Apache-2.0
"""Backtest jobs (Phase 5 WP6): one at a time, cancel, limits, results in ``backtest_run``."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from suncore.app.backtests import (
    STOP_CANCELLED,
    STOP_MEMORY,
    STOP_RESTART,
    STOP_RUNTIME,
    BacktestJobs,
    JobLimits,
)
from suncore.core.battery import BatteryModel, BatterySettings
from suncore.planner.inputs import PlannerInputs
from suncore.planner.run import PlannerRun
from suncore.sim.backtest import BacktestSite, default_solve
from suncore.sim.backtest_models import (
    BacktestBusyError,
    BacktestJobView,
    BacktestRequest,
    BacktestUnavailableError,
    BacktestWindowError,
)
from suncore.sim.demo_history import EXPORT_PRICE, PRICE_SOURCE, PV_SOURCES, seed_demo_history
from suncore.storage import Database, open_timeseries
from suncore.storage.backtests import BacktestRunStore
from suncore.storage.timeseries import TimeseriesStore

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 23)
BERLIN = ZoneInfo("Europe/Berlin")
REQUEST = BacktestRequest(start=date(2026, 11, 21), end=date(2026, 11, 22))

SITE = BacktestSite(
    tz=BERLIN,
    tz_name="Europe/Berlin",
    battery=BatterySettings(
        model=BatteryModel(capacity_wh=10_000, max_charge_w=4000, max_discharge_w=4000)
    ),
    price_source_id=PRICE_SOURCE,
    export_fallback=EXPORT_PRICE,
    currency="EUR",
    pv_source_ids=tuple(s for s, _, _ in PV_SOURCES),
)


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("jobs") / "t.db"

    async def seed() -> None:
        async with await open_timeseries(path) as db:
            await seed_demo_history(TimeseriesStore(db), now=NOW, days=5)

    asyncio.run(seed())
    return path


@dataclass
class Env:
    store: BacktestRunStore
    history: TimeseriesStore
    db: Database


@pytest.fixture
async def env(demo_db: Path, tmp_path: Path) -> AsyncIterator[Env]:
    db = await Database.open(tmp_path / "suncore.db")
    async with await open_timeseries(demo_db) as ts:
        yield Env(BacktestRunStore(db), TimeseriesStore(ts), db)
    await db.close()


def slow_solve(delay: float = 0.05) -> Any:
    def solve(inputs: PlannerInputs, budget: float, gap: float, prev: Any) -> PlannerRun:
        time.sleep(delay)
        return default_solve(inputs, budget, gap, prev)

    return solve


def jobs_for(env: Env, **kw: Any) -> BacktestJobs:
    async def site() -> BacktestSite:
        return SITE

    fields: dict[str, Any] = {
        "history": lambda: env.history,
        "site": site,
        "today": lambda: TODAY,
    }
    return BacktestJobs(env.store, **{**fields, **kw})


async def finished(jobs: BacktestJobs, run_id: int) -> BacktestJobView:
    async with asyncio.timeout(60):
        while True:
            view = await jobs.get(run_id)
            assert view is not None
            if view.status != "running" and jobs.running == 0:
                return view
            await asyncio.sleep(0.02)


async def test_a_run_finishes_and_keeps_its_result(env: Env) -> None:
    jobs = jobs_for(env)
    started = await jobs.start(REQUEST)
    assert started.status == "running"
    assert (started.progress_total, started.request) == (2, REQUEST)
    view = await finished(jobs, started.id)
    assert view.status == "done"
    assert view.result is not None
    assert view.result.totals is not None
    assert view.result.totals.days == 2
    assert view.error is None
    listed = await jobs.runs()
    assert [(v.id, v.status, v.result) for v in listed] == [(started.id, "done", None)]


async def test_only_one_run_at_a_time_and_not_while_the_planner_solves(env: Env) -> None:
    jobs = jobs_for(env, solve=slow_solve())
    first = await jobs.start(REQUEST)
    with pytest.raises(BacktestBusyError):
        await jobs.start(REQUEST)
    assert await jobs.cancel(first.id) == "cancelled"
    planner_busy = jobs_for(env, busy=lambda: True)
    with pytest.raises(BacktestBusyError):
        await planner_busy.start(REQUEST)


async def test_cancel_stops_before_the_next_replan_and_delete_removes(env: Env) -> None:
    calls = 0

    def counting(inputs: PlannerInputs, budget: float, gap: float, prev: Any) -> PlannerRun:
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return default_solve(inputs, budget, gap, prev)

    jobs = jobs_for(env, solve=counting)
    run = await jobs.start(REQUEST)
    await asyncio.sleep(0.2)
    assert await jobs.cancel(run.id) == "cancelled"
    view = await finished(jobs, run.id)
    assert view.status == "cancelled"
    assert view.error is not None
    assert view.error.key == STOP_CANCELLED
    assert view.result is None
    assert calls < 48  # stopped long before the two days were replayed
    assert await jobs.cancel(run.id) == "deleted"
    assert await jobs.get(run.id) is None
    assert await jobs.cancel(run.id) is None


@pytest.mark.parametrize(
    ("limits", "rss", "key"),
    [
        (JobLimits(max_runtime_s=0.0), 10.0, STOP_RUNTIME),
        (JobLimits(memory_limit_mb=100.0), 999.0, STOP_MEMORY),
    ],
)
async def test_limits_stop_a_run(env: Env, limits: JobLimits, rss: float, key: str) -> None:
    jobs = jobs_for(env, limits=limits, rss_mb=lambda: rss)
    run = await jobs.start(REQUEST)
    view = await finished(jobs, run.id)
    assert view.status == "failed"
    assert view.error is not None
    assert view.error.key == key


async def test_refusals(env: Env) -> None:
    jobs = jobs_for(env)
    with pytest.raises(BacktestWindowError):
        await jobs.start(BacktestRequest(start=date(2026, 11, 22), end=TODAY))
    without = jobs_for(env, history=lambda: None)
    with pytest.raises(BacktestUnavailableError):
        await without.start(REQUEST)
    assert await env.store.headers() == []


async def test_runs_left_running_are_failed_at_start_up(env: Env) -> None:
    run_id = await env.store.start(
        REQUEST.model_dump(mode="json"),
        window_start=datetime(2026, 11, 21, tzinfo=BERLIN),
        window_end=datetime(2026, 11, 22, tzinfo=BERLIN),
    )
    jobs = jobs_for(env)
    await jobs.recover()
    view = await jobs.get(run_id)
    assert view is not None
    assert view.status == "failed"
    assert view.error is not None
    assert view.error.key == STOP_RESTART
    assert view.request == REQUEST


async def test_shutdown_stops_a_running_run(env: Env) -> None:
    jobs = jobs_for(env, solve=slow_solve())
    run = await jobs.start(REQUEST)
    await asyncio.sleep(0.1)
    await jobs.shutdown()
    view = await jobs.get(run.id)
    assert view is not None
    assert view.status == "cancelled"
    assert view.error is not None
    assert view.error.key == STOP_RESTART


async def test_the_demo_runtime_replays_without_touching_home_assistant(tmp_path: Path) -> None:
    from suncore.app.main import build_runtime  # noqa: PLC0415
    from suncore.sim.fake_ha import FakeHAConnection  # noqa: PLC0415
    from tests.app.conftest import demo_settings  # noqa: PLC0415

    runtime = await build_runtime(demo_settings(tmp_path, demo_history_days=3), env={})
    try:
        jobs = runtime.backtests()
        assert jobs is not None
        connection = runtime.connection
        assert isinstance(connection, FakeHAConnection)
        today = runtime.clock().astimezone(BERLIN).date()
        day = today - timedelta(days=1)
        run = await jobs.start(BacktestRequest(start=day, end=day))
        view = await finished(jobs, run.id)
        assert view.status == "done", view.error
        assert view.result is not None
        assert view.result.totals is not None
        assert connection.call_log == []  # not one service call during the replay
        assert await runtime.config.get("expert") == runtime.expert  # nothing written back
    finally:
        await runtime.stop()
