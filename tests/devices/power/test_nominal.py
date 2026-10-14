# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from suncore.core.control import DeviceState
from suncore.core.devices import (
    ContinuousControl,
    Mode,
    ModeControl,
    OnOffControl,
    SetpointControl,
    Step,
    StepsControl,
)
from suncore.core.signals import Quality
from suncore.devices.power import NominalPowerModel
from tests.devices.helpers import T0

ON_OFF = OnOffControl(kind="on_off", role="switch")


def est(model: NominalPowerModel, state: DeviceState | None):  # type: ignore[no-untyped-def]
    return model.estimate(T0, state, {})


def test_on_off() -> None:
    model = NominalPowerModel(1000, ON_OFF)
    assert est(model, DeviceState(on=True)).value_w == 1000
    assert est(model, DeviceState(on=True)).uncertainty_w == pytest.approx(200)
    off = est(model, DeviceState(on=False))
    assert off.value_w == 0
    assert off.uncertainty_w <= 10


def test_unknown_state_is_unavailable_with_full_nominal_doubt() -> None:
    model = NominalPowerModel(1000, ON_OFF)
    none = est(model, None)
    assert none.quality is Quality.UNAVAILABLE
    assert none.uncertainty_w == 1000
    missing = est(model, DeviceState(quality=Quality.UNAVAILABLE))
    assert missing.quality is Quality.UNAVAILABLE


def test_state_without_on_information_is_unavailable() -> None:
    assert est(NominalPowerModel(1000, ON_OFF), DeviceState()).quality is Quality.UNAVAILABLE


def test_steps_use_the_step_power() -> None:
    control = StepsControl(
        kind="steps",
        role="level",
        steps=(
            Step(value="off", power_w=0),
            Step(value="1", power_w=1000),
            Step(value="3", power_w=3000),
        ),
    )
    model = NominalPowerModel(3000, control)
    assert est(model, DeviceState(step=1)).value_w == 1000
    assert est(model, DeviceState(step=2)).value_w == 3000
    assert est(model, DeviceState(step=0)).value_w == 0
    # an unknown step index falls back to on/off
    assert est(model, DeviceState(step=9, on=True)).value_w == 3000
    assert est(NominalPowerModel(None, control), None).uncertainty_w == 3000


def test_modes() -> None:
    control = ModeControl(
        kind="mode",
        role="m",
        modes=(Mode(value="eco", power_w=500), Mode(value="boost", power_w=2000)),
    )
    model = NominalPowerModel(2000, control)
    assert est(model, DeviceState(mode="eco")).value_w == 500
    assert est(model, DeviceState(mode="off")).value_w == 0
    assert est(model, DeviceState(mode="other", on=True)).value_w == 2000
    assert NominalPowerModel(None, control).estimate(T0, None, {}).uncertainty_w == 2000


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("W", 1500, 1500), ("A", 10, 2300), ("%", 50, 1000)],
)
def test_continuous_units(unit: str, value: float, expected: float) -> None:
    control = ContinuousControl(
        kind="continuous", role="n", unit=unit, min=0, max=3000 if unit == "W" else 100
    )  # type: ignore[arg-type]
    model = NominalPowerModel(2000, control)
    assert est(model, DeviceState(value=value)).value_w == pytest.approx(expected)


def test_three_phase_current() -> None:
    control = ContinuousControl(kind="continuous", role="n", unit="A", min=0, max=16, phases=3)
    assert est(NominalPowerModel(11000, control), DeviceState(value=10)).value_w == pytest.approx(
        6900
    )


def test_continuous_without_value_uses_on_off() -> None:
    control = ContinuousControl(kind="continuous", role="n", unit="W", min=0, max=3000)
    assert est(NominalPowerModel(2000, control), DeviceState(on=True)).value_w == 2000


def test_climate_is_wide() -> None:
    control = SetpointControl(kind="setpoint", role="c", shift_max_c=2)
    model = NominalPowerModel(900, control)
    on = est(model, DeviceState(on=True, mode="cool", setpoint_c=24))
    assert on.value_w == 900
    assert on.uncertainty_w == pytest.approx(450)
    assert est(model, DeviceState(on=False, mode="off")).value_w == 0
    assert est(model, DeviceState(mode="off")).value_w == 0
