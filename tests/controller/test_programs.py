# SPDX-License-Identifier: Apache-2.0
"""PR 6-L: the program controller, deterministic with injected time.

It schedules a job from a fresh plan's ``start`` band, starts it there or at the latest start
that still meets the deadline, powers a plug down right after arming (P4), never starts on
unknown inputs, detects a manual start by power, marks a run done after its run time plus idle,
cancels a missed job, asks once for a notify-only program and releases once after a job it
wrote for. It never asks to switch off a running program.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from suncore.controller.loop import Controller, ControllerLoop
from suncore.controller.programs import (
    IN_FLIGHT_TIMEOUT_S,
    RETRY_QUIET_S,
    RETRY_S,
    ProgramController,
    ProgramInputs,
    ProgramOutcome,
)
from suncore.core.control import DeviceBand, DeviceState, Plan, PlanInterval, PowerEstimate
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Reason,
    ReleaseAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig
from suncore.core.flex import JobState, ProgramJob, transition
from suncore.core.signals import Quality
from tests.safety.helpers import NOTIFY, PLUG, REMOTE, T0

DEADLINE = T0 + timedelta(hours=6)
LATEST = DEADLINE - timedelta(minutes=120)


def job(*, armed_ago_s: float = 30.0) -> ProgramJob:
    return ProgramJob(
        id="j1", device_id="washer", deadline=DEADLINE, armed_at=T0 - timedelta(seconds=armed_ago_s)
    )


def plan_with_start(at: datetime, *, created_ago_min: float = 0.0) -> Plan:
    return Plan(
        created_at=at - timedelta(minutes=created_ago_min),
        intervals=(
            PlanInterval(
                start=at,
                end=at + timedelta(minutes=15),
                devices={"washer": DeviceBand(start=True)},
            ),
        ),
        source="solver",
    )


class Rig:
    """One program device, its job book and the appliance's observed state."""

    def __init__(self, device: DeviceConfig = PLUG, jobs: Sequence[ProgramJob] = ()) -> None:
        self.ctrl = ProgramController([device])
        self.device = device
        self.jobs = {j.id: j for j in jobs}
        self.now = T0
        self.on: bool | None = False
        self.power: float | None = 0.0
        self.quality = Quality.OK
        self.site_ok = True
        self.island = False
        self.plan = Plan.default()
        self.blocked: dict[str, object] = {}

    def step(self, seconds: float = 0.0) -> ProgramOutcome:
        self.now += timedelta(seconds=seconds)
        estimates = (
            {}
            if self.power is None
            else {
                "washer": PowerEstimate(value_w=self.power, uncertainty_w=0, quality=self.quality)
            }
        )
        states = {} if self.on is None else {"washer": DeviceState(on=self.on)}
        open_jobs = [j for j in self.jobs.values() if not j.final]
        out = self.ctrl.step(
            self.now,
            ProgramInputs(
                jobs={"washer": open_jobs},
                states=states,
                estimates=estimates,
                blocked={},
                plan=self.plan,
                site_ok=self.site_ok,
                island=self.island,
            ),
        )
        for changed in out.jobs:
            self.jobs[changed.id] = changed
        return out

    def answer(
        self, intent: Intent, kind: DecisionKind = DecisionKind.ACCEPTED, *reasons: str
    ) -> None:
        decision = Decision(
            intent=intent,
            kind=kind,
            ts=self.now,
            mode=OperatingMode.AUTOMATIC,
            reasons=tuple(Reason(key=k) for k in reasons),
        )
        assert self.ctrl.report(decision)
        if kind is DecisionKind.ACCEPTED and isinstance(intent.action, SwitchAction):
            self.on = intent.action.on

    @property
    def job(self) -> ProgramJob:
        (only,) = self.jobs.values()
        return only

    def state(self) -> JobState:
        return self.job.state


def switch_ons(out: ProgramOutcome) -> list[Intent]:
    return [i for i in out.intents if isinstance(i.action, SwitchAction) and i.action.on]


def switch_offs(out: ProgramOutcome) -> list[Intent]:
    return [i for i in out.intents if isinstance(i.action, SwitchAction) and not i.action.on]


# --- arming, P4 -------------------------------------------------------------------------------


def test_the_plug_is_powered_down_right_after_arming_and_waits_for_the_latest_start() -> None:
    rig = Rig(jobs=[job()])
    rig.on, rig.power = True, 2.0
    out = rig.step()
    (off,) = switch_offs(out)
    assert off.reasons[0].key == "controller.program.arm_off"
    rig.answer(off)
    rig.power = 0.0
    for _ in range(10):
        assert rig.step(60).intents == ()
    assert rig.job.state is JobState.ARMED


def test_no_power_down_after_the_arm_window_or_when_the_machine_draws_power() -> None:
    rig = Rig(jobs=[job(armed_ago_s=900.0)])
    rig.on, rig.power = True, 2.0
    assert switch_offs(rig.step()) == []
    rig = Rig(jobs=[job()])
    rig.on, rig.power = True, 300.0
    assert switch_offs(rig.step()) == []


def test_a_remote_start_is_never_powered_down() -> None:
    rig = Rig(device=REMOTE, jobs=[job()])
    rig.on, rig.power = True, 2.0
    assert switch_offs(rig.step()) == []


# --- starting ---------------------------------------------------------------------------------


def test_without_a_plan_the_start_is_at_the_latest_start() -> None:
    rig = Rig(jobs=[job()])
    assert switch_ons(rig.step()) == []
    rig.now = LATEST - timedelta(seconds=30)
    assert switch_ons(rig.step()) == []
    (start,) = switch_ons(rig.step(30))
    assert start.reasons[0].key == "controller.program.deadline_start"
    rig.answer(start)
    out = rig.step(30)
    assert rig.job.state is JobState.RUNNING
    assert [e.kind for e in out.events] == ["started"]


def test_a_fresh_plan_schedules_and_starts_in_its_window() -> None:
    rig = Rig(jobs=[job()])
    window = T0 + timedelta(hours=2)
    rig.plan = Plan(
        created_at=T0,
        intervals=(
            PlanInterval(start=T0, end=window, devices={}),
            PlanInterval(
                start=window,
                end=window + timedelta(minutes=15),
                devices={"washer": DeviceBand(start=True)},
            ),
        ),
        source="solver",
    )
    rig.step()
    assert rig.job.state is JobState.SCHEDULED
    assert rig.job.planned_start == window
    # the plan is refreshed at the window (a stale plan permits nothing)
    rig.now = window
    rig.plan = rig.plan.model_copy(update={"created_at": window})
    (start,) = switch_ons(rig.step())
    assert start.reasons[0].key == "controller.program.start"


def test_a_stale_plan_does_not_start_before_the_latest_start() -> None:
    window = T0 + timedelta(hours=2)
    rig = Rig(jobs=[job()])
    rig.plan = plan_with_start(window, created_ago_min=24 * 60)
    rig.now = window
    assert switch_ons(rig.step()) == []


@given(
    power=st.one_of(st.none(), st.just(0.0)),
    quality=st.sampled_from([Quality.OK, Quality.STALE, Quality.UNAVAILABLE]),
    site_ok=st.booleans(),
    island=st.booleans(),
)
def test_unknown_inputs_or_island_never_start(
    power: float | None, quality: Quality, site_ok: bool, island: bool
) -> None:
    rig = Rig(jobs=[job()])
    rig.power, rig.quality, rig.site_ok, rig.island = power, quality, site_ok, island
    rig.now = LATEST
    out = rig.step()
    known = power is not None and quality is Quality.OK and site_ok
    if not known or island:
        assert switch_ons(out) == []
        reason = out.reasons["washer"][0].key
        assert reason in {"controller.program.inputs_unknown", "controller.program.island"}
    else:
        assert len(switch_ons(out)) == 1


def test_a_rejected_start_is_retried_after_a_pause_and_a_shadow_one_after_a_longer_one() -> None:
    for kind, wait in ((DecisionKind.REJECTED, RETRY_S), (DecisionKind.SHADOW, RETRY_QUIET_S)):
        rig = Rig(jobs=[job()])
        rig.now = LATEST
        (start,) = switch_ons(rig.step())
        rig.answer(start, kind)
        assert switch_ons(rig.step(wait - 1)) == []
        assert len(switch_ons(rig.step(1))) == 1


# --- running, done, missed --------------------------------------------------------------------


def test_a_manual_start_is_detected_by_power_and_never_switched_off() -> None:
    rig = Rig(jobs=[job(armed_ago_s=3600.0)])
    rig.on, rig.power = True, 1800.0
    rig.step()
    out = rig.step(30)
    assert rig.job.state is JobState.RUNNING
    assert [e.kind for e in out.events] == ["started"]
    for _ in range(60):
        out = rig.step(60)
        assert switch_offs(out) == []


def test_a_run_is_done_after_its_run_time_and_five_idle_minutes_then_released_once() -> None:
    rig = Rig(jobs=[job()])
    rig.now = LATEST
    (start,) = switch_ons(rig.step())
    rig.answer(start)
    rig.power = 1500.0
    rig.step(30)
    assert rig.job.state is JobState.RUNNING
    rig.power = 0.0  # a pause inside the programme is not the end
    rig.step(600)
    assert rig.job.state is JobState.RUNNING
    rig.power = 1500.0
    rig.now = LATEST + timedelta(minutes=119)
    rig.step()
    rig.power = 0.0
    rig.now = LATEST + timedelta(minutes=121)
    rig.step()
    rig.step(299)
    assert rig.state() is JobState.RUNNING  # idle for less than five minutes
    out = rig.step(1)
    assert rig.state() is JobState.DONE
    assert [e.kind for e in out.events] == ["done"]
    out = rig.step(30)
    releases = [i for i in out.intents if isinstance(i.action, ReleaseAction)]
    assert len(releases) == 1
    assert rig.step(30).intents == ()


def test_unknown_power_never_ends_a_run() -> None:
    rig = Rig(jobs=[job()])
    rig.jobs["j1"] = transition(rig.job, JobState.RUNNING, T0)
    rig.power = None
    rig.now = T0 + timedelta(hours=5)
    for _ in range(20):
        rig.step(60)
    assert rig.job.state is JobState.RUNNING


def test_a_job_that_did_not_start_by_its_deadline_is_missed() -> None:
    rig = Rig(jobs=[job()])
    rig.power = None  # never a known power: never started
    rig.now = DEADLINE
    out = rig.step()
    assert rig.job.state is JobState.CANCELLED
    assert [e.kind for e in out.events] == ["missed"]


# --- notify ------------------------------------------------------------------------------------


def test_a_notify_program_asks_once_and_writes_nothing() -> None:
    rig = Rig(device=NOTIFY, jobs=[job()])
    rig.on = None
    assert rig.step().events == ()
    rig.now = LATEST
    out = rig.step()
    assert [e.kind for e in out.events] == ["start_now"]
    for _ in range(10):
        out = rig.step(60)
        assert out.events == ()
        assert out.intents == ()


def test_the_controller_is_deterministic() -> None:
    def run() -> list[tuple[str, ...]]:
        rig = Rig(jobs=[job()])
        rig.on, rig.power = True, 2.0
        trace: list[tuple[str, ...]] = []
        for _ in range(500):
            out = rig.step(60)
            for intent in out.intents:
                rig.answer(intent)
            trace.append(tuple(i.id for i in out.intents))
        return trace

    assert run() == run()


# --- review 6-L: releases are retried, nothing in flight blocks them, notices -----------------


def run_to_done(rig: Rig) -> None:
    """Start at the latest start (accepted), run, and finish; the job is ``done`` after it."""
    rig.now = LATEST
    (start,) = switch_ons(rig.step())
    rig.answer(start)
    rig.power = 1500.0
    rig.step(30)
    rig.now = LATEST + timedelta(minutes=121)
    rig.power = 0.0
    rig.step()
    rig.step(300)
    assert rig.state() is JobState.DONE


def releases(out: ProgramOutcome) -> list[Intent]:
    return [i for i in out.intents if isinstance(i.action, ReleaseAction)]


def test_h1_a_failed_release_is_sent_again_until_it_is_settled() -> None:
    rig = Rig(jobs=[job()])
    run_to_done(rig)
    (first,) = releases(rig.step(30))
    rig.answer(first, DecisionKind.FAILED)
    assert releases(rig.step(RETRY_S - 1)) == []
    (second,) = releases(rig.step(1))
    rig.answer(second, DecisionKind.REJECTED, "safety.state_unknown")
    (third,) = releases(rig.step(RETRY_S))
    rig.answer(third, DecisionKind.ACCEPTED)
    for _ in range(5):
        assert releases(rig.step(RETRY_S)) == []


def test_h1_a_held_or_not_under_control_release_is_settled() -> None:
    for kind, reasons in (
        (DecisionKind.HELD, ()),
        (DecisionKind.REJECTED, ("safety.not_under_control",)),
    ):
        rig = Rig(jobs=[job()])
        run_to_done(rig)
        (release,) = releases(rig.step(30))
        rig.answer(release, kind, *reasons)
        for _ in range(5):
            assert releases(rig.step(RETRY_S)) == []


def test_m1_a_submit_error_does_not_block_the_release() -> None:
    rig = Rig(jobs=[job()])
    run_to_done(rig)
    (release,) = releases(rig.step(30))
    rig.ctrl.reset_pending(release)  # the submission raised: no decision comes
    assert releases(rig.step(RETRY_S - 31)) == []
    assert len(releases(rig.step(31))) == 1


def test_m1_an_intent_without_a_decision_expires_and_the_release_follows() -> None:
    rig = Rig(jobs=[job()])
    rig.now = LATEST
    (start,) = switch_ons(rig.step())
    rig.answer(start)  # the start was written: a release is due when the job ends
    rig.jobs["j1"] = transition(rig.job, JobState.RUNNING, LATEST)
    rig.step(30)
    rig.jobs["j1"] = transition(rig.job, JobState.DONE, rig.now)
    assert len(releases(rig.step(30))) == 1  # never answered (transport lost)
    assert releases(rig.step(IN_FLIGHT_TIMEOUT_S - 1)) == []
    assert len(releases(rig.step(1))) == 1


def test_l2_a_notify_job_asked_before_a_restart_is_not_asked_again() -> None:
    rig = Rig(device=NOTIFY, jobs=[job()])
    rig.on = None
    rig.now = LATEST
    out = rig.step()
    (asked,) = out.events
    assert asked.job.notified_at == LATEST
    assert rig.job.notified_at == LATEST  # persisted with the job
    restarted = Rig(device=NOTIFY, jobs=[rig.job])
    restarted.on, restarted.now = None, LATEST + timedelta(minutes=5)
    for _ in range(5):
        assert restarted.step(60).events == ()


def test_l4_a_missed_job_with_a_power_gap_has_an_unknown_outcome() -> None:
    rig = Rig(jobs=[job()])
    rig.power = None
    rig.step()
    rig.power = 0.0
    rig.now = DEADLINE
    out = rig.step()
    (missed,) = out.events
    assert missed.kind == "missed"
    assert not missed.power_known
    clean = Rig(jobs=[job()])
    clean.site_ok = False  # known power, never started: really missed
    clean.step()
    clean.now = DEADLINE
    (really,) = clean.step().events
    assert really.power_known


async def test_m1_the_loop_resets_a_program_intent_whose_submission_raised() -> None:
    controller = Controller([PLUG])
    rig = Rig(jobs=[job()])
    rig.ctrl = controller.programs
    rig.now = LATEST
    (start,) = switch_ons(rig.step())

    async def broken(_: Intent) -> Decision:
        raise ConnectionError("gate unreachable")

    loop = ControllerLoop(controller, submit=broken)
    await loop._submit_one(start)
    assert switch_ons(rig.step(RETRY_S - 1)) == []
    assert len(switch_ons(rig.step(1))) == 1  # no longer in flight: retried
