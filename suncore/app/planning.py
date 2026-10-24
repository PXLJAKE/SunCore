# SPDX-License-Identifier: Apache-2.0
"""The planner runtime (Phase 4 WP9): when to plan, with what, and what the controller gets.

:class:`PlanningService` runs :func:`suncore.planner.run` (HiGHS plus the heuristic fallback) in a
worker thread and hands the resulting :class:`~suncore.core.control.Plan` to the control layer.
Time is injected twice (``clock`` = the now, ``sleep`` = the poll period), like the other
services, so tests and the accelerated demo run it in simulated time.

**When it runs** (:meth:`PlanningService.tick`, polled every ``poll_s``):

* *first run* once the inputs exist (prices and forecasts available, or ``first_run_grace_s``
  after the first connection with the missing ones reported by the plan's reasons);
* *every 15 minutes*, aligned to the quarter hour plus ``offset_s`` (so the price and forecast
  refreshes of the same quarter land first);
* *on events* (:meth:`request`): new prices, a forecast update, a deviation, a configuration
  change, a user override and a manual replan. Event runs are debounced: at most one per
  ``min_event_gap_s`` (60 s); events that arrive meanwhile are merged into one run. A run starts
  only when no other run is active (single flight) - events during a run wait for its end.

**Deviation** (documented thresholds, all in :class:`PlanningSettings`): the live SoC differs from
the plan's SoC at ``now`` (linear between the interval boundaries) by more than
``soc_deviation_pct`` (10 %-points), or the actual grid power differs from the planned net power
(import - export) by more than ``net_deviation_w`` (1500 W) for ``net_deviation_s`` (30 min) -
judged on a 10-minute exponential average of the difference so a short cloud does not count.

**Hand-over**: the new plan replaces ``self._plan`` (one reference assignment on the event loop);
the control stack reads :meth:`active_plan` once per tick. The solve runs in a daemon thread
(``run_in_daemon_thread``), never on the event loop; a run that hangs is abandoned after
``run_timeout_s`` (the thread cannot be killed; it is a daemon thread, no new run starts while it
lives, and it never delays shutdown).

**Failure**: a failed or skipped run keeps the last good plan. After ``stale_after_s`` (2 h) the
plan is *stale*: still served by the API, flagged, but :meth:`active_plan` returns
``Plan.default()`` (the Phase 3 behaviour) - as it does once every interval lies in the past.
Nothing here writes to a device; the plan's bands can only restrict devices and move the
surplus share (``suncore.controller``).
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, TypeVar

import structlog

from suncore.core.control import Plan
from suncore.core.decisions import Reason
from suncore.planner import reasons as rk
from suncore.planner.inputs import DEFAULT_BUDGET_S, DEFAULT_MIP_GAP, PlannerInputs
from suncore.planner.run import PlannerRun
from suncore.planner.run import run as run_planner
from suncore.planner.views import (
    PlanStatusView,
    PlanSummary,
    PlanView,
    ReplanAck,
    RunOutcome,
    Trigger,
)
from suncore.storage import PlanSnapshotStore

log = structlog.get_logger(__name__)

T = TypeVar("T")

QUARTER: Final = timedelta(minutes=15)
EVENT_PRIORITY: Final[tuple[Trigger, ...]] = (
    "manual",
    "config",
    "override",
    "deviation",
    "prices",
    "forecast",
)
"""The trigger a merged event run reports: the first of these that is pending."""
SOC_START_KEY: Final = "soc_start_pct"
"""``Plan.assumptions`` entry: the live SoC the run started from (deviation check)."""
NET_EMA_S: Final = 600.0


@dataclass(frozen=True, slots=True)
class PlanningSettings:
    """Cadence, thresholds and budget of the planner runtime."""

    budget_s: float = DEFAULT_BUDGET_S
    """Solver time budget of a run (ADR-0003)."""
    mip_gap: float = DEFAULT_MIP_GAP
    offset_s: float = 20.0
    """The scheduled run starts this long after the quarter hour."""
    min_event_gap_s: float = 60.0
    """Debounce: at most one event-triggered run per this many seconds."""
    stale_after_s: float = 7200.0
    """Age after which a plan is stale: flagged, and no longer handed to the controller."""
    soc_deviation_pct: float = 10.0
    net_deviation_w: float = 1500.0
    net_deviation_s: float = 1800.0
    first_run_grace_s: float = 300.0
    """How long the first plan waits for prices and forecasts after the first connection."""
    poll_s: float = 5.0
    retry_skipped_s: float = 15.0
    """Re-check of a skipped (not ready) run."""
    run_timeout_margin_s: float = 30.0
    """A run is abandoned ``budget_s`` plus this long after it started."""

    @property
    def run_timeout_s(self) -> float:
        return self.budget_s + self.run_timeout_margin_s


@dataclass(frozen=True, slots=True)
class PlanningSnapshot:
    """What the collector found: the inputs, or why there are none."""

    inputs: PlannerInputs | None
    complete: bool = True
    """Prices and every configured forecast are there (the first plan waits for them)."""
    reason: Reason | None = None
    """Why ``inputs`` is ``None``."""


@dataclass(frozen=True, slots=True)
class LiveReading:
    """The two live values the deviation check compares with the plan."""

    soc_pct: float | None = None
    grid_w: float | None = None
    """Grid power, + = import."""


Collector = Callable[[datetime, Plan | None], Awaitable[PlanningSnapshot]]
"""``(now, previous plan) -> snapshot``; the runtime reads its services and calls ``build_inputs``."""
Solver = Callable[[PlannerInputs, float, float, Plan | None], PlannerRun]
ThreadRunner = Callable[[Callable[[], T]], Awaitable[T]]
PlanListener = Callable[[], None]


def default_solver(
    inputs: PlannerInputs, budget_s: float, mip_gap: float, previous: Plan | None
) -> PlannerRun:
    return run_planner(inputs, budget_s=budget_s, mip_gap=mip_gap, warm_start=previous)


async def run_in_daemon_thread(fn: Callable[[], T]) -> T:
    """Run ``fn`` in a daemon thread and await its result without ever blocking the loop.

    Unlike ``asyncio.to_thread`` (default executor, joined at shutdown) an abandoned call cannot
    keep the process or the loop's shutdown waiting: the awaiting task can be cancelled at any
    time and the daemon thread just finishes (or hangs) unobserved.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def deliver(result: T | None, error: BaseException | None) -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)  # type: ignore[arg-type]

    def work() -> None:
        result: T | None = None
        error: BaseException | None = None
        try:
            result = fn()
        except BaseException as exc:
            error = exc
        with contextlib.suppress(RuntimeError):  # the loop is closed: nobody is waiting
            loop.call_soon_threadsafe(deliver, result, error)

    threading.Thread(target=work, name="suncore-planner", daemon=True).start()
    return await future


# --- pure helpers ---------------------------------------------------------------------------


def floor_quarter(moment: datetime) -> datetime:
    """The quarter hour (UTC) containing ``moment``."""
    utc = moment.astimezone(UTC)
    return utc.replace(minute=utc.minute - utc.minute % 15, second=0, microsecond=0)


def planned_soc_at(plan: Plan, now: datetime) -> float | None:
    """The plan's SoC at ``now``: linear between the interval's start and end SoC.

    The start SoC of the first interval is the live SoC the run started from
    (``assumptions[soc_start_pct]``), of the later ones the previous interval's end SoC.
    ``None`` when the plan does not cover ``now`` or has no battery.
    """
    ordered = plan.intervals
    for index, interval in enumerate(ordered):
        if not interval.covers(now):
            continue
        end = None if interval.expected is None else interval.expected.soc_pct
        if end is None:
            return None
        if index == 0:
            raw = plan.assumptions.get(SOC_START_KEY)
            start = (
                float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else end
            )
        else:
            previous = ordered[index - 1].expected
            start = end if previous is None or previous.soc_pct is None else previous.soc_pct
        span = (interval.end - interval.start).total_seconds()
        share = 0.0 if span <= 0 else (now - interval.start).total_seconds() / span
        return start + (end - start) * min(1.0, max(0.0, share))
    return None


def planned_net_w(plan: Plan, now: datetime) -> float | None:
    """The planned grid power at ``now`` (+ = import); ``None`` outside the plan."""
    interval = plan.interval_at(now)
    if interval is None or interval.expected is None:
        return None
    return interval.expected.import_w - interval.expected.export_w


def plan_ends_at(plan: Plan) -> datetime | None:
    return plan.intervals[-1].end if plan.intervals else None


# --- the service ----------------------------------------------------------------------------


class PlanningService:
    """Schedules the planner, runs it off the event loop and owns the current plan."""

    def __init__(
        self,
        collect: Collector,
        live: Callable[[], LiveReading],
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        settings: PlanningSettings | None = None,
        store: PlanSnapshotStore | None = None,
        solve: Solver = default_solver,
        thread: ThreadRunner[PlannerRun] = run_in_daemon_thread,
    ) -> None:
        self._collect = collect
        self._live = live
        self._clock = clock
        self._sleep = sleep
        self._settings = settings or PlanningSettings()
        self._store = store
        self._solve = solve
        self._thread = thread
        self._plan: Plan | None = None
        self._plan_stamp: datetime | None = None
        self._pending: dict[Trigger, datetime] = {}
        self._running = False
        self._thread_busy = False
        self._last_trigger: Trigger | None = None
        self._last_started: datetime | None = None
        self._last_finished: datetime | None = None
        self._last_duration_s: float | None = None
        self._last_outcome: RunOutcome | None = None
        self._last_ok: datetime | None = None
        self._last_reason: Reason | None = None
        self._failures = 0
        self._last_slot: datetime | None = None
        self._retry_at: datetime | None = None
        self._first_input_at: datetime | None = None
        self._net_ema: float | None = None
        self._net_ema_at: datetime | None = None
        self._net_over_since: datetime | None = None
        self._listeners: list[PlanListener] = []

    # --- configuration -------------------------------------------------------------------

    @property
    def settings(self) -> PlanningSettings:
        return self._settings

    # --- events --------------------------------------------------------------------------

    def request(self, trigger: Trigger) -> ReplanAck:
        """Ask for a run because of an event; merged and debounced (module doc)."""
        now = self._clock()
        self._pending.setdefault(trigger, now)
        return ReplanAck(earliest_start_at=self._earliest_event_run(now))

    def _earliest_event_run(self, now: datetime) -> datetime:
        if self._last_started is None or self._plan is None:
            return now
        return max(now, self._last_started + timedelta(seconds=self._settings.min_event_gap_s))

    def add_listener(self, listener: PlanListener) -> Callable[[], None]:
        """Call ``listener`` after every new plan; returns the remover."""
        self._listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                log.exception("planning.listener_failed")

    # --- the plan the controller follows ---------------------------------------------------

    def fallback_reason(self, now: datetime | None = None) -> Reason | None:
        """Why the controller does not follow a plan right now (``None`` = it does)."""
        moment = self._clock() if now is None else now
        plan = self._plan
        if plan is None or not plan.intervals:
            return Reason(key=rk.STATUS_NO_PLAN)
        created = plan.created_at or moment
        age_s = (moment - created).total_seconds()
        if age_s > self._settings.stale_after_s:
            return Reason(key=rk.STATUS_STALE, args={"age_h": round(age_s / 3600.0, 1)})
        end = plan_ends_at(plan)
        if end is not None and moment >= end:
            return Reason(key=rk.STATUS_EXPIRED)
        return None

    def active_plan(self, now: datetime | None = None) -> Plan:
        """The plan for the controller's tick: the current one, or ``Plan.default()`` when there
        is none, it is stale or every interval is in the past. One reference read, no locks."""
        plan = self._plan
        if plan is None or self.fallback_reason(now) is not None:
            return Plan.default()
        return plan

    @property
    def plan(self) -> Plan | None:
        """The newest good plan, however old."""
        return self._plan

    # --- read models -----------------------------------------------------------------------

    def summary(self, now: datetime | None = None) -> PlanSummary:
        moment = self._clock() if now is None else now
        plan = self._plan
        fallback = self.fallback_reason(moment)
        stale = fallback is not None and fallback.key == rk.STATUS_STALE
        if plan is None:
            return PlanSummary(
                created_at=None,
                source="default",
                status=None,
                solve_time_s=None,
                intervals=0,
                horizon_end=None,
                stale=False,
                in_use=False,
            )
        return PlanSummary(
            created_at=plan.created_at,
            source=plan.source,
            status=None if plan.stats is None else plan.stats.status,
            solve_time_s=None if plan.stats is None else plan.stats.solve_time_s,
            intervals=len(plan.intervals),
            horizon_end=plan_ends_at(plan),
            stale=stale,
            in_use=fallback is None,
            reasons=list(plan.reasons),
        )

    def view(self, hours: int = 48, now: datetime | None = None) -> PlanView:
        moment = self._clock() if now is None else now
        plan = self._plan
        fallback = self.fallback_reason(moment)
        if plan is None:
            return PlanView(
                generated_at=moment,
                plan=Plan.default(),
                stale=False,
                expired=False,
                in_use=False,
                fallback=fallback,
            )
        horizon = moment + timedelta(hours=hours)
        kept = tuple(i for i in plan.intervals if i.end > moment and i.start < horizon)
        return PlanView(
            generated_at=moment,
            plan=plan.model_copy(update={"intervals": kept}),
            stale=fallback is not None and fallback.key == rk.STATUS_STALE,
            expired=fallback is not None and fallback.key == rk.STATUS_EXPIRED,
            in_use=fallback is None,
            fallback=fallback,
        )

    def next_scheduled_at(self, now: datetime | None = None) -> datetime:
        moment = self._clock() if now is None else now
        offset = timedelta(seconds=self._settings.offset_s)
        slot = floor_quarter(moment - offset)
        if self._last_slot is None or slot > self._last_slot:
            return moment
        return slot + QUARTER + offset

    def status(self, now: datetime | None = None) -> PlanStatusView:
        moment = self._clock() if now is None else now
        pending = [t for t in EVENT_PRIORITY if t in self._pending]
        return PlanStatusView(
            generated_at=moment,
            running=self._running,
            last_trigger=self._last_trigger,
            last_started_at=self._last_started,
            last_finished_at=self._last_finished,
            last_duration_s=self._last_duration_s,
            last_outcome=self._last_outcome,
            last_ok_at=self._last_ok,
            consecutive_failures=self._failures,
            reason=self._last_reason,
            next_scheduled_at=self.next_scheduled_at(moment),
            pending=pending,
            earliest_event_run_at=self._earliest_event_run(moment) if pending else None,
            plan=self.summary(moment),
        )

    # --- scheduling ------------------------------------------------------------------------

    def _due(self, now: datetime) -> Trigger | None:
        """The trigger of the run that should start now, if any (no run is active)."""
        pending = [t for t in EVENT_PRIORITY if t in self._pending]
        if pending and now >= self._earliest_event_run(now):
            return pending[0]
        if self._retry_at is not None and now < self._retry_at:
            return None
        offset = timedelta(seconds=self._settings.offset_s)
        slot = floor_quarter(now - offset)
        if self._last_slot is None:
            return "start" if self._plan is None else "schedule"
        return "schedule" if slot > self._last_slot else None

    async def tick(self) -> bool:
        """One scheduler step: deviation check, then at most one run; ``True`` if a run ran."""
        if self._running:
            return False
        now = self._clock()
        self._check_deviation(now)
        trigger = self._due(now)
        if trigger is None:
            return False
        await self._run(now, trigger)
        return True

    async def run(self) -> None:
        """Poll :meth:`tick` every ``poll_s`` until cancelled; never raises."""
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("planning.tick_crashed")
            await self._sleep(self._settings.poll_s)

    # --- deviation -------------------------------------------------------------------------

    def _check_deviation(self, now: datetime) -> None:
        plan = self._plan
        if plan is None or self.fallback_reason(now) is not None:
            self._net_over_since = None
            return
        reading = self._live()
        if reading.soc_pct is not None:
            planned = planned_soc_at(plan, now)
            if planned is not None and abs(reading.soc_pct - planned) > (
                self._settings.soc_deviation_pct
            ):
                self.request("deviation")
        net = planned_net_w(plan, now)
        if net is None or reading.grid_w is None:
            self._net_over_since = None
            return
        self._update_net_deviation(now, reading.grid_w - net)

    def _update_net_deviation(self, now: datetime, difference_w: float) -> None:
        last = self._net_ema_at
        if self._net_ema is None or last is None or now <= last:
            self._net_ema = difference_w
        else:
            alpha = 1.0 - math.exp(-(now - last).total_seconds() / NET_EMA_S)
            self._net_ema += alpha * (difference_w - self._net_ema)
        self._net_ema_at = now
        if abs(self._net_ema) <= self._settings.net_deviation_w:
            self._net_over_since = None
            return
        if self._net_over_since is None:
            self._net_over_since = now
        elif (now - self._net_over_since).total_seconds() >= self._settings.net_deviation_s:
            self._net_over_since = now  # re-arm: the next event comes after another period
            self.request("deviation")

    # --- one run ---------------------------------------------------------------------------

    def _waiting(self, now: datetime) -> bool:
        """The first plan is still within its grace period for prices and forecasts."""
        first = self._first_input_at
        return (
            first is not None and (now - first).total_seconds() < self._settings.first_run_grace_s
        )

    async def _run(self, now: datetime, trigger: Trigger) -> None:  # noqa: PLR0915
        s = self._settings
        self._running = True
        self._pending.clear()  # this run's inputs include everything requested so far
        self._last_started = now
        self._last_trigger = trigger
        began = time.perf_counter()
        outcome: RunOutcome = "failed"
        reason: Reason | None = None
        new_plan: Plan | None = None
        try:
            warm = self._plan if self.fallback_reason(now) is None else None
            snapshot = await self._collect(now, warm)
            inputs = snapshot.inputs
            if inputs is None:
                outcome = "skipped"
                reason = snapshot.reason or Reason(key=rk.STATUS_NO_INPUTS)
            else:
                if self._first_input_at is None:
                    self._first_input_at = now
                if not snapshot.complete and self._plan is None and self._waiting(now):
                    outcome = "skipped"
                    reason = Reason(key=rk.STATUS_WAITING, args={"grace_s": s.first_run_grace_s})
                elif self._thread_busy:
                    outcome = "skipped"
                    reason = Reason(key=rk.STATUS_RUN_STUCK)
                else:
                    new_plan = await self._solve_in_thread(inputs, warm)
                    outcome = "ok"
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            reason = Reason(key=rk.STATUS_RUN_TIMEOUT, args={"limit_s": s.run_timeout_s})
            log.error("planning.run_timeout", limit_s=s.run_timeout_s)
        except Exception as exc:
            reason = Reason(
                key=rk.STATUS_RUN_FAILED, args={"error": f"{type(exc).__name__}: {exc}"[:160]}
            )
            log.exception("planning.run_failed")
        finally:
            self._running = False
            self._last_duration_s = round(time.perf_counter() - began, 3)
        finished = self._clock()
        self._last_finished = finished
        self._last_outcome = outcome
        self._last_reason = reason
        if outcome == "ok":
            assert new_plan is not None
            self._failures = 0
            self._retry_at = None
            self._last_ok = finished
            self._last_slot = floor_quarter(now - timedelta(seconds=s.offset_s))
            self._plan = new_plan  # the atomic hand-over: the controller reads this reference
            self._plan_stamp = finished
            self._net_ema = None
            self._net_over_since = None
            self._notify()
            await self._save(new_plan)
        elif outcome == "skipped":
            self._retry_at = finished + timedelta(seconds=s.retry_skipped_s)
        else:
            self._failures += 1
            backoff = min(900.0, 60.0 * 2 ** (self._failures - 1))
            self._retry_at = finished + timedelta(seconds=backoff)
            # A failed cadence run is not retried before the back-off, but the quarter is spent.
            self._last_slot = floor_quarter(now - timedelta(seconds=s.offset_s))

    async def _solve_in_thread(self, inputs: PlannerInputs, warm: Plan | None) -> Plan:
        s = self._settings
        self._thread_busy = True

        def work() -> PlannerRun:
            try:
                return self._solve(inputs, s.budget_s, s.mip_gap, warm)
            finally:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(self._thread_done)

        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(self._thread(work), timeout=s.run_timeout_s)
        except asyncio.CancelledError:
            # The thread (if any) clears the flag itself when it ends.
            raise
        built = result.plan
        if not built.intervals:
            msg = "the planner returned no intervals"
            raise ValueError(msg)
        soc = None if inputs.battery is None else round(inputs.battery.soc_pct, 2)
        assumptions = dict(built.assumptions)
        if soc is not None:
            assumptions[SOC_START_KEY] = soc
        assumptions["trigger"] = self._last_trigger or "schedule"
        return built.model_copy(update={"assumptions": assumptions})

    def _thread_done(self) -> None:
        self._thread_busy = False

    async def _save(self, plan: Plan) -> None:
        if self._store is None:
            return
        try:
            await self._store.save(
                plan.model_dump(mode="json"),
                source=plan.source,
                schema_version=plan.schema_version,
                created_at=plan.created_at,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("planning.snapshot_failed")


__all__ = [
    "EVENT_PRIORITY",
    "Collector",
    "LiveReading",
    "PlanListener",
    "PlanningService",
    "PlanningSettings",
    "PlanningSnapshot",
    "Solver",
    "default_solver",
    "floor_quarter",
    "plan_ends_at",
    "planned_net_w",
    "planned_soc_at",
    "run_in_daemon_thread",
]
