# SPDX-License-Identifier: Apache-2.0
"""Clipping detection: quarter hours whose measured PV was limited (Phase 4 WP5).

When the inverter's AC rating or the grid's feed-in limit caps the plant, the measured PV is
smaller than what the sun gave. Such a slot would read as a forecast error (a too-high forecast)
and would teach the blending weights and the bias calibration the wrong lesson, so the accuracy
job and the calibration learner skip it (``is_clipped`` hook). Everything here works on the
15-minute means of ``timeseries.db``; values in W, grid + = import (export is negative).

Two signals, both conservative (a missed clip costs a little accuracy; a false one only drops a
sample):

**Export limit** (``site.grid.export_limit_w``, or ``export_limit_pct_kwp`` x the installed kWp)
    A slot is flagged when the mean grid export lies within ``max(3 % of the limit, 50 W)`` of
    the limit **and** the reference forecast is higher than the measured PV (when one is known).
    The plateau of an export limit shows in the *grid export*, not in the PV: a limiting inverter
    lets the PV follow the house load (PV = limit + load + battery charge), so the export pinned
    at the limit is the flat signal, and "PV flat" is not required here.

**Inverter plateau** (derived from the data)
    SunCore has no inverter AC rating in its configuration (``PvArray.inverter`` is a name), so
    none is invented: the cap is the PV level that the data itself repeats. The window's largest
    PV value is the cap when on at least ``MIN_CAP_DAYS`` local days the PV stayed for at least
    ``MIN_RUN`` consecutive quarter hours within ``PLATEAU_TOLERANCE`` of it - a natural noon
    peak is rounded and noisy, a clipping inverter is flat at the same value every sunny day. A
    slot is flagged when its PV is within ``CAP_BAND`` of the cap and the reference forecast is
    higher (when known).

The **reference forecast** is the mean of the sources' P50 as they stood at the start of each
day (see :mod:`suncore.forecast.pv.learning`); "higher" means more than ``FORECAST_MARGIN``
above the measured PV.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, tzinfo
from typing import Final

from suncore.core.series import TimeSeries
from suncore.core.site import SiteConfig

PLATEAU_TOLERANCE: Final = 0.003
"""Relative spread of a plateau's quarter hours around the cap."""
MIN_RUN: Final = 4
"""Consecutive quarter hours at the cap that make a plateau (1 h; a noise-free clear-sky noon
stays within 0.3 % of its peak for about 45 min around the solstice, a clipping inverter for
hours)."""
MIN_CAP_DAYS: Final = 2
"""Days with a plateau at the same level before it counts as the inverter's cap."""
CAP_BAND: Final = 0.02
"""A slot within this share below the cap counts as clipped."""
MIN_CAP_W: Final = 200.0
"""A cap below this is not a cap (night noise, a broken sensor)."""
EXPORT_TOLERANCE_SHARE: Final = 0.03
EXPORT_TOLERANCE_MIN_W: Final = 50.0
FORECAST_MARGIN: Final = 0.02


def export_limit_w(site: SiteConfig | None) -> float | None:
    """The static feed-in limit in W from the site, or ``None`` (no limit configured)."""
    if site is None:
        return None
    grid = site.grid
    if grid.export_limit_w is not None:
        return grid.export_limit_w
    if grid.export_limit_pct_kwp is not None and site.total_kwp is not None:
        return grid.export_limit_pct_kwp / 100 * site.total_kwp * 1000
    return None


def plateau_cap_w(pv: TimeSeries, tz: tzinfo) -> float | None:
    """The inverter's cap as the data shows it (module doc), or ``None``."""
    known = [v for v in pv.values if v is not None]
    if not known:
        return None
    top = max(known)
    if top < MIN_CAP_W:
        return None
    floor = top * (1 - PLATEAU_TOLERANCE)
    days: set[date] = set()
    run = 0
    for begin, _, value in pv.slots():
        if value is not None and floor <= value <= top:
            run += 1
            if run >= MIN_RUN:
                days.add(begin.astimezone(tz).date())
        else:
            run = 0
    return top if len(days) >= MIN_CAP_DAYS else None


def _value_at(series: TimeSeries | None, begin: datetime) -> float | None:
    if series is None or not len(series):
        return None
    offset = (begin - series.start) / series.step
    index = int(offset)
    if index != offset or not 0 <= index < len(series):
        return None
    return series.values[index]


def _forecast_higher(reference: TimeSeries | None, begin: datetime, measured: float) -> bool:
    """Whether the reference forecast says more than was measured (``True`` if unknown)."""
    predicted = _value_at(reference, begin)
    if predicted is None:
        return reference is None
    return predicted > measured * (1 + FORECAST_MARGIN)


def clipped_slots(
    pv: TimeSeries,
    *,
    tz: tzinfo,
    grid: TimeSeries | None = None,
    export_limit: float | None = None,
    reference: TimeSeries | None = None,
    cap_w: float | None = None,
) -> frozenset[datetime]:
    """Start times of the quarter hours of ``pv`` that were limited (module doc).

    ``grid`` and ``reference`` are 15-minute series in W (any extent; slots they lack are
    unknown). ``cap_w`` overrides the plateau found in ``pv`` itself. With a ``reference``,
    a slot it does not cover is not flagged; without one the forecast condition is skipped.
    """
    cap = cap_w if cap_w is not None else plateau_cap_w(pv, tz)
    tolerance = (
        None
        if export_limit is None
        else max(EXPORT_TOLERANCE_SHARE * export_limit, EXPORT_TOLERANCE_MIN_W)
    )
    flagged: set[datetime] = set()
    for begin, _, measured in pv.slots():
        if measured is None or measured <= 0:
            continue
        at_cap = cap is not None and measured >= cap * (1 - CAP_BAND)
        at_export = False
        if export_limit is not None and tolerance is not None and export_limit > 0:
            grid_w = _value_at(grid, begin)
            at_export = grid_w is not None and -grid_w >= export_limit - tolerance
        if (at_cap or at_export) and _forecast_higher(reference, begin, measured):
            flagged.add(begin)
    return frozenset(flagged)


def clip_filter(slots: Iterable[datetime]) -> Callable[[datetime], bool]:
    """``is_clipped`` hook over a set of flagged slot starts."""
    flagged = frozenset(slots)

    def is_clipped(slot_start: datetime) -> bool:
        return slot_start in flagged

    return is_clipped


__all__ = [
    "CAP_BAND",
    "MIN_CAP_DAYS",
    "MIN_RUN",
    "PLATEAU_TOLERANCE",
    "clip_filter",
    "clipped_slots",
    "export_limit_w",
    "plateau_cap_w",
]
