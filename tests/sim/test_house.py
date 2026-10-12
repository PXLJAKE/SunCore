# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.sim.house import (
    BatteryParams,
    House,
    HouseParams,
    LoadParams,
    PVParams,
    clear_sky_w,
    sin_elevation,
)

TOL = 1e-6


@st.composite
def batteries(draw: st.DrawFn) -> BatteryParams:
    lo = draw(st.floats(0, 60))
    hi = draw(st.floats(lo, 100))
    return BatteryParams(
        capacity_wh=draw(st.one_of(st.just(0.0), st.floats(500, 50_000))),
        max_charge_w=draw(st.floats(0, 12_000)),
        max_discharge_w=draw(st.floats(0, 12_000)),
        round_trip_efficiency=draw(st.floats(0.5, 1.0)),
        soc_min_pct=lo,
        soc_max_pct=hi,
        initial_soc_pct=draw(st.floats(lo, hi)),
    )


@st.composite
def houses(draw: st.DrawFn) -> tuple[House, int]:
    params = HouseParams(
        pv=PVParams(
            kwp=draw(st.floats(0, 30)),
            latitude=draw(st.floats(-60, 60)),
            longitude=draw(st.floats(-180, 180)),
            performance_ratio=draw(st.floats(0.5, 1.0)),
        ),
        load=LoadParams(noise_fraction=draw(st.floats(0, 1))),
        battery=draw(batteries()),
        seed=draw(st.integers(0, 2**31)),
    )
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=draw(st.integers(0, 365 * 86400)))
    step_s = draw(st.sampled_from([1, 10, 30, 60, 300, 900, 3600]))
    cloud = draw(st.lists(st.floats(0, 1), max_size=20))
    steps = draw(st.integers(1, 120))
    return House(params, start, step_s, cloud), steps


@settings(max_examples=150, deadline=None)
@given(houses())
def test_balance_closes_every_step(case: tuple[House, int]) -> None:
    house, steps = case
    for s in house.run(steps):
        assert s.pv_w + s.grid_w == pytest.approx(s.load_w + s.battery_w, abs=TOL)


@settings(max_examples=150, deadline=None)
@given(houses())
def test_battery_limits_and_soc_bounds(case: tuple[House, int]) -> None:
    house, steps = case
    b = house.params.battery
    for s in house.run(steps):
        assert b.soc_min_pct - TOL <= s.soc_pct <= b.soc_max_pct + TOL
        assert -b.max_discharge_w - TOL <= s.battery_w <= b.max_charge_w + TOL
        if b.capacity_wh == 0:
            assert s.battery_w == 0


@settings(max_examples=150, deadline=None)
@given(houses())
def test_pv_bounds(case: tuple[House, int]) -> None:
    house, steps = case
    kwp_w = house.params.pv.kwp * 1000
    for s in house.run(steps):
        assert 0 <= s.pv_w <= kwp_w + TOL
        assert s.load_w >= 0


@settings(max_examples=100, deadline=None)
@given(houses())
def test_soc_energy_consistent_with_efficiency(case: tuple[House, int]) -> None:
    """Stored energy change never exceeds terminal energy (losses only reduce it)."""
    house, steps = case
    b = house.params.battery
    prev = b.initial_soc_pct
    for s in house.run(steps):
        stored_wh = (s.soc_pct - prev) / 100 * b.capacity_wh
        terminal_wh = s.battery_w * house.step_s / 3600
        assert abs(stored_wh) <= abs(terminal_wh) + 1e-3 or terminal_wh * stored_wh >= 0
        if terminal_wh > 0:
            assert stored_wh <= terminal_wh + 1e-3
        if terminal_wh < 0:
            assert stored_wh <= 1e-3  # discharging never raises SoC
        prev = s.soc_pct


def test_pv_zero_at_night_and_positive_at_noon() -> None:
    pv = PVParams(kwp=10, latitude=48, longitude=11)
    night = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)
    noon = datetime(2026, 6, 21, 11, 15, tzinfo=UTC)  # solar noon at 11 deg E
    assert clear_sky_w(night, pv) == 0
    assert clear_sky_w(noon, pv) > 7000
    assert clear_sky_w(noon, pv) <= 10_000


def test_winter_shorter_than_summer() -> None:
    def day_energy(month: int) -> float:
        pv = PVParams()
        t0 = datetime(2026, month, 21, tzinfo=UTC)
        return sum(clear_sky_w(t0 + timedelta(minutes=m), pv) for m in range(0, 1440, 10))

    assert day_energy(12) < day_energy(6) / 2


def test_sin_elevation_range() -> None:
    for h in range(24):
        v = sin_elevation(datetime(2026, 3, 20, h, tzinfo=UTC), 48.0, 11.0)
        assert -1 <= v <= 1


def test_time_is_injected_and_deterministic() -> None:
    tz = timezone(timedelta(hours=2))
    start = datetime(2026, 6, 21, 6, 0, tzinfo=tz)
    params = HouseParams(load=LoadParams(noise_fraction=0.3), seed=42)
    a = list(House(params, start, 60, [0.5]).run(200))
    b = list(House(params, start, 60, [0.5]).run(200))
    assert a == b
    assert a[0].time == start
    assert a[1].time - a[0].time == timedelta(seconds=60)
    other = list(House(params.model_copy(update={"seed": 43}), start, 60, [0.5]).run(200))
    assert other != a


def test_cloud_series_scales_pv_and_holds_last_value() -> None:
    start = datetime(2026, 6, 21, 11, 0, tzinfo=UTC)
    clear = list(House(HouseParams(), start, 60).run(3))
    cloudy = list(House(HouseParams(), start, 60, [0.5]).run(3))
    for c, d in zip(clear, cloudy, strict=True):
        assert d.pv_w == pytest.approx(c.pv_w * 0.5)
        assert d.cloud_factor == 0.5


def test_surplus_charges_deficit_discharges() -> None:
    params = HouseParams(
        load=LoadParams(hourly_w=(500.0,) * 24),
        battery=BatteryParams(initial_soc_pct=50, max_charge_w=3000, max_discharge_w=3000),
    )
    noon = House(params, datetime(2026, 6, 21, 11, 0, tzinfo=UTC), 60).step()
    assert noon.battery_w > 0
    assert noon.grid_w <= 0 or noon.battery_w == 3000
    night = House(params, datetime(2026, 6, 21, 0, 0, tzinfo=UTC), 60).step()
    assert night.battery_w == pytest.approx(-500)
    assert night.grid_w == pytest.approx(0, abs=TOL)


def test_validation_errors() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        House(HouseParams(), datetime(2026, 1, 1), 60)  # noqa: DTZ001
    with pytest.raises(ValueError, match="positive"):
        House(HouseParams(), start, 0)
    with pytest.raises(ValueError, match="cloud"):
        House(HouseParams(), start, 60, [1.5])
    with pytest.raises(ValueError, match="soc_min_pct"):
        BatteryParams(soc_min_pct=80, soc_max_pct=20)
    with pytest.raises(ValueError, match="initial_soc_pct"):
        BatteryParams(soc_min_pct=20, soc_max_pct=80, initial_soc_pct=90)
    with pytest.raises(ValueError, match="negative"):
        LoadParams(hourly_w=(-1.0,) + (0.0,) * 23)
