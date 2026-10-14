# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.control import DeviceState
from suncore.core.signals import Quality
from suncore.devices.power import (
    HOUSE_POWER,
    OUTDOOR_TEMP,
    ROOM_TEMP,
    LearnedPowerModel,
)
from suncore.devices.power.learned import features
from suncore.devices.power.rls import RecursiveLeastSquares
from tests.devices.helpers import T0, at, climate_state, energy_sample, power_sample, temp_sample

NOMINAL = 1000.0


def true_power(state: DeviceState, room: float, outdoor: float) -> float:
    """The unit the model has to learn: a cooling compressor following dT and outdoor."""
    if not state.on:
        return 5.0
    dt = abs((state.setpoint_c or room) - room)
    return 150.0 + 90.0 * dt + 12.0 * (outdoor - 20.0)


def samples_for(ts: float, room: float, outdoor: float, **extra: object) -> dict[str, object]:
    base = {ROOM_TEMP: temp_sample(room, at(ts)), OUTDOOR_TEMP: temp_sample(outdoor, at(ts))}
    base.update(extra)
    return base


def test_starts_as_nominal() -> None:
    model = LearnedPowerModel(NOMINAL, power_role="p")
    on = model.estimate(T0, climate_state(), {})
    assert on.value_w == NOMINAL
    assert on.uncertainty_w == pytest.approx(500)
    assert not model.ready
    off = model.estimate(T0, climate_state(on=False, mode="off"), {})
    assert off.value_w == 0
    unknown = model.estimate(T0, None, {})
    assert unknown.quality is Quality.UNAVAILABLE


def test_learns_from_measured_samples_and_beats_the_prior() -> None:
    model = LearnedPowerModel(NOMINAL, power_role="p", cadence_s=10)
    for i in range(400):
        ts = i * 30.0
        room = 26.0 - (i % 7) * 0.5
        outdoor = 28.0 + (i % 5)
        state = climate_state(on=True, mode="cool", setpoint=22.0)
        y = true_power(state, room, outdoor)
        s = samples_for(ts, room, outdoor, p=power_sample(y, at(ts)))
        model.observe(at(ts), state, s)  # type: ignore[arg-type]
    assert model.ready
    probe_state = climate_state(on=True, mode="cool", setpoint=22.0)
    probe = samples_for(12000, 25.0, 30.0)
    est = model.estimate(at(12000), probe_state, probe)  # type: ignore[arg-type]
    expected = true_power(probe_state, 25.0, 30.0)
    assert est.value_w == pytest.approx(expected, rel=0.1)
    assert abs(est.value_w - expected) < abs(NOMINAL - expected)
    assert est.uncertainty_w < 0.5 * NOMINAL


def test_learns_from_counter_intervals() -> None:
    model = LearnedPowerModel(NOMINAL, energy_role="e", counter_update_s=3600)
    counter = 0.0
    for hour in range(40):
        # the unit runs the whole hour, the load follows the outdoor temperature
        outdoor = 24.0 + (hour % 6) * 2
        state = climate_state(on=True, mode="cool", setpoint=24.0)
        power = 200.0 + 25.0 * (outdoor - 20.0)
        for minute in range(0, 60, 5):
            ts = hour * 3600.0 + minute * 60.0
            model.observe(
                at(ts),
                state,
                samples_for(ts, 25.0, outdoor, e=energy_sample(math.floor(counter), at(ts))),
            )  # type: ignore[arg-type]
        counter += power
        ts = (hour + 1) * 3600.0
        model.observe(
            at(ts),
            state,
            samples_for(ts, 25.0, outdoor, e=energy_sample(math.floor(counter), at(ts))),
        )  # type: ignore[arg-type]
    assert model.samples_seen >= 20
    probe_state = climate_state(on=True, mode="cool", setpoint=24.0)
    est = model.estimate(at(50 * 3600), probe_state, samples_for(0, 25.0, 30.0))  # type: ignore[arg-type]
    assert est.value_w == pytest.approx(200.0 + 25.0 * 10.0, rel=0.15)


def test_counter_interval_without_state_history_is_not_used() -> None:
    model = LearnedPowerModel(NOMINAL, energy_role="e", counter_update_s=3600)
    for s, v in ((0, 0), (3600, 900)):
        model.observe(at(s), None, {"e": energy_sample(v, at(s))})
    assert model.samples_seen == 0


def test_output_is_bounded_even_for_wild_training() -> None:
    model = LearnedPowerModel(NOMINAL, power_role="p", cadence_s=10)
    state = climate_state()
    for i in range(100):
        ts = i * 30.0
        model.observe(at(ts), state, {"p": power_sample(50_000.0, at(ts))})
    est = model.estimate(at(3000), state, {})
    assert 0 <= est.value_w <= 2 * NOMINAL
    assert est.uncertainty_w <= 2 * NOMINAL


def test_stale_measured_sample_is_not_trained() -> None:
    model = LearnedPowerModel(NOMINAL, power_role="p", cadence_s=10)
    model.observe(at(100), climate_state(), {"p": power_sample(500, at(0))})
    assert model.samples_seen == 0


def test_state_roundtrip_through_json() -> None:
    model = LearnedPowerModel(NOMINAL, power_role="p", cadence_s=10)
    state = climate_state()
    for i in range(30):
        ts = i * 30.0
        model.observe(at(ts), state, {"p": power_sample(600, at(ts))})
    dumped = json.loads(json.dumps(model.dump_state(), allow_nan=False))
    fresh = LearnedPowerModel(NOMINAL, power_role="p")
    fresh.load_state(dumped)
    assert fresh.samples_seen == model.samples_seen
    assert fresh.weights == pytest.approx(model.weights)
    assert fresh.estimate(T0, state, {}).value_w == pytest.approx(
        model.estimate(T0, state, {}).value_w
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"version": 2},
        {"version": 1, "nominal_w": 5000.0, "rls": {}},
        {"version": 1, "nominal_w": NOMINAL, "rls": {"weights": [1], "cov": [[1]], "updates": 1}},
        {"version": 1, "nominal_w": "x", "rls": None},
        {
            "version": 1,
            "nominal_w": NOMINAL,
            "rls": {"weights": [float("nan")] * 7, "cov": [], "updates": 1},
        },
    ],
)
def test_unusable_stored_state_leaves_the_prior(data: dict[str, object]) -> None:
    model = LearnedPowerModel(NOMINAL, power_role="p")
    model.load_state(data)
    assert model.samples_seen == 0
    assert model.estimate(T0, climate_state(), {}).value_w == NOMINAL


def test_changed_nominal_power_discards_the_stored_state() -> None:
    model = LearnedPowerModel(NOMINAL, power_role="p", cadence_s=10)
    for i in range(30):
        model.observe(at(i * 30.0), climate_state(), {"p": power_sample(600, at(i * 30.0))})
    other = LearnedPowerModel(2 * NOMINAL, power_role="p")
    other.load_state(model.dump_state())
    assert other.samples_seen == 0


def test_features_resolve_auto_mode_by_the_sign_of_the_gap() -> None:
    cold_room = features(climate_state(mode="auto", setpoint=22), {ROOM_TEMP: temp_sample(18, T0)})
    warm_room = features(climate_state(mode="auto", setpoint=22), {ROOM_TEMP: temp_sample(27, T0)})
    assert cold_room is not None
    assert warm_room is not None
    assert (cold_room[2], cold_room[3]) == (1.0, 0.0)
    assert (warm_room[2], warm_room[3]) == (0.0, 1.0)
    assert features(DeviceState(), {}) is None
    off = features(climate_state(on=False, mode="off"), {})
    assert off == [1.0, 0, 0, 0, 0, 0, 0]


def test_features_survive_missing_temperatures() -> None:
    x = features(climate_state(mode="dry"), {})
    assert x is not None
    assert x[2:] == [0.0] * 5


def test_grid_balance_validation_widens_a_wrong_estimate() -> None:
    model = LearnedPowerModel(NOMINAL)
    off = climate_state(on=False, mode="off")
    on = climate_state()
    for s in range(0, 120, 5):
        model.observe(at(s), off, {HOUSE_POWER: power_sample(400, at(s))})
    # the unit switches on but the house load does not move: the nominal estimate is wrong
    for s in range(120, 320, 5):
        model.observe(at(s), on, {HOUSE_POWER: power_sample(420, at(s))})
    assert model.validator.factor > 1.0
    assert model.estimate(at(320), on, {}).uncertainty_w > 0.5 * NOMINAL


def test_grid_balance_validation_confirms_a_right_estimate() -> None:
    model = LearnedPowerModel(NOMINAL)
    off = climate_state(on=False, mode="off")
    on = climate_state()
    for s in range(0, 120, 5):
        model.observe(at(s), off, {HOUSE_POWER: power_sample(400, at(s))})
    for s in range(120, 320, 5):
        model.observe(at(s), on, {HOUSE_POWER: power_sample(1400, at(s))})
    assert model.validator.factor == 1.0
    assert model.validator.last_check is not None
    assert model.validator.last_check.confirmed


def test_switching_off_is_validated_with_the_last_on_operating_point() -> None:
    model = LearnedPowerModel(NOMINAL)
    on = climate_state()
    off = climate_state(on=False, mode="off")
    for s in range(0, 120, 5):
        model.observe(at(s), on, {HOUSE_POWER: power_sample(1400, at(s))})
    model.observe(at(120), off, {HOUSE_POWER: power_sample(400, at(120))})
    for s in range(125, 320, 5):
        model.observe(at(s), off, {HOUSE_POWER: power_sample(400, at(s))})
    assert model.validator.last_check is not None
    assert model.validator.last_check.expected_delta_w == -NOMINAL
    assert model.validator.last_check.confirmed


def test_nominal_must_be_positive() -> None:
    with pytest.raises(ValueError, match="nominal_w"):
        LearnedPowerModel(0)


# --- RLS properties -----------------------------------------------------------------


def test_rls_rejects_bad_construction() -> None:
    with pytest.raises(ValueError, match="same non-zero length"):
        RecursiveLeastSquares([1.0], [1.0, 2.0], noise_variance=1)
    with pytest.raises(ValueError, match="positive"):
        RecursiveLeastSquares([1.0], [0.0], noise_variance=1)
    with pytest.raises(ValueError, match="forgetting"):
        RecursiveLeastSquares([1.0], [1.0], noise_variance=1, forgetting=0.5)


def test_rls_ignores_non_finite_input() -> None:
    rls = RecursiveLeastSquares([0.0, 1.0], [1.0, 1.0], noise_variance=1)
    assert not rls.update([1.0, float("nan")], 1.0)
    assert not rls.update([1.0, 1.0], float("inf"))
    assert not rls.update([1.0], 1.0)
    assert rls.updates == 0


def test_rls_converges_on_a_linear_target() -> None:
    rls = RecursiveLeastSquares([0.0, 0.0], [100.0**2, 100.0**2], noise_variance=1.0)
    for i in range(200):
        x = [1.0, (i % 10) / 10.0]
        rls.update(x, 50.0 + 200.0 * x[1])
    assert rls.weights == pytest.approx([50.0, 200.0], abs=2.0)


@given(
    data=st.lists(
        st.tuples(
            st.booleans(),
            st.sampled_from(["heat", "cool", "auto", "dry", "fan_only"]),
            st.floats(-10, 40),
            st.floats(-30, 50),
            st.floats(10, 30),
            st.floats(-1e6, 1e6),
        ),
        min_size=1,
        max_size=80,
    ),
    nominal=st.floats(50, 5000),
)
@settings(max_examples=100)
def test_rls_stays_bounded(
    data: list[tuple[bool, str, float, float, float, float]], nominal: float
) -> None:
    model = LearnedPowerModel(nominal, power_role="p", cadence_s=10)
    for i, (on, mode, room, outdoor, setpoint, y) in enumerate(data):
        ts = i * 30.0
        state = climate_state(on=on, mode=mode if on else "off", setpoint=setpoint)
        model.observe(at(ts), state, samples_for(ts, room, outdoor, p=power_sample(y, at(ts))))  # type: ignore[arg-type]
        est = model.estimate(at(ts), state, samples_for(ts, room, outdoor))  # type: ignore[arg-type]
        assert 0 <= est.value_w <= 2 * nominal + 1e-9
        assert math.isfinite(est.uncertainty_w)
        assert 0 <= est.uncertainty_w <= 2 * nominal + 1e-9
    assert all(math.isfinite(w) for w in model.weights)
    json.dumps(model.dump_state(), allow_nan=False)
