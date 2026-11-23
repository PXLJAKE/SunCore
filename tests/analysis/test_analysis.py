# SPDX-License-Identifier: Apache-2.0
"""Analytics core (Phase 5 WP2, ADR-0014): flows, KPIs, baselines, cost, period buckets."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.analysis.baselines import compute_savings
from suncore.analysis.cost import actual_cost, slot_prices
from suncore.analysis.energy import SiteEnergy, SlotEnergy, build_site_energy
from suncore.analysis.flows import slot_flows, total_flows
from suncore.analysis.kpi import equivalent_full_cycles, kpis
from suncore.analysis.period import (
    Granularity,
    bucket_for,
    buckets_between,
    group_slots,
)
from suncore.analysis.summary import summarize
from suncore.core.battery import BatteryModel
from suncore.core.series import Grid, PriceSeries, SeriesUnit, TimeSeries

BERLIN = ZoneInfo("Europe/Berlin")
STEP = timedelta(minutes=15)
T0 = datetime(2026, 6, 1, tzinfo=UTC)

wh = st.floats(min_value=0, max_value=5000, allow_nan=False)
batt = st.floats(min_value=-3000, max_value=3000, allow_nan=False)
price = st.floats(min_value=0, max_value=1, allow_nan=False)


def consistent(pv: float, house: float, battery: float, start: datetime = T0) -> SlotEnergy:
    return SlotEnergy(
        start=start, pv_wh=pv, grid_wh=house + battery - pv, battery_wh=battery, house_wh=house
    )


slot_st = st.builds(consistent, wh, wh, batt)


def slots_st(max_size: int = 40) -> st.SearchStrategy[list[SlotEnergy]]:
    return st.lists(st.tuples(wh, wh, batt), min_size=1, max_size=max_size).map(
        lambda rows: [consistent(p, h, b, T0 + STEP * i) for i, (p, h, b) in enumerate(rows)]
    )


def flat_prices(
    slots: list[SlotEnergy], imp: float, exp: float
) -> dict[datetime, tuple[float, float]]:
    return {s.start: (imp, exp) for s in slots}


# --- flows ---------------------------------------------------------------------------


@given(slot_st)
def test_flows_balance_and_are_non_negative(slot: SlotEnergy) -> None:
    f = slot_flows(slot)
    for value in (
        f.pv_to_house_wh,
        f.pv_to_battery_wh,
        f.pv_to_grid_wh,
        f.battery_to_house_wh,
        f.battery_to_grid_wh,
        f.grid_to_house_wh,
        f.grid_to_battery_wh,
    ):
        assert value >= 0
    assert math.isclose(f.pv_wh, slot.pv_wh, abs_tol=1e-6)
    assert math.isclose(f.house_wh, slot.house_wh, abs_tol=1e-6)
    assert math.isclose(f.charge_wh, max(slot.battery_wh, 0), abs_tol=1e-6)
    assert math.isclose(f.discharge_wh, max(-slot.battery_wh, 0), abs_tol=1e-6)
    # a consistent measurement leaves no residual: import - export is the measured grid
    assert math.isclose(f.import_wh - f.export_wh, slot.grid_wh, abs_tol=1e-6)
    assert abs(f.residual_wh) < 1e-6


@given(slot_st)
def test_grid_charges_the_battery_only_for_what_pv_cannot(slot: SlotEnergy) -> None:
    f = slot_flows(slot)
    if f.grid_to_battery_wh > 1e-9:
        assert f.pv_to_grid_wh <= 1e-6  # no PV left over while the grid charges
    assert f.pv_to_house_wh == pytest.approx(min(slot.pv_wh, slot.house_wh))


def test_attribution_rule_example() -> None:
    f = slot_flows(consistent(pv=1000, house=400, battery=300))
    assert (f.pv_to_house_wh, f.pv_to_battery_wh, f.pv_to_grid_wh) == (400, 300, 300)
    assert f.grid_to_house_wh == f.grid_to_battery_wh == 0
    g = slot_flows(consistent(pv=100, house=400, battery=300))
    assert (g.pv_to_house_wh, g.grid_to_house_wh, g.grid_to_battery_wh) == (100, 300, 300)
    d = slot_flows(consistent(pv=0, house=400, battery=-250))
    assert (d.battery_to_house_wh, d.grid_to_house_wh) == (250, 150)


@given(slots_st())
def test_total_flows_balance(slots: list[SlotEnergy]) -> None:
    f = total_flows(slots)
    assert math.isclose(f.pv_wh, sum(s.pv_wh for s in slots), rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(f.house_wh, sum(s.house_wh for s in slots), rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(
        f.import_wh - f.export_wh, sum(s.grid_wh for s in slots), rel_tol=1e-9, abs_tol=1e-6
    )


def test_negative_noise_counts_as_zero() -> None:
    f = slot_flows(SlotEnergy(T0, pv_wh=-5, grid_wh=10, battery_wh=0, house_wh=10))
    assert f.pv_wh == 0
    assert f.grid_to_house_wh == 10


# --- KPIs ----------------------------------------------------------------------------


@given(slots_st())
def test_autarky_and_self_consumption_in_unit_interval(slots: list[SlotEnergy]) -> None:
    k = kpis(total_flows(slots))
    for ratio in (k.autarky, k.self_consumption):
        assert ratio is None or 0.0 <= ratio <= 1.0


def test_kpi_edge_cases() -> None:
    assert kpis(total_flows([])).autarky is None
    assert kpis(total_flows([])).self_consumption is None
    only_load = kpis(total_flows([consistent(0, 100, 0)]))
    assert only_load.autarky == 0.0
    assert only_load.self_consumption is None
    all_pv = kpis(total_flows([consistent(100, 100, 0)]))
    assert (all_pv.autarky, all_pv.self_consumption) == (1.0, 1.0)


def _battery() -> BatteryModel:
    return BatteryModel(
        capacity_wh=10_000, max_charge_w=5000, max_discharge_w=5000, reserve_pct=10, max_soc_pct=95
    )


def test_equivalent_full_cycles_use_the_usable_capacity() -> None:
    bat = _battery()
    assert bat.usable_wh == pytest.approx(8000)
    flows = total_flows([consistent(4000, 0, 4000), consistent(0, 4000, -4000)])
    assert equivalent_full_cycles(flows, bat) == pytest.approx(0.5)
    assert equivalent_full_cycles(flows, None) is None
    assert kpis(flows, bat).equivalent_full_cycles == pytest.approx(0.5)


# --- cost and baselines --------------------------------------------------------------


@given(slots_st(), price, price)
def test_savings_are_baseline_minus_actual(slots: list[SlotEnergy], imp: float, exp: float) -> None:
    s = compute_savings(slots, flat_prices(slots, imp, exp))
    assert s.savings_vs_no_pv == pytest.approx(s.baseline_no_pv.net - s.actual.net)
    assert s.savings_vs_pv_only == pytest.approx(s.baseline_pv_only.net - s.actual.net)
    assert s.pv_contribution == pytest.approx(s.savings_vs_no_pv - s.savings_vs_pv_only)


@given(slots_st(), price, price)
def test_pv_only_baseline_never_exceeds_no_pv_baseline(
    slots: list[SlotEnergy], imp: float, exp: float
) -> None:
    s = compute_savings(slots, flat_prices(slots, imp, exp))
    assert s.baseline_pv_only.net <= s.baseline_no_pv.net + 1e-9
    assert s.pv_contribution >= -1e-9


@given(slots_st(), price, price)
def test_without_battery_actual_equals_pv_only_baseline(
    slots: list[SlotEnergy], imp: float, exp: float
) -> None:
    no_battery = [consistent(s.pv_wh, s.house_wh, 0.0, s.start) for s in slots]
    s = compute_savings(no_battery, flat_prices(no_battery, imp, exp))
    assert s.savings_vs_pv_only == pytest.approx(0.0, abs=1e-9)


def test_cost_example_and_unpriced_slots_are_skipped() -> None:
    a = consistent(0, 1000, 0, T0)  # imports 1 kWh
    b = consistent(2000, 500, 0, T0 + STEP)  # exports 1.5 kWh
    c = consistent(0, 1000, 0, T0 + 2 * STEP)  # no price
    prices = {a.start: (0.30, 0.08), b.start: (0.30, 0.08)}
    cost, skipped = actual_cost([a, b, c], prices)
    assert skipped == 1
    assert cost.import_cost == pytest.approx(0.30)
    assert cost.export_revenue == pytest.approx(0.12)
    assert cost.net == pytest.approx(0.18)
    s = compute_savings([a, b, c], prices)
    assert s.skipped_slots == 1
    assert s.baseline_no_pv.net == pytest.approx(0.45)


def _series(values: list[float | None], step: timedelta, unit: SeriesUnit) -> TimeSeries:
    return TimeSeries(start=T0, step=step, values=tuple(values), unit=unit)


def test_build_site_energy_drops_incomplete_slots_and_derives_house() -> None:
    grid = Grid(start=T0, step=STEP, count=4)
    pv = _series([400.0, 400.0, None, 400.0], STEP, SeriesUnit.W)
    grd = _series([100.0, -200.0, 100.0, 100.0], STEP, SeriesUnit.W)
    bat = _series([0.0, 200.0, 0.0, 0.0], STEP, SeriesUnit.W)
    e = build_site_energy(pv_w=pv, grid_w=grd, battery_w=bat, house_w=None, grid=grid)
    assert len(e.slots) == 3
    assert e.coverage == pytest.approx(0.75)
    assert not e.complete
    assert e.slots[0].pv_wh == pytest.approx(100.0)  # 400 W for 15 min
    assert e.slots[0].house_wh == pytest.approx(125.0)  # (100 + 400 - 0) W for 15 min
    assert e.slots[1].house_wh == pytest.approx(0.0)  # (-200 + 400 - 200) W
    with_house = build_site_energy(
        pv_w=pv,
        grid_w=grd,
        battery_w=bat,
        house_w=_series([500.0, None, 500.0, 500.0], STEP, SeriesUnit.W),
        grid=grid,
    )
    assert [s.start for s in with_house.slots] == [T0, T0 + 3 * STEP]


def test_slot_prices_use_the_source_export_or_the_fallback() -> None:
    grid = Grid(start=T0, step=STEP, count=4)
    energy = SiteEnergy(start=T0, step=STEP, total_slots=4, slots=())
    hourly = _series([0.2], timedelta(hours=1), SeriesUnit.PRICE)
    ps = PriceSeries(source_id="x", price_includes="all_in", import_price=hourly)
    assert slot_prices(ps, energy, export_fallback=None) == {}
    out = slot_prices(ps, energy, export_fallback=0.07)
    assert out == dict.fromkeys(grid.starts(), (0.2, 0.07))
    with_export = PriceSeries(
        source_id="x",
        price_includes="all_in",
        import_price=hourly,
        export_price=_series([0.05], timedelta(hours=1), SeriesUnit.PRICE),
    )
    assert slot_prices(with_export, energy, export_fallback=0.07)[T0] == (0.2, 0.05)


# --- periods and DST -----------------------------------------------------------------


def _local(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 12, tzinfo=BERLIN)


@pytest.mark.parametrize(
    ("day", "hours"),
    [((2026, 3, 29), 23), ((2026, 10, 25), 25), ((2026, 6, 10), 24)],
)
def test_dst_days_have_23_24_25_hours(day: tuple[int, int, int], hours: int) -> None:
    bucket = bucket_for(_local(*day), Granularity.DAY, BERLIN)
    assert bucket.duration == timedelta(hours=hours)
    assert bucket.key == f"{day[0]}-{day[1]:02d}-{day[2]:02d}"


def test_iso_weeks_cross_the_year_boundary() -> None:
    # 2026-01-01 is a Thursday: ISO week 2026-W01 starts Monday 2025-12-29.
    b = bucket_for(_local(2026, 1, 1), Granularity.WEEK, BERLIN)
    assert (b.key, b.first_day.isoformat()) == ("2026-W01", "2025-12-29")
    assert b.duration == timedelta(days=7)
    # 2027-01-01 is a Friday and belongs to ISO week 2026-W53.
    assert bucket_for(_local(2027, 1, 1), Granularity.WEEK, BERLIN).key == "2026-W53"
    # The DST week is one hour shorter.
    assert bucket_for(_local(2026, 3, 29), Granularity.WEEK, BERLIN).duration == timedelta(
        days=7, hours=-1
    )


def test_month_and_year_buckets() -> None:
    assert bucket_for(_local(2026, 2, 10), Granularity.MONTH, BERLIN).duration == timedelta(days=28)
    assert bucket_for(_local(2026, 12, 31), Granularity.MONTH, BERLIN).key == "2026-12"
    year = bucket_for(_local(2026, 6, 1), Granularity.YEAR, BERLIN)
    assert year.key == "2026"
    assert year.duration == timedelta(days=365)  # one 23 h and one 25 h day cancel


@pytest.mark.parametrize("granularity", list(Granularity))
def test_buckets_tile_without_gaps_or_overlaps(granularity: Granularity) -> None:
    start = datetime(2025, 12, 20, tzinfo=UTC)
    end = datetime(2027, 1, 10, tzinfo=UTC)
    buckets = buckets_between(start, end, granularity, BERLIN)
    assert buckets[0].start <= start < buckets[0].end
    assert buckets[-1].start < end <= buckets[-1].end
    for a, b in itertools.pairwise(buckets):
        assert a.end == b.start
    assert buckets_between(end, start, granularity, BERLIN) == []


@settings(max_examples=60, deadline=None)
@given(
    st.integers(min_value=0, max_value=96 * 400),
    st.integers(min_value=1, max_value=96 * 3),
    st.sampled_from(list(Granularity)),
    st.sampled_from(["Europe/Berlin", "UTC", "America/St_Johns", "Asia/Kolkata"]),
)
def test_bucketing_preserves_energy_across_dst(
    offset: int, count: int, granularity: Granularity, zone: str
) -> None:
    """Every slot lands in exactly one bucket; the bucket sums add up to the total."""
    tz = ZoneInfo(zone)
    start = datetime(2026, 2, 1, tzinfo=UTC) + STEP * offset  # spans both 2026 DST changes
    slots = [
        SlotEnergy(
            start=start + STEP * i,
            pv_wh=1.0 + i % 7,
            grid_wh=2.0,
            battery_wh=0.0,
            house_wh=3.0 + i % 5,
        )
        for i in range(count)
    ]
    groups = group_slots(slots, granularity, tz)
    assert sum(len(g) for _, g in groups) == count
    assert math.isclose(
        sum(s.pv_wh for _, g in groups for s in g), sum(s.pv_wh for s in slots), abs_tol=1e-9
    )
    for bucket, members in groups:
        assert all(bucket.start <= s.start < bucket.end for s in members)
    keys = [b.key for b, _ in groups]
    assert len(keys) == len(set(keys))
    assert [b.start for b, _ in groups] == sorted(b.start for b, _ in groups)


def test_a_full_dst_day_has_92_or_100_slots_and_full_coverage() -> None:
    for day, count in (((2026, 3, 29), 92), ((2026, 10, 25), 100)):
        bucket = bucket_for(_local(*day), Granularity.DAY, BERLIN)
        slots = [consistent(100, 80, 0, bucket.start + STEP * i) for i in range(count)]
        groups = group_slots(slots, Granularity.DAY, BERLIN)
        assert len(groups) == 1
        summary = summarize(bucket, groups[0][1], flat_prices(slots, 0.3, 0.08), step=STEP)
        assert summary.coverage == 1.0
        assert summary.complete


def test_summary_marks_sparse_days_incomplete() -> None:
    bucket = bucket_for(_local(2026, 6, 10), Granularity.DAY, BERLIN)
    slots = [consistent(100, 80, 0, bucket.start + STEP * i) for i in range(50)]
    summary = summarize(bucket, slots, flat_prices(slots, 0.3, 0.08), step=STEP, battery=_battery())
    assert summary.coverage == pytest.approx(50 / 96)
    assert not summary.complete
    assert summary.kpis.equivalent_full_cycles == 0.0
