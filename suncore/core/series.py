# SPDX-License-Identifier: Apache-2.0
"""Time series contracts and resampling onto the planning grid (Phase 4 WP2).

A :class:`TimeSeries` is a regular grid: ``values[i]`` belongs to the half-open interval
``[start + i*step, start + (i+1)*step)``. ``None`` marks a missing value; it is never
replaced by zero. All arithmetic is done in UTC so a DST day (23 h or 25 h local) needs no
special case: the planning grid is a run of 15-minute intervals in absolute time.

Meaning of a value by unit (``core/units.py`` base units, signs as there):

* ``W``: mean power over the interval (Solcast-style "average power over the slot").
* ``Wh``: energy *in* the interval.
* ``currency/kWh``: price that applies to every instant of the interval.
* ``%`` and ``°C``: state averaged over the interval.

Resampling (all pure functions):

* power and state: time-weighted mean over the overlap (energy-preserving for power);
* energy: each source value is split by the share of its interval that overlaps;
* prices: step-hold (a coarse price holds for every finer interval inside it; several
  finer prices inside one target interval are averaged by time).

A target interval gets ``None`` unless known source values cover it completely. Input may
consist of several series with different steps (e.g. 15-minute prices today, hourly prices
tomorrow); they must not overlap.
"""

from __future__ import annotations

import itertools
from collections.abc import Collection, Iterable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum, unique
from typing import Annotated, Final, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from suncore.core.validation import invalid

PLANNING_STEP: Final = timedelta(minutes=15)
"""Step of the planning grid (BRIEF §4.8)."""

QUANTILE_TOLERANCE: Final = 1e-6
"""Rounding slack allowed when checking p10 <= p50 <= p90."""

MAX_POINTS: Final = 20_000
"""Upper bound of values per series (two weeks of minutes); protects the Pi's memory."""

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

PriceIncludes = Literal["spot_only", "all_in"]
"""Whether a price is the bare spot price or already the all-in end price (ADR-0012)."""


@unique
class SeriesUnit(StrEnum):
    """Unit of a series; always a base unit (adapters convert kW, kWh at the edge)."""

    W = "W"
    WH = "Wh"
    PERCENT = "%"
    CELSIUS = "°C"
    PRICE = "currency/kWh"
    """Region currency per kWh (``RegionConfig.currency``)."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class TimeRange(_Frozen):
    """Half-open absolute time range ``[start, end)``, e.g. the window a source is asked for."""

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.start < self.end:
            raise invalid("range_order", "time range: start must be before end")
        return self

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


class TimeSeries(_Frozen):
    """A regular series: ``values[i]`` covers ``[start + i*step, start + (i+1)*step)``."""

    start: AwareDatetime
    """Normalised to UTC."""
    step: timedelta
    values: Annotated[tuple[FiniteFloat | None, ...], Field(max_length=MAX_POINTS)]
    unit: SeriesUnit

    @field_validator("start")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _step(self) -> Self:
        if self.step <= timedelta(0) or self.step > timedelta(days=1):
            raise invalid("series_step", "series: step must be between 0 and one day")
        return self

    @property
    def end(self) -> datetime:
        return self.start + self.step * len(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def slot_start(self, index: int) -> datetime:
        return self.start + self.step * index

    def slots(self) -> Iterator[tuple[datetime, datetime, float | None]]:
        """``(start, end, value)`` of every interval, in order."""
        for i, value in enumerate(self.values):
            begin = self.slot_start(i)
            yield begin, begin + self.step, value

    def same_grid(self, other: TimeSeries) -> bool:
        return (self.start, self.step, len(self.values)) == (
            other.start,
            other.step,
            len(other.values),
        )

    def total(self) -> float | None:
        """Energy (Wh) of a ``W`` or ``Wh`` series; ``None`` if a value is missing."""
        if self.unit not in (SeriesUnit.W, SeriesUnit.WH):
            msg = f"total() needs a power or energy series, not {self.unit.value}"
            raise ValueError(msg)
        if any(v is None for v in self.values):
            return None
        hours = self.step / timedelta(hours=1)
        factor = hours if self.unit is SeriesUnit.W else 1.0
        return sum(v * factor for v in self.values if v is not None)


class QuantileSeries(_Frozen):
    """P10/P50/P90 of one quantity on one grid (PV or load forecast).

    Where all three values of an interval are known, ``p10 <= p50 <= p90`` holds. A source
    without quantiles sets all three to the same series.
    """

    p10: TimeSeries
    p50: TimeSeries
    p90: TimeSeries

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not (self.p50.same_grid(self.p10) and self.p50.same_grid(self.p90)):
            raise invalid("quantile_grid", "quantiles: p10, p50 and p90 need the same grid")
        if not self.p10.unit is self.p50.unit is self.p90.unit:
            raise invalid("quantile_unit", "quantiles: p10, p50 and p90 need the same unit")
        for lo, mid, hi in zip(self.p10.values, self.p50.values, self.p90.values, strict=True):
            if lo is None or mid is None or hi is None:
                continue
            if lo > mid + QUANTILE_TOLERANCE or mid > hi + QUANTILE_TOLERANCE:
                raise invalid("quantile_order", "quantiles: p10 <= p50 <= p90 is violated")
        return self

    @classmethod
    def deterministic(cls, series: TimeSeries) -> QuantileSeries:
        """A forecast without spread (all quantiles equal)."""
        return cls(p10=series, p50=series, p90=series)

    @property
    def start(self) -> datetime:
        return self.p50.start

    @property
    def step(self) -> timedelta:
        return self.p50.step

    @property
    def unit(self) -> SeriesUnit:
        return self.p50.unit

    def __len__(self) -> int:
        return len(self.p50)


class PriceSeries(_Frozen):
    """Import (and optionally export) prices per interval from one source.

    ``import_price`` and ``export_price`` are in currency/kWh and may be negative.
    ``price_includes`` says whether the import price still needs the price formula
    (``spot_only``) or is the end price (``all_in``). ``export_price = None`` means the
    export price comes from the tariff configuration, not from this source.
    """

    source_id: Annotated[str, Field(min_length=1, max_length=64)]
    price_includes: PriceIncludes
    import_price: TimeSeries
    export_price: TimeSeries | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        series = (
            [self.import_price]
            if self.export_price is None
            else [
                self.import_price,
                self.export_price,
            ]
        )
        if any(s.unit is not SeriesUnit.PRICE for s in series):
            raise invalid("price_unit", "prices: unit must be currency/kWh")
        if self.export_price is not None and not self.import_price.same_grid(self.export_price):
            raise invalid("price_grid", "prices: import and export need the same grid")
        return self

    @property
    def start(self) -> datetime:
        return self.import_price.start

    @property
    def step(self) -> timedelta:
        return self.import_price.step

    @property
    def resolution_min(self) -> float:
        """Interval length in minutes (15 or 60 for the sources SunCore knows)."""
        return self.step / timedelta(minutes=1)

    def __len__(self) -> int:
        return len(self.import_price)


# --- planning grid -------------------------------------------------------------------


class Grid(_Frozen):
    """The target grid of a resampling: ``count`` intervals of ``step`` from ``start``."""

    start: AwareDatetime
    step: timedelta = PLANNING_STEP
    count: Annotated[int, Field(ge=1, le=MAX_POINTS)]

    @field_validator("start")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _step(self) -> Self:
        if self.step <= timedelta(0) or self.step > timedelta(days=1):
            raise invalid("series_step", "series: step must be between 0 and one day")
        return self

    @property
    def end(self) -> datetime:
        return self.start + self.step * self.count

    def starts(self) -> list[datetime]:
        return [self.start + self.step * i for i in range(self.count)]


def floor_to_step(at: datetime, step: timedelta = PLANNING_STEP) -> datetime:
    """``at`` rounded down to a multiple of ``step`` since the UNIX epoch (in UTC).

    For 15 minutes this is also the local quarter hour in every zone whose offset is a
    multiple of 15 minutes (all current IANA zones).
    """
    utc = _utc(at)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return epoch + step * ((utc - epoch) // step)


def planning_grid(now: datetime, horizon: timedelta, step: timedelta = PLANNING_STEP) -> Grid:
    """The grid that starts at the interval containing ``now`` and covers ``horizon``."""
    start = floor_to_step(now, step)
    end = _utc(now) + horizon
    count = max(1, -(-(end - start) // step))
    return Grid(start=start, step=step, count=count)


# --- resampling ----------------------------------------------------------------------

Slot = tuple[datetime, datetime, float | None]


def _segments(series: TimeSeries | Sequence[TimeSeries]) -> list[TimeSeries]:
    items = [series] if isinstance(series, TimeSeries) else list(series)
    if not items:
        return []
    units = {s.unit for s in items}
    if len(units) != 1:
        msg = "resample: all segments need the same unit"
        raise ValueError(msg)
    items.sort(key=lambda s: s.start)
    for a, b in itertools.pairwise(items):
        if b.start < a.end:
            msg = "resample: segments overlap"
            raise ValueError(msg)
    return items


def _slots(items: Iterable[TimeSeries]) -> list[Slot]:
    return [slot for s in items for slot in s.slots()]


def _seconds(delta: timedelta) -> float:
    return delta.total_seconds()


def _resample(slots: list[Slot], grid: Grid, *, split: bool) -> tuple[float | None, ...]:
    """Core of all resamplers.

    ``split`` = energy semantics (source value shared by overlap share, then summed);
    otherwise intensive semantics (time-weighted mean over the overlap).
    """
    out: list[float | None] = []
    j = 0
    for target_start in grid.starts():
        target_end = target_start + grid.step
        while j < len(slots) and slots[j][1] <= target_start:
            j += 1
        covered = 0.0
        acc = 0.0
        known = True
        single: float | None = None
        parts = 0
        k = j
        while k < len(slots) and slots[k][0] < target_end:
            s_start, s_end, value = slots[k]
            overlap = _seconds(min(s_end, target_end) - max(s_start, target_start))
            if overlap > 0:
                if value is None:
                    known = False
                    break
                covered += overlap
                parts += 1
                single = value
                if split:
                    acc += value * overlap / _seconds(s_end - s_start)
                else:
                    acc += value * overlap
            k += 1
        full = _seconds(grid.step)
        if not known or abs(covered - full) > 1e-6:
            out.append(None)
        elif split:
            out.append(acc)
        else:  # one source value holds the whole interval: keep it exactly (step-hold)
            out.append(single if parts == 1 else acc / full)
    return tuple(out)


def resample_power(series: TimeSeries | Sequence[TimeSeries], grid: Grid) -> TimeSeries:
    """Mean power (W) per grid interval; energy over fully covered intervals is preserved.

    Also used for ``%`` and ``°C`` series (time-weighted mean of a state).
    """
    items = _segments(series)
    unit = items[0].unit if items else SeriesUnit.W
    if unit in (SeriesUnit.WH, SeriesUnit.PRICE):
        msg = f"resample_power: unit {unit.value} is not intensive, use the matching resampler"
        raise ValueError(msg)
    values = _resample(_slots(items), grid, split=False)
    return TimeSeries(start=grid.start, step=grid.step, values=values, unit=unit)


def resample_energy(series: TimeSeries | Sequence[TimeSeries], grid: Grid) -> TimeSeries:
    """Energy (Wh) per grid interval; each source value is split by its overlap share."""
    items = _segments(series)
    if items and items[0].unit is not SeriesUnit.WH:
        msg = "resample_energy: needs a Wh series"
        raise ValueError(msg)
    values = _resample(_slots(items), grid, split=True)
    return TimeSeries(start=grid.start, step=grid.step, values=values, unit=SeriesUnit.WH)


def energy_to_power(series: TimeSeries) -> TimeSeries:
    """A Wh-per-interval series as mean power (W)."""
    if series.unit is not SeriesUnit.WH:
        msg = "energy_to_power: needs a Wh series"
        raise ValueError(msg)
    hours = series.step / timedelta(hours=1)
    values = tuple(None if v is None else v / hours for v in series.values)
    return series.model_copy(update={"values": values, "unit": SeriesUnit.W})


def resample_quantiles(
    series: QuantileSeries | Sequence[QuantileSeries], grid: Grid
) -> QuantileSeries:
    """Resample P10/P50/P90 alike; the order of the quantiles is kept (same weights).

    Coarsening applies the same linear operation to every quantile, which is not how
    quantiles of a sum behave: the P10 of a 60-minute total is *higher* than the mean (or
    sum) of four 15-minute P10 values, because errors partly cancel. The resampled P10 is
    therefore conservative (too low), the P90 too high - the safe direction for a target
    constraint. The planner's risk model works on the 15-minute grid, where refining
    (step-hold of a mean power) is exact.
    """
    items = [series] if isinstance(series, QuantileSeries) else list(series)
    unit = items[0].unit if items else SeriesUnit.W
    resample = resample_energy if unit is SeriesUnit.WH else resample_power
    return QuantileSeries(
        p10=resample([q.p10 for q in items], grid),
        p50=resample([q.p50 for q in items], grid),
        p90=resample([q.p90 for q in items], grid),
    )


def _hold(series: Sequence[TimeSeries], grid: Grid) -> TimeSeries:
    items = _segments(series)
    if items and items[0].unit is not SeriesUnit.PRICE:
        msg = "resample_prices: needs a currency/kWh series"
        raise ValueError(msg)
    values = _resample(_slots(items), grid, split=False)
    return TimeSeries(start=grid.start, step=grid.step, values=values, unit=SeriesUnit.PRICE)


def resample_prices(series: PriceSeries | Sequence[PriceSeries], grid: Grid) -> PriceSeries:
    """Step-hold prices onto ``grid``; segments of one source may mix 15 and 60 minutes.

    All segments must come from the same source with the same ``price_includes``; the
    export series is kept only if every segment has one.
    """
    items = [series] if isinstance(series, PriceSeries) else list(series)
    if not items:
        msg = "resample_prices: no input"
        raise ValueError(msg)
    if len({(p.source_id, p.price_includes) for p in items}) != 1:
        msg = "resample_prices: segments from different sources"
        raise ValueError(msg)
    exports = [p.export_price for p in items]
    export = (
        None
        if any(e is None for e in exports)
        else _hold([e for e in exports if e is not None], grid)
    )
    return PriceSeries(
        source_id=items[0].source_id,
        price_includes=items[0].price_includes,
        import_price=_hold([p.import_price for p in items], grid),
        export_price=export,
    )


def segments_from_points(
    points: Sequence[tuple[datetime, float | None]],
    unit: SeriesUnit,
    *,
    expected_step: timedelta,
    other_steps: Collection[timedelta] = (),
    last_step: timedelta | None = None,
) -> tuple[TimeSeries, ...]:
    """Regular segments from timestamped points (e.g. an integration's price list).

    ``expected_step`` is the resolution the source publishes at; ``other_steps`` are the
    further resolutions the caller accepts (e.g. ``{60 min}`` for a 15-minute price source
    whose next day is still hourly). Each distance ``d`` between two neighbouring points,
    judged against the step ``cur`` of the running segment:

    * ``d == cur``: the segment continues;
    * ``d`` is an accepted step (expected or other) and either not a multiple of ``cur``
      or the next distance is ``d`` again (or the point is the last but one): a
      **resolution change** - a new segment with step ``d`` starts at this point;
    * ``d`` is a multiple of ``cur``: a **gap** - the point lasts ``cur`` and the missing
      slots become ``None`` (a gap is never filled with a neighbour's value);
    * anything else raises ``ValueError`` (irregular timestamps; SunCore never guesses).

    So 15-minute prices at 10:00, 10:15, 10:45 give one segment with ``None`` at 10:30,
    and a 60-minute distance inside 15-minute data is a gap unless the caller accepts
    60 minutes *and* the hourly rhythm continues. The last point lasts ``last_step``
    (default: the step of its segment). Points must be strictly increasing in time;
    duplicates must be removed by the adapter.
    """
    if not points:
        return ()
    if expected_step <= timedelta(0) or any(s <= timedelta(0) for s in other_steps):
        msg = "segments_from_points: steps must be positive"
        raise ValueError(msg)
    stamps = [_utc(t) for t, _ in points]
    if any(b <= a for a, b in itertools.pairwise(stamps)):
        msg = "segments_from_points: timestamps must be strictly increasing"
        raise ValueError(msg)
    accepted = {expected_step, *other_steps}
    distances = [b - a for a, b in itertools.pairwise(stamps)]
    segments: list[TimeSeries] = []
    cur = expected_step
    begin = stamps[0]
    values: list[float | None] = []

    def close() -> None:
        if values:
            segments.append(TimeSeries(start=begin, step=cur, values=tuple(values), unit=unit))

    for i, d in enumerate(distances):
        value = points[i][1]
        if d == cur:
            values.append(value)
            continue
        steady = i + 1 >= len(distances) or distances[i + 1] == d
        if d in accepted and (d % cur != timedelta(0) or steady):
            close()
            cur, begin, values = d, stamps[i], [value]
        elif d % cur == timedelta(0):
            values.append(value)
            values.extend([None] * (d // cur - 1))
        else:
            msg = (
                f"segments_from_points: distance {d} at {stamps[i].isoformat()} is neither "
                f"an accepted step nor a gap of whole {cur} slots"
            )
            raise ValueError(msg)
    if last_step is not None and last_step != cur:
        close()
        cur, begin, values = last_step, stamps[-1], []
    values.append(points[-1][1])
    close()
    return tuple(segments)


__all__ = [
    "MAX_POINTS",
    "PLANNING_STEP",
    "QUANTILE_TOLERANCE",
    "Grid",
    "PriceIncludes",
    "PriceSeries",
    "QuantileSeries",
    "SeriesUnit",
    "TimeRange",
    "TimeSeries",
    "energy_to_power",
    "floor_to_step",
    "planning_grid",
    "resample_energy",
    "resample_power",
    "resample_prices",
    "resample_quantiles",
    "segments_from_points",
]
