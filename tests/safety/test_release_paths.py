# SPDX-License-Identifier: Apache-2.0
"""Release paths found in the control-safety review of 3-B (WP8/WP9 wiring).

* a switch is recorded when it is *sent*: a slow echo (late confirmation) or a release during the
  settle wait must not leave a switch-on without its minimum run time;
* a release gives back a manual change that the override detection has not judged yet (made while
  SunCore was down, or during an outage whose resync races the release);
* a configuration change hands the devices over without breaking min run/pause
  (``handover`` + ``recover(after_handover=True)``), while a crash recovery, a changed device and
  the emergency stop still release at once.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.control import DeviceState
from suncore.core.decisions import DecisionKind, SetpointAction, SwitchAction
from suncore.safety.runtime import DeviceRuntime, MemoryRuntimeStore
from tests.safety.helpers import T0, climate_device, make_intent, switch_device
from tests.safety.test_gate import Harness, make_harness

OFF = SwitchAction(on=False)
ON = SwitchAction(on=True)


def switch_on(h: Harness, *, min_run_s: int) -> None:
    """SunCore switches the pool on at the harness' now (a well-behaved device)."""
    decision = asyncio.run(h.gate.submit(make_intent(ON, ts=h.clock.now)))
    assert decision.kind is DecisionKind.ACCEPTED
    h.states["pool"] = DeviceState(on=True)
    runtime = h.gate.runtime_of("pool")
    assert runtime is not None
    assert runtime.baseline == OFF
    assert runtime.last_on_ts is not None
    assert min_run_s >= 0


def successor(h: Harness, *, min_run_s: int) -> Harness:
    """The gate a rebuild creates: same store, same device, same time."""
    nxt = make_harness((switch_device(min_run_s=min_run_s),), store=h.store)
    nxt.clock.now = h.clock.now
    nxt.actuators["pool"].observed = h.actuators["pool"].observed
    nxt.states["pool"] = h.states["pool"]
    return nxt


# --- switches are recorded when sent --------------------------------------------------------------


def test_a_late_confirmed_switch_on_keeps_its_minimum_run_time() -> None:
    """The LG cloud echoes later than the settle time: the read-back fails, the echo confirms the
    write later. The switch-on used to be recorded only by the read-back (or, for the late echo,
    only when SunCore had written before), so the first switch-on had no minimum run time."""
    h = make_harness((switch_device(min_run_s=300),))
    h.actuators["pool"].follow = False  # the device has not echoed yet at the read-back
    first = asyncio.run(h.gate.submit(make_intent(ON, ts=h.clock.now)))
    assert first.kind is DecisionKind.FAILED
    assert first.reasons[0].key == "safety.readback_mismatch"
    h.clock.advance(10)
    h.actuators["pool"].observed = ON
    h.states["pool"] = DeviceState(on=True)
    verdict = asyncio.run(h.gate.observe("pool", ON))
    assert verdict.late_confirm
    runtime = h.gate.runtime_of("pool")
    assert runtime is not None
    assert runtime.last_written == ON
    assert runtime.last_on_ts == T0
    assert len(runtime.switch_times) == 1  # recorded once, not again by the late echo
    h.clock.advance(60)
    off = asyncio.run(h.gate.submit(make_intent(OFF, intent_id="i-2", ts=h.clock.now)))
    assert off.kind is DecisionKind.REJECTED
    assert off.reasons[0].key == "safety.min_run"


# --- a release gives back what the user left -----------------------------------------------------


def test_a_release_gives_back_a_manual_change_not_judged_yet() -> None:
    h = make_harness((climate_device(),))
    h.states["ac"] = DeviceState(on=True, mode="cool", setpoint_c=24.0)
    h.actuators["ac"].observed = SetpointAction(setpoint_c=24.0, base_c=24.0)
    shift = SetpointAction(setpoint_c=23.0, base_c=24.0)
    taken = asyncio.run(h.gate.submit(make_intent(shift, asset_id="ac", ts=h.clock.now)))
    assert taken.kind is DecisionKind.ACCEPTED
    h.clock.advance(120)
    user = SetpointAction(setpoint_c=21.0, base_c=21.0)
    h.actuators["ac"].observed = user  # the feed has not reached ``observe`` yet
    asyncio.run(h.gate.shutdown())
    assert h.actuators["ac"].released == [user]
    runtime = h.gate.runtime_of("ac")
    assert runtime is not None
    assert runtime.override  # respected per the override policy
    assert runtime.is_held(h.clock.now)


def test_crash_recovery_does_not_overwrite_a_change_made_while_suncore_was_down() -> None:
    """Observations reach the gate only after ``recover()``; the recovery release judges the
    observed state itself."""
    store = MemoryRuntimeStore()
    asyncio.run(
        store.save_runtime(
            DeviceRuntime(
                device_id="ac",
                baseline=SetpointAction(setpoint_c=24.0, base_c=24.0),
                last_written=SetpointAction(setpoint_c=23.0, base_c=24.0),
                last_write_ts=T0 - timedelta(hours=1),
            )
        )
    )
    h = make_harness((climate_device(),), store=store)
    user = SetpointAction(setpoint_c=20.0, base_c=20.0)
    h.actuators["ac"].observed = user
    asyncio.run(h.gate.recover())
    assert h.actuators["ac"].released == [user]


def test_a_release_without_a_manual_change_restores_the_takeover_state() -> None:
    h = make_harness((climate_device(),))
    h.states["ac"] = DeviceState(on=True, mode="cool", setpoint_c=24.0)
    h.actuators["ac"].observed = SetpointAction(setpoint_c=24.0, base_c=24.0)
    shift = SetpointAction(setpoint_c=23.0, base_c=24.0)
    asyncio.run(h.gate.submit(make_intent(shift, asset_id="ac", ts=h.clock.now)))
    h.clock.advance(120)
    asyncio.run(h.gate.shutdown())
    assert h.actuators["ac"].released == [SetpointAction(setpoint_c=24.0, base_c=24.0)]
    runtime = h.gate.runtime_of("ac")
    assert runtime is not None
    assert not runtime.override


# --- configuration change: handover ------------------------------------------------------------


@settings(max_examples=40, deadline=None)
@given(
    min_run_s=st.integers(min_value=60, max_value=1800),
    edit_after_s=st.integers(min_value=0, max_value=3600),
)
def test_a_handover_never_switches_a_kept_device_off_inside_its_minimum_run_time(
    min_run_s: int, edit_after_s: int
) -> None:
    """A rename (or any edit that keeps the device) must not short-cycle it: the switch-off of
    the release happens at ``max(edit, switch-on + min_run)``, executed by the next gate."""
    h = make_harness((switch_device(min_run_s=min_run_s),))
    switch_on(h, min_run_s=min_run_s)
    recorded = h.gate.runtime_of("pool")
    assert recorded is not None
    assert recorded.last_on_ts is not None
    on_at = recorded.last_on_ts  # recorded when the switch was sent
    h.clock.advance(edit_after_s)
    asyncio.run(h.gate.handover({"pool"}))
    nxt = successor(h, min_run_s=min_run_s)
    asyncio.run(nxt.gate.recover(after_handover=True))
    due = on_at + timedelta(seconds=min_run_s)
    released_at = None
    for actuator_owner in (h, nxt):
        if actuator_owner.actuators["pool"].released:
            released_at = actuator_owner.clock.now
    while released_at is None:
        assert nxt.clock.now < due + timedelta(seconds=10), "the deferred release never ran"
        nxt.clock.advance(5)
        asyncio.run(nxt.gate.poll())
        if nxt.actuators["pool"].released:
            released_at = nxt.clock.now
    assert released_at >= due
    assert released_at < max(due, on_at + timedelta(seconds=edit_after_s)) + timedelta(seconds=10)
    runtime = nxt.gate.runtime_of("pool")
    assert runtime is not None
    assert not runtime.under_control


def test_a_handover_releases_a_changed_device_at_once() -> None:
    h = make_harness((switch_device(min_run_s=600),))
    switch_on(h, min_run_s=600)
    h.clock.advance(30)
    asyncio.run(h.gate.handover(set()))  # the device's entities or control changed
    assert h.actuators["pool"].released == [OFF]


def test_crash_recovery_does_not_wait_for_a_deferred_release() -> None:
    h = make_harness((switch_device(min_run_s=600),))
    switch_on(h, min_run_s=600)
    h.clock.advance(30)
    asyncio.run(h.gate.handover({"pool"}))
    assert h.actuators["pool"].released == []
    nxt = successor(h, min_run_s=600)
    asyncio.run(nxt.gate.recover())  # a new process: no handover
    assert nxt.actuators["pool"].released == [OFF]


def test_the_emergency_stop_does_not_wait_for_a_deferred_release_after_a_handover() -> None:
    h = make_harness((switch_device(min_run_s=600),))
    switch_on(h, min_run_s=600)
    h.clock.advance(30)
    asyncio.run(h.gate.handover({"pool"}))
    asyncio.run(h.store.save_emergency_stop(True))  # pressed while the stack was rebuilt
    nxt = successor(h, min_run_s=600)
    asyncio.run(nxt.gate.recover(after_handover=True))
    assert nxt.actuators["pool"].released == [OFF]


def test_a_handover_during_the_settle_wait_keeps_the_switch_protected() -> None:
    """The switch-on is recorded when sent: a rebuild during its settle wait defers the release."""
    holder: dict[str, Harness] = {}

    async def settle(seconds: float) -> None:
        h = holder["h"]
        h.clock.advance(seconds)
        await h.gate.handover({"pool"})

    h = make_harness((switch_device(min_run_s=600, settle_s=5),), sleep=settle)
    holder["h"] = h
    asyncio.run(h.gate.submit(make_intent(ON, ts=h.clock.now)))
    assert h.actuators["pool"].released == []
    runtime = h.gate.runtime_of("pool")
    assert runtime is not None
    assert runtime.release_due_ts == T0 + timedelta(seconds=600)
