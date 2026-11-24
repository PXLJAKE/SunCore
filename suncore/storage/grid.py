# SPDX-License-Identifier: Apache-2.0
"""``grid_event`` and ``grid_peak`` (``timeseries.db`` schema 3, Phase 6 PR 6-B, ADR-0018).

Grid events (island and §14a start/end, new monthly peaks, limit breaches) are kept
:data:`GRID_EVENT_KEEP`; monthly peaks for ever. The writers arrive with PR 6-F; the tables and
the repository exist from 6-B so the migration ships once.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from suncore.core.decisions import ReasonArg
from suncore.core.series import TimeRange
from suncore.storage.db import Database

GRID_EVENT_KEEP: Final = timedelta(days=400)

GridEventKind = Literal[
    "island_start",
    "island_end",
    "para14a_start",
    "para14a_end",
    "para14a_unknown",
    "peak_new",
    "import_limit_exceeded",
    "export_limit_exceeded",
]


@dataclass(frozen=True, slots=True)
class GridEvent:
    ts: datetime
    kind: GridEventKind
    value_w: float | None = None
    detail: Mapping[str, ReasonArg] | None = None


@dataclass(frozen=True, slots=True)
class MonthPeak:
    month: str
    """Local ``YYYY-MM`` (``core.grid.month_key``)."""
    peak_w: float
    slot: datetime
    """Start of the 15-minute slot that set the peak."""


def _epoch(moment: datetime) -> int:
    if moment.tzinfo is None:
        msg = "timestamps must be timezone-aware"
        raise ValueError(msg)
    return math.floor(moment.timestamp())


def _from_epoch(seconds: object) -> datetime:
    return datetime.fromtimestamp(int(str(seconds)), tz=UTC)


class GridStore:
    """Repository over ``grid_event`` and ``grid_peak``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_event(self, event: GridEvent) -> None:
        if event.value_w is not None and not math.isfinite(event.value_w):
            msg = "value_w must be finite"
            raise ValueError(msg)
        detail = (
            None
            if event.detail is None
            else json.dumps(dict(event.detail), allow_nan=False, sort_keys=True)
        )
        await self._db.execute(
            "INSERT INTO grid_event (ts, kind, value_w, detail) VALUES (?, ?, ?, ?)",
            (_epoch(event.ts), event.kind, event.value_w, detail),
        )

    async def events(self, window: TimeRange) -> list[GridEvent]:
        rows = await self._db.fetchall(
            "SELECT ts, kind, value_w, detail FROM grid_event WHERE ts >= ? AND ts < ? "
            "ORDER BY ts, id",
            (_epoch(window.start), _epoch(window.end)),
        )
        return [
            GridEvent(
                ts=_from_epoch(ts),
                kind=str(kind),  # type: ignore[arg-type]  # CHECK constraint guarantees it
                value_w=None if value is None else float(str(value)),
                detail=None if detail is None else json.loads(str(detail)),
            )
            for ts, kind, value, detail in rows
        ]

    async def record_quarter(
        self, month: str, slot: datetime, mean_import_w: float, now: datetime
    ) -> bool:
        """Raise the month's peak if ``mean_import_w`` is higher; returns whether it did."""
        if not math.isfinite(mean_import_w) or mean_import_w < 0:
            return False
        changed = await self._db.execute(
            "INSERT INTO grid_peak (month, peak_w, slot_ts, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(month) DO UPDATE SET peak_w = excluded.peak_w, "
            "slot_ts = excluded.slot_ts, updated_at = excluded.updated_at "
            "WHERE excluded.peak_w > grid_peak.peak_w",
            (month, mean_import_w, _epoch(slot), _epoch(now)),
        )
        return changed > 0

    async def peak(self, month: str) -> MonthPeak | None:
        row = await self._db.fetchone(
            "SELECT month, peak_w, slot_ts FROM grid_peak WHERE month = ?", (month,)
        )
        if row is None:
            return None
        return MonthPeak(str(row[0]), float(str(row[1])), _from_epoch(row[2]))

    async def peaks(self) -> list[MonthPeak]:
        rows = await self._db.fetchall(
            "SELECT month, peak_w, slot_ts FROM grid_peak ORDER BY month"
        )
        return [MonthPeak(str(m), float(str(p)), _from_epoch(s)) for m, p, s in rows]

    async def apply_retention(self, now: datetime) -> int:
        """Delete grid events older than :data:`GRID_EVENT_KEEP` (peaks are kept)."""
        cut = _epoch(now - GRID_EVENT_KEEP)
        return await self._db.execute("DELETE FROM grid_event WHERE ts < ?", (cut,))


__all__ = ["GRID_EVENT_KEEP", "GridEvent", "GridEventKind", "GridStore", "MonthPeak"]
