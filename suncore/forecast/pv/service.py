# SPDX-License-Identifier: Apache-2.0
"""The PV forecast service: refresh, keep the last good data, store, expose (Phase 4 WP5).

* **Refresh** every 30 minutes and whenever one of the sources' entities changes (the runtime
  calls :meth:`PvForecastService.on_entities`; at most one refresh per ``min_refresh_gap_s``).
* **Keep the last good data.** A fetch that yields nothing (an unavailable sensor, an empty or
  all-``None`` series, data that lies entirely in the past while the old data still reaches into
  the future, an exception) never replaces what is held; only the status changes. Slots a fetch
  lacks are taken from the previous good fetch for up to ``gap_fill_max_age`` after the last
  *complete* fetch (``filled_slots``), so one flapping day sensor does not cost the plan its
  tomorrow.
* **Store** the fetched forecast in ``timeseries.db`` (kind ``pv``, the next ``horizon`` from the
  current slot) at most once an hour, or earlier - but not within ``min_store_gap_s`` - when it
  changed materially against the last stored issue (decision from the review of PR 4-B: a forecast
  refresh every few minutes would write 18 000 rows a day per source). What is stored is what
  the source said, never the gap-filled series.
* **Expose** per source the latest series with its freshness, and the *blended* forecast. The
  blend is a **placeholder until part B**: the P50 of the first configured source that has data.

Time and sleeping are injected; the service never reads the wall clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal, Protocol

import structlog

from suncore.core.forecast import ForecastSource
from suncore.core.series import (
    PLANNING_STEP,
    QuantileSeries,
    TimeRange,
    TimeSeries,
    floor_to_step,
)
from suncore.forecast.pv.adapter import FetchReport, FetchStatus
from suncore.forecast.pv.parsing import crop, has_known
from suncore.forecast.views import (
    MAX_VIEW_HOURS,
    BlendedView,
    PvForecastView,
    PvSeriesView,
    PvSourceView,
)

log = structlog.get_logger(__name__)

REFRESH_INTERVAL_S: Final = 1800.0
STORE_INTERVAL_S: Final = 3600.0
MIN_STORE_GAP_S: Final = 900.0
MIN_REFRESH_GAP_S: Final = 30.0
HORIZON: Final = timedelta(hours=48)
GAP_FILL_MAX_AGE: Final = timedelta(hours=6)
MATERIAL_CHANGE: Final = 0.15
"""Relative L1 difference of the next 24 h that makes a forecast worth an extra stored issue."""
MATERIAL_REFERENCE_WH: Final = 1000.0
"""Floor of the denominator, so a night-time forecast of a few Wh never counts as a change."""
STALE_AFTER_REFRESHES: Final = 3


class ReportingSource(ForecastSource, Protocol):
    """A forecast source that tells what it found and which entities it watches."""

    @property
    def last_report(self) -> FetchReport: ...

    @property
    def watched_entities(self) -> frozenset[str]: ...


class ForecastStore(Protocol):
    """The part of ``TimeseriesStore`` the service writes to."""

    async def store_forecast(
        self,
        source_id: str,
        kind: Literal["pv", "load", "price"],
        issued_at: datetime,
        forecast: QuantileSeries | TimeSeries,
    ) -> int: ...


@dataclass(slots=True)
class SourceSnapshot:
    """What the service knows about one source."""

    source_id: str
    series: QuantileSeries | None = None
    fetched_at: datetime | None = None
    complete_at: datetime | None = None
    """Last fetch that needed no gap filling (gap filling is allowed for a while after it)."""
    attempted_at: datetime | None = None
    status: str = FetchStatus.NO_DATA.value
    report: FetchReport | None = None
    filled_slots: int = 0
    error: str | None = None
    last_stored_at: datetime | None = None
    last_stored: QuantileSeries | None = field(default=None, repr=False)

    def age_s(self, now: datetime) -> float | None:
        return None if self.fetched_at is None else (now - self.fetched_at).total_seconds()


def materially_changed(old: QuantileSeries, new: QuantileSeries, now: datetime) -> bool:
    """Whether the next 24 h of ``new`` differ from ``old`` by more than ``MATERIAL_CHANGE``.

    Compares the P50 of the slots both know, as the sum of absolute energy differences relative to
    the energy of the older issue (at least ``MATERIAL_REFERENCE_WH``).
    """
    horizon_end = now + timedelta(hours=24)
    a = crop(old, now, horizon_end)
    b = crop(new, now, horizon_end)
    if a.step != b.step:
        return True
    diff = 0.0
    base = 0.0
    hours = a.step / timedelta(hours=1)
    for begin, _, old_value in a.p50.slots():
        offset = (begin - b.start) / b.step
        if offset != int(offset) or not 0 <= int(offset) < len(b):
            continue
        new_value = b.p50.values[int(offset)]
        if old_value is None or new_value is None:
            continue
        diff += abs(new_value - old_value) * hours
        base += old_value * hours
    return diff > MATERIAL_CHANGE * max(base, MATERIAL_REFERENCE_WH)


def fill_from_previous(
    new: QuantileSeries, previous: QuantileSeries, now: datetime
) -> tuple[QuantileSeries, int]:
    """``new`` with its unknown future slots taken from ``previous`` (same grid only).

    Returns the series and the number of slots filled. A slot that ended before ``now`` is not
    filled. The result may extend ``new`` to where ``previous`` reaches.
    """
    step = new.step
    if previous.step != step or (new.start - previous.start) % step != timedelta(0):
        return new, 0
    shift = (new.start - previous.start) // step  # index in previous of new's slot 0
    last_prev = len(previous) - shift  # new-index just past previous's end
    total = max(len(new), last_prev)
    quantiles = ([*new.p10.values], [*new.p50.values], [*new.p90.values])
    for column in quantiles:
        column.extend([None] * (total - len(column)))
    filled = 0
    longest = len(new)
    for j in range(total):
        slot_end = new.start + step * (j + 1)
        k = j + shift
        if (
            quantiles[1][j] is not None
            or slot_end <= now
            or not 0 <= k < len(previous)
            or previous.p50.values[k] is None
        ):
            continue
        quantiles[0][j] = previous.p10.values[k]
        quantiles[1][j] = previous.p50.values[k]
        quantiles[2][j] = previous.p90.values[k]
        filled += 1
        longest = max(longest, j + 1)
    if filled == 0:
        return new, 0

    def part(values: list[float | None]) -> TimeSeries:
        return TimeSeries(start=new.start, step=step, values=tuple(values[:longest]), unit=new.unit)

    return QuantileSeries(
        p10=part(quantiles[0]), p50=part(quantiles[1]), p90=part(quantiles[2])
    ), filled


def _future_known(series: QuantileSeries, now: datetime) -> bool:
    return any(end > now and v is not None for _, end, v in series.p50.slots())


class PvForecastService:
    """Refreshes the sources, keeps their last good series and stores them."""

    def __init__(
        self,
        sources: Sequence[ReportingSource],
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        store: ForecastStore | None = None,
        refresh_interval_s: float = REFRESH_INTERVAL_S,
        store_interval_s: float = STORE_INTERVAL_S,
        min_store_gap_s: float = MIN_STORE_GAP_S,
        min_refresh_gap_s: float = MIN_REFRESH_GAP_S,
        horizon: timedelta = HORIZON,
        gap_fill_max_age: timedelta = GAP_FILL_MAX_AGE,
    ) -> None:
        self._sources = list(sources)
        self._clock = clock
        self._sleep = sleep
        self._store = store
        self._refresh_interval_s = refresh_interval_s
        self._store_interval = timedelta(seconds=store_interval_s)
        self._min_store_gap = timedelta(seconds=min_store_gap_s)
        self._min_refresh_gap_s = min_refresh_gap_s
        self._horizon = horizon
        self._gap_fill_max_age = gap_fill_max_age
        self._state = {s.source_id: SourceSnapshot(s.source_id) for s in self._sources}
        self._watched = frozenset().union(*(s.watched_entities for s in self._sources))
        self._woken = False
        self._sleeper: asyncio.Future[None] | None = None
        self._lock = asyncio.Lock()
        self._last_refresh_at: datetime | None = None

    # --- inputs ---------------------------------------------------------------------

    @property
    def source_ids(self) -> list[str]:
        return [s.source_id for s in self._sources]

    @property
    def watched_entities(self) -> frozenset[str]:
        return self._watched

    def on_entities(self, entity_ids: Iterable[str]) -> None:
        """Request a refresh if one of the sources' entities changed (cheap, sync)."""
        if not self._watched.isdisjoint(entity_ids) and not self._woken:
            self._woken = True
            if self._sleeper is not None:
                self._sleeper.cancel()

    # --- refresh --------------------------------------------------------------------

    async def refresh(self) -> list[SourceSnapshot]:
        """Fetch every source once; failures are logged and never raised."""
        async with self._lock:
            now = self._clock()
            self._last_refresh_at = now
            begin = floor_to_step(now, PLANNING_STEP)
            window = TimeRange(start=begin, end=begin + self._horizon)
            for source in self._sources:
                await self._refresh_one(source, window, now)
            return list(self._state.values())

    async def _refresh_one(self, source: ReportingSource, window: TimeRange, now: datetime) -> None:
        state = self._state[source.source_id]
        state.attempted_at = now
        try:
            fetched = await source.fetch(window)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("forecast.pv.fetch_failed", source=source.source_id, error=repr(exc))
            state.status = FetchStatus.ERROR.value
            state.error = type(exc).__name__
            return
        report = source.last_report
        state.report = report
        state.error = None
        if not has_known(fetched):
            state.status = report.status.value
            return
        if (
            state.series is not None
            and _future_known(state.series, now)
            and not _future_known(fetched, now)
        ):
            log.warning("forecast.pv.fetch_in_the_past", source=source.source_id)
            state.status = FetchStatus.NO_DATA.value
            return
        effective, filled = fetched, 0
        if (
            state.series is not None
            and state.complete_at is not None
            and now - state.complete_at <= self._gap_fill_max_age
        ):
            effective, filled = fill_from_previous(fetched, state.series, now)
        state.series = effective
        state.filled_slots = filled
        state.fetched_at = now
        state.status = report.status.value
        if filled == 0:
            state.complete_at = now
        await self._maybe_store(source.source_id, state, fetched, now)

    async def _maybe_store(
        self, source_id: str, state: SourceSnapshot, fetched: QuantileSeries, now: datetime
    ) -> None:
        if self._store is None or not self._store_due(state, fetched, now):
            return
        begin = floor_to_step(now, PLANNING_STEP)
        issue = crop(fetched, begin, begin + self._horizon)
        if not has_known(issue):
            return
        try:
            await self._store.store_forecast(source_id, "pv", now, issue)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.pv.store_failed", source=source_id)
            return
        state.last_stored_at = now
        state.last_stored = issue

    def _store_due(self, state: SourceSnapshot, fetched: QuantileSeries, now: datetime) -> bool:
        if state.last_stored_at is None or state.last_stored is None:
            return True
        age = now - state.last_stored_at
        if age >= self._store_interval:
            return True
        return age >= self._min_store_gap and materially_changed(state.last_stored, fetched, now)

    # --- loop -----------------------------------------------------------------------

    async def run(self) -> None:
        """Refresh now, then every interval or when a watched entity changes."""
        while True:
            await self.refresh()
            await self._wait()

    async def _wait(self) -> None:
        """Sleep for the refresh interval; an entity change ends the sleep early (debounced)."""
        self._woken = False
        self._sleeper = asyncio.ensure_future(self._sleep(self._refresh_interval_s))
        try:
            await self._sleeper
        except asyncio.CancelledError:
            if not self._woken:
                raise  # the service itself is being stopped
        finally:
            self._sleeper = None
        if self._woken:
            await self._sleep(self._min_refresh_gap_s)  # debounce a burst of entity updates

    # --- outputs --------------------------------------------------------------------

    def snapshot(self, source_id: str) -> SourceSnapshot | None:
        return self._state.get(source_id)

    def snapshots(self) -> list[SourceSnapshot]:
        return [self._state[s.source_id] for s in self._sources]

    def blended(self, now: datetime | None = None) -> tuple[str, QuantileSeries] | None:
        """The forecast the planner would use - **a placeholder until part B adds blending**:
        the series of the first configured source that has data reaching into the future."""
        moment = self._clock() if now is None else now
        for snap in self.snapshots():
            if snap.series is not None and _future_known(snap.series, moment):
                return snap.source_id, snap.series
        return None

    def is_stale(self, snap: SourceSnapshot, now: datetime) -> bool:
        age = snap.age_s(now)
        return age is None or age > STALE_AFTER_REFRESHES * self._refresh_interval_s

    def view(self, hours: int = 48) -> PvForecastView:
        """The API read model: every source cropped to ``hours`` from the current slot."""
        now = self._clock()
        hours = min(max(hours, 1), MAX_VIEW_HOURS)
        begin = floor_to_step(now, PLANNING_STEP)
        end = begin + timedelta(hours=hours)
        sources = [self._source_view(snap, begin, end, now) for snap in self.snapshots()]
        chosen = self.blended(now)
        blended = None
        if chosen is not None:
            source_id, series = chosen
            cut = crop(series, begin, end)
            blended = BlendedView(
                method="first_source_p50",
                source_id=source_id,
                start=cut.start,
                step_s=int(cut.step.total_seconds()),
                p50_w=list(cut.p50.values),
            )
        return PvForecastView(generated_at=now, hours=hours, sources=sources, blended=blended)

    def _source_view(
        self, snap: SourceSnapshot, begin: datetime, end: datetime, now: datetime
    ) -> PvSourceView:
        series = None
        if snap.series is not None:
            cut = crop(snap.series, begin, end)
            series = PvSeriesView(
                start=cut.start,
                step_s=int(cut.step.total_seconds()),
                p10_w=list(cut.p10.values),
                p50_w=list(cut.p50.values),
                p90_w=list(cut.p90.values),
            )
        return PvSourceView(
            source_id=snap.source_id,
            status=snap.status,
            fetched_at=snap.fetched_at,
            age_s=snap.age_s(now),
            stale=self.is_stale(snap, now),
            filled_slots=snap.filled_slots,
            series=series,
        )


__all__ = [
    "GAP_FILL_MAX_AGE",
    "HORIZON",
    "MATERIAL_CHANGE",
    "MIN_STORE_GAP_S",
    "REFRESH_INTERVAL_S",
    "STORE_INTERVAL_S",
    "ForecastStore",
    "PvForecastService",
    "ReportingSource",
    "SourceSnapshot",
    "fill_from_previous",
    "materially_changed",
]
