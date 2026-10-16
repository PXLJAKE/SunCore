# SPDX-License-Identifier: Apache-2.0
"""A cloud integration shows the new state before the gate confirms the write.

``lg_thinq`` pushes the set-point a few seconds after the call; the gate reads back only after the
settle time. Both findings come from the closed-loop simulator (Phase 3 WP8), whose devices push
with a delay - the instant devices of ``tests.controller.sim`` never exposed them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from suncore.controller import Controller, ControllerInputs
from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings, DeviceState, PowerEstimate
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    SetpointAction,
    SwitchAction,
)
from suncore.safety.invariants import InvariantContext, check_comfort
from tests.safety.helpers import T0, climate_device, flows, runtime

GOALS = GoalsConfig(climate_before_export=True)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def decide(intent: Intent) -> Decision:
    return Decision(
        intent=intent, kind=DecisionKind.ACCEPTED, ts=intent.ts, mode=OperatingMode.AUTOMATIC
    )


def tick(
    controller: Controller, when: datetime, state: DeviceState, grid_w: float = -3000.0
) -> list[Intent]:
    draw = 800.0 if state.on else 0.0
    result = controller.step(
        when,
        ControllerInputs(
            flows=flows(grid_w=grid_w),
            states={"ac": state},
            estimates={"ac": PowerEstimate(value_w=draw, uncertainty_w=10.0)},
            goals=GOALS,
        ),
    )
    return list(result.intents)


def drive_until_intent(
    controller: Controller, state: DeviceState, start: float, until: float = 600.0
) -> tuple[Intent, float]:
    t = start
    while t < until:
        intents = tick(controller, at(t), state)
        if intents:
            return intents[0], t
        t += 5.0
    msg = "the controller never proposed anything"
    raise AssertionError(msg)


def test_a_pushed_shift_does_not_become_the_users_base_while_it_is_in_flight() -> None:
    """Regression: the push arrives while the intent is still in flight (the gate has not
    confirmed it); the table was rebased onto the pushed 23.5, the confirmed level 1 then read
    as level 0 ("state mismatch") and every further shift moved the base again."""
    ac = climate_device("ac", nominal_w=800.0)
    controller = Controller([ac])
    state = DeviceState(on=True, mode="cool", setpoint_c=24.0)
    intent, t = drive_until_intent(controller, state, 0.0)
    assert isinstance(intent.action, SetpointAction)
    assert (intent.action.setpoint_c, intent.action.base_c) == (23.5, 24.0)
    # the push: the unit reports 23.5 while the write is not confirmed yet
    pushed = DeviceState(on=True, mode="cool", setpoint_c=23.5)
    for n in (1, 2):
        assert tick(controller, at(t + 5.0 * n), pushed) == []
    machine = controller.allocator.machine("ac")
    assert machine is not None
    assert machine.base_setpoint_c == 24.0  # still the user's own set-point
    # the gate confirms: level 1 at the base 24, no deviation
    assert controller.report(decide(intent))
    result = controller.step(
        at(t + 20.0),
        ControllerInputs(
            flows=flows(grid_w=-3000.0),
            states={"ac": pushed},
            estimates={"ac": PowerEstimate(value_w=800.0, uncertainty_w=10.0)},
            goals=GOALS,
        ),
    )
    assert result.deviations == ()
    assert machine.level == 1
    assert machine.base_setpoint_c == 24.0
    # the next shift is relative to 24 (22), not to the pushed 23.5 (22 either way, but the base
    # carried in the action tells which)
    nxt, _ = drive_until_intent(controller, pushed, t + 25.0)
    assert isinstance(nxt.action, SetpointAction)
    assert nxt.action.base_c == 24.0


def test_a_unit_switched_on_by_us_can_be_switched_off_again() -> None:
    """Regression: the switch-on makes the unit's set-point known (a cloud integration reports
    none while it is off), which added the shift levels; the controller treated that as a new
    table, forgot it had switched the unit on and could no longer take it off."""
    ac = climate_device("ac", nominal_w=800.0, allow_switch_on=True)
    controller = Controller([ac])
    off = DeviceState(on=False, mode="off")
    intent, t = drive_until_intent(controller, off, 0.0)
    assert intent.action == SwitchAction(on=True)
    assert controller.report(decide(intent))
    on = DeviceState(on=True, mode="cool", setpoint_c=24.0)  # now the set-point is known
    for n in range(1, 4):
        tick(controller, at(t + 5.0 * n), on)
    machine = controller.allocator.machine("ac")
    assert machine is not None
    assert machine.switched_on_by_us
    assert machine.table is not None
    assert machine.table.switchable
    assert [lv.power_w > 0 for lv in machine.table.levels][:2] == [False, True]


def test_the_way_down_from_a_shift_restores_the_base_before_the_switch_off() -> None:
    """Regression: level 1 of a unit that is on was ``SwitchAction(on=True)`` - a no-op that left
    the shifted set-point in place, so the unit was switched off shifted and came back on with the
    shifted value as the user's new base (24 -> 23 -> 22 ... on every cycle)."""
    ac = climate_device("ac", nominal_w=800.0, allow_switch_on=True)
    controller = Controller([ac])
    state = DeviceState(on=True, mode="cool", setpoint_c=24.0)
    intent, t = drive_until_intent(controller, DeviceState(on=False, mode="off"), 0.0)
    assert controller.report(decide(intent))
    for n in range(1, 4):
        tick(controller, at(t + 5.0 * n), state)
    machine = controller.allocator.machine("ac")
    assert machine is not None
    assert machine.table is not None
    level_one = machine.table.level(1)
    assert level_one.action == SetpointAction(setpoint_c=24.0, base_c=24.0)
    assert machine.table.level(0).action == SwitchAction(on=False)


def test_a_base_outside_the_comfort_band_does_not_block_the_way_down() -> None:
    """Review 3-B (control-safety): with the base set-point (28, set by the user while the unit
    was off) outside the comfort band (20-26), level 1 as ``SetpointAction(28)`` is refused by the
    gate's comfort invariant for ever; a unit SunCore switched on and shifted could then never be
    switched off again (grid import caused by SunCore). Level 1 stays the no-op switch-on then."""
    ac = climate_device("ac", nominal_w=800.0, allow_switch_on=True)
    controller = Controller([ac])
    intent, t = drive_until_intent(controller, DeviceState(on=False, mode="off"), 0.0)
    assert intent.action == SwitchAction(on=True)
    assert controller.report(decide(intent))
    hot = DeviceState(on=True, mode="cool", setpoint_c=28.0)
    for n in range(1, 4):
        tick(controller, at(t + 5.0 * n), hot)
    machine = controller.allocator.machine("ac")
    assert machine is not None
    assert machine.table is not None
    assert machine.switched_on_by_us
    assert machine.table.level(0).action == SwitchAction(on=False)
    assert machine.table.level(1).action == SwitchAction(on=True)
    # every level the way down passes through is acceptable to the comfort invariant
    for level in machine.table.levels[:-1]:
        ctx = InvariantContext(
            action=level.action,
            device=ac,
            state=hot,
            runtime=runtime("ac"),
            now=T0,
            settings=ControlSettings(),
        )
        assert check_comfort(ctx) is None, level
