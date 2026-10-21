# SPDX-License-Identifier: Apache-2.0
"""Parsing of PV forecast points into a quantile series in W (Phase 4 WP5; no I/O).

Every function is pure. The shapes it understands are the ones the research documents
(``docs/research/integrations.md`` "PV forecasts"):

* a **list of point objects** (Solcast ``detailedForecast``: ``period_start`` ISO 8601 with the
  local offset, ``pv_estimate`` / ``pv_estimate10`` / ``pv_estimate90`` in kW average power over
  the slot; the action ``query_forecast_data`` answers ``{"data": [same objects]}``);
* a **mapping** ``{ISO timestamp: value}`` (Open-Meteo Solar Forecast ``wh_period_15m``: Wh per
  15 minutes, key = interval start; ``energy/solar_forecast`` ``wh_hours``: Wh per period, key =
  period start, UNVERIFIED for Forecast.Solar).

A point is the value of the period that **starts** at its timestamp. The result lies on the
source's native grid (one regular segment, absolute time, so a DST day with 23 or 25 local
hours needs no special case); a missing period is ``None``, never filled with a neighbour's
value (the one exception is ``missing_is_zero``: Forecast.Solar's integration drops a zero
value that starts exactly at midnight, so a period missing between two known ones is 0).

Units: power (W, kW) is the average power over the slot and kept; energy (Wh, kWh) per slot is
converted with ``W = Wh * 3600 / slot_seconds``, which preserves the energy exactly.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import reduce
from typing import Any, Final

from suncore.core.series import (
    QuantileSeries,
    SeriesUnit,
    TimeSeries,
    floor_to_step,
    segments_from_points,
)
from suncore.core.units import Dimension, Unit, to_base
from suncore.templates.models import SeriesFields

NEGATIVE_TOLERANCE: Final = 1e-6
"""A forecast below ``-tolerance`` (in the source's unit) is invalid data, not a small error."""


@dataclass(frozen=True, slots=True)
class RawPoint:
    """One parsed point in the template's unit; ``None`` = the value is missing or invalid."""

    start: datetime
    value: float | None
    p10: float | None = None
    p90: float | None = None


@dataclass(frozen=True, slots=True)
class ParsedPoints:
    points: tuple[RawPoint, ...]
    skipped: int
    """Entries that could not be used at all (no parsable timestamp, not an object)."""


def parse_timestamp(raw: object) -> datetime | None:
    """An ISO 8601 timestamp **with** a UTC offset; anything else is ``None``.

    A timestamp without an offset is refused: the documented sources always carry one, and
    guessing a zone is how forecasts end up an hour off on a DST day.
    """
    if isinstance(raw, datetime):
        moment = raw
    elif isinstance(raw, str):
        try:
            moment = datetime.fromisoformat(raw.strip())
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo is not None and moment.utcoffset() is not None else None


def number(raw: object) -> float | None:
    """A finite number (not a bool); numeric strings are accepted, everything else is ``None``."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError:
            return None
    else:
        return None
    return value if math.isfinite(value) else None


def dig(raw: object, path: str | None) -> object:
    """Follow a dotted path of mapping keys (``None`` path = ``raw`` itself)."""
    current = raw
    if path is None:
        return current
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def dig_response(raw: object, path: str) -> list[Any]:
    """Entries at ``path`` of an action response; a ``*`` segment spreads over a mapping's values.

    ``data`` -> the list at ``data``; ``prices.*`` -> the lists of every value of ``prices``
    concatenated. A path that does not lead to lists gives an empty list.
    """
    nodes: list[object] = [raw]
    for segment in path.split("."):
        nxt: list[object] = []
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            if segment == "*":
                nxt.extend(node.values())
            elif segment in node:
                nxt.append(node[segment])
        nodes = nxt
    out: list[Any] = []
    for node in nodes:
        if isinstance(node, list | tuple):
            out.extend(node)
    return out


def list_points(raw: object, fields: SeriesFields) -> ParsedPoints:
    """Points from a list of objects named by ``fields`` (``None`` or a non-list: no points)."""
    if not isinstance(raw, list | tuple):
        return ParsedPoints((), 0)
    ts_field, value_field = fields.timestamp_field, fields.value_field
    if ts_field is None or value_field is None:
        msg = "list_points needs timestamp_field and value_field"
        raise ValueError(msg)
    points: list[RawPoint] = []
    skipped = 0
    for entry in raw:
        if not isinstance(entry, Mapping):
            skipped += 1
            continue
        start = parse_timestamp(entry.get(ts_field))
        if start is None:
            skipped += 1
            continue
        value = number(entry.get(value_field))
        p10 = number(entry.get(fields.p10_field)) if fields.p10_field is not None else None
        p90 = number(entry.get(fields.p90_field)) if fields.p90_field is not None else None
        points.append(RawPoint(start, value, p10, p90))
    return ParsedPoints(tuple(points), skipped)


def mapping_points(raw: object) -> ParsedPoints:
    """Points from ``{ISO timestamp: value}`` (``None`` or a non-mapping: no points)."""
    if not isinstance(raw, Mapping):
        return ParsedPoints((), 0)
    points: list[RawPoint] = []
    skipped = 0
    for key, value in raw.items():
        start = parse_timestamp(key)
        if start is None:
            skipped += 1
            continue
        points.append(RawPoint(start, number(value)))
    return ParsedPoints(tuple(points), skipped)


def infer_step(points: Sequence[RawPoint], default: timedelta) -> timedelta:
    """The period length from the spacing of the timestamps (their greatest common divisor).

    ``energy/solar_forecast`` has no unit or period field (the type is only "Wh per timestamp"),
    so the length is inferred and never assumed; one point or none: ``default``.
    """
    stamps = sorted({p.start for p in points})
    gaps = {int((b - a).total_seconds()) for a, b in itertools.pairwise(stamps)}
    gaps.discard(0)
    if not gaps:
        return default
    divisor = reduce(math.gcd, gaps)
    return timedelta(seconds=max(divisor, 60))


ALLOWED_STEPS: Final = (timedelta(minutes=60), timedelta(minutes=30), timedelta(minutes=15))
"""Period lengths an energy mapping may have (``energy/solar_forecast``)."""


@dataclass(frozen=True, slots=True)
class Regularized:
    """Energy points brought onto one grid (:func:`regularize_energy`)."""

    points: tuple[RawPoint, ...]
    step: timedelta
    dropped: int
    """Off-grid points without energy (0 Wh or unknown) that were left out."""
    merged: int
    """Off-grid points whose energy was added to the period that encloses them."""


def _on_grid(moment: datetime, step: timedelta) -> bool:
    return floor_to_step(moment, step) == moment


def _sum(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a + b


def regularize_energy(points: Sequence[RawPoint], default: timedelta) -> Regularized:
    """Energy points (Wh per period, key = period start) on one grid of 15, 30 or 60 minutes.

    The period is the most frequent gap between consecutive points that lie on the 15-minute
    grid, if that gap is 15, 30 or 60 minutes (a tie takes the longer one; no such gap:
    ``default``). A point that is not on the grid of that period - a sunrise or sunset key such
    as ``07:41:13`` that a vendor may emit (UNVERIFIED for Forecast.Solar) - never fails the
    source: without energy (0 Wh or unknown) it is dropped, with energy it is **added to the
    period that encloses it** (energy preserving; P10/P90 only where both points have them).
    """
    base = ALLOWED_STEPS[-1]
    aligned = sorted({p.start for p in points if _on_grid(p.start, base)})
    gaps = Counter(b - a for a, b in itertools.pairwise(aligned) if b - a in ALLOWED_STEPS)
    step = default
    if gaps:
        top = max(gaps.values())
        step = max(g for g, count in gaps.items() if count == top)
    regular: dict[datetime, RawPoint] = {}
    off: list[RawPoint] = []
    for point in points:
        if _on_grid(point.start, step):
            regular[point.start] = point  # the later one wins, like _dedupe
        else:
            off.append(point)
    dropped = merged = 0
    for point in off:
        if point.value is None or abs(point.value) <= NEGATIVE_TOLERANCE:
            dropped += 1
            continue
        key = floor_to_step(point.start, step)
        current = regular.get(key)
        if current is None:
            regular[key] = RawPoint(key, point.value, point.p10, point.p90)
        elif current.value is None:
            regular[key] = RawPoint(current.start, point.value, point.p10, point.p90)
        else:
            regular[key] = RawPoint(
                current.start,
                current.value + point.value,
                _sum(current.p10, point.p10),
                _sum(current.p90, point.p90),
            )
        merged += 1
    ordered = tuple(sorted(regular.values(), key=lambda p: p.start))
    return Regularized(ordered, step, dropped, merged)


def _dedupe(points: Sequence[RawPoint]) -> list[RawPoint]:
    """Sorted by time, one point per instant (the later one in input order wins)."""
    unique = {p.start.timestamp(): p for p in points}
    return [unique[key] for key in sorted(unique)]


def _fill_missing_as_zero(
    points: list[RawPoint], step: timedelta, *, quantiles: bool
) -> list[RawPoint]:
    out: list[RawPoint] = []
    for point in points:
        if out:
            previous = out[-1].start
            gap = point.start - previous
            if gap > step and gap % step == timedelta(0):
                out.extend(
                    RawPoint(
                        previous + step * (i + 1),
                        0.0,
                        0.0 if quantiles else None,
                        0.0 if quantiles else None,
                    )
                    for i in range(gap // step - 1)
                )
        out.append(point)
    return out


def _watts(value: float | None, unit: Unit, step: timedelta) -> float | None:
    if value is None or value < -NEGATIVE_TOLERANCE:
        return None
    base = max(to_base(value, unit), 0.0)
    if unit.dimension is Dimension.ENERGY:
        return base * 3600.0 / step.total_seconds()
    return base


def _segment(
    stamps: Sequence[datetime], values: Sequence[float | None], step: timedelta
) -> TimeSeries:
    segments = segments_from_points(
        list(zip(stamps, values, strict=True)), SeriesUnit.W, expected_step=step
    )
    if len(segments) != 1:  # only one accepted step: a single regular segment
        msg = "forecast points do not form one regular series"
        raise ValueError(msg)
    return segments[0]


def build_quantiles(
    points: Sequence[RawPoint],
    unit: Unit,
    step: timedelta,
    *,
    missing_is_zero: bool = False,
) -> QuantileSeries | None:
    """The points as P10/P50/P90 in W on the grid of ``step``; ``None`` if there are none.

    * the value of a point without a known P50 is unknown in all three quantiles;
    * P10/P90 that a point lacks stay ``None`` (an interval is not invented), and a source
      without quantile fields gets equal quantiles (``QuantileSeries.deterministic``);
    * an inverted pair (P10 > P50 or P90 < P50) is clamped to P50 - the safe direction;
    * a negative value is invalid data and becomes ``None``.

    Raises:
        ValueError: timestamps that are not on the grid of ``step`` (an irregular source is
            refused as a whole, SunCore never guesses).
    """
    ordered = _dedupe(points)
    if not ordered:
        return None
    has_quantiles = any(p.p10 is not None or p.p90 is not None for p in ordered)
    if missing_is_zero:
        ordered = _fill_missing_as_zero(ordered, step, quantiles=has_quantiles)
    p10s: list[float | None] = []
    p50s: list[float | None] = []
    p90s: list[float | None] = []
    for point in ordered:
        p50 = _watts(point.value, unit, step)
        p10 = _watts(point.p10, unit, step) if p50 is not None else None
        p90 = _watts(point.p90, unit, step) if p50 is not None else None
        if p50 is not None:
            if p10 is not None:
                p10 = min(p10, p50)
            if p90 is not None:
                p90 = max(p90, p50)
        p50s.append(p50)
        p10s.append(p10)
        p90s.append(p90)
    stamps = [p.start for p in ordered]
    series50 = _segment(stamps, p50s, step)
    if not has_quantiles:
        return QuantileSeries.deterministic(series50)
    return QuantileSeries(
        p10=_segment(stamps, p10s, step), p50=series50, p90=_segment(stamps, p90s, step)
    )


def has_known(series: QuantileSeries) -> bool:
    """Whether any P50 value is known."""
    return any(v is not None for v in series.p50.values)


def crop(series: QuantileSeries, start: datetime, end: datetime) -> QuantileSeries:
    """The slots of ``series`` that overlap ``[start, end)``; never padded."""
    step = series.step
    first = max(0, math.floor((start - series.start) / step))
    last = min(len(series), math.ceil((end - series.start) / step))
    last = max(first, last)

    def cut(part: TimeSeries) -> TimeSeries:
        return TimeSeries(
            start=part.start + step * first,
            step=step,
            values=part.values[first:last],
            unit=part.unit,
        )

    return QuantileSeries(p10=cut(series.p10), p50=cut(series.p50), p90=cut(series.p90))


def empty_series(start: datetime, step: timedelta) -> QuantileSeries:
    """A series without slots (the answer of a source that has nothing)."""
    empty = TimeSeries(start=start, step=step, values=(), unit=SeriesUnit.W)
    return QuantileSeries(p10=empty, p50=empty, p90=empty)


__all__ = [
    "ALLOWED_STEPS",
    "NEGATIVE_TOLERANCE",
    "ParsedPoints",
    "RawPoint",
    "Regularized",
    "build_quantiles",
    "crop",
    "dig",
    "dig_response",
    "empty_series",
    "has_known",
    "infer_step",
    "list_points",
    "mapping_points",
    "number",
    "parse_timestamp",
    "regularize_energy",
]
