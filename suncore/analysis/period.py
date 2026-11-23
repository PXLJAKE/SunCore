# SPDX-License-Identifier: Apache-2.0
"""Day, week, month and year buckets in a time zone (Phase 5 WP2, ADR-0014).

A bucket is a half-open absolute range ``[start, end)`` between two local midnights, so a DST
day is 23 or 25 hours long and the buckets tile the time line without gaps or overlaps. A slot
belongs to the bucket that contains its start; quarter-hour slots never straddle a local
midnight because every zone's offset is a multiple of 15 minutes. Weeks are ISO weeks (Monday
first, the key uses the ISO year). Where a zone has no local midnight (a DST gap at 00:00) the
bucket starts at the instant the clock jumps to.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum, unique

from suncore.analysis.energy import SlotEnergy


@unique
class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class Bucket:
    key: str
    """``2026-03-29`` (day), ``2026-W13`` (ISO week), ``2026-03`` (month), ``2026`` (year)."""
    first_day: date
    start: datetime
    """Absolute start (UTC)."""
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def _local_midnight(day: date, tz: tzinfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def _first_day(day: date, granularity: Granularity) -> date:
    if granularity is Granularity.DAY:
        return day
    if granularity is Granularity.WEEK:
        return day - timedelta(days=day.weekday())
    if granularity is Granularity.MONTH:
        return day.replace(day=1)
    return day.replace(month=1, day=1)


def _next_first_day(first: date, granularity: Granularity) -> date:
    if granularity is Granularity.DAY:
        return first + timedelta(days=1)
    if granularity is Granularity.WEEK:
        return first + timedelta(days=7)
    if granularity is Granularity.MONTH:
        if first.month == 12:
            return first.replace(year=first.year + 1, month=1)
        return first.replace(month=first.month + 1)
    return first.replace(year=first.year + 1)


def _key(first: date, granularity: Granularity) -> str:
    if granularity is Granularity.DAY:
        return first.isoformat()
    if granularity is Granularity.WEEK:
        iso = first.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if granularity is Granularity.MONTH:
        return f"{first.year}-{first.month:02d}"
    return f"{first.year}"


def _bucket(first: date, granularity: Granularity, tz: tzinfo) -> Bucket:
    return Bucket(
        key=_key(first, granularity),
        first_day=first,
        start=_local_midnight(first, tz),
        end=_local_midnight(_next_first_day(first, granularity), tz),
    )


def bucket_for(at: datetime, granularity: Granularity, tz: tzinfo) -> Bucket:
    """The bucket that contains the instant ``at``."""
    first = _first_day(at.astimezone(tz).date(), granularity)
    bucket = _bucket(first, granularity, tz)
    if at.astimezone(UTC) < bucket.start:  # only in a zone whose day starts after local 00:00
        bucket = _bucket(_first_day(first - timedelta(days=1), granularity), granularity, tz)
    return bucket


def buckets_between(
    start: datetime, end: datetime, granularity: Granularity, tz: tzinfo
) -> list[Bucket]:
    """Every bucket that overlaps ``[start, end)``, in order (empty if ``end <= start``)."""
    if end <= start:
        return []
    out: list[Bucket] = []
    bucket = bucket_for(start, granularity, tz)
    limit = end.astimezone(UTC)
    while bucket.start < limit:
        out.append(bucket)
        bucket = _bucket(_next_first_day(bucket.first_day, granularity), granularity, tz)
    return out


def group_slots(
    slots: Iterable[SlotEnergy], granularity: Granularity, tz: tzinfo
) -> list[tuple[Bucket, list[SlotEnergy]]]:
    """Slots grouped by bucket, buckets in time order; every slot lands in exactly one."""
    groups: dict[str, tuple[Bucket, list[SlotEnergy]]] = {}
    cached: Bucket | None = None
    for slot in slots:
        if cached is None or not cached.start <= slot.start < cached.end:
            cached = bucket_for(slot.start, granularity, tz)
        entry = groups.setdefault(cached.key, (cached, []))
        entry[1].append(slot)
    return sorted(groups.values(), key=lambda item: item[0].start)
