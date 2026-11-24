# SPDX-License-Identifier: Apache-2.0
"""PR 6-L: the shiftable-program invariants P1, P2, P4, P5 as pure functions (property tests).

``safety.programs`` judges every action on a program device; these tests pin each invariant
over arbitrary jobs, powers and times: a start only for an armed job with a fresh power reading
(P1), never a switch-off while the appliance may run (P2), a switch-off only right after arming
(P4), and a deadline-bound start imports at most the program's peak power, within the import
limit (P5).
"""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given
from hypothesis import strategies as st

from suncore.core.decisions import ReleaseAction, SetpointAction, SwitchAction
from suncore.core.devices import (
    program_peak_w,
)
from suncore.core.flex import (
    PROGRAM_ARM_WINDOW_S,
    PROGRAM_OFF_MARGIN_S,
    JobState,
    ProgramJob,
)
from suncore.safety.programs import (
    POWER_TOLERANCE_W,
    REASON_PROGRAM_ARM_WINDOW,
    REASON_PROGRAM_NO_JOB,
    REASON_PROGRAM_NOTIFY_ONLY,
    REASON_PROGRAM_POWER_UNKNOWN,
    REASON_PROGRAM_REMOTE_OFF,
    REASON_PROGRAM_RUNNING,
    REASON_PROGRAM_SWITCH_ONLY,
    ProgramSafety,
    check_program,
    check_program_grid,
    is_armed,
    program_allowance_w,
    program_running,
)
from tests.safety.helpers import IDLE_W, NOTIFY, PLUG, REMOTE, RUNTIME_MIN, T0

ON = SwitchAction(on=True)
OFF = SwitchAction(on=False)


states = st.sampled_from(list(JobState))
powers = st.one_of(st.none(), st.floats(min_value=0, max_value=5000, allow_nan=False))
ages = st.floats(min_value=0, max_value=48 * 3600, allow_nan=False)


@st.composite
def jobs(draw: st.DrawFn) -> ProgramJob | None:
    if draw(st.booleans()) and draw(st.booleans()):
        return None
    state = draw(states)
    armed_ago = draw(ages)
    armed_at = T0 - timedelta(seconds=armed_ago)
    started = (
        armed_at + timedelta(seconds=1) if state in (JobState.RUNNING, JobState.DONE) else None
    )
    return ProgramJob(
        id="j1",
        device_id="washer",
        state=state,
        deadline=T0 + timedelta(hours=draw(st.integers(min_value=1, max_value=24))),
        armed_at=armed_at,
        planned_start=T0 if state is JobState.SCHEDULED else None,
        started_at=started,
        finished_at=started if state is JobState.DONE else None,
    )


@st.composite
def safeties(draw: st.DrawFn) -> ProgramSafety:
    last_on = draw(st.one_of(st.none(), ages.map(lambda s: T0 - timedelta(seconds=s))))
    return ProgramSafety(
        job=draw(jobs()), power_w=draw(powers), start_bound=draw(st.booleans()), last_on=last_on
    )


# --- P1: a start only for an armed job with a fresh power reading ----------------------------


@given(safeties())
def test_p1_a_start_needs_an_armed_job_and_a_known_power(ps: ProgramSafety) -> None:
    reason = check_program(ON, PLUG, ps, T0)
    if reason is None:
        assert is_armed(ps.job)
        assert ps.power_w is not None
    if not is_armed(ps.job):
        assert reason == REASON_PROGRAM_NO_JOB
    elif ps.power_w is None:
        assert reason == REASON_PROGRAM_POWER_UNKNOWN


# --- P2: never a switch-off while the appliance may run ---------------------------------------


@given(safeties())
def test_p2_a_switch_off_never_passes_while_the_program_may_run(ps: ProgramSafety) -> None:
    reason = check_program(OFF, PLUG, ps, T0)
    running = program_running(PLUG, ps, T0)
    if running is not False:
        assert reason in (REASON_PROGRAM_RUNNING, REASON_PROGRAM_POWER_UNKNOWN)
    if reason is None:
        assert ps.power_w is not None
        assert ps.power_w < IDLE_W
        assert ps.job is None or ps.job.state is not JobState.RUNNING
        assert ps.last_on is None or T0 - ps.last_on >= timedelta(
            minutes=RUNTIME_MIN, seconds=PROGRAM_OFF_MARGIN_S
        )


@given(st.floats(min_value=IDLE_W, max_value=5000, allow_nan=False), jobs())
def test_p2_drawing_idle_power_or_more_counts_as_running(
    power: float, job: ProgramJob | None
) -> None:
    ps = ProgramSafety(job=job, power_w=power)
    assert program_running(PLUG, ps, T0) is True
    assert check_program(OFF, PLUG, ps, T0) == REASON_PROGRAM_RUNNING


@given(st.floats(min_value=0, max_value=RUNTIME_MIN * 60 + PROGRAM_OFF_MARGIN_S - 1))
def test_p2_switched_on_by_suncore_recently_counts_as_running_even_at_zero_power(
    ago_s: float,
) -> None:
    ps = ProgramSafety(power_w=0.0, last_on=T0 - timedelta(seconds=ago_s))
    assert check_program(OFF, PLUG, ps, T0) == REASON_PROGRAM_RUNNING


@given(safeties())
def test_p2_a_remote_start_is_never_switched_off(ps: ProgramSafety) -> None:
    assert check_program(OFF, REMOTE, ps, T0) == REASON_PROGRAM_REMOTE_OFF


# --- P4: a switch-off only right after arming -------------------------------------------------


@given(safeties())
def test_p4_a_switch_off_needs_an_armed_job_within_the_arm_window(ps: ProgramSafety) -> None:
    if check_program(OFF, PLUG, ps, T0) is None:
        assert ps.job is not None
        assert is_armed(ps.job)
        assert T0 - ps.job.armed_at <= timedelta(seconds=PROGRAM_ARM_WINDOW_S)


@given(st.floats(min_value=PROGRAM_ARM_WINDOW_S + 1, max_value=24 * 3600, allow_nan=False))
def test_p4_a_plug_that_is_on_later_stays_on(armed_ago_s: float) -> None:
    job = ProgramJob(
        id="j1",
        device_id="washer",
        deadline=T0 + timedelta(hours=8),
        armed_at=T0 - timedelta(seconds=armed_ago_s),
    )
    ps = ProgramSafety(job=job, power_w=0.0)
    assert check_program(OFF, PLUG, ps, T0) == REASON_PROGRAM_ARM_WINDOW


# --- notify-only and the action kinds ---------------------------------------------------------


@given(safeties(), st.booleans())
def test_a_notify_program_is_never_written(ps: ProgramSafety, on: bool) -> None:
    assert check_program(SwitchAction(on=on), NOTIFY, ps, T0) == REASON_PROGRAM_NOTIFY_ONLY
    assert check_program(ReleaseAction(), NOTIFY, ps, T0) is None


@given(safeties())
def test_only_switches_reach_a_program(ps: ProgramSafety) -> None:
    action = SetpointAction(setpoint_c=21.0, base_c=20.0)
    assert check_program(action, PLUG, ps, T0) == REASON_PROGRAM_SWITCH_ONLY


# --- P5: the deadline allowance and the import limit -----------------------------------------


@given(safeties())
def test_p5_the_allowance_is_the_peak_power_only_for_a_bound_armed_start(
    ps: ProgramSafety,
) -> None:
    allowance = program_allowance_w(PLUG, ps)
    if ps.start_bound and is_armed(ps.job):
        assert allowance == program_peak_w(PLUG)
    else:
        assert allowance == 0.0


@given(
    after_w=st.floats(min_value=0, max_value=5000, allow_nan=False),
    surplus_w=st.one_of(st.none(), st.floats(min_value=0, max_value=8000, allow_nan=False)),
    claimed_w=st.floats(min_value=0, max_value=3000, allow_nan=False),
    grid_w=st.one_of(st.none(), st.floats(min_value=-8000, max_value=20_000, allow_nan=False)),
    limit_w=st.one_of(st.none(), st.floats(min_value=1000, max_value=20_000, allow_nan=False)),
    allowance_w=st.sampled_from([0.0, 2000.0]),
)
def test_p5_an_accepted_start_stays_within_allowance_and_import_limit(  # noqa: PLR0917
    after_w: float,
    surplus_w: float | None,
    claimed_w: float,
    grid_w: float | None,
    limit_w: float | None,
    allowance_w: float,
) -> None:
    reason = check_program_grid(
        after_w=after_w,
        current_lower_w=0.0,
        surplus_lower_w=surplus_w,
        claimed_w=claimed_w,
        grid_w=grid_w,
        import_limit_w=limit_w,
        allowance_w=allowance_w,
    )
    if surplus_w is None:
        assert reason is not None
        assert reason.key == "safety.surplus_unknown"
        return
    if reason is not None:
        return
    # the import it causes (beyond the surplus not yet claimed) stays within the allowance
    assert after_w - max(0.0, surplus_w - claimed_w) <= allowance_w + POWER_TOLERANCE_W
    if limit_w is not None:
        assert grid_w is not None
        assert grid_w + claimed_w + after_w <= limit_w
