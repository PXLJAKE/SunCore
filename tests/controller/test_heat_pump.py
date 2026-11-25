# SPDX-License-Identifier: Apache-2.0
"""The controller drives SG-Ready heat pumps and hot-water lifts (PR 6-K, plan WP11).

Levels (state 2/3/4, the lift upward only), the dwell, and the fail-safe: back to state 2 at once
for an unknown or stale probe, the heat limit, §14a and island - also while the surplus is
unknown, and regardless of dwell, minimum run and switch limit.
"""

from __future__ import annotations

from datetime import timedelta

from suncore.controller import Controller, ControllerInputs
from suncore.controller import reasons as rs
from suncore.controller.grid import GridInputs
from suncore.controller.hysteresis import DeviceMachine
from suncore.controller.levels import ConfigQuantiser, build_levels, shift_direction
from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings, DeviceState, PowerEstimate
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    SetpointAction,
    StepAction,
)
from suncore.core.grid import GridState, Para14aBudget, Para14aStatus
from tests.safety.helpers import T0, flows
from tests.safety.test_heatpump import lift_device, sg_device

SETTINGS = ControlSettings()


def hp_state(step: int, temperature: float | None = 45.0) -> DeviceState:
    return DeviceState(step=step, on=step > 0, temperature_c=temperature)


def test_sg_levels() -> None:
    table = build_levels(
        sg_device(allow_forced=True), hp_state(0), None, SETTINGS, ConfigQuantiser()
    )
    assert table is not None
    assert [lv.action for lv in table.levels] == [StepAction(step=i) for i in range(3)]
    assert [lv.power_w for lv in table.levels] == [0.0, 2000.0, 2000.0]
    assert table.ramped
    assert table.switchable
    two = build_levels(sg_device(), hp_state(0), None, SETTINGS, ConfigQuantiser())
    assert two is not None
    assert two.top == 1
    # Not controllable: no probe, or contacts in a pattern SunCore may not write.
    assert (
        build_levels(sg_device(probe=False), hp_state(0), None, SETTINGS, ConfigQuantiser()) is None
    )
    unknown = DeviceState(on=None, mode="sg:10", temperature_c=45.0)
    assert build_levels(sg_device(), unknown, None, SETTINGS, ConfigQuantiser()) is None


def test_lift_shifts_upward_only() -> None:
    device = lift_device()
    state = DeviceState(on=True, mode="heat", setpoint_c=50.0)
    assert shift_direction(DeviceState(on=True, mode="eco", setpoint_c=50.0), device) == 1
    table = build_levels(device, state, None, SETTINGS, ConfigQuantiser())
    assert table is not None
    targets = [lv.setpoint_c for lv in table.levels]
    assert targets[0] == 50.0
    assert all(t is not None and 50.0 <= t <= 58.0 for t in targets)


def run(
    device_args: dict[str, object],
    *,
    step: int,
    temperature: float | None = 45.0,
    grid_w: float | None = -6000.0,
    grid: GridInputs | None = None,
    ticks: int = 40,
    controller: Controller | None = None,
) -> tuple[Controller, list[object]]:
    device = sg_device(**device_args)  # type: ignore[arg-type]
    controller = controller or Controller([device])
    actions: list[object] = []
    for n in range(ticks):
        result = controller.step(
            T0 + timedelta(seconds=5 * n),
            ControllerInputs(
                flows=flows(grid_w=grid_w),
                states={"hp": hp_state(step, temperature)},
                estimates={"hp": PowerEstimate(value_w=2000.0 * (step > 0), uncertainty_w=20.0)},
                goals=GoalsConfig(),
                grid=grid,
            ),
        )
        actions.extend(i.action for i in result.intents)
    return controller, actions


def test_state_3_on_surplus() -> None:
    _, actions = run({}, step=0)
    assert actions
    assert actions[0] == StepAction(step=1)


def test_failsafe_returns_to_state_2_at_once() -> None:
    cases: list[tuple[dict[str, object], dict[str, object]]] = [
        ({}, {"temperature": None}),
        ({}, {"temperature": 60.0}),
        ({}, {"grid_w": None, "temperature": None}),  # surplus unknown as well
        (
            {"para14a": True},
            {"grid": GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, 4200.0))},
        ),
        ({"para14a": True}, {"grid": GridInputs(para14a=Para14aBudget(Para14aStatus.UNKNOWN))}),
        ({}, {"grid": GridInputs(grid_state=GridState.ISLAND)}),
    ]
    for device_args, situation in cases:
        # A long minimum run and one switch an hour must not hold the heat pump in state 3.
        args = {"min_run_s": 3600, "max_switches_per_hour": 1, **device_args}
        _, actions = run(args, step=1, ticks=2, **situation)  # type: ignore[arg-type]
        assert actions[:1] == [StepAction(step=0)], (device_args, situation)


def test_unflagged_heat_pump_ignores_para14a() -> None:
    grid = GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, 4200.0))
    _, actions = run({}, step=1, ticks=2, grid=grid)
    assert StepAction(step=0) not in actions


def test_dwell_holds_a_change_after_a_state_change() -> None:
    device = sg_device(allow_forced=True)
    machine = DeviceMachine(device, SETTINGS)
    table = build_levels(device, hp_state(0), None, SETTINGS, ConfigQuantiser())
    assert table is not None
    machine.sync(T0, table, 0)
    machine.submitted(T0, "i-1", 1)
    machine.report(
        Decision(
            intent=Intent(
                id="i-1", ts=T0, asset_id="hp", action=StepAction(step=1), origin=Origin.CONTROLLER
            ),
            kind=DecisionKind.ACCEPTED,
            ts=T0 + timedelta(seconds=10),
            mode=OperatingMode.AUTOMATIC,
        )
    )
    assert machine.level == 1
    reason = machine.blocked_by_protection(T0 + timedelta(seconds=60), 2)
    assert reason is not None
    assert reason.key == rs.HP_DWELL_WAIT
    assert machine.blocked_by_protection(T0 + timedelta(seconds=611), 2) is None
    assert machine.blocked_by_protection(T0 + timedelta(seconds=60), 0, failsafe=True) is None


def test_lift_goes_back_to_base_under_para14a() -> None:
    device = lift_device(para14a=True)
    controller = Controller([device])
    grid = GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, 4200.0))
    actions: list[object] = []
    lifted = DeviceState(on=True, mode="heat", setpoint_c=52.0)
    for n in range(3):
        result = controller.step(
            T0 + timedelta(seconds=5 * n),
            ControllerInputs(
                flows=flows(grid_w=-6000.0),
                states={"dhw": lifted},
                estimates={"dhw": PowerEstimate(value_w=1500.0, uncertainty_w=20.0)},
                goals=GoalsConfig(),
                grid=grid,
            ),
        )
        actions.extend(i.action for i in result.intents)
    # The controller adopts 52 °C as the user's base (nothing SunCore lifted): it never lifts.
    assert all(not isinstance(a, SetpointAction) or a.setpoint_c <= 52.0 for a in actions)
