# SPDX-License-Identifier: Apache-2.0
"""Plain types of the EV, program and grid routes (PR 6-M), importable without the API models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class NotAnEvError(LookupError):
    """The device is not an EV charger or an evcc loadpoint."""


class EvTargetError(ValueError):
    """The EV target time is in the past or too far ahead; ``key`` is the catalogue key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class FlexStoreError(RuntimeError):
    """The runtime state could not be persisted (``api.error.flex_store_failed``)."""


@dataclass(frozen=True, slots=True)
class GridStatus:
    """The grid situation of the site (``core.grid``) and this month's peak."""

    state: str
    """``connected``, ``island`` or ``unknown``."""
    para14a: str
    """``inactive``, ``active`` or ``unknown``."""
    month: str
    month_peak_w: float | None
    peak_limit_w: float | None
    """The import limit that peak shaving derives from the month's peak (``None`` = off)."""


@dataclass(frozen=True, slots=True)
class GridEventRow:
    ts: datetime
    kind: str
    value_w: float | None


@dataclass(frozen=True, slots=True)
class MonthPeakRow:
    month: str
    peak_w: float
    slot: datetime


@dataclass(frozen=True, slots=True)
class GridEvents:
    events: tuple[GridEventRow, ...]
    peaks: tuple[MonthPeakRow, ...]


__all__ = ["GridEventRow", "GridEvents", "GridStatus", "MonthPeakRow", "NotAnEvError"]
