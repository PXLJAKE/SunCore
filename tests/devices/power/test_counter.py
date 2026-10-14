# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import itertools
import math
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.control import DeviceState
from suncore.core.signals import Quality
from suncore.core.units import Unit
from suncore.devices.power import CounterDerivedModel
from tests.devices.helpers import T0, at, climate_state, energy_sample, power_sample

ROLE = "energy"
HOUR = 3600.0


def feed(model: CounterDerivedModel, seconds: float, value: float, **kw: object) -> None:
    ts = at(seconds)
    model.observe(ts, None, {ROLE: energy_sample(value, ts, **kw)})  # type: ignore[arg-type]


def lg_model() -> CounterDerivedModel:
    return CounterDerivedModel(ROLE, update_s=HOUR, nominal_w=900)


def test_no_history_falls_back_to_nominal_prior() -> None:
    model = lg_model()
    est = model.estimate(T0, None, {})
    assert est.quality in (Quality.STALE, Quality.UNAVAILABLE)
    assert est.uncertainty_w == 450
    feed(model, 0, 100)
    assert model.interval is None
    running = model.estimate(T0, DeviceState(on=True), {ROLE: energy_sample(100, T0)})
    assert running.value_w == 900


def test_without_nominal_and_history_the_estimate_is_unavailable() -> None:
    est = CounterDerivedModel(ROLE).estimate(T0, None, {})
    assert est.quality is Quality.UNAVAILABLE


def test_hourly_delta_gives_average_power() -> None:
    model = lg_model()
    feed(model, 0, 1000)
    feed(model, HOUR, 1900)
    assert model.interval is not None
    assert model.interval.avg_w == pytest.approx(900)
    est = model.estimate(at(HOUR + 60), None, {ROLE: energy_sample(1900, at(HOUR))})
    assert est.value_w == pytest.approx(900)
    assert est.quality is Quality.OK
    assert est.age_s == 60


def test_quantisation_uncertainty_is_one_wh_per_period() -> None:
    model = lg_model()
    feed(model, 0, 0)
    feed(model, HOUR, 900)
    fresh = model.estimate(at(HOUR), None, {ROLE: energy_sample(900, at(HOUR))})
    # quantisation 1 Wh / 1 h = 1 W plus a quarter of the span (900 W)
    assert fresh.uncertainty_w == pytest.approx(1.0 + 0.25 * 900)


def test_kwh_counter_is_converted() -> None:
    model = lg_model()
    feed(model, 0, 1.0, unit=Unit.KWH)
    feed(model, HOUR, 1.9, unit=Unit.KWH)
    assert model.interval is not None
    assert model.interval.avg_w == pytest.approx(900)


def test_quiet_hour_between_changes_is_not_averaged_over_two_hours() -> None:
    model = lg_model()
    feed(model, 0, 500)
    # nothing consumed in hour one, 600 Wh in hour two: the counter changes after 2 h
    feed(model, 2 * HOUR, 1100)
    assert model.interval is not None
    assert model.interval.avg_w == pytest.approx(600)  # not 300


def test_live_counter_without_change_means_zero_consumption() -> None:
    model = lg_model()
    feed(model, 0, 0)
    feed(model, HOUR, 900)
    # alive but unchanged for 2 hours
    for s in (HOUR + 600, 2 * HOUR, 3 * HOUR):
        feed(model, s, 900)
    est = model.estimate(at(3 * HOUR), None, {ROLE: energy_sample(900, at(3 * HOUR))})
    assert est.value_w == 0
    emitted = model.pop_intervals()
    assert [round(i.avg_w) for i in emitted] == [900, 0, 0]


def test_stale_counter_is_not_read_as_idle() -> None:
    model = lg_model()
    feed(model, 0, 0)
    feed(model, HOUR, 900)
    stale = {ROLE: energy_sample(900, at(HOUR), quality=Quality.STALE)}
    est = model.estimate(at(5 * HOUR), None, stale)
    assert est.value_w == pytest.approx(900)
    assert est.quality is Quality.STALE


def test_midnight_reset_rebaselines_and_skips_the_period() -> None:
    model = lg_model()
    feed(model, 0, 4000)
    feed(model, HOUR, 4900)  # 23:00 value
    feed(model, 2 * HOUR, 30)  # counter reset at midnight, 30 Wh since
    assert model.resets == 1
    assert model.interval is not None
    assert model.interval.avg_w == pytest.approx(900)  # unchanged: no negative power
    feed(model, 3 * HOUR, 530)
    assert model.interval.avg_w == pytest.approx(500)


def test_small_backwards_step_is_jitter() -> None:
    model = lg_model()
    feed(model, 0, 1000)
    feed(model, 60, 999.5)
    assert model.resets == 0
    feed(model, HOUR, 1900)
    assert model.interval is not None
    assert model.interval.avg_w == pytest.approx(900)


def test_non_energy_unit_is_invalid_and_ignored() -> None:
    model = lg_model()
    wrong = {ROLE: power_sample(500, T0)}
    model.observe(T0, None, wrong)
    assert model.interval is None
    assert model.estimate(T0, None, wrong).quality in (
        Quality.INVALID,
        Quality.UNAVAILABLE,
        Quality.STALE,
    )


def test_known_off_state_is_standby() -> None:
    model = lg_model()
    feed(model, 0, 0)
    feed(model, HOUR, 900)
    est = model.estimate(
        at(HOUR + 30), climate_state(on=False, mode="off"), {ROLE: energy_sample(900, at(HOUR))}
    )
    assert est.value_w == 0
    assert est.uncertainty_w <= 10


def test_unknown_cadence_uses_elapsed_time_and_learns_the_gap() -> None:
    model = CounterDerivedModel(ROLE)
    feed(model, 0, 0)
    feed(model, 300, 75)  # 75 Wh in 5 minutes = 900 W
    assert model.interval is not None
    assert model.interval.avg_w == pytest.approx(900)
    assert model.update_s == 300


def test_out_of_order_sample_is_ignored() -> None:
    model = lg_model()
    feed(model, HOUR, 1000)
    feed(model, 0, 2000)  # earlier timestamp, different value
    assert model.interval is None


def test_implausible_jump_is_a_glitch() -> None:
    model = CounterDerivedModel(ROLE)
    feed(model, 0, 0)
    feed(model, 2, 10_000_000)
    assert model.interval is None


def test_update_period_must_be_positive() -> None:
    with pytest.raises(ValueError, match="update_s"):
        CounterDerivedModel(ROLE, update_s=0)


@st.composite
def counter_streams(draw: st.DrawFn) -> list[tuple[float, float]]:
    """(seconds since T0, counter value) with resets, backwards steps, gaps and kWh-ish noise."""
    steps = draw(
        st.lists(
            st.tuples(
                st.floats(1, 2 * HOUR),
                st.one_of(st.floats(0, 3000), st.floats(-500, 0), st.just(-1e9)),
            ),
            min_size=1,
            max_size=40,
        )
    )
    t, value, out = 0.0, 1000.0, []
    for dt, delta in steps:
        t += dt
        value = 0.0 if delta == -1e9 else max(0.0, value + delta)
        out.append((t, round(value)))
    return out


@given(counter_streams(), st.one_of(st.none(), st.just(HOUR), st.just(300.0)))
@settings(max_examples=200)
def test_counter_stream_never_breaks_the_estimate(
    stream: list[tuple[float, float]], update_s: float | None
) -> None:
    model = CounterDerivedModel(ROLE, update_s=update_s, nominal_w=1500)
    feed(model, 0, 1000)
    for seconds, value in stream:
        feed(model, seconds, value)
        est = model.estimate(at(seconds), None, {ROLE: energy_sample(value, at(seconds))})
        assert math.isfinite(est.value_w)
        assert est.value_w >= 0
        assert est.uncertainty_w >= 0
        interval = model.interval
        if interval is not None:
            assert interval.avg_w >= 0
            assert interval.end - interval.start <= timedelta(seconds=update_s or 1e9) + timedelta(
                seconds=1
            )


@given(
    power=st.floats(50, 3000),
    hours=st.integers(2, 12),
    offset=st.floats(0, 1000),
    reset_after=st.one_of(st.none(), st.integers(1, 10)),
)
def test_constant_power_is_recovered_within_quantisation(
    power: float, hours: int, offset: float, reset_after: int | None
) -> None:
    """A constant load seen through an hourly integer-Wh counter (with a midnight reset)."""
    model = lg_model()
    base = math.floor(offset)
    cumulative = 0.0
    feed(model, 0, base)
    for h in range(1, hours + 1):
        cumulative += power
        if reset_after is not None and h == reset_after:
            base, cumulative = 0, 0.0  # midnight: the counter restarts at zero
            continue
        feed(model, h * HOUR, base + math.floor(cumulative))
        interval = model.interval
        clean = reset_after is None or h > reset_after + 1  # the reset period is skipped
        if interval is not None and clean:
            # integer rounding of two readings: at most 1 W off over a one-hour period
            assert abs(interval.avg_w - power) <= 1.0 + 1e-6


@given(
    value=st.floats(0, 5000),
    ages=st.lists(st.floats(0, 4 * HOUR), min_size=2, max_size=8),
    nominal=st.one_of(st.none(), st.floats(100, 4000)),
)
def test_uncertainty_is_monotone_in_age(
    value: float, ages: list[float], nominal: float | None
) -> None:
    model = CounterDerivedModel(ROLE, update_s=HOUR, nominal_w=nominal)
    feed(model, 0, 0)
    feed(model, HOUR, math.floor(value))
    sample = {ROLE: energy_sample(math.floor(value), at(HOUR))}
    results = [model.estimate(at(HOUR + a), None, sample) for a in sorted(ages)]
    for first, second in itertools.pairwise(results):
        assert second.uncertainty_w >= first.uncertainty_w - 1e-9
        assert second.age_s >= first.age_s
