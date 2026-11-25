# SPDX-License-Identifier: Apache-2.0
"""Backtest jobs (Phase 5 WP6): start, progress, result and cancel of :mod:`suncore.sim.backtest`.

One run at a time by default (``JobLimits.max_concurrent``); a run is refused while the planner
itself is solving. A run is an asyncio task on the event loop that reads one day of history at a
time and replays it in a worker thread; it stops - checked before every hourly replan - when it is
cancelled, when it runs longer than ``max_runtime_s`` or when the process uses more than
``memory_limit_mb`` (RSS). Results go to ``backtest_run`` (the last five are kept); a run left
``running`` by an earlier process is marked ``failed`` at start-up.

**No device writes, by construction.** The jobs get history through :class:`ReadOnlyHistory`
(it exposes four read methods and nothing else) and the configuration as a
:class:`~suncore.sim.backtest.BacktestSite` value; no connection, gate or config store is
ever handed to them. This module imports nothing from ``safety``,
``ha``, ``devices``, ``controller`` or ``app.control`` (``tests/sim/test_backtest_boundary.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Literal

import structlog
from pydantic import ValidationError

from suncore.app.planning import run_in_daemon_thread
from suncore.core.decisions import Reason
from suncore.core.series import PriceSeries, QuantileSeries, SeriesUnit, TimeRange, TimeSeries
from suncore.sim.backtest import (
    BacktestSite,
    BacktestStoppedError,
    DayRun,
    HistoryReader,
    Solve,
    Thread,
    apply_overlay,
    default_solve,
    run_backtest,
)
from suncore.sim.backtest_models import (
    BacktestBusyError,
    BacktestJobView,
    BacktestRequest,
    BacktestResult,
    BacktestUnavailableError,
    BacktestWindowError,
)
from suncore.storage.backtests import BacktestRun, BacktestRunMeta, BacktestRunStore

log = structlog.get_logger(__name__)

STOP_CANCELLED: Final = "backtest.stop.cancelled"
STOP_RUNTIME: Final = "backtest.stop.runtime_limit"
STOP_MEMORY: Final = "backtest.stop.memory_limit"
STOP_ERROR: Final = "backtest.stop.error"
STOP_RESTART: Final = "backtest.stop.restart"
STOP_WAIT_S: Final = 2.0
"""Longest a cancel or the shutdown waits for a run before abandoning its solve."""
THREAD_NICENESS: Final = 10


@dataclass(frozen=True, slots=True)
class JobLimits:
    max_concurrent: int = 1
    max_runtime_s: float = 900.0
    """A 30-day run with perfect foresight takes about a minute on amd64; the Pi is slower."""
    memory_limit_mb: float = 280.0
    """Process RSS above which a run stops (the Pi budget is 300 MB including a run)."""


def current_rss_mb() -> float | None:
    """Resident set size of this process (Linux ``/proc``); ``None`` elsewhere."""
    try:
        with Path("/proc/self/statm").open(encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)


class ReadOnlyHistory:
    """The four reads a backtest needs, bound at construction; no other attribute exists.

    The engine and the job only ever see this view (slots, no ``__dict__``), never the store,
    its database or a write method (``tests/sim/test_backtest_boundary.py`` checks it).
    """

    __slots__ = ("_forecast", "_prices", "_series", "_sources")

    def __init__(self, store: HistoryReader) -> None:
        self._series = store.read_series
        self._forecast = store.latest_forecast_before
        self._prices = store.read_prices
        self._sources = store.price_source_ids

    async def read_series(
        self,
        key: str,
        window: TimeRange,
        resolution: Literal["15m"] = "15m",
        unit: SeriesUnit | None = None,
        *,
        include_recent: bool = True,
    ) -> TimeSeries:
        return await self._series(key, window, resolution, unit, include_recent=include_recent)

    async def latest_forecast_before(
        self,
        source_id: str,
        kind: Literal["pv", "load"],
        before: datetime,
        window: TimeRange | None = None,
    ) -> QuantileSeries | None:
        return await self._forecast(source_id, kind, before, window)

    async def read_prices(self, source_id: str, window: TimeRange) -> list[PriceSeries]:
        return await self._prices(source_id, window)

    async def price_source_ids(self, window: TimeRange) -> list[str]:
        return await self._sources(window)


@dataclass(slots=True)
class _Job:
    run_id: int
    total: int
    started: float
    stop: threading.Event = field(default_factory=threading.Event)
    reason: str | None = None
    done: int = 0
    task: asyncio.Task[None] | None = None


def _request_of(body: dict[str, Any]) -> BacktestRequest | None:
    try:
        return BacktestRequest.model_validate(body)
    except ValidationError:
        return None


def _error_of(result: dict[str, Any] | None) -> Reason | None:
    raw = None if result is None else result.get("error")
    if not isinstance(raw, dict):
        return None
    try:
        return Reason.model_validate(raw)
    except ValidationError:
        return None


class BacktestJobs:
    """The job API's backend (``Services.backtests()``)."""

    def __init__(
        self,
        store: BacktestRunStore,
        *,
        history: Callable[[], HistoryReader | None],
        site: Callable[[], Awaitable[BacktestSite | None]],
        today: Callable[[], date],
        busy: Callable[[], bool] = lambda: False,
        limits: JobLimits | None = None,
        rss_mb: Callable[[], float | None] = current_rss_mb,
        solve: Solve = default_solve,
        thread: Thread | None = None,
    ) -> None:
        self._store = store
        self._history = history
        self._site = site
        self._today = today
        self._busy = busy
        self._limits = limits or JobLimits()
        self._rss_mb = rss_mb
        self._solve = solve
        self._thread = thread
        self._jobs: dict[int, _Job] = {}
        self._closing = False
        self._lock = asyncio.Lock()

    @property
    def limits(self) -> JobLimits:
        return self._limits

    @property
    def running(self) -> int:
        return len(self._jobs)

    async def recover(self) -> None:
        """Runs an earlier process left ``running`` are ``failed`` now."""
        await self._store.abandon_running()

    async def start(self, request: BacktestRequest) -> BacktestJobView:
        """Start a run. Raises :class:`BacktestWindowError` (not in the past),
        :class:`BacktestBusyError` (a run or the planner is active) and
        :class:`BacktestUnavailableError` (no history or site, or shutting down) and
        :class:`~suncore.sim.backtest_models.BacktestOverlayError` (the overlay does not fit the
        site; checked before the run is stored)."""
        if request.end >= self._today():
            raise BacktestWindowError(request.end.isoformat())
        async with self._lock:
            if self._closing:
                raise BacktestUnavailableError
            if len(self._jobs) >= self._limits.max_concurrent or self._busy():
                raise BacktestBusyError
            history = self._history()
            site = await self._site()
            if history is None or site is None:
                raise BacktestUnavailableError
            apply_overlay(site, request)  # BacktestOverlayError before anything is stored
            reader = ReadOnlyHistory(history)
            run_id = await self._store.start(
                request.model_dump(mode="json"),
                window_start=_midnight(request.start, site),
                window_end=_midnight(request.end, site),
            )
            job = _Job(run_id=run_id, total=request.days, started=time.monotonic())
            self._jobs[run_id] = job
            job.task = asyncio.create_task(
                self._run(job, reader, site, request), name=f"suncore-backtest-{run_id}"
            )
        log.info("backtest.started", run_id=run_id, days=request.days)
        view = await self.get(run_id)
        assert view is not None
        return view

    def _should_stop(self, job: _Job) -> bool:
        """Checked before every replan (also from the worker thread)."""
        if job.stop.is_set():
            return True
        if time.monotonic() - job.started > self._limits.max_runtime_s:
            job.reason = STOP_RUNTIME
            job.stop.set()
            return True
        rss = self._rss_mb()
        if rss is not None and rss > self._limits.memory_limit_mb:
            job.reason = STOP_MEMORY
            job.stop.set()
            return True
        return False

    def _progress(self, job: _Job, done: int, _total: int) -> None:
        job.done = done

    async def _run(
        self, job: _Job, history: HistoryReader, site: BacktestSite, request: BacktestRequest
    ) -> None:
        status: Literal["done", "failed", "cancelled"] = "failed"
        payload: dict[str, Any] = {"error": Reason(key=STOP_ERROR).model_dump(mode="json")}
        try:
            kwargs: dict[str, Any] = {"thread": self._thread or low_priority_daemon_thread}
            result = await run_backtest(
                history,
                site,
                request,
                today=self._today(),
                solve=self._solve,
                progress=lambda done, total: self._progress(job, done, total),
                should_stop=lambda: self._should_stop(job),
                **kwargs,
            )
            status, payload = "done", result.model_dump(mode="json")
        except BacktestStoppedError:
            reason = job.reason or STOP_CANCELLED
            status = "cancelled" if reason in (STOP_CANCELLED, STOP_RESTART) else "failed"
            payload = {"error": Reason(key=reason).model_dump(mode="json")}
        except asyncio.CancelledError:
            status = "cancelled"
            payload = {"error": Reason(key=job.reason or STOP_RESTART).model_dump(mode="json")}
            raise
        except Exception:
            log.exception("backtest.failed", run_id=job.run_id)
        finally:
            self._jobs.pop(job.run_id, None)
            with contextlib.suppress(Exception):
                await asyncio.shield(self._store.finish(job.run_id, status, payload))
            log.info("backtest.finished", run_id=job.run_id, status=status)

    async def runs(self) -> list[BacktestJobView]:
        """The kept runs, newest first (without results)."""
        return [self._view(meta, None) for meta in await self._store.headers()]

    async def get(self, run_id: int) -> BacktestJobView | None:
        run = await self._store.get(run_id)
        return None if run is None else self._view(run.meta, run)

    def _view(self, meta: BacktestRunMeta, run: BacktestRun | None) -> BacktestJobView:
        job = self._jobs.get(meta.id)
        result = None
        error = None
        if run is not None and run.result is not None:
            if meta.status == "done":
                try:
                    result = BacktestResult.model_validate(run.result)
                except ValidationError:
                    log.warning("backtest.result_unreadable", run_id=meta.id)
            error = _error_of(run.result)
        if meta.status == "failed" and error is None and job is None:
            error = Reason(key=STOP_RESTART if run is None or run.result is None else STOP_ERROR)
        request = None if run is None else _request_of(run.request)
        return BacktestJobView(
            id=meta.id,
            status=meta.status,
            created_at=meta.created_at,
            finished_at=meta.finished_at,
            start=meta.window_start.date() if request is None else request.start,
            end=meta.window_end.date() if request is None else request.end,
            request=request,
            progress_done=None if job is None else job.done,
            progress_total=None if job is None else job.total,
            result=result,
            error=error,
        )

    async def cancel(self, run_id: int) -> Literal["cancelled", "deleted"] | None:
        """Cancel a running run (kept as ``cancelled``) or delete a finished one; ``None`` if
        there is no such run. Waits at most :data:`STOP_WAIT_S` for the run to stop."""
        job = self._jobs.get(run_id)
        if job is not None:
            job.reason = job.reason or STOP_CANCELLED
            await self._stop(job)
            return "cancelled"
        return "deleted" if await self._store.delete(run_id) else None

    async def shutdown(self) -> None:
        """Refuse new runs and stop every running one (kept as ``cancelled``) within
        :data:`STOP_WAIT_S`; called before the database closes. A solve still running in its
        daemon thread is abandoned, never awaited."""
        self._closing = True
        jobs = list(self._jobs.values())
        for job in jobs:
            job.reason = job.reason or STOP_RESTART
        await asyncio.gather(*(self._stop(job) for job in jobs))

    async def _stop(self, job: _Job) -> None:
        job.stop.set()
        task = job.task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), STOP_WAIT_S)
        except TimeoutError:
            task.cancel()  # abandons the daemon thread; the run is recorded as cancelled
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        except (asyncio.CancelledError, Exception):
            if not task.done():
                raise


def lower_thread_priority(niceness: int = THREAD_NICENESS) -> bool:
    """Lower the calling thread's CPU priority (Linux: ``setpriority`` on the native thread id
    sets that thread only). ``False`` where unsupported or refused; never raises."""
    setpriority = getattr(os, "setpriority", None)
    which = getattr(os, "PRIO_PROCESS", None)
    if setpriority is None or which is None:
        return False
    try:
        setpriority(which, threading.get_native_id(), niceness)
    except (OSError, ValueError):
        return False
    return True


async def low_priority_daemon_thread(fn: Callable[[], DayRun]) -> DayRun:
    """The live planner's daemon-thread helper, with the thread's priority lowered first, so a
    backtest never delays the controller or the planner and never holds the shutdown."""

    def work() -> DayRun:
        lower_thread_priority()
        return fn()

    return await run_in_daemon_thread(work)


def _midnight(day: date, site: BacktestSite) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=site.tz)


__all__ = [
    "STOP_CANCELLED",
    "STOP_ERROR",
    "STOP_MEMORY",
    "STOP_RESTART",
    "STOP_RUNTIME",
    "STOP_WAIT_S",
    "THREAD_NICENESS",
    "BacktestJobs",
    "JobLimits",
    "ReadOnlyHistory",
    "current_rss_mb",
    "low_priority_daemon_thread",
    "lower_thread_priority",
]
