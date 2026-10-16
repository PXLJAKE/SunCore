# SPDX-License-Identifier: Apache-2.0
"""Level tables per control kind, quantisation and the observed level."""

from __future__ import annotations

import itertools

from suncore.controller.levels import (
    ConfigQuantiser,
    build_levels,
    nominal_power_w,
    observed_level,
    shift_direction,
)
from suncore.core.control import ControlSettings, DeviceState, PowerEstimate
from suncore.core.decisions import (
    ModeAction,
    PowerSetpointAction,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import (
    ContinuousControl,
    DeviceConfig,
    DeviceKind,
    Mode,
    ModeControl,
    PowerModel,
    PowerModelKind,
    RelaysControl,
    RelayStep,
)
from suncore.core.signals import Quality
from suncore.core.site import EntityRef
from tests.safety.helpers import (
    climate_device,
    continuous_device,
    steps_device,
    switch_device,
)

SETTINGS = ControlSettings()
Q = ConfigQuantiser()


class StepQuantiser(ConfigQuantiser):
    """A climate unit with a 1 K grid (LG: integer set-points)."""

    def setpoint_step_c(self, device_id: str) -> float | None:  # noqa: ARG002
        return 1.0


def mode_device(*, zero_floor: bool = True) -> DeviceConfig:
    modes = (
        Mode(value="boost", power_w=2000),
        Mode(value="off" if zero_floor else "eco", power_w=0 if zero_floor else 300),
        Mode(value="normal", power_w=1000),
    )
    return DeviceConfig(
        id="hp",
        template_id="generic.mode",
        name="HP",
        kind=DeviceKind.HEAT_PUMP,
        entities={"mode": EntityRef(entity_id="select.hp")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=2000),
        control=ModeControl(kind="mode", role="mode", modes=modes),
    )


def relays_device() -> DeviceConfig:
    return DeviceConfig(
        id="rod",
        template_id="generic.water_heater_relays",
        name="Rod",
        kind=DeviceKind.WATER_HEATER,
        entities={
            "r1": EntityRef(entity_id="switch.r1"),
            "r2": EntityRef(entity_id="switch.r2"),
        },
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=3000),
        control=RelaysControl(
            kind="relays",
            roles=("r1", "r2"),
            steps=(
                RelayStep(relays=(), power_w=0),
                RelayStep(relays=("r1",), power_w=1000),
                RelayStep(relays=("r1", "r2"), power_w=3000),
            ),
        ),
    )


def number_device(control: ContinuousControl, nominal: float | None = 3000.0) -> DeviceConfig:
    return continuous_device().model_copy(
        update={
            "control": control,
            "power_model": PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=nominal)
            if nominal is not None
            else PowerModel(kind=PowerModelKind.MEASURED, power_role="p"),
        }
    )


# --- tables per kind -------------------------------------------------------------------


def test_on_off_is_off_and_nominal() -> None:
    table = build_levels(switch_device(nominal_w=1200), None, None, SETTINGS, Q)
    assert table is not None
    assert [lv.power_w for lv in table.levels] == [0.0, 1200.0]
    assert table.levels[1].action == SwitchAction(on=True)
    assert table.levels[1].on
    assert not table.levels[0].on
    assert table.switchable
    assert not table.ramped
    assert table.need_w(0, 1) == 1200.0
    assert table.need_w(1, 0) == 0.0
    assert table.level(99) is table.levels[-1]


def test_on_off_without_any_power_figure_is_not_controllable() -> None:
    device = switch_device().model_copy(
        update={"power_model": PowerModel(kind=PowerModelKind.MEASURED, power_role="p")}
    )
    assert build_levels(device, None, None, SETTINGS, Q) is None
    estimate = PowerEstimate(value_w=500, uncertainty_w=50)
    table = build_levels(device, None, estimate, SETTINGS, Q)
    assert table is not None
    assert table.levels[1].power_w == 550.0


def test_steps_and_relays_have_one_level_per_step() -> None:
    for device in (steps_device(), relays_device()):
        table = build_levels(device, None, None, SETTINGS, Q)
        assert table is not None
        assert [lv.power_w for lv in table.levels] == [0.0, 1000.0, 3000.0]
        assert table.levels[2].action == StepAction(step=2)


def test_modes_are_ordered_by_power() -> None:
    table = build_levels(mode_device(), None, None, SETTINGS, Q)
    assert table is not None
    assert [lv.action for lv in table.levels] == [
        ModeAction(mode="off"),
        ModeAction(mode="normal"),
        ModeAction(mode="boost"),
    ]
    assert table.switchable
    floor = build_levels(mode_device(zero_floor=False), None, None, SETTINGS, Q)
    assert floor is not None
    assert not floor.switchable


def test_continuous_levels_are_on_the_dead_band_grid() -> None:
    table = build_levels(continuous_device(), None, None, SETTINGS, Q)
    assert table is not None
    powers = [lv.power_w for lv in table.levels]
    assert powers[0] == 0.0
    assert powers[-1] == 3000.0
    steps = {round(b - a, 6) for a, b in itertools.pairwise(powers)}
    assert min(steps) >= SETTINGS.default_hysteresis_w
    assert all(isinstance(lv.action, PowerSetpointAction) for lv in table.levels)
    assert table.switchable


def test_continuous_in_amps_snaps_to_the_control_step() -> None:
    control = ContinuousControl(
        kind="continuous", role="power", unit="A", min=6, max=16, step=1, voltage_v=230
    )
    device = number_device(control)
    quantiser = ConfigQuantiser({device.id: device})
    table = build_levels(device, None, None, SETTINGS, quantiser)
    assert table is not None
    powers = [lv.power_w for lv in table.levels]
    assert powers[0] == 6 * 230
    assert powers[-1] == 16 * 230
    assert all(round(p / 230, 6).is_integer() for p in powers)
    assert not table.switchable  # 6 A is the floor, never 0


def test_continuous_without_range_is_not_controllable() -> None:
    control = ContinuousControl(kind="continuous", role="power", unit="%", min=0, max=100)
    # % without a nominal power: 0..0 W.
    device = number_device(control, nominal=None)
    assert build_levels(device, None, None, SETTINGS, Q) is None


def test_config_quantiser() -> None:
    control = ContinuousControl(
        kind="continuous", role="power", unit="W", min=100, max=2000, step=250
    )
    device = number_device(control)
    q = ConfigQuantiser({device.id: device})
    assert q.power_w(device.id, 50.0) == 100.0
    assert q.power_w(device.id, 500.0) == 350.0
    assert q.power_w(device.id, 9999.0) == 1850.0
    assert q.power_w("unknown", -5.0) == 0.0
    assert q.setpoint_step_c(device.id) is None
    q.update({})
    assert q.power_w(device.id, 500.0) == 500.0


def test_nominal_power_fallbacks() -> None:
    measured = PowerModel(kind=PowerModelKind.MEASURED, power_role="p")
    assert nominal_power_w(steps_device().model_copy(update={"power_model": measured}), None) == (
        3000.0
    )
    assert nominal_power_w(mode_device().model_copy(update={"power_model": measured}), None) == (
        2000.0
    )
    watts = ContinuousControl(kind="continuous", role="power", unit="W", min=0, max=1500)
    assert (
        nominal_power_w(number_device(watts).model_copy(update={"power_model": measured}), None)
        == 1500.0
    )
    pct = ContinuousControl(kind="continuous", role="power", unit="%", min=0, max=100)
    device = number_device(pct).model_copy(update={"power_model": measured})
    assert nominal_power_w(device, None) is None


# --- climate -----------------------------------------------------------------------------


HEAT_ON = DeviceState(on=True, mode="heat", setpoint_c=21.0)


def test_heating_shifts_up_in_steps_inside_the_comfort_band() -> None:
    table = build_levels(climate_device(max_c=22.0), HEAT_ON, None, SETTINGS, Q)
    assert table is not None
    assert table.ramped
    assert not table.switchable
    assert table.base_setpoint_c == 21.0
    assert [lv.setpoint_c for lv in table.levels] == [21.0, 21.5, 22.0]
    assert table.levels[0].action == SetpointAction(setpoint_c=21.0, base_c=21.0)
    assert [lv.power_w for lv in table.levels] == [0.0, 800.0, 800.0]


def test_cooling_shifts_down() -> None:
    state = DeviceState(on=True, mode="cool", setpoint_c=24.0)
    table = build_levels(climate_device(), state, None, SETTINGS, Q)
    assert table is not None
    assert [lv.setpoint_c for lv in table.levels] == [24.0, 23.5, 23.0, 22.5, 22.0]


def test_a_coarse_device_step_rounds_toward_the_base() -> None:
    state = DeviceState(on=True, mode="heat", setpoint_c=21.0)
    table = build_levels(climate_device(), state, None, SETTINGS, StepQuantiser())
    assert table is not None
    assert [lv.setpoint_c for lv in table.levels] == [21.0, 22.0, 23.0]


def test_a_base_outside_the_band_never_shifts_against_the_direction() -> None:
    state = DeviceState(on=True, mode="heat", setpoint_c=25.5)
    table = build_levels(climate_device(max_c=25.0), state, None, SETTINGS, Q)
    assert table is None  # clamping would *lower* a heat set-point: no level


def test_the_base_is_kept_while_shifted() -> None:
    shifted = DeviceState(on=True, mode="heat", setpoint_c=22.0)
    table = build_levels(climate_device(), shifted, None, SETTINGS, Q, base_setpoint_c=21.0)
    assert table is not None
    assert table.base_setpoint_c == 21.0
    assert observed_level(table, climate_device(), shifted) == 2


def test_climate_off_needs_the_opt_in() -> None:
    off = DeviceState(on=False, mode="off", setpoint_c=21.0)
    assert build_levels(climate_device(), off, None, SETTINGS, Q) is None
    device = climate_device(allow_switch_on=True)  # switch-on mode: cool
    table = build_levels(device, off, None, SETTINGS, Q)
    assert table is not None
    assert table.switchable
    assert table.levels[0].action == SwitchAction(on=False)
    assert table.levels[1].action == SwitchAction(on=True)
    assert [lv.setpoint_c for lv in table.levels[1:]] == [21.0, 20.5, 20.0]
    assert observed_level(table, device, off) == 0


def test_climate_switched_on_by_us_keeps_the_off_level() -> None:
    device = climate_device(allow_switch_on=True)
    on = DeviceState(on=True, mode="cool", setpoint_c=21.0)
    table = build_levels(device, on, None, SETTINGS, Q, switched_on_by_us=True)
    assert table is not None
    assert table.levels[0].action == SwitchAction(on=False)
    assert observed_level(table, device, on) == 1


def test_climate_not_controllable_cases() -> None:
    device = climate_device()
    assert build_levels(device, None, None, SETTINGS, Q) is None
    unknown = DeviceState(on=True, mode="heat", setpoint_c=21.0, quality=Quality.UNAVAILABLE)
    assert build_levels(device, unknown, None, SETTINGS, Q) is None
    no_setpoint = DeviceState(on=True, mode="heat")
    assert build_levels(device, no_setpoint, None, SETTINGS, Q) is None
    fan = DeviceState(on=True, mode="fan_only", setpoint_c=21.0)
    assert build_levels(device, fan, None, SETTINGS, Q) is None
    no_comfort = device.model_copy(update={"comfort": None})
    assert build_levels(no_comfort, HEAT_ON, None, SETTINGS, Q) is None


def test_shift_direction() -> None:
    device = climate_device(allow_switch_on=True)
    assert shift_direction(HEAT_ON, device) == 1
    assert shift_direction(DeviceState(on=True, mode="cool"), device) == -1
    assert shift_direction(DeviceState(on=False, mode="off"), device) == -1  # switch-on mode
    assert shift_direction(None, climate_device()) == 0
    assert shift_direction(DeviceState(on=True, mode="auto"), device) == 0


# --- observed level ----------------------------------------------------------------------


def test_observed_level_per_kind() -> None:
    sw = switch_device()
    table = build_levels(sw, None, None, SETTINGS, Q)
    assert table is not None
    assert observed_level(table, sw, DeviceState(on=True)) == 1
    assert observed_level(table, sw, DeviceState(on=None)) is None
    assert observed_level(table, sw, None) is None
    assert observed_level(table, sw, DeviceState(on=True, quality=Quality.INVALID)) is None

    steps = steps_device()
    table = build_levels(steps, None, None, SETTINGS, Q)
    assert table is not None
    assert observed_level(table, steps, DeviceState(step=2)) == 2
    assert observed_level(table, steps, DeviceState(step=7)) is None

    modes = mode_device()
    table = build_levels(modes, None, None, SETTINGS, Q)
    assert table is not None
    assert observed_level(table, modes, DeviceState(mode="boost")) == 2
    assert observed_level(table, modes, DeviceState(mode="party")) is None

    number = continuous_device()
    table = build_levels(number, None, None, SETTINGS, Q)
    assert table is not None
    assert observed_level(table, number, DeviceState(value=None)) is None
    level = observed_level(table, number, DeviceState(value=1010.0))
    assert level is not None
    assert abs(table.levels[level].power_w - 1010.0) <= SETTINGS.default_hysteresis_w

    ac = climate_device()
    table = build_levels(ac, HEAT_ON, None, SETTINGS, Q)
    assert table is not None
    assert observed_level(table, ac, DeviceState(on=True, mode="heat", setpoint_c=24.9)) is None
    assert observed_level(table, ac, DeviceState(on=True, mode="heat")) is None
    assert observed_level(table, ac, DeviceState(on=False, mode="off")) is None
