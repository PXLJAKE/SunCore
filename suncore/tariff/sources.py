# SPDX-License-Identifier: Apache-2.0
"""Price sources behind one small protocol: fixed, HT/NT and the template-driven adapters.

A :class:`SegmentSource` returns what it knows for a window as regular segments (a source may
mix 15- and 60-minute slots); :func:`finest_series` turns them into the single
:class:`~suncore.core.series.PriceSeries` that the ``PriceSource`` protocol of
``core/forecast.py`` promises. The :class:`~suncore.tariff.service.TariffService` works with
the segments directly and resamples once onto the planning grid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Literal, Protocol

from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    PriceIncludes,
    PriceSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
    floor_to_step,
    resample_prices,
)
from suncore.core.tariff import TouTariff
from suncore.tariff.parse import ParseReport

SourceKind = Literal["dynamic", "fixed", "tou"]


@dataclass(frozen=True, slots=True)
class SourceData:
    """What one fetch returned: segments (one source, one ``price_includes``) and the parse
    report (what was dropped)."""

    segments: tuple[PriceSeries, ...]
    report: ParseReport = field(default_factory=ParseReport)

    @property
    def known_end(self) -> datetime | None:
        """End of the last segment, ``None`` when nothing was returned."""
        return max((s.import_price.end for s in self.segments), default=None)


class SegmentSource(Protocol):
    """What the :class:`TariffService` needs from an import price source."""

    @property
    def source_id(self) -> str: ...

    @property
    def kind(self) -> SourceKind: ...

    @property
    def price_includes(self) -> PriceIncludes: ...

    @property
    def uses_action(self) -> bool:
        """Whether a fetch calls a Home Assistant action (rate limited) rather than reading
        entity attributes."""
        ...

    @property
    def watched_entities(self) -> tuple[str, ...]:
        """Entities whose state change may mean new prices."""
        ...

    @property
    def delivery_tz(self) -> tzinfo | None:
        """Time zone of the market's delivery day when it is not the site's (Nord Pool: CET),
        else ``None``. The day-ahead prices end at the end of that zone's next day."""
        ...

    async def fetch_segments(self, window: TimeRange, now: datetime) -> SourceData: ...


def finest_series(
    source_id: str,
    price_includes: PriceIncludes,
    segments: Sequence[PriceSeries],
    window: TimeRange,
) -> PriceSeries:
    """One series on the finest native step of ``segments`` (step-hold for coarser ones).

    Without segments: an empty 15-minute series at ``window.start`` (nothing is invented).
    """
    if not segments:
        empty = TimeSeries(start=window.start, step=PLANNING_STEP, values=(), unit=SeriesUnit.PRICE)
        return PriceSeries(source_id=source_id, price_includes=price_includes, import_price=empty)
    step = min(s.step for s in segments)
    start = floor_to_step(min(s.start for s in segments), step)
    end = max(s.import_price.end for s in segments)
    count = max(1, -(-(end - start) // step))
    return resample_prices(list(segments), Grid(start=start, step=step, count=count))


def _slots(window: TimeRange) -> tuple[datetime, int]:
    start = floor_to_step(window.start)
    return start, max(1, -(-(window.end - start) // PLANNING_STEP))


def _static_series(
    source_id: str, values: Sequence[float], start: datetime, price_includes: PriceIncludes
) -> SourceData:
    series = TimeSeries(
        start=start, step=PLANNING_STEP, values=tuple(values), unit=SeriesUnit.PRICE
    )
    price = PriceSeries(source_id=source_id, price_includes=price_includes, import_price=series)
    return SourceData(segments=(price,), report=ParseReport(points=len(values)))


@dataclass(frozen=True, slots=True)
class FixedPriceSource:
    """One all-in price per kWh at all times (``tariff.import.kind = fixed``)."""

    price: float
    source_id: str = "fixed"
    kind: SourceKind = "fixed"
    price_includes: PriceIncludes = "all_in"
    uses_action: bool = False
    watched_entities: tuple[str, ...] = ()
    delivery_tz: tzinfo | None = None

    async def fetch_segments(self, window: TimeRange, now: datetime) -> SourceData:  # noqa: ARG002
        start, count = _slots(window)
        return _static_series(self.source_id, [self.price] * count, start, self.price_includes)

    async def fetch(self, window: TimeRange) -> PriceSeries:
        data = await self.fetch_segments(window, window.start)
        return data.segments[0]


@dataclass(frozen=True, slots=True)
class TouPriceSource:
    """HT/NT prices from local weekly windows (``tariff.import.kind = tou``).

    A slot is priced at its **start** instant, judged in the site's local time (DST safe: the
    slots are absolute 15-minute intervals). A window boundary that is not on a quarter hour
    therefore takes effect at the next slot start.
    """

    tou: TouTariff
    tz: tzinfo
    source_id: str = "tou"
    kind: SourceKind = "tou"
    price_includes: PriceIncludes = "all_in"
    uses_action: bool = False
    watched_entities: tuple[str, ...] = ()
    delivery_tz: tzinfo | None = None

    async def fetch_segments(self, window: TimeRange, now: datetime) -> SourceData:  # noqa: ARG002
        start, count = _slots(window)
        values = [self.tou.price_at(start + PLANNING_STEP * i, self.tz) for i in range(count)]
        return _static_series(self.source_id, values, start, self.price_includes)

    async def fetch(self, window: TimeRange) -> PriceSeries:
        data = await self.fetch_segments(window, window.start)
        return data.segments[0]
