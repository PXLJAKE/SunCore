# SPDX-License-Identifier: Apache-2.0
"""Contract tests for suncore.core.control (WP2): estimates, state, settings, plan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from suncore.core.control import (
    DEFAULT_BAND,
    DEFAULT_BATTERY_BAND,
    BandPreference,
    BatteryBand,
    ControlSettings,
    DeviceBand,
    DeviceState,
    Plan,
    PlanInterval,
    PowerEstimate,
)
from suncore.core.signals import Quality

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def test_power_estimate_band_never_below_zero() -> None:
    e = PowerEstimate(value_w=100.0, uncertainty_w=250.0, quality=Quality.STALE, age_s=30)
    assert e.lower_w == 0.0
    assert e.upper_w == 350.0
    with pytest.raises(ValidationError):
        PowerEstimate(value_w=-1.0, uncertainty_w=0.0)
    with pytest.raises(ValidationError):
        PowerEstimate(value_w=1.0, uncertainty_w=float("inf"))


@given(st.floats(min_value=0, max_value=1e6), st.floats(min_value=0, max_value=1e6))
def test_power_estimate_bounds(value: float, unc: float) -> None:
    e = PowerEstimate(value_w=value, uncertainty_w=unc)
    assert 0 <= e.lower_w <= e.value_w <= e.upper_w


def test_device_state_known_only_for_ok_and_stale() -> None:
    assert DeviceState(on=True).known
    assert DeviceState(on=True, quality=Quality.STALE).known
    assert not DeviceState(on=True, quality=Quality.UNAVAILABLE).known
    assert not DeviceState(quality=Quality.INVALID).known
    with pytest.raises(ValidationError):
        DeviceState(step=16)
    with pytest.raises(ValidationError):
        DeviceState(ts=datetime(2026, 1, 1))  # noqa: DTZ001 - naive on purpose


def test_control_settings_defaults_follow_the_plan() -> None:
    s = ControlSettings()
    assert s.tick_s == 5.0
    assert s.smoothing_s == 60.0
    assert s.deficit_delay_s == 30.0
    assert s.deficit_budget_wh == 50.0
    assert s.proposal_ttl_s == 120.0
    assert s.release_deadline_s == 10.0
    assert s.min_write_interval_s >= 0.25
    with pytest.raises(ValidationError):
        ControlSettings(tick_s=0.5)
    with pytest.raises(ValidationError):
        ControlSettings(min_write_interval_s=0.1)


def test_default_plan_allows_everything() -> None:
    plan = Plan.default()
    assert plan.is_default
    assert plan.band_for("any", T0) == DEFAULT_BAND
    assert plan.band_for("any", T0).allowed
    assert plan.battery_band_at(T0) == DEFAULT_BATTERY_BAND
    assert plan.interval_at(T0) is None
    assert Plan.model_validate_json(plan.model_dump_json()) == plan


def test_plan_intervals_are_sorted_and_must_not_overlap() -> None:
    a = PlanInterval(
        start=T0, end=T0 + timedelta(minutes=15), devices={"ac": DeviceBand(allowed=False)}
    )
    b = PlanInterval(
        start=T0 + timedelta(minutes=15),
        end=T0 + timedelta(minutes=30),
        devices={"ac": DeviceBand(preference=BandPreference.PREFER_ON, max_power_w=500)},
        battery=BatteryBand(min_soc_pct=20, charge_from_surplus=False),
    )
    plan = Plan(intervals=(b, a), created_at=T0)
    assert plan.intervals == (a, b)
    assert not plan.is_default
    assert plan.band_for("ac", T0).allowed is False
    assert plan.band_for("ac", T0 + timedelta(minutes=20)).max_power_w == 500
    assert plan.band_for("other", T0) == DEFAULT_BAND
    assert plan.band_for("ac", T0 + timedelta(hours=1)) == DEFAULT_BAND
    assert plan.battery_band_at(T0 + timedelta(minutes=16)).charge_from_surplus is False
    with pytest.raises(ValidationError, match="overlap"):
        Plan(
            intervals=(
                a,
                PlanInterval(start=T0 + timedelta(minutes=5), end=T0 + timedelta(minutes=20)),
            )
        )
    with pytest.raises(ValidationError, match="start must be before end"):
        PlanInterval(start=T0, end=T0)
    with pytest.raises(ValidationError):
        BatteryBand(min_power_w=10, max_power_w=5)
    with pytest.raises(ValidationError):
        BatteryBand(min_soc_pct=50, max_soc_pct=10)


def test_resume_time_default_is_one_constant() -> None:
    from suncore.core import control, devices  # noqa: PLC0415

    assert control.RESUME_TIME_DEFAULT is devices.RESUME_TIME_DEFAULT
    assert devices.OverridePolicy().resume_time == devices.RESUME_TIME_DEFAULT
    assert ControlSettings().inputs_max_age_ticks == 3
