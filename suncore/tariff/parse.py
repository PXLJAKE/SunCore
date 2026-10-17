# SPDX-License-Identifier: Apache-2.0
"""Pure parsing of price lists (Phase 4 WP4): no I/O, no clock, time zones explicit.

Shapes (all from ``docs/research/integrations.md``, pinned sources there):

* Tibber ``tibber.get_prices``: ``{"prices": {"<home>": [{"start_time": iso, "price": float}]}}``;
* Nord Pool ``nordpool.get_prices_for_date``: ``{"<AREA>": [{"start", "end", "price"}]}``, UTC
  ``+00:00``, currency per MWh;
* ENTSO-E ``prices`` attribute: ``[{"time": "2026-10-16 00:00:00+02:00", "price": float}]``
  (``str(datetime)``: a space instead of ``T``);
* EPEX Spot ``data`` attribute: ``[{"start_time", "end_time", "price_per_kwh"}]``, local ISO;
* Octopus ``rates`` attribute: ``[{"start", "end", "value_inc_vat"}]``, local datetimes;
* ``mapping`` shape: ``{iso_timestamp: value}``.

A timestamp without a UTC offset is rejected (SunCore never guesses a zone); all arithmetic is
done in UTC, so a DST day of 23 h or 25 h needs no special case.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from suncore.core.series import SeriesUnit, TimeSeries, segments_from_points
from suncore.tariff.errors import AmbiguousSeriesError, MalformedPayloadError
from suncore.templates.models import SeriesFields

DEFAULT_STEP: Final = timedelta(hours=1)
"""Step assumed when a list has a single point and the template names no resolution."""
KNOWN_STEPS: Final = (timedelta(minutes=15), timedelta(hours=1))
"""Resolutions SunCore accepts to change to inside one list (15 min <-> 60 min)."""


@dataclass(frozen=True, slots=True)
class RawSlot:
    """One price point: ``start`` (UTC), optional ``end`` (UTC) and the value (``None`` = gap)."""

    start: datetime
    end: datetime | None
    value: float | None


@dataclass(frozen=True, slots=True)
class ParseReport:
    """What the parser had to drop; shown as quality, never silently ignored."""

    points: int = 0
    unreadable_values: int = 0
    """Points whose value was missing or not a finite number (they become gaps)."""
    duplicates: int = 0
    """Points with a start already seen (the later one wins)."""
    overlaps: int = 0
    """Points that start before the previous slot ends (dropped, the earlier one wins)."""


def parse_timestamp(value: object) -> datetime:
    """An ISO 8601 timestamp (``T`` or space separated, ``Z`` or offset) as aware UTC.

    Accepts ``str(datetime)`` output (ENTSO-E) and ``datetime.isoformat()`` (EPEX Spot,
    Octopus, Nord Pool, Tibber). Raises :class:`MalformedPayloadError` for anything else,
    including a timestamp without an offset.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            msg = f"not an ISO 8601 timestamp: {value!r}"
            raise MalformedPayloadError(msg) from exc
    else:
        msg = f"timestamp is neither text nor datetime: {type(value).__name__}"
        raise MalformedPayloadError(msg)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = f"timestamp without a UTC offset: {value!r}"
        raise MalformedPayloadError(msg)
    return parsed.astimezone(UTC)


def parse_number(value: object) -> float | None:
    """A finite number from a number or numeric text; ``None`` for everything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


# --- locating the points ---------------------------------------------------------------


def walk(value: object, path: str) -> object:
    """Follow a dotted path of mapping keys (no ``*``); ``MalformedPayloadError`` if absent."""
    node = value
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            msg = f"path {path!r} not found at {part!r}"
            raise MalformedPayloadError(msg)
        node = node[part]
    return node


def expand_path(payload: object, path: str) -> list[tuple[str, object]]:
    """All ``(label, node)`` pairs a response path leads to; ``*`` fans out over a mapping.

    The label is the last expanded mapping key (home name, area) or ``""``. A path through a
    missing key yields no candidate (an empty response is not an error); a path through a
    non-mapping raises :class:`MalformedPayloadError`.
    """
    found: list[tuple[str, object]] = [("", payload)]
    for part in path.split("."):
        nxt: list[tuple[str, object]] = []
        for label, node in found:
            if not isinstance(node, Mapping):
                msg = f"path {path!r}: {part!r} is not inside an object"
                raise MalformedPayloadError(msg)
            if part == "*":
                nxt.extend((str(k), v) for k, v in node.items())
            elif part in node:
                nxt.append((label, node[part]))
        found = nxt
    return found


def select_series(candidates: Sequence[tuple[str, object]], key: str | None) -> object | None:
    """The one node of a fan-out: the entry named ``key`` or the only one.

    Raises :class:`AmbiguousSeriesError` when several entries exist and no key is configured
    (SunCore never merges two homes' prices), and :class:`MalformedPayloadError` when the
    configured key is absent while others exist. No candidate at all returns ``None``.
    """
    if key is not None:
        for label, node in candidates:
            if label == key or label.casefold() == key.casefold():
                return node
        if candidates:
            msg = f"series key {key!r} not in the response"
            raise MalformedPayloadError(msg, key=key, available=[c[0] for c in candidates])
        return None
    if len(candidates) > 1:
        raise AmbiguousSeriesError(
            "the response holds several series", available=sorted(c[0] for c in candidates)
        )
    return candidates[0][1] if candidates else None


def _optional_stamp(value: object) -> datetime | None:
    return None if value is None else parse_timestamp(value)


def slots_from_records(records: object, fields: SeriesFields) -> tuple[list[RawSlot], int]:
    """Points from a list of objects named by ``fields``; also the count of unreadable values.

    A record without a timestamp is malformed (raises); a missing or non-numeric value is a
    gap. ``fields.end_field`` gives the slot end when present in the record.
    """
    if not isinstance(records, list):
        msg = f"expected a list of price points, got {type(records).__name__}"
        raise MalformedPayloadError(msg)
    assert fields.timestamp_field is not None
    assert fields.value_field is not None
    slots: list[RawSlot] = []
    bad = 0
    for record in records:
        if not isinstance(record, Mapping) or fields.timestamp_field not in record:
            msg = f"price point without `{fields.timestamp_field}`"
            raise MalformedPayloadError(msg)
        value = parse_number(record.get(fields.value_field))
        bad += value is None
        end = (
            _optional_stamp(record.get(fields.end_field)) if fields.end_field is not None else None
        )
        slots.append(RawSlot(parse_timestamp(record[fields.timestamp_field]), end, value))
    return slots, bad


def slots_from_mapping(mapping: object) -> tuple[list[RawSlot], int]:
    """Points from ``{iso_timestamp: value}``."""
    if not isinstance(mapping, Mapping):
        msg = f"expected an object of timestamp -> value, got {type(mapping).__name__}"
        raise MalformedPayloadError(msg)
    slots: list[RawSlot] = []
    bad = 0
    for stamp, raw in mapping.items():
        value = parse_number(raw)
        bad += value is None
        slots.append(RawSlot(parse_timestamp(stamp), None, value))
    return slots, bad


# --- normalising and segmenting ----------------------------------------------------------


def normalise(
    slots: Iterable[RawSlot], *, unreadable: int = 0
) -> tuple[list[RawSlot], ParseReport]:
    """Sort by start, collapse duplicate starts (later wins) and drop overlapping slots.

    Merging two lists (Octopus current day + next day) therefore needs no special case.
    Slots with an ``end`` not after their ``start`` are dropped as overlaps.
    """
    ordered = sorted(enumerate(slots), key=lambda p: (p[1].start, p[0]))
    unique: dict[datetime, RawSlot] = {}
    total = 0
    for _, slot in ordered:
        total += 1
        unique[slot.start] = slot  # same start: the later one in input order wins
    duplicates = total - len(unique)
    result: list[RawSlot] = []
    overlaps = 0
    for slot in unique.values():
        if slot.end is not None and slot.end <= slot.start:
            overlaps += 1
            continue
        if result and (prev_end := result[-1].end) is not None and slot.start < prev_end:
            overlaps += 1
            continue
        result.append(slot)
    report = ParseReport(
        points=len(result), unreadable_values=unreadable, duplicates=duplicates, overlaps=overlaps
    )
    return result, report


def infer_step(starts: Sequence[datetime], fallback: timedelta | None) -> timedelta:
    """The resolution of a list without slot ends: the most common distance of neighbours.

    Ties go to the finer step. A single point uses ``fallback`` (the template's
    ``resolution_min``), else one hour.
    """
    distances = Counter(b - a for a, b in itertools.pairwise(starts))
    if not distances:
        return fallback or DEFAULT_STEP
    best = max(distances.values())
    return min(d for d, n in distances.items() if n == best)


def _explicit_segments(slots: Sequence[RawSlot], divisor: float) -> list[TimeSeries]:
    """Segments for slots that all carry an ``end``: runs of equal length that touch."""
    segments: list[TimeSeries] = []
    run: list[RawSlot] = []

    def close() -> None:
        if run:
            first = run[0]
            assert first.end is not None
            segments.append(
                TimeSeries(
                    start=first.start,
                    step=first.end - first.start,
                    values=tuple(None if s.value is None else s.value / divisor for s in run),
                    unit=SeriesUnit.PRICE,
                )
            )
            run.clear()

    for slot in slots:
        assert slot.end is not None
        if run:
            prev = run[-1]
            assert prev.end is not None
            same_length = slot.end - slot.start == prev.end - prev.start
            if not (same_length and slot.start == prev.end):
                close()
        run.append(slot)
    close()
    return segments


def build_segments(
    slots: Sequence[RawSlot], *, divisor: float, resolution: timedelta | None
) -> tuple[TimeSeries, ...]:
    """Regular ``currency/kWh`` segments from normalised slots.

    * every slot has an ``end`` (Nord Pool, EPEX Spot, Octopus): the slot length is
      ``end - start`` and consecutive slots of equal length that touch form one segment, so a
      15/60-minute change or a gap just starts a new segment;
    * otherwise (Tibber, ENTSO-E, mapping attributes): the resolution is the template's
      ``resolution_min`` or the most common distance; 15 <-> 60 minutes changes inside the list
      are accepted, missing slots become ``None`` (:func:`segments_from_points`).

    ``divisor`` converts the source unit to currency/kWh (``1000`` for per-MWh values).
    """
    if not slots:
        return ()
    if all(s.end is not None for s in slots):
        return tuple(_explicit_segments(slots, divisor))
    expected = resolution or infer_step([s.start for s in slots], None)
    other = {s for s in KNOWN_STEPS if s != expected}
    points = [(s.start, None if s.value is None else s.value / divisor) for s in slots]
    try:
        return segments_from_points(
            points, SeriesUnit.PRICE, expected_step=expected, other_steps=other
        )
    except ValueError as exc:
        raise MalformedPayloadError(str(exc)) from exc


def clip_series(series: TimeSeries, start: datetime, end: datetime) -> TimeSeries | None:
    """The part of ``series`` that overlaps ``[start, end)`` (whole slots), else ``None``."""
    if series.end <= start or series.start >= end:
        return None
    first = max(0, (start - series.start) // series.step)
    last = min(len(series), -(-(end - series.start) // series.step))
    if last <= first:
        return None
    return TimeSeries(
        start=series.slot_start(first),
        step=series.step,
        values=series.values[first:last],
        unit=series.unit,
    )


def as_attributes(value: Any) -> Mapping[str, Any]:
    """Attributes mapping of an entity state (never ``None``)."""
    return value if isinstance(value, Mapping) else {}
