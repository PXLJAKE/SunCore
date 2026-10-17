# SPDX-License-Identifier: Apache-2.0
"""Batched writer of the controller's site measurements into ``timeseries.db`` (Phase 4 WP3).

The controller calls :meth:`MeasurementRecorder.record` once per tick; it only appends to an
in-memory list. :meth:`flush` (every 60 s from the runtime, and once at shutdown) writes the
list in one transaction. A database error is logged and the batch is kept for the next flush
(bounded: the oldest rows are dropped first), so a broken disk can neither crash nor slow the
control loop and memory stays bounded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Final

import structlog

from suncore.storage.timeseries import MeasurementRow, TimeseriesStore

log = structlog.get_logger(__name__)

FLUSH_INTERVAL_S: Final = 60.0
MAX_BUFFERED_ROWS: Final = 50_000
"""About 7 hours of a 5 s tick with 10 keys (2 rows/s): the cap for a database that stays broken."""


class MeasurementRecorder:
    """Collects ``(key, ts, value)`` rows and writes them in batches."""

    def __init__(
        self, store: TimeseriesStore | None, *, max_buffered: int = MAX_BUFFERED_ROWS
    ) -> None:
        self._store = store
        self._max = max_buffered
        self._buffer: list[MeasurementRow] = []
        self._lock = asyncio.Lock()
        self.dropped = 0
        """Rows discarded because the buffer was full (a database that stays unavailable)."""

    @property
    def enabled(self) -> bool:
        return self._store is not None

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def record(self, ts: datetime, values: Mapping[str, float | None]) -> None:
        """Queue one tick's values; a ``None`` value is a gap and is not stored."""
        if self._store is None:
            return
        self._buffer.extend((key, ts, value) for key, value in values.items() if value is not None)
        overflow = len(self._buffer) - self._max
        if overflow > 0:
            del self._buffer[:overflow]
            self.dropped += overflow

    async def flush(self) -> int:
        """Write the queued rows; returns how many were written (0 on error or nothing)."""
        if self._store is None:
            return 0
        async with self._lock:
            batch, self._buffer = self._buffer, []
            if not batch:
                return 0
            try:
                return await self._store.append_measurements(batch)
            except BaseException as exc:
                # Also on cancellation (shutdown): the batch goes back, nothing is lost silently.
                combined = batch + self._buffer
                self.dropped += max(0, len(combined) - self._max)
                self._buffer = combined[-self._max :]
                if not isinstance(exc, Exception):
                    raise
                log.exception("timeseries.flush_failed", rows=len(batch))
                return 0
