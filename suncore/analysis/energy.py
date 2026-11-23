# SPDX-License-Identifier: Apache-2.0
"""Quarter-hour site energy from mean-power series (Phase 5 WP2, ADR-0014).

``suncore.core.series`` resamples the recorded means (W) onto a grid in an energy-preserving
way; this module turns them into energy per slot (Wh) and drops every slot in which a needed
signal is missing (never replaced by zero). The share of kept slots is the coverage; a period
below :data:`MIN_COVERAGE` counts as incomplete (ADR-0014 decision 4).

Signs: ``grid_wh`` + = import, ``battery_wh`` + = charging, ``pv_wh`` + = generation,
``house_wh`` + = consumption. Without a measured house load it is derived from the balance
``house = grid + pv - battery``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from suncore.core.series import Grid, TimeSeries, resample_power

MIN_COVERAGE: Final = 0.8
"""Below this share of measured slots a day is incomplete and excluded from averages."""


@dataclass(frozen=True, slots=True)
class SlotEnergy:
    """Energy (Wh) of one interval ``[start, start + step)``; ``start`` is UTC."""

    start: datetime
    pv_wh: float
    grid_wh: float
    battery_wh: float
    house_wh: float


@dataclass(frozen=True, slots=True)
class SiteEnergy:
    """The complete slots of a grid and how many the grid asked for."""

    start: datetime
    step: timedelta
    total_slots: int
    slots: tuple[SlotEnergy, ...]

    @property
    def coverage(self) -> float:
        """Share of the requested slots with all signals present, in [0, 1]."""
        return len(self.slots) / self.total_slots if self.total_slots else 0.0

    @property
    def complete(self) -> bool:
        return self.coverage >= MIN_COVERAGE


def _wh(series: TimeSeries) -> tuple[float | None, ...]:
    hours = series.step / timedelta(hours=1)
    return tuple(None if v is None else v * hours for v in series.values)


def build_site_energy(
    *,
    pv_w: TimeSeries,
    grid_w: TimeSeries,
    battery_w: TimeSeries,
    house_w: TimeSeries | None,
    grid: Grid,
) -> SiteEnergy:
    """Energy per slot of ``grid`` from the mean-power series of the site measurements.

    A slot is kept only if PV, grid and battery (and the house load, when given) are known for
    the whole interval.
    """
    pv = _wh(resample_power(pv_w, grid))
    grd = _wh(resample_power(grid_w, grid))
    bat = _wh(resample_power(battery_w, grid))
    house = _wh(resample_power(house_w, grid)) if house_w is not None else None
    slots: list[SlotEnergy] = []
    for i, start in enumerate(grid.starts()):
        p, g, b = pv[i], grd[i], bat[i]
        if p is None or g is None or b is None:
            continue
        h = g + p - b if house is None else house[i]
        if h is None:
            continue
        slots.append(SlotEnergy(start=start, pv_wh=p, grid_wh=g, battery_wh=b, house_wh=h))
    return SiteEnergy(start=grid.start, step=grid.step, total_slots=grid.count, slots=tuple(slots))
