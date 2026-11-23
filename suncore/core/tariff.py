# SPDX-License-Identifier: Apache-2.0
"""Tariff mathematics (Phase 4 WP2): weekly time windows, price formula, HT/NT, export tiers.

Pure and frozen. Prices are currency/kWh in the region's currency; spot prices may be
negative. Time windows are *local* wall-clock windows; callers must pass the region's time
zone (``tz`` is required everywhere - an aware ``at`` in UTC would otherwise silently be
judged in UTC), the conversion happens here (``datetime.astimezone``), so DST days need no
special case.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Annotated, Final, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from suncore.core.forecast import PriceSource
from suncore.core.validation import invalid

HHMM_PATTERN: Final = r"^([01]\d|2[0-3]):[0-5]\d$"
"""Local wall-clock time ``HH:MM`` (24 h)."""

ALL_DAYS: Final = frozenset(range(7))

Hhmm = Annotated[str, StringConstraints(pattern=HHMM_PATTERN)]
Weekday = Annotated[int, Field(ge=0, le=6)]
"""0 = Monday ... 6 = Sunday (``datetime.weekday()``)."""
Month = Annotated[int, Field(ge=1, le=12)]
UnitPrice = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
"""Non-negative currency/kWh component (fees, levies, fixed prices)."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _local(at: datetime, tz: tzinfo) -> datetime:
    if at.tzinfo is None:
        msg = "time windows need an aware datetime"
        raise ValueError(msg)
    return at.astimezone(tz)


# --- weekly windows --------------------------------------------------------------------


class TouWindow(_Frozen):
    """A local time window on a set of weekdays, e.g. NT Mon-Fri 22:00-06:00.

    * ``end`` before ``start`` crosses midnight; the part after midnight belongs to the
      weekday the window *started* on (Fri 22:00-06:00 covers Saturday 00:00-06:00, not
      Friday 00:00-06:00).
    * ``start == end`` is the whole day (00:00-00:00 = 24 h).
    * ``start`` is included, ``end`` is excluded.
    """

    days: frozenset[Weekday] = ALL_DAYS
    start: Hhmm
    end: Hhmm

    @model_validator(mode="after")
    def _days(self) -> Self:
        if not self.days:
            raise invalid("window_no_days", "window: at least one weekday is required")
        return self

    @property
    def crosses_midnight(self) -> bool:
        return _minutes(self.end) < _minutes(self.start)

    def contains(self, at: datetime, tz: tzinfo) -> bool:
        """Whether ``at`` (aware) lies in the window, judged in the local zone ``tz``."""
        return self.occurrence_date(at, tz) is not None

    def occurrence_date(self, at: datetime, tz: tzinfo) -> date | None:
        """Local date the occurrence containing ``at`` *started* on; ``None`` = outside.

        For a window crossing midnight the part after midnight belongs to the previous day
        (Fri 22:00-06:00 at Sat 03:00 -> Friday's date); seasonal rules (months) use this
        date, so a window starting on 31 October counts as October until it ends.
        """
        local = _local(at, tz)
        minute = local.hour * 60 + local.minute
        start, end = _minutes(self.start), _minutes(self.end)
        today = local.date()
        if start == end:
            return today if today.weekday() in self.days else None
        if start < end:
            inside = today.weekday() in self.days and start <= minute < end
            return today if inside else None
        if today.weekday() in self.days and minute >= start:
            return today
        yesterday = today - timedelta(days=1)
        if yesterday.weekday() in self.days and minute < end:
            return yesterday
        return None


class WeeklySchedule(_Frozen):
    """Union of weekly windows (grid-charge schedule, HT/NT windows, §14a module 3)."""

    windows: Annotated[tuple[TouWindow, ...], Field(min_length=1, max_length=28)]

    def contains(self, at: datetime, tz: tzinfo) -> bool:
        return any(w.contains(at, tz) for w in self.windows)

    @classmethod
    def daily(cls, start: str, end: str) -> WeeklySchedule:
        """One window every day, e.g. ``daily("22:00", "06:00")``."""
        return cls(windows=(TouWindow(start=start, end=end),))


NIGHT_WINDOW: Final = WeeklySchedule.daily("22:00", "06:00")
"""Plan §6 question 7 default night window, the target of the ``goals`` v1 migration."""


# --- price formula ---------------------------------------------------------------------


def _in_season(window: TouWindow, months: frozenset[int] | None, at: datetime, tz: tzinfo) -> bool:
    """``at`` lies in ``window`` and the occurrence *started* in one of ``months``."""
    started = window.occurrence_date(at, tz)
    return started is not None and (months is None or started.month in months)


class GridFeeWindow(_Frozen):
    """A grid fee that applies inside ``window`` (time-variable grid fee, §14a module 3).

    ``months`` restricts it to some months (module 3 fees are set per quarter, e.g. only
    Q1 and Q4); ``None`` = all year. The month is that of the day the occurrence started
    (see :meth:`TouWindow.occurrence_date`).
    """

    window: TouWindow
    fee: UnitPrice
    months: frozenset[Month] | None = None

    def applies(self, at: datetime, tz: tzinfo) -> bool:
        return _in_season(self.window, self.months, at, tz)


class PriceFormula(_Frozen):
    """Spot price -> end price (BRIEF §4.4).

    ``end = (spot * spot_multiplier + grid_fee(at) + levies + margin) * (1 + vat_pct/100)``

    ``grid_fee(at)`` is the fee of the first ``grid_fee_windows`` entry containing ``at``,
    else ``grid_fee``. VAT is applied to the whole sum, as on a German bill; a negative
    spot price can make the end price negative. Only for ``price_includes = spot_only``
    sources - an all-in price never gets the formula on top.
    """

    spot_multiplier: float = Field(default=1.0, gt=0, le=10, allow_inf_nan=False)
    """E.g. 1.0; some contracts pass the spot price on with a factor."""
    grid_fee: UnitPrice = 0.0
    grid_fee_windows: Annotated[tuple[GridFeeWindow, ...], Field(max_length=12)] = ()
    levies: UnitPrice = 0.0
    """Taxes and levies per kWh (e.g. electricity tax, surcharges), before VAT."""
    margin: UnitPrice = 0.0
    """Supplier margin per kWh, before VAT."""
    vat_pct: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)

    def grid_fee_at(self, at: datetime | None, tz: tzinfo) -> float:
        if at is not None:
            for entry in self.grid_fee_windows:
                if entry.applies(at, tz):
                    return entry.fee
        return self.grid_fee

    def apply(self, spot: float, at: datetime | None, tz: tzinfo) -> float:
        """End price for ``spot`` at ``at`` (``None``: the base grid fee is used)."""
        net = spot * self.spot_multiplier + self.grid_fee_at(at, tz) + self.levies + self.margin
        return net * (1 + self.vat_pct / 100)


# --- HT/NT (time of use) ---------------------------------------------------------------


class TouPeriod(_Frozen):
    """A price that applies inside ``window``, optionally only in some ``months``."""

    window: TouWindow
    price: UnitPrice
    months: frozenset[Month] | None = None
    """``None`` = all year (seasonal tariffs list the months). The month of a window that
    crosses midnight is that of the day it started (:meth:`TouWindow.occurrence_date`)."""

    def applies(self, at: datetime, tz: tzinfo) -> bool:
        return _in_season(self.window, self.months, at, tz)


class TouTariff(_Frozen):
    """Fixed time-of-use import tariff (HT/NT, seasonal).

    The first period in list order that applies wins; outside every period ``base_price``
    applies. Overlaps are therefore allowed and resolved by order (put the special case
    first).
    """

    base_price: UnitPrice
    periods: Annotated[tuple[TouPeriod, ...], Field(min_length=1, max_length=24)]

    def price_at(self, at: datetime, tz: tzinfo) -> float:
        for period in self.periods:
            if period.applies(at, tz):
                return period.price
        return self.base_price


# --- export tiers ----------------------------------------------------------------------


class PriceTier(Protocol):
    """A feed-in band up to ``up_to_kwp`` (``None`` = unbounded) with its rate."""

    @property
    def up_to_kwp(self) -> float | None: ...

    @property
    def price(self) -> float: ...


def blended_export_price(tiers: Sequence[PriceTier], total_kwp: float) -> float:
    """Feed-in rate of a plant split into kWp bands (German EEG "anteilig" rule).

    A 17 kWp plant with ``<= 10 kWp: a``, ``rest: b`` earns ``(10 a + 7 b) / 17`` per kWh.
    Tiers must be ordered with strictly increasing bounds and only the last one unbounded
    (``ExportTariff`` validates that). Raises ``ValueError`` if ``total_kwp`` is not
    positive or the tiers do not cover it - SunCore never guesses a rate.
    """
    if not total_kwp > 0:
        msg = "blended_export_price: total_kwp must be positive"
        raise ValueError(msg)
    if not tiers:
        msg = "blended_export_price: no tiers"
        raise ValueError(msg)
    bounds = [t.up_to_kwp for t in tiers]
    if None in bounds[:-1] or any(
        a is not None and b is not None and a >= b for a, b in itertools.pairwise(bounds)
    ):
        msg = "blended_export_price: tiers must be ordered, only the last may be unbounded"
        raise ValueError(msg)
    revenue = 0.0
    lower = 0.0
    for tier in tiers:
        upper = total_kwp if tier.up_to_kwp is None else min(tier.up_to_kwp, total_kwp)
        if upper > lower:
            revenue += (upper - lower) * tier.price
            lower = upper
        if lower >= total_kwp:
            return revenue / total_kwp
    msg = f"blended_export_price: tiers end at {lower} kWp below the plant's {total_kwp} kWp"
    raise ValueError(msg)


def hhmm(value: time) -> str:
    """``time`` -> ``HH:MM`` (seconds are dropped)."""
    return f"{value.hour:02d}:{value.minute:02d}"


def window_duration(window: TouWindow) -> timedelta:
    """Length of one occurrence of the window."""
    start, end = _minutes(window.start), _minutes(window.end)
    minutes = (end - start) % (24 * 60) or 24 * 60
    return timedelta(minutes=minutes)


__all__ = [
    "ALL_DAYS",
    "HHMM_PATTERN",
    "NIGHT_WINDOW",
    "GridFeeWindow",
    "PriceFormula",
    "PriceSource",
    "PriceTier",
    "TouPeriod",
    "TouTariff",
    "TouWindow",
    "WeeklySchedule",
    "blended_export_price",
    "hhmm",
    "window_duration",
]
