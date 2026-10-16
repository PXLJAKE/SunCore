# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.core.signals import Quality
from suncore.core.units import Unit
from suncore.devices.power import MeasuredPowerModel
from tests.devices.helpers import T0, at, climate_state, energy_sample, power_sample

ROLE = "power"


def test_fresh_sample_is_the_value_with_small_uncertainty() -> None:
    model = MeasuredPowerModel(ROLE, cadence_s=10)
    est = model.estimate(T0, None, {ROLE: power_sample(800, T0)})
    assert est.value_w == 800
    assert est.quality is Quality.OK
    assert 0 < est.uncertainty_w < 50
    assert est.age_s == 0


def test_kilowatt_is_converted() -> None:
    est = MeasuredPowerModel(ROLE).estimate(T0, None, {ROLE: power_sample(1.2, T0, unit=Unit.KW)})
    assert est.value_w == pytest.approx(1200)


def test_slow_sensor_is_not_stale_within_its_cadence() -> None:
    slow = MeasuredPowerModel(ROLE, cadence_s=300)
    fast = MeasuredPowerModel(ROLE, cadence_s=10)
    samples = {ROLE: power_sample(900, T0)}
    now = at(200)
    assert slow.estimate(now, None, samples).quality is Quality.OK
    assert fast.estimate(now, None, samples).quality is Quality.STALE
    assert (
        slow.estimate(now, None, samples).uncertainty_w
        < fast.estimate(now, None, samples).uncertainty_w
    )
    assert slow.estimate(at(700), None, samples).quality is Quality.STALE


def test_unit_that_is_not_power_is_invalid() -> None:
    est = MeasuredPowerModel(ROLE).estimate(T0, None, {ROLE: energy_sample(500, T0)})
    assert est.quality is Quality.INVALID
    assert est.value_w == 0


def test_unit_missing_is_invalid_never_guessed() -> None:
    est = MeasuredPowerModel(ROLE).estimate(T0, None, {ROLE: power_sample(500, T0, unit=None)})
    assert est.quality is Quality.INVALID


def test_missing_sample_is_unavailable_with_nominal_as_doubt() -> None:
    est = MeasuredPowerModel(ROLE, nominal_w=1000).estimate(T0, None, {})
    assert est.quality is Quality.UNAVAILABLE
    assert est.uncertainty_w == 1000


def test_unavailable_sample_falls_back_to_last_good_value() -> None:
    model = MeasuredPowerModel(ROLE, cadence_s=10)
    good = {ROLE: power_sample(700, T0)}
    model.observe(T0, None, good)
    bad = {ROLE: power_sample(None, at(30), quality=Quality.UNAVAILABLE)}
    est = model.estimate(at(30), None, bad)
    assert est.value_w == 700
    assert est.quality is Quality.UNAVAILABLE  # worst quality propagates
    assert est.age_s == 30
    assert est.uncertainty_w > model.estimate(T0, None, good).uncertainty_w


def test_stale_sample_quality_propagates() -> None:
    est = MeasuredPowerModel(ROLE).estimate(
        T0, None, {ROLE: power_sample(100, T0, quality=Quality.STALE)}
    )
    assert est.quality is Quality.STALE


def test_small_negative_noise_is_clamped_and_widens() -> None:
    est = MeasuredPowerModel(ROLE).estimate(T0, climate_state(), {ROLE: power_sample(-4, T0)})
    assert est.value_w == 0
    assert est.uncertainty_w >= 4


def test_cadence_must_be_positive() -> None:
    with pytest.raises(ValueError, match="cadence"):
        MeasuredPowerModel(ROLE, cadence_s=0)


@given(
    value=st.floats(0, 20000),
    cadence=st.floats(1, 600),
    ages=st.lists(st.floats(0, 5000), min_size=2, max_size=8),
    nominal=st.one_of(st.none(), st.floats(1, 5000)),
)
def test_uncertainty_is_monotone_in_age(
    value: float, cadence: float, ages: list[float], nominal: float | None
) -> None:
    model = MeasuredPowerModel(ROLE, cadence_s=cadence, nominal_w=nominal)
    samples = {ROLE: power_sample(value, T0)}
    results = [(a, model.estimate(at(a), None, samples)) for a in sorted(ages)]
    for (_, first), (_, second) in itertools.pairwise(results):
        assert second.uncertainty_w >= first.uncertainty_w - 1e-9
        assert second.age_s >= first.age_s
        assert second.quality.severity >= first.quality.severity
    assert all(r.value_w == pytest.approx(value) for _, r in results)
