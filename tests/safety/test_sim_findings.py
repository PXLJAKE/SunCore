# SPDX-License-Identifier: Apache-2.0
"""Regressions found by the closed-loop simulator (Phase 3 WP8).

The unit tests of the gate use a ``states`` stub that never changes; against real drivers the
entity cache already shows the new state when the write is confirmed, which exposed two gaps.
"""

from __future__ import annotations

import asyncio

from suncore.core.control import DeviceState
from suncore.core.decisions import DecisionKind, Intent, SetpointAction, SwitchAction
from tests.safety.helpers import climate_device, make_intent, switch_device
from tests.safety.test_gate import Harness, make_harness


def follow_the_write(h: Harness, device_id: str) -> None:
    """The entity cache shows the new state as soon as the device has applied the write."""
    actuator = h.actuators[device_id]
    original = actuator.apply

    async def apply(intent: Intent) -> str | None:
        context = await original(intent)
        action = intent.action
        if isinstance(action, SwitchAction):
            h.states[device_id] = DeviceState(on=action.on)
        return context

    actuator.apply = apply  # type: ignore[method-assign]


def test_a_switch_is_recorded_when_the_state_already_shows_it_after_the_settle_wait() -> None:
    """Regression: ``is_switch_transition`` was judged after the settle wait, against a state that
    already followed the write - so min run / min pause and the switch counter never engaged."""
    h = make_harness((switch_device(min_run_s=300, min_pause_s=300),))
    follow_the_write(h, "pool")
    on = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True), ts=h.clock.now)))
    assert on.kind is DecisionKind.ACCEPTED
    runtime = h.gate.runtime_of("pool")
    assert runtime is not None
    assert runtime.last_on_ts is not None
    assert len(runtime.switch_times) == 1
    h.clock.advance(60)  # far inside min_run_s
    off = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=False), intent_id="i-2", ts=h.clock.now))
    )
    assert off.kind is DecisionKind.REJECTED
    assert off.reasons[0].key == "safety.min_run"
    h.clock.advance(300)
    later = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=False), intent_id="i-3", ts=h.clock.now))
    )
    assert later.kind is DecisionKind.ACCEPTED


def test_the_switches_per_hour_limit_counts_with_a_following_state() -> None:
    h = make_harness((switch_device(max_switches_per_hour=2),))
    follow_the_write(h, "pool")
    kinds = []
    for n, on in enumerate((True, False, True), start=1):
        decision = asyncio.run(
            h.gate.submit(make_intent(SwitchAction(on=on), intent_id=f"i-{n}", ts=h.clock.now))
        )
        kinds.append(decision.kind)
        h.clock.advance(10)
    assert kinds == [DecisionKind.ACCEPTED, DecisionKind.ACCEPTED, DecisionKind.REJECTED]


def test_a_release_after_an_override_gives_back_what_the_user_left() -> None:
    """Regression: the takeover baseline (the unit's own 24) was written over the user's manual
    change (25) by a shutdown, an emergency stop or a mode change during the hold."""
    h = make_harness((climate_device(),))
    h.states["ac"] = DeviceState(on=True, mode="cool", setpoint_c=24.0)
    h.actuators["ac"].observed = SetpointAction(setpoint_c=24.0, base_c=24.0)
    shift = SetpointAction(setpoint_c=23.0, base_c=24.0)
    taken = asyncio.run(h.gate.submit(make_intent(shift, asset_id="ac", ts=h.clock.now)))
    assert taken.kind is DecisionKind.ACCEPTED
    runtime = h.gate.runtime_of("ac")
    assert runtime is not None
    assert runtime.baseline == SetpointAction(setpoint_c=24.0, base_c=24.0)
    h.clock.advance(60)
    user = SetpointAction(setpoint_c=25.0, base_c=25.0)
    verdict = asyncio.run(h.gate.observe("ac", user))
    assert verdict.override
    runtime = h.gate.runtime_of("ac")
    assert runtime is not None
    assert runtime.baseline == user
    assert runtime.base_setpoint_c == 25.0
    asyncio.run(h.gate.shutdown())
    assert h.actuators["ac"].released == [user]
