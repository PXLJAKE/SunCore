# SPDX-License-Identifier: Apache-2.0
"""Clipping detection: export-limit plateau, inverter plateau from the data, natural peaks."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from suncore.core.series import SeriesUnit, TimeSeries
from suncore.core.site import EntityRef, GridConfig, PvArray, SiteConfig
from suncore.forecast.accuracy import quarter_errors
from suncore.forecast.pv.clearsky import ArrayGeometry, PlantGeometry, clear_sky_slot_w
from suncore.forecast.pv.clipping import (
    clip_filter,
    clipped_slots,
    export_limit_w,
    plateau_cap_w,
)

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)
PLANT = PlantGeometry(48.0, 11.0, (ArrayGeometry(10.0, 180.0, 30.0),))


def days_series(first: date, days: int, fn: Any) -> TimeSeries:
    start = datetime.combine(first, time(), tzinfo=BERLIN).astimezone(UTC)
    count = days * 96
    return TimeSeries(
        start=start,
        step=Q,
        values=tuple(fn(start + Q * i) for i in range(count)),
        unit=SeriesUnit.W,
    )


def sun(kt: float = 0.8) -> Any:
    return lambda begin: kt * clear_sky_slot_w(PLANT, begin, Q)


def test_export_limit_from_watts_or_percent_of_kwp() -> None:
    meter = EntityRef(entity_id="sensor.grid")
    watts = SiteConfig(grid=GridConfig(power=meter, export_limit_w=5000))
    assert export_limit_w(watts) == 5000
    pct = SiteConfig(
        grid=GridConfig(power=meter, export_limit_pct_kwp=70),
        pv=[PvArray(name="a", kwp=6.0), PvArray(name="b", kwp=4.0)],
    )
    assert export_limit_w(pct) == 7000
    assert export_limit_w(SiteConfig(grid=GridConfig(power=meter))) is None
    assert export_limit_w(None) is None


def test_a_plateau_at_the_export_limit_is_flagged() -> None:
    limit = 4000.0
    load = 500.0
    first = date(2026, 6, 21)
    potential = days_series(first, 1, sun())
    pv = TimeSeries(
        start=potential.start,
        step=Q,
        values=tuple(min(v or 0.0, limit + load) for v in potential.values),
        unit=SeriesUnit.W,
    )
    grid = TimeSeries(
        start=pv.start,
        step=Q,
        values=tuple(load - (v or 0.0) for v in pv.values),
        unit=SeriesUnit.W,
    )
    flagged = clipped_slots(
        pv, tz=BERLIN, grid=grid, export_limit=limit, reference=potential, cap_w=None
    )
    expected = {
        b
        for (b, _, p), (_, _, f) in zip(pv.slots(), potential.slots(), strict=True)
        if f is not None and p is not None and f > p * 1.02
    }
    assert flagged
    assert flagged == expected
    # without the forecast condition the flat export alone flags the same slots
    assert clipped_slots(pv, tz=BERLIN, grid=grid, export_limit=limit) >= flagged


def test_export_at_the_limit_is_not_clipping_when_the_forecast_was_not_higher() -> None:
    limit = 4000.0
    pv = days_series(date(2026, 6, 21), 1, lambda _b: 4500.0)
    grid = days_series(date(2026, 6, 21), 1, lambda _b: -4000.0)
    assert not clipped_slots(pv, tz=BERLIN, grid=grid, export_limit=limit, reference=pv)


def test_the_inverter_cap_is_derived_from_repeated_plateaus() -> None:
    cap = 6000.0
    potential = days_series(date(2026, 6, 20), 3, sun(0.9))
    pv = TimeSeries(
        start=potential.start,
        step=Q,
        values=tuple(min(v or 0.0, cap) for v in potential.values),
        unit=SeriesUnit.W,
    )
    assert plateau_cap_w(pv, BERLIN) == cap
    flagged = clipped_slots(pv, tz=BERLIN, reference=potential)
    assert flagged
    assert all(pv.values[(b - pv.start) // Q] == cap for b in flagged)


def test_a_natural_peak_is_no_cap() -> None:
    pv = days_series(date(2026, 6, 20), 3, sun(0.8))
    assert plateau_cap_w(pv, BERLIN) is None
    assert not clipped_slots(pv, tz=BERLIN, reference=pv)


def test_one_clipped_day_is_not_enough_for_a_cap() -> None:
    cap = 6000.0
    potential = days_series(date(2026, 6, 20), 3, sun(0.9))
    values = list(potential.values)
    values[:96] = [min(v or 0.0, cap) for v in values[:96]]
    values[96:] = [0.5 * (v or 0.0) for v in values[96:]]  # two dull days
    pv = TimeSeries(start=potential.start, step=Q, values=tuple(values), unit=SeriesUnit.W)
    assert plateau_cap_w(pv, BERLIN) is None


def test_clipped_slots_are_excluded_from_the_accuracy() -> None:
    limit = 4000.0
    potential = days_series(date(2026, 6, 21), 1, sun())
    pv = TimeSeries(
        start=potential.start,
        step=Q,
        values=tuple(min(v or 0.0, limit) for v in potential.values),
        unit=SeriesUnit.W,
    )
    grid = TimeSeries(
        start=pv.start, step=Q, values=tuple(-(v or 0.0) for v in pv.values), unit=SeriesUnit.W
    )
    flagged = clipped_slots(pv, tz=BERLIN, grid=grid, export_limit=limit, reference=potential)
    all_errors = quarter_errors(potential, pv)
    kept = quarter_errors(potential, pv, is_clipped=clip_filter(flagged))
    assert len(kept) == len(all_errors) - len(flagged)
    assert min(all_errors) < -100.0
    assert min(kept) > -1e-6  # the forecast was exact wherever nothing was clipped
