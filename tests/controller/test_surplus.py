# SPDX-License-Identifier: Apache-2.0
"""Surplus: smoothing window, battery share from the goals, available/free surplus."""

from __future__ import annotations

from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.controller.surplus import (
    SiteSurplus,
    SurplusTracker,
    WindowMedian,
    battery_share_available,
)
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.config import BatteryTarget, GoalsConfig
from suncore.core.control import BatteryBand
from suncore.core.signals import Quality
from tests.safety.helpers import T0, flows

GOALS = GoalsConfig(climate_before_export=True)


def at(seconds: float) -> object:
    return T0 + timedelta(seconds=seconds)


# --- window --------------------------------------------------------------------------


def test_window_median_prunes_old_samples() -> None:
    w = WindowMedian(60)
    assert w.median() is None
    assert w.latest() is None
    assert w.spread() == 0.0
    for i, v in enumerate([100.0, 200.0, 300.0]):
        w.add(T0 + timedelta(seconds=30 * i), v)
    assert len(w) == 3
    assert w.median() == 200.0
    w.add(T0 + timedelta(seconds=100), 1000.0)
    # 0 s and 30 s are older than 100 - 60: dropped.
    assert len(w) == 2
    assert w.median() == 650.0
    assert w.latest() == 1000.0


def test_window_restarts_when_the_clock_goes_backwards() -> None:
    w = WindowMedian(60)
    w.add(T0 + timedelta(seconds=10), 5.0)
    w.add(T0, 7.0)
    assert len(w) == 1
    assert w.median() == 7.0


def test_window_rejects_negative_length() -> None:
    with pytest.raises(ValueError, match="window_s"):
        WindowMedian(-1)


def test_spread_sees_the_downside_of_two_level_flicker() -> None:
    w = WindowMedian(60)
    # 7 sunny samples, 5 cloudy ones: the MAD is 0, the downside gap is not.
    for i, v in enumerate([3000.0] * 7 + [500.0] * 5):
        w.add(T0 + timedelta(seconds=i), v)
    assert w.median() == 3000.0
    assert w.spread() == 2500.0


def test_spread_of_noise_is_the_robust_sigma() -> None:
    w = WindowMedian(60)
    for i, v in enumerate([100.0, 110.0, 90.0, 100.0, 105.0, 95.0]):
        w.add(T0 + timedelta(seconds=i), v)
    assert 0 < w.spread() <= 15.0


def test_shift_rebases_every_sample() -> None:
    w = WindowMedian(60)
    w.add(T0, 100.0)
    w.add(T0 + timedelta(seconds=1), 200.0)
    w.shift(50.0)
    assert w.median() == 200.0
    w.shift(0.0)
    assert w.latest() == 250.0


@given(st.lists(st.floats(min_value=-1e5, max_value=1e5, allow_nan=False), min_size=1))
def test_median_lies_within_the_samples(values: list[float]) -> None:
    w = WindowMedian(1e6)
    for i, v in enumerate(values):
        w.add(T0 + timedelta(seconds=i), v)
    median = w.median()
    assert median is not None
    assert min(values) <= median <= max(values)
    assert w.spread() >= 0


# --- battery share -------------------------------------------------------------------


TARGET = BatteryTarget(soc_pct=80, time="18:00")


@pytest.mark.parametrize(
    ("goals", "soc", "expected"),
    [
        (GoalsConfig(battery_target=TARGET), 50.0, (False, 80)),
        (GoalsConfig(battery_target=TARGET), None, (False, 80)),
        (GoalsConfig(battery_target=TARGET, climate_before_export=True), 50.0, (False, 80)),
        (GoalsConfig(battery_target=TARGET), 80.0, (True, None)),
        (GoalsConfig(climate_before_export=True), 10.0, (True, None)),
        (GoalsConfig(), 50.0, (False, None)),
    ],
)
def test_battery_share_follows_the_goals(
    goals: GoalsConfig, soc: float | None, expected: tuple[bool, float | None]
) -> None:
    assert battery_share_available(goals, soc) == expected


def test_a_plan_band_decides_over_the_goals() -> None:
    goals = GoalsConfig(battery_target=TARGET)
    band = BatteryBand(charge_from_surplus=False)
    assert battery_share_available(goals, 10.0, band) == (True, None)
    assert battery_share_available(GOALS, 90.0, BatteryBand()) == (False, None)


def test_the_adr_0013_band_fields_do_not_change_the_controller_yet() -> None:
    """Until WP10 (4-G) the controller ignores ``grid_charge_max_w`` and ``discharge_lock``."""
    for plain in (BatteryBand(), BatteryBand(charge_from_surplus=False)):
        planned = plain.model_copy(update={"grid_charge_max_w": 3000.0, "discharge_lock": True})
        for soc in (None, 10.0, 90.0):
            assert battery_share_available(GOALS, soc, planned) == battery_share_available(
                GOALS, soc, plain
            )


# --- tracker -------------------------------------------------------------------------


def test_available_is_export_plus_battery_share_plus_controlled_draw() -> None:
    tracker = SurplusTracker(60)
    site = tracker.update(
        T0, flows(grid_w=-500.0, battery_w=800.0), GOALS, controlled_draw={"pool": 1000.0}
    )
    assert site.latest_site_w == 1300.0
    assert site.latest_available_w == 2300.0
    assert site.available_w == 2300.0
    assert site.total_w(100.0) == 2200.0
    assert site.free_w(100.0, 1000.0) == 1200.0
    assert site.known
    assert site.trusted


def test_battery_charging_is_not_available_before_the_target() -> None:
    tracker = SurplusTracker(60)
    goals = GoalsConfig(battery_target=TARGET)
    site = tracker.update(T0, flows(grid_w=-500.0, battery_w=800.0, soc_pct=40.0), goals)
    assert site.latest_site_w == 500.0
    assert not site.charging_available
    assert site.target_soc_pct == 80
    # Discharge always counts: it covers a deficit.
    site = tracker.update(at(5), flows(grid_w=0.0, battery_w=-300.0, soc_pct=40.0), goals)
    assert site.latest_site_w == -300.0


def test_switching_a_device_on_does_not_move_the_available_median() -> None:
    """The device's draw leaves the export and enters the controlled sum: ``a`` is flat."""
    tracker = SurplusTracker(60)
    for i in range(6):
        # Controllable and off: a member with 0 W (the allocator always reports it).
        tracker.update(
            at(5 * i), flows(grid_w=-2000.0, battery_w=0.0), GOALS, controlled_draw={"p": 0.0}
        )
    for i in range(6, 9):
        site = tracker.update(
            at(5 * i), flows(grid_w=-1000.0, battery_w=0.0), GOALS, controlled_draw={"p": 1000.0}
        )
        assert site.available_w == 2000.0
        assert site.uncertainty_w == 0.0


def test_a_device_leaving_the_controlled_set_rebases_the_window() -> None:
    tracker = SurplusTracker(60)
    for i in range(6):
        tracker.update(
            at(5 * i), flows(grid_w=-1000.0, battery_w=0.0), GOALS, controlled_draw={"p": 1000.0}
        )
    # Held by the user: same physics, the draw is no longer ours - no phantom surplus.
    site = tracker.update(at(30), flows(grid_w=-1000.0, battery_w=0.0), GOALS)
    assert site.available_w == 1000.0
    # Joining again adds it back.
    site = tracker.update(
        at(35), flows(grid_w=-1000.0, battery_w=0.0), GOALS, controlled_draw={"p": 1000.0}
    )
    assert site.available_w == 2000.0


def test_unusable_flows_report_nothing_but_keep_the_window() -> None:
    tracker = SurplusTracker(60)
    tracker.update(T0, flows(grid_w=-1000.0), GOALS)
    for bad in (None, flows(grid_w=None), flows(battery_w=None)):
        site = tracker.update(at(5), bad, GOALS)
        assert site.available_w is None
        assert not site.known
        assert site.total_w(100) is None
        assert site.free_w(100, 0) is None
        assert site.gate_estimate(100, 0) is None
    invalid = FlowSnapshot(
        grid_w=Flow(-1000.0, Quality.INVALID),
        pv_w=Flow(0.0, Quality.OK),
        battery_w=Flow(0.0, Quality.OK),
        house_w=Flow(None, Quality.UNAVAILABLE),
        battery_soc_pct=Flow(None, Quality.UNAVAILABLE),
    )
    assert tracker.update(at(6), invalid, GOALS).available_w is None
    site = tracker.update(at(10), flows(grid_w=-3000.0), GOALS)
    assert site.available_w == 2000.0  # median of the kept sample and the new one


def test_stale_flows_are_known_but_not_trusted() -> None:
    tracker = SurplusTracker(60)
    stale = FlowSnapshot(
        grid_w=Flow(-1000.0, Quality.STALE),
        pv_w=Flow(0.0, Quality.OK),
        battery_w=Flow(0.0, Quality.OK),
        house_w=Flow(None, Quality.UNAVAILABLE),
        battery_soc_pct=Flow(None, Quality.UNAVAILABLE),
    )
    site = tracker.update(T0, stale, GOALS)
    assert site.known
    assert not site.trusted
    estimate = site.gate_estimate(100, 0)
    assert estimate is not None
    assert estimate.quality is Quality.STALE


def test_gate_estimate_is_never_negative() -> None:
    site = SiteSurplus(
        available_w=100.0,
        latest_available_w=100.0,
        latest_site_w=100.0,
        uncertainty_w=30.0,
        grid_w=-100.0,
        battery_w=0.0,
        soc_pct=None,
        charging_available=True,
        target_soc_pct=None,
        quality=Quality.OK,
        ts=T0,
    )
    estimate = site.gate_estimate(100.0, 500.0)
    assert estimate is not None
    assert estimate.value_w == 0.0
    assert estimate.uncertainty_w == 30.0


def test_reset_forgets_window_and_members() -> None:
    tracker = SurplusTracker(60)
    tracker.update(T0, flows(grid_w=-1000.0), GOALS, controlled_draw={"p": 500.0})
    tracker.reset()
    site = tracker.update(at(5), flows(grid_w=-200.0), GOALS, controlled_draw={"p": 500.0})
    assert site.available_w == 700.0


# --- priority_until_soc_pct against the live SoC (Phase 4 WP9) ---------------------------

PRIORITY_GOALS = st.sampled_from(
    [
        GoalsConfig(),
        GoalsConfig(climate_before_export=True),
        GoalsConfig(battery_target=TARGET),
        GoalsConfig(battery_target=TARGET, climate_before_export=True),
    ]
)
SOC = st.one_of(st.none(), st.floats(min_value=0.0, max_value=100.0))


def test_priority_keeps_the_battery_first_until_the_live_soc_reaches_it() -> None:
    band = BatteryBand(charge_from_surplus=False, priority_until_soc_pct=60.0)
    assert battery_share_available(GOALS, 59.9, band) == (False, 60.0)
    assert battery_share_available(GOALS, None, band) == (False, 60.0)  # unknown = not reached
    # Reached: the band's own rule applies again (devices before the battery here).
    assert battery_share_available(GOALS, 60.0, band) == (True, None)
    assert battery_share_available(
        GOALS, 60.0, band.model_copy(update={"charge_from_surplus": True})
    ) == (
        False,
        None,
    )


@given(
    goals=PRIORITY_GOALS,
    soc=SOC,
    priority=st.floats(min_value=0.0, max_value=100.0),
    from_surplus=st.booleans(),
)
def test_below_its_priority_a_plan_never_gives_the_consumers_more(
    goals: GoalsConfig, soc: float | None, priority: float, from_surplus: bool
) -> None:
    band = BatteryBand(charge_from_surplus=from_surplus, priority_until_soc_pct=priority)
    without_priority = battery_share_available(
        goals, soc, band.model_copy(update={"priority_until_soc_pct": None})
    )
    with_priority, _ = battery_share_available(goals, soc, band)
    if soc is None or soc < priority:
        assert with_priority is False  # battery first, whatever else the band says
    # A priority only ever takes surplus away from the consumers.
    assert with_priority <= without_priority[0]
