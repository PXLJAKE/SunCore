# SPDX-License-Identifier: Apache-2.0
"""Grid bookkeeping of the control stack: monthly peak and grid events (plan WP6, PR 6-F).

:class:`GridMonitor` is fed once per controller tick with the site flows and the tick's
:class:`~suncore.controller.grid.GridInputs`. It keeps

* the **month's peak**: the highest 15-minute mean import of the local month (ADR-0018
  decision 2; the same tick values the recorder aggregates into ``measurement_15m``, summed here
  without reading the database back in the tick). A quarter counts only when at least half of
  it was measured (``OK`` grid readings); missing time counts as no import. Peaks reset at the
  local month boundary (``core.grid.month_key``);
* **grid events**: island start/end, §14a start/end/unknown, a new monthly peak and the start
  of an import above the fuse limit - for ``grid_event`` (``timeseries.db``) and, from 6-C/6-E,
  MQTT and notifications.

It never writes to a device and never blocks the tick: the persistence is an awaitable the
stack runs as a background task; a failure is logged and swallowed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Final

import structlog

from suncore.controller.grid import GridInputs
from suncore.core.balance import FlowSnapshot
from suncore.core.grid import GridState, Para14aStatus, month_key, updated_month_peak_w
from suncore.core.signals import Quality
from suncore.storage.grid import GridEvent, GridStore

log = structlog.get_logger(__name__)

QUARTER_S: Final = 900
MIN_COVERAGE: Final = 0.5
"""A quarter with less measured time than this share does not set a peak."""
MAX_GAP_S: Final = 60.0
"""A gap between two ticks longer than this is not integrated (missing time)."""


@dataclass(slots=True)
class _Quarter:
    start: datetime
    import_ws: float = 0.0
    covered_s: float = 0.0


@dataclass(slots=True)
class GridMonitor:
    """Monthly peak and grid events, fed per tick (time injected, no I/O of its own)."""

    tz: tzinfo
    store: GridStore | None = None
    _month: str | None = None
    _peak_w: float | None = None
    _quarter: _Quarter | None = None
    _last_ts: datetime | None = None
    _last_import_w: float | None = None
    _grid_state: GridState | None = None
    _para14a: Para14aStatus | None = None
    _above_fuse: bool = False
    pending: list[GridEvent] = field(default_factory=list)
    """Events not yet persisted (drained by :meth:`flush`)."""
    pending_peaks: list[tuple[str, datetime, float, datetime]] = field(default_factory=list)

    # --- peak ----------------------------------------------------------------------------------

    def month_peak_w(self, now: datetime) -> float | None:
        """This local month's peak so far (``None`` = none recorded this month)."""
        if self._month != month_key(now, self.tz):
            return None
        return self._peak_w

    def seed(self, month: str, peak_w: float) -> None:
        """Start from a stored peak (``grid_peak``); a higher in-memory one wins."""
        if self._month != month:
            self._month, self._peak_w = month, peak_w
        else:
            self._peak_w = max(self._peak_w or 0.0, peak_w)

    async def load(self, now: datetime) -> None:
        """Seed this month's peak from the store (no-op without one)."""
        if self.store is None:
            return
        month = month_key(now, self.tz)
        try:
            stored = await self.store.peak(month)
        except Exception:
            log.exception("grid.peak_load_failed")
            return
        if stored is not None:
            self.seed(month, stored.peak_w)

    def _integrate(self, now: datetime, import_w: float | None) -> None:
        slot = datetime.fromtimestamp(
            math.floor(now.timestamp() / QUARTER_S) * QUARTER_S, tz=now.tzinfo
        )
        if self._last_ts is not None and self._last_import_w is not None:
            gap = (now - self._last_ts).total_seconds()
            if 0 < gap <= MAX_GAP_S and self._quarter is not None:
                self._quarter.import_ws += self._last_import_w * gap
                self._quarter.covered_s += gap
        if self._quarter is None:
            self._quarter = _Quarter(slot)
        elif slot != self._quarter.start:
            self._close(self._quarter, now)
            self._quarter = _Quarter(slot)
        self._last_ts = now
        self._last_import_w = import_w

    def _close(self, quarter: _Quarter, now: datetime) -> None:
        if quarter.covered_s < MIN_COVERAGE * QUARTER_S:
            return
        mean = quarter.import_ws / QUARTER_S
        month = month_key(quarter.start, self.tz)
        if self._month != month:
            self._month, self._peak_w = month, None
        before = self._peak_w
        self._peak_w = updated_month_peak_w(before, mean)
        if before is None or self._peak_w > before:
            self.pending_peaks.append((month, quarter.start, mean, now))
            if before is not None:
                self.pending.append(GridEvent(now, "peak_new", round(mean, 1)))

    # --- events ----------------------------------------------------------------------------------

    def observe(self, now: datetime, flows: FlowSnapshot | None, grid: GridInputs) -> None:
        """One tick: integrate the import, detect the grid events."""
        reading = None if flows is None else flows.grid_w
        import_w: float | None = None
        if reading is not None and reading.value is not None and reading.quality is Quality.OK:
            import_w = max(0.0, reading.value)
        self._integrate(now, import_w)
        state = grid.grid_state
        if state is GridState.ISLAND and self._grid_state is not GridState.ISLAND:
            self.pending.append(GridEvent(now, "island_start"))
        elif (
            self._grid_state is GridState.ISLAND
            and state is not GridState.ISLAND
            and state is not GridState.UNKNOWN
        ):
            self.pending.append(GridEvent(now, "island_end"))
        if state is not GridState.UNKNOWN or self._grid_state is not GridState.ISLAND:
            self._grid_state = state  # unknown after island: wait for a definite state
        status = grid.para14a.status
        if status is not self._para14a:
            if status is Para14aStatus.ACTIVE:
                self.pending.append(GridEvent(now, "para14a_start", grid.para14a.budget_w))
            elif status is Para14aStatus.UNKNOWN:
                self.pending.append(GridEvent(now, "para14a_unknown"))
            elif self._para14a is not None:
                self.pending.append(GridEvent(now, "para14a_end"))
            self._para14a = status
        fuse = grid.fuse_limit_w
        above = fuse is not None and import_w is not None and import_w > fuse
        if above and not self._above_fuse:
            self.pending.append(GridEvent(now, "import_limit_exceeded", round(import_w or 0.0)))
        self._above_fuse = above

    async def flush(self) -> None:
        """Persist the pending events and peaks (no-op without a store; errors are logged)."""
        events, peaks = self.pending, self.pending_peaks
        self.pending, self.pending_peaks = [], []
        if self.store is None:
            return
        try:
            for month, slot, mean, at in peaks:
                await self.store.record_quarter(month, slot, mean, at)
            for event in events:
                await self.store.record_event(event)
        except Exception:
            log.exception("grid.persist_failed")


def quarter_start(now: datetime) -> datetime:
    """Start of the 15-minute slot containing ``now`` (for tests and the API)."""
    return now - timedelta(seconds=now.timestamp() % QUARTER_S)


__all__ = ["GridMonitor", "quarter_start"]
