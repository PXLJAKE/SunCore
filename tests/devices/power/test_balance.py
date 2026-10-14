# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.devices.power import GridBalanceValidator
from tests.devices.helpers import at


def feed(v: GridBalanceValidator, start: int, stop: int, watts: float) -> None:
    for s in range(start, stop, 5):
        v.note_house(at(s), watts)


def run_switch(
    expected: float, actual: float, *, uncertainty: float = 50.0
) -> GridBalanceValidator:
    v = GridBalanceValidator()
    feed(v, -120, 0, 500.0)
    v.note_switch(at(0), expected, uncertainty)
    feed(v, 0, 200, 500.0 + actual)
    v.evaluate(at(120))
    return v


def test_matching_change_confirms() -> None:
    v = run_switch(900, 880)
    assert v.last_check is not None
    assert v.last_check.confirmed
    assert v.factor == 1.0


def test_mismatching_change_widens_and_repeats_up_to_the_cap() -> None:
    v = run_switch(900, 100)
    assert v.last_check is not None
    assert not v.last_check.confirmed
    assert v.factor == pytest.approx(1.5)
    for _ in range(10):
        v.note_switch(at(0), 900, 50)
        v.evaluate(at(120))
    assert v.factor <= 4.0


def test_confirmation_decays_the_factor() -> None:
    v = run_switch(900, 100)
    v._house.clear()
    feed(v, 300, 500, 500.0)
    v.note_switch(at(500), 900, 50)
    feed(v, 500, 700, 1400.0)
    v.evaluate(at(620))
    assert v.factor < 1.5


def test_wide_uncertainty_makes_a_deviation_consistent() -> None:
    v = run_switch(900, 300, uncertainty=700)
    assert v.last_check is not None
    assert v.last_check.confirmed


def test_small_switch_is_not_validated() -> None:
    v = GridBalanceValidator()
    feed(v, -120, 200, 500.0)
    v.note_switch(at(0), 50, 10)
    assert v.evaluate(at(300)) is None


def test_second_switch_in_the_window_confounds_the_first() -> None:
    v = GridBalanceValidator()
    feed(v, -120, 0, 500.0)
    v.note_switch(at(0), 900, 50)
    v.note_switch(at(20), -900, 50)
    feed(v, 0, 200, 500.0)
    assert v.evaluate(at(40)) is None  # window of the second switch not over
    check = v.evaluate(at(140))
    assert check is not None
    assert check.expected_delta_w == -900


def test_missing_house_samples_expire_the_pending_switch() -> None:
    v = GridBalanceValidator()
    v.note_switch(at(0), 900, 50)
    assert v.evaluate(at(120)) is None
    assert v.evaluate(at(1000)) is None
    assert v.evaluate(at(1001)) is None
    assert v.factor == 1.0


def test_state_roundtrip_and_bad_data() -> None:
    v = run_switch(900, 100)
    other = GridBalanceValidator()
    other.load_state(v.dump_state())
    assert other.factor == v.factor
    other.load_state({"factor": float("nan")})
    other.load_state({"factor": 99})
    other.load_state({"factor": "x"})
    assert other.factor == v.factor


@given(
    expected=st.floats(-3000, 3000),
    actual=st.floats(-3000, 3000),
    uncertainty=st.floats(0, 2000),
)
def test_factor_stays_in_bounds(expected: float, actual: float, uncertainty: float) -> None:
    v = run_switch(expected, actual, uncertainty=uncertainty)
    assert 1.0 <= v.factor <= 4.0
