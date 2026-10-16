# SPDX-License-Identifier: Apache-2.0
"""Forecast and price source contracts (Phase 4 WP2).

Adapters in ``suncore/forecast`` and ``suncore/tariff`` implement these protocols; the
planner only sees the resulting series. A source returns what it has for the requested
window - it may be shorter (``None`` values or a shorter series), never padded with
invented data.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum, unique
from typing import Annotated, Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from suncore.core.series import PriceSeries, QuantileSeries, TimeRange

SourceId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_.:-]+$")]
"""Stable id of a forecast or price source, usually its template id."""


@runtime_checkable
class ForecastSource(Protocol):
    """A PV (or load) forecast source; values in W (mean power per interval)."""

    @property
    def source_id(self) -> str: ...

    async def fetch(self, window: TimeRange) -> QuantileSeries:
        """The forecast for ``window`` (P10/P50/P90; equal quantiles if the source has none)."""
        ...


@runtime_checkable
class PriceSource(Protocol):
    """An import (and optionally export) price source; currency/kWh per interval."""

    @property
    def source_id(self) -> str: ...

    async def fetch(self, window: TimeRange) -> PriceSeries:
        """Prices for ``window`` as far as the source publishes them."""
        ...


@unique
class HorizonBucket(StrEnum):
    """Lead-time bucket the accuracy of a forecast is tracked in."""

    H0_6 = "0_6h"
    H6_24 = "6_24h"
    H24_48 = "24_48h"
    H48_PLUS = "48h_plus"

    @classmethod
    def of(cls, lead: timedelta) -> HorizonBucket:
        """Bucket of a lead time (issue time to interval start); negative counts as 0."""
        hours = lead / timedelta(hours=1)
        if hours < 6:
            return cls.H0_6
        if hours < 24:
            return cls.H6_24
        if hours < 48:
            return cls.H24_48
        return cls.H48_PLUS


class AccuracyRecord(BaseModel):
    """Measured error of one source in one horizon bucket (WP5 bookkeeping).

    ``mae_w`` is the mean absolute error and ``bias_w`` the mean signed error
    (forecast - actual, so + = over-forecast) of P50 over ``n`` intervals.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: SourceId
    horizon: HorizonBucket
    mae_w: float = Field(ge=0, allow_inf_nan=False)
    bias_w: float = Field(allow_inf_nan=False)
    n: int = Field(ge=0)


ACCURACY_MIN_SAMPLES: Final = 96
"""Intervals (one day of quarter hours) before a record counts for blending weights."""


__all__ = [
    "ACCURACY_MIN_SAMPLES",
    "AccuracyRecord",
    "ForecastSource",
    "HorizonBucket",
    "PriceSource",
    "SourceId",
]
