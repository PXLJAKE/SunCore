# SPDX-License-Identifier: Apache-2.0
"""PR 6-L: shiftable programs through the safety gate (P1-P6 in context).

The gate is the only writer: a switch on a program device is judged with the device's open job
(the injected lookup), a fresh measured power, the deadline binding (P5) and the grid. The
plug's takeover baseline is on (P3); a release never switches off a program that may run (P2);
the G2 fuse waiver never sheds a running program (6-F review H3); a notify-only program is
never written; shadow stays the default and P6 caps an unconfirmed device at assisted.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta

from suncore.core.control import ControlSettings, DeviceState, PowerEstimate
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    OperatingMode,
    Reason,
    ReleaseAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig
from suncore.core.flex import JobState, ProgramJob
from suncore.core.signals import Quality
from suncore.safety.gate import GateInputs, SafetyGate
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import DeviceRuntime, MemoryRuntimeStore
from tests.safety.helpers import (
    NOTIFY,
    PLUG,
    REMOTE,
    T0,
    FakeClock,
    SpyActuator,
    flows,
    make_intent,
    surplus,
)

AUTO = OperatingMode.AUTOMATIC
ON = SwitchAction(on=True)
OFF = SwitchAction(on=False)
DEADLINE = T0 + timedelta(hours=6)  # latest start: T0 + 4 h


def armed(*, ago_s: float = 60.0, state: JobState = JobState.ARMED) -> ProgramJob:
    armed_at = T0 - timedelta(seconds=ago_s)
    started = armed_at if state is JobState.RUNNING else None
    return ProgramJob(
        id="j1",
        device_id="washer",
        state=state,
        deadline=DEADLINE,
        armed_at=armed_at,
        started_at=started,
    )


@dataclasses.dataclass
class Harness:
    gate: SafetyGate
    clock: FakeClock
    spy: SpyActuator
    store: MemoryRuntimeStore
    jobs: list[ProgramJob]


def make(
    *,
    device: DeviceConfig = PLUG,
    jobs: list[ProgramJob] | None = None,
    power_w: float | None = 2.0,
    quality: Quality = Quality.OK,
    surplus_w: float = 3000.0,
    grid_w: float = -3000.0,
    import_limit_w: float | None = 16_000.0,
    fuse_limit_w: float | None = None,
    observed_on: bool = False,
    store: MemoryRuntimeStore | None = None,
    device_mode: OperatingMode = AUTO,
) -> Harness:
    clock = FakeClock()
    job_list = [armed()] if jobs is None else jobs
    spy = SpyActuator("washer", observed=SwitchAction(on=observed_on))
    estimates = (
        {}
        if power_w is None
        else {"washer": PowerEstimate(value_w=power_w, uncertainty_w=0.0, quality=quality)}
    )
    inputs = GateInputs(
        flows=flows(grid_w=grid_w),
        surplus=surplus(surplus_w),
        estimates=estimates,
        import_limit_w=import_limit_w,
        fuse_limit_w=fuse_limit_w,
    )
    states: dict[str, DeviceState | None] = {"washer": DeviceState(on=observed_on)}
    store = store or MemoryRuntimeStore()

    async def sink(_: Decision) -> None:
        return None

    gate = SafetyGate(
        clock=clock,
        sink=sink,
        actuators={"washer": spy},
        modes=StaticModes(AUTO, {"washer": device_mode}),
        devices={"washer": device}.get,
        states=states.get,
        inputs=lambda: dataclasses.replace(inputs, ts=clock.now),
        store=store,
        settings=ControlSettings(min_write_interval_s=5.0, default_settle_s=2.0),
        sleep=clock.sleep,
        programs=lambda _id: job_list,
    )
    return Harness(gate, clock, spy, store, job_list)


def submit(h: Harness, action: SwitchAction | ReleaseAction) -> Decision:
    return asyncio.run(h.gate.submit(make_intent(action, asset_id="washer", ts=h.clock.now)))


def keys(decision: Decision) -> list[str]:
    return [r.key for r in decision.reasons]


# --- P1: a start only for an armed job with a fresh power reading ----------------------------


def test_p1_an_armed_start_on_surplus_is_written_and_the_baseline_is_on() -> None:
    h = make()
    decision = submit(h, ON)
    assert decision.kind is DecisionKind.ACCEPTED
    assert len(h.spy.applied) == 1
    rt = h.gate.runtime_of("washer")
    assert rt is not None
    assert rt.baseline == ON  # P3: the plug's baseline is on, although it was observed off


def test_p1_no_start_without_an_armed_job() -> None:
    for jobs in ([], [armed(state=JobState.RUNNING)]):
        h = make(jobs=jobs)
        decision = submit(h, ON)
        assert decision.kind is DecisionKind.REJECTED
        assert "safety.program_no_job" in keys(decision)
        assert h.spy.applied == []


def test_p1_no_start_while_the_power_is_unknown_or_stale() -> None:
    for power, quality in ((None, Quality.OK), (0.0, Quality.STALE)):
        h = make(power_w=power, quality=quality)
        decision = submit(h, ON)
        assert decision.kind is DecisionKind.REJECTED
        assert "safety.program_power_unknown" in keys(decision)
        assert h.spy.applied == []


def test_an_unreadable_job_lookup_counts_as_no_job() -> None:
    h = make()

    def broken(_: str) -> list[ProgramJob]:
        raise RuntimeError("store down")

    h.gate._programs = broken
    decision = submit(h, ON)
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.program_no_job" in keys(decision)


# --- P5: the deadline allowance and the import limit -----------------------------------------


def test_p5_without_surplus_a_start_waits_until_the_deadline_binds() -> None:
    h = make(surplus_w=0.0, grid_w=0.0)
    early = submit(h, ON)
    assert early.kind is DecisionKind.REJECTED
    assert "safety.grid_import_caused" in keys(early)  # no allowance yet: the plain G1
    h.clock.advance(4 * 3600)  # the latest start that still meets the deadline
    late = submit(h, ON)
    assert late.kind is DecisionKind.ACCEPTED


def test_p5_the_import_limit_holds_for_a_bound_start() -> None:
    h = make(surplus_w=0.0, grid_w=15_000.0, import_limit_w=16_000.0)
    h.clock.advance(4 * 3600)
    decision = submit(h, ON)
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.grid_import_limit" in keys(decision)
    assert h.spy.applied == []


def test_p5_stale_site_inputs_never_start_a_program() -> None:
    h = make()
    h.gate._inputs = GateInputs  # no ts: stale
    h.clock.advance(4 * 3600)
    decision = submit(h, ON)
    assert decision.kind is DecisionKind.REJECTED
    assert h.spy.applied == []


# --- P2/P4: switching off --------------------------------------------------------------------


def test_p4_the_plug_is_switched_off_right_after_arming() -> None:
    h = make(observed_on=True, power_w=2.0)
    decision = submit(h, OFF)
    assert decision.kind is DecisionKind.ACCEPTED
    assert h.spy.applied[0].action == OFF


def test_p4_no_switch_off_after_the_arm_window() -> None:
    h = make(observed_on=True, jobs=[armed(ago_s=900.0)])
    decision = submit(h, OFF)
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.program_arm_window" in keys(decision)


def test_p2_a_running_program_is_not_switched_off_even_above_the_fuse_limit() -> None:
    # G2 waiver (shed above the fuse limit, min run skipped) never applies to a program (H3)
    h = make(
        observed_on=True,
        power_w=1800.0,
        grid_w=20_000.0,
        import_limit_w=16_000.0,
        fuse_limit_w=16_000.0,
    )
    decision = submit(h, OFF)
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.program_running" in keys(decision)
    assert h.spy.applied == []


def test_p2_an_unknown_power_blocks_the_switch_off() -> None:
    h = make(observed_on=True, power_w=None)
    decision = submit(h, OFF)
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.program_power_unknown" in keys(decision)


def test_a_remote_start_is_never_switched_off() -> None:
    h = make(device=REMOTE, observed_on=True)
    decision = submit(h, OFF)
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.program_remote_off" in keys(decision)


# --- release (P2, P3) --------------------------------------------------------------------------


def _controlled(
    baseline: SwitchAction, *, last_on_ago_s: float | None = None
) -> MemoryRuntimeStore:
    store = MemoryRuntimeStore()
    last_on = None if last_on_ago_s is None else T0 - timedelta(seconds=last_on_ago_s)
    rt = DeviceRuntime(device_id="washer").model_copy(
        update={
            "under_control": True,
            "baseline": baseline,
            "baseline_ts": T0 - timedelta(hours=1),
            "last_on_ts": last_on,
        }
    )
    asyncio.run(store.save_runtime(rt))
    return store


def release(h: Harness) -> Decision:
    return asyncio.run(h.gate.release_device("washer", Reason(key="safety.emergency_stop")))


def test_p3_a_release_restores_the_on_baseline() -> None:
    h = make(store=_controlled(ON), observed_on=False, power_w=0.0)
    decision = release(h)
    assert decision.kind is DecisionKind.ACCEPTED
    assert h.spy.released == [ON]


def test_p2_a_release_never_switches_a_running_program_off() -> None:
    for power, last_on in ((1500.0, None), (None, None), (0.0, 600.0)):
        h = make(store=_controlled(OFF, last_on_ago_s=last_on), observed_on=True, power_w=power)
        decision = release(h)
        assert decision.kind is DecisionKind.ACCEPTED
        assert h.spy.released == [None]  # no write: the plug stays as it is


def test_an_off_baseline_is_restored_when_the_program_is_idle() -> None:
    h = make(store=_controlled(OFF), observed_on=True, power_w=0.0, jobs=[])
    release(h)
    assert h.spy.released == [OFF]


# --- notify, modes ----------------------------------------------------------------------------


def test_a_notify_program_is_never_written() -> None:
    h = make(device=NOTIFY)
    for action in (ON, OFF):
        decision = submit(h, action)
        assert decision.kind is DecisionKind.REJECTED
        assert keys(decision) == ["safety.program_notify_only"]
    assert h.spy.applied == []


def test_shadow_writes_nothing() -> None:
    h = make(device_mode=OperatingMode.SHADOW)
    decision = submit(h, ON)
    assert decision.kind is DecisionKind.SHADOW
    assert h.spy.applied == []


def test_p6_an_unconfirmed_program_is_capped_at_assisted() -> None:
    h = make(device=PLUG.model_copy(update={"behaviour_confirmed": False}))
    decision = submit(h, ON)
    assert decision.kind is DecisionKind.PROPOSED
    assert h.spy.applied == []
