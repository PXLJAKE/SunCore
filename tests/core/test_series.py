# SPDX-License-Identifier: Apache-2.0
"""Series contracts and resampling onto the 15-minute planning grid (Phase 4 WP2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    PriceSeries,
    QuantileSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
    energy_to_power,
    floor_to_step,
    planning_grid,
    resample_energy,
    resample_power,
    resample_prices,
    resample_quantiles,
    segments_from_points,
)

T0 = datetime(2026, 6, 1, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")
MIN15 = timedelta(minutes=15)
HOUR = timedelta(hours=1)


def ts(
    values: list[float | None],
    step: timedelta = HOUR,
    start: datetime = T0,
    unit: SeriesUnit = SeriesUnit.W,
) -> TimeSeries:
    return TimeSeries(start=start, step=step, values=tuple(values), unit=unit)


def prices(values: list[float | None], step: timedelta, start: datetime = T0) -> PriceSeries:
    return PriceSeries(
        source_id="tibber.price",
        price_includes="all_in",
        import_price=ts(values, step, start, SeriesUnit.PRICE),
    )


# --- contracts -------------------------------------------------------------------------


def test_time_series_basics() -> None:
    s = ts([1000.0, None, 500.0], start=T0.astimezone(BERLIN))
    assert s.start.tzinfo is UTC  # normalised
    assert s.end == T0 + 3 * HOUR
    assert len(s) == 3
    assert [b for b, _, _ in s.slots()] == [T0, T0 + HOUR, T0 + 2 * HOUR]
    assert s.total() is None
    assert ts([1000.0, 500.0]).total() == 1500.0
    assert ts([250.0, 250.0], MIN15, unit=SeriesUnit.WH).total() == 500.0
    with pytest.raises(ValueError, match="power or energy"):
        ts([1.0], unit=SeriesUnit.PERCENT).total()


@pytest.mark.parametrize(
    "bad",
    [
        {"step": timedelta(0)},
        {"step": timedelta(days=2)},
        {"values": (float("nan"),)},
        {"values": (float("inf"),)},
        {"start": datetime(2026, 1, 1)},  # noqa: DTZ001 - naive on purpose
    ],
)
def test_time_series_rejects(bad: dict[str, object]) -> None:
    doc: dict[str, object] = {"start": T0, "step": HOUR, "values": (1.0,), "unit": "W"} | bad
    with pytest.raises(ValidationError):
        TimeSeries.model_validate(doc)


def test_time_range() -> None:
    assert TimeRange(start=T0, end=T0 + HOUR).duration == HOUR
    with pytest.raises(ValidationError, match="start must be before end"):
        TimeRange(start=T0, end=T0)


def test_quantiles_validate_order_grid_and_unit() -> None:
    p10, p50, p90 = ts([1.0, None]), ts([2.0, 5.0]), ts([1.5, 6.0])
    q = QuantileSeries(p10=p10, p50=p50, p90=ts([3.0, None]))
    assert (q.start, q.step, q.unit, len(q)) == (T0, HOUR, SeriesUnit.W, 2)
    with pytest.raises(ValidationError, match="p10 <= p50 <= p90"):
        QuantileSeries(p10=p10, p50=p50, p90=p90)
    with pytest.raises(ValidationError, match="same grid"):
        QuantileSeries(p10=ts([1.0]), p50=p50, p90=p50)
    with pytest.raises(ValidationError, match="same unit"):
        QuantileSeries(p10=ts([1.0, 1.0], unit=SeriesUnit.WH), p50=p50, p90=p50)
    flat = QuantileSeries.deterministic(p50)
    assert flat.p10 == flat.p90 == p50


def test_price_series_validation() -> None:
    p = prices([0.3, 0.2], HOUR)
    assert p.resolution_min == 60
    assert len(p) == 2
    assert p.start == T0
    assert p.step == HOUR
    with pytest.raises(ValidationError, match="currency/kWh"):
        PriceSeries(source_id="x", price_includes="all_in", import_price=ts([0.3]))
    with pytest.raises(ValidationError, match="same grid"):
        PriceSeries(
            source_id="x",
            price_includes="spot_only",
            import_price=ts([0.3, 0.2], unit=SeriesUnit.PRICE),
            export_price=ts([0.1], unit=SeriesUnit.PRICE),
        )


# --- grid ------------------------------------------------------------------------------


def test_floor_and_planning_grid() -> None:
    at = datetime(2026, 6, 1, 10, 7, 30, tzinfo=BERLIN)
    assert floor_to_step(at) == datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    grid = planning_grid(at, timedelta(hours=48))
    assert grid.step == PLANNING_STEP
    assert grid.start == datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    assert grid.end >= at + timedelta(hours=48)
    assert grid.count == 193  # 48 h plus the started quarter hour
    with pytest.raises(ValidationError):
        Grid(start=T0, step=timedelta(0), count=1)


@pytest.mark.parametrize(
    ("day", "hours"),
    [(datetime(2026, 3, 29, tzinfo=BERLIN), 23), (datetime(2026, 10, 25, tzinfo=BERLIN), 25)],
)
def test_dst_days_have_23_and_25_hours_of_quarter_hours(day: datetime, hours: int) -> None:
    next_day = datetime(day.year, day.month, day.day + 1, tzinfo=BERLIN)
    grid = Grid(start=day, count=int((next_day.astimezone(UTC) - day.astimezone(UTC)) / MIN15))
    assert grid.count == hours * 4
    # An hourly price list in local time covers the DST day exactly.
    local_hours = [day.astimezone(UTC) + HOUR * i for i in range(hours)]
    held = resample_prices(prices([float(i) for i in range(hours)], HOUR, local_hours[0]), grid)
    assert None not in held.import_price.values
    assert held.import_price.values[-1] == hours - 1


# --- resampling ------------------------------------------------------------------------


def test_power_hourly_to_quarter_hours_holds_the_mean() -> None:
    grid = Grid(start=T0, count=8)
    out = resample_power(ts([1000.0, 2000.0]), grid)
    assert out.values == (1000.0,) * 4 + (2000.0,) * 4
    assert out.total() == ts([1000.0, 2000.0]).total()


def test_power_five_minutes_to_quarter_hours_averages() -> None:
    src = ts([0.0, 300.0, 600.0, 900.0, 900.0, 900.0], timedelta(minutes=5))
    out = resample_power(src, Grid(start=T0, count=2))
    assert out.values == (300.0, 900.0)


def test_partial_coverage_and_missing_values_give_none() -> None:
    src = ts([1000.0, None], MIN15, start=T0 + MIN15)
    out = resample_power(src, Grid(start=T0, count=4))
    assert out.values == (None, 1000.0, None, None)


def test_energy_is_split_by_overlap() -> None:
    src = ts([400.0], HOUR, unit=SeriesUnit.WH)
    out = resample_energy(src, Grid(start=T0, count=4))
    assert out.values == (100.0,) * 4
    assert energy_to_power(out).values == (400.0,) * 4
    with pytest.raises(ValueError, match="Wh"):
        resample_energy(ts([1.0]), Grid(start=T0, count=1))
    with pytest.raises(ValueError, match="Wh"):
        energy_to_power(ts([1.0]))
    with pytest.raises(ValueError, match="not intensive"):
        resample_power(src, Grid(start=T0, count=1))


def test_overlapping_or_mixed_unit_segments_are_refused() -> None:
    grid = Grid(start=T0, count=4)
    with pytest.raises(ValueError, match="overlap"):
        resample_power([ts([1.0, 1.0]), ts([1.0], start=T0 + MIN15)], grid)
    with pytest.raises(ValueError, match="same unit"):
        resample_power([ts([1.0]), ts([1.0], start=T0 + HOUR, unit=SeriesUnit.PERCENT)], grid)
    assert resample_power([], grid).values == (None,) * 4


def test_prices_step_hold_with_mixed_15_and_60_minutes() -> None:
    today = prices([0.30, 0.31, 0.32, 0.33], MIN15)
    tomorrow = prices([0.20, 0.10], HOUR, start=T0 + HOUR)
    out = resample_prices([tomorrow, today], Grid(start=T0, count=12))
    assert out.import_price.values == (0.30, 0.31, 0.32, 0.33) + (0.20,) * 4 + (0.10,) * 4
    assert out.export_price is None
    assert out.source_id == "tibber.price"
    with pytest.raises(ValueError, match="different sources"):
        resample_prices(
            [today, tomorrow.model_copy(update={"source_id": "x"})], Grid(start=T0, count=1)
        )
    with pytest.raises(ValueError, match="no input"):
        resample_prices([], Grid(start=T0, count=1))


def test_export_prices_are_kept_when_every_segment_has_them() -> None:
    p = PriceSeries(
        source_id="nordpool",
        price_includes="spot_only",
        import_price=ts([0.1, -0.05], HOUR, unit=SeriesUnit.PRICE),
        export_price=ts([0.05, -0.05], HOUR, unit=SeriesUnit.PRICE),
    )
    out = resample_prices(p, Grid(start=T0, count=8))
    assert out.export_price is not None
    assert out.export_price.values == (0.05,) * 4 + (-0.05,) * 4
    assert out.price_includes == "spot_only"


def test_segments_from_points_split_on_resolution_change() -> None:
    points = [(T0 + MIN15 * i, float(i)) for i in range(4)] + [
        (T0 + HOUR, 10.0),
        (T0 + 2 * HOUR, 20.0),
    ]
    segments = segments_from_points(points, SeriesUnit.PRICE)
    assert [(s.step, len(s)) for s in segments] == [(MIN15, 4), (HOUR, 2)]
    assert segments[-1].end == T0 + 3 * HOUR
    single = segments_from_points([(T0, 1.0)], SeriesUnit.W, last_step=MIN15)
    assert single[0].end == T0 + MIN15
    assert segments_from_points([], SeriesUnit.W) == ()
    with pytest.raises(ValueError, match="last_step"):
        segments_from_points([(T0, 1.0)], SeriesUnit.W)
    with pytest.raises(ValueError, match="strictly increasing"):
        segments_from_points([(T0, 1.0), (T0, 2.0)], SeriesUnit.W)


# --- properties --------------------------------------------------------------------------

_power = st.floats(min_value=0, max_value=30_000, allow_nan=False)
_step_min = st.sampled_from([5, 15, 30, 60])
_offset_q = st.integers(min_value=-8, max_value=8)


@settings(max_examples=150, deadline=None)
@given(values=st.lists(_power, min_size=1, max_size=96), step_min=_step_min)
def test_resampling_power_preserves_energy(values: list[float], step_min: int) -> None:
    step = timedelta(minutes=step_min)
    src = ts(list(values), step)
    # Source is aligned to the hour, so a grid that spans it exactly covers every interval.
    span = step * len(values)
    count = -(-span // MIN15)
    grid = Grid(start=T0, count=count)
    out = resample_power(src, grid)
    known = [v for v in out.values if v is not None]
    covered = len(known) * MIN15
    # Only the last, partially covered quarter hour may be None.
    assert len(out.values) - len(known) <= 1
    energy_out = sum(known) * MIN15 / HOUR
    energy_in = sum(
        v * (min(b + step, T0 + covered) - b) / HOUR
        for v, (b, _, _) in zip(values, src.slots(), strict=True)
        if b < T0 + covered
    )
    assert energy_out == pytest.approx(energy_in, rel=1e-9, abs=1e-6)


@settings(max_examples=150, deadline=None)
@given(
    values=st.lists(st.floats(0, 10_000, allow_nan=False), min_size=1, max_size=48),
    step_min=st.sampled_from([15, 30, 60]),
)
def test_resampling_energy_preserves_energy(values: list[float], step_min: int) -> None:
    src = ts(list(values), timedelta(minutes=step_min), unit=SeriesUnit.WH)
    grid = Grid(start=T0, count=len(values) * step_min // 15)
    out = resample_energy(src, grid)
    assert out.total() == pytest.approx(src.total(), rel=1e-9, abs=1e-6)


@settings(max_examples=100, deadline=None)
@given(
    rows=st.lists(st.tuples(_power, _power, _power).map(sorted), min_size=1, max_size=48),
    step_min=_step_min,
    offset=_offset_q,
)
def test_resampling_keeps_quantile_order(
    rows: list[list[float]], step_min: int, offset: int
) -> None:
    step = timedelta(minutes=step_min)
    q = QuantileSeries(
        p10=ts([r[0] for r in rows], step),
        p50=ts([r[1] for r in rows], step),
        p90=ts([r[2] for r in rows], step),
    )
    grid = Grid(start=T0 + MIN15 * offset, count=len(rows) * 4)
    out = resample_quantiles(q, grid)  # the validator re-checks the order
    assert len(out) == grid.count


@settings(max_examples=100, deadline=None)
@given(
    values=st.lists(st.floats(-0.5, 1.0, allow_nan=False), min_size=1, max_size=48),
    step_min=st.sampled_from([15, 60]),
)
def test_price_hold_never_invents_prices(values: list[float], step_min: int) -> None:
    step = timedelta(minutes=step_min)
    grid = Grid(start=T0, count=len(values) * step_min // 15)
    out = resample_prices(prices(list(values), step), grid)
    for i, v in enumerate(out.import_price.values):
        assert v == values[(i * 15) // step_min]
