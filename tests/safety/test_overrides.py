# SPDX-License-Identifier: Apache-2.0
"""Override detection (value-based, context id as positive evidence) and the hold policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.decisions import ModeAction, SetpointAction, StepAction, SwitchAction
from suncore.core.devices import OverridePolicy
from suncore.safety import overrides
from suncore.safety.runtime import DeviceRuntime
from tests.safety.helpers import T0, actions, runtime

BERLIN = ZoneInfo("Europe/Berlin")


def controlled(**update: object) -> DeviceRuntime:
    base = {
        "baseline": SwitchAction(on=False),
        "last_written": SwitchAction(on=True),
        "last_write_ts": T0 - timedelta(minutes=5),
        "last_write_context_id": "ctx-7",
    }
    base.update(update)
    return runtime(**base)


def test_not_under_control_is_never_an_override() -> None:
    assert not overrides.judge(SwitchAction(on=False), runtime(), T0, settle_s=10).override
    rt = runtime(baseline=SwitchAction(on=False))  # taken over but nothing written yet
    assert not overrides.judge(SwitchAction(on=False), rt, T0, settle_s=10).override


def test_value_match_is_not_an_override_and_context_match_is_proof() -> None:
    rt = controlled()
    assert not overrides.judge(SwitchAction(on=True), rt, T0, settle_s=10).override
    assert not overrides.judge(
        SwitchAction(on=False), rt, T0, settle_s=10, context_id="ctx-7"
    ).override
    assert overrides.is_ours("ctx-7", rt)
    assert not overrides.is_ours("ctx-8", rt)
    assert not overrides.is_ours(None, rt)
    assert not overrides.is_ours("ctx-7", runtime())


def test_mismatching_context_proves_nothing_the_value_decides() -> None:
    rt = controlled()
    verdict = overrides.judge(SwitchAction(on=False), rt, T0, settle_s=10, context_id="01KFRESH")
    assert verdict.override
    assert verdict.reason is not None
    assert verdict.reason.key == "safety.override_off"


def test_pending_write_is_not_judged() -> None:
    rt = controlled(last_write_ts=T0 - timedelta(seconds=3))
    assert not overrides.judge(SwitchAction(on=False), rt, T0, settle_s=5).override
    assert overrides.judge(SwitchAction(on=False), rt, T0, settle_s=3).override


def test_changed_setpoint_becomes_the_new_base() -> None:
    rt = controlled(last_written=SetpointAction(setpoint_c=23.0, base_c=22.0))
    v = overrides.judge(SetpointAction(setpoint_c=25.0, base_c=22.0), rt, T0, settle_s=1)
    assert v.override
    assert v.new_base_setpoint_c == 25.0
    assert v.reason is not None
    assert v.reason.key == "safety.override_setpoint"
    applied = overrides.apply_verdict(rt, v, T0, OverridePolicy(), UTC)
    assert applied.base_setpoint_c == 25.0
    assert applied.override
    other = overrides.judge(ModeAction(mode="heat"), rt, T0, settle_s=1)
    assert other.reason is not None
    assert other.reason.key == "safety.override_detected"
    assert other.new_base_setpoint_c is None


@settings(max_examples=200, deadline=None)
@given(actions, actions, st.booleans())
def test_override_iff_value_differs_after_settle(
    written: object, observed: object, ours: bool
) -> None:
    from suncore.core.decisions import Action  # noqa: PLC0415
    from suncore.safety.invariants import action_matches  # noqa: PLC0415

    assert isinstance(written, Action.__origin__) if hasattr(Action, "__origin__") else True
    rt = controlled(last_written=written)
    v = overrides.judge(observed, rt, T0, settle_s=1, context_id="ctx-7" if ours else "x")  # type: ignore[arg-type]
    expected = not ours and not action_matches(written, observed)  # type: ignore[arg-type]
    assert v.override is expected


def test_resume_at_next_day_uses_the_local_time_of_the_following_day() -> None:
    policy = OverridePolicy()  # next_day 04:00
    at_noon = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)  # 12:00 Berlin
    assert overrides.resume_at(policy, at_noon, BERLIN) == datetime(
        2026, 10, 15, 4, 0, tzinfo=BERLIN
    )
    # 03:00 local still waits for the *next* day's 04:00, never the same morning.
    early = datetime(2026, 10, 14, 1, 0, tzinfo=UTC)  # 03:00 Berlin
    assert overrides.resume_at(policy, early, BERLIN) == datetime(2026, 10, 15, 4, 0, tzinfo=BERLIN)
    # DST end (2026-10-25 in Europe): the local wall time still reads 04:00.
    before_dst_end = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    resume = overrides.resume_at(policy, before_dst_end, BERLIN)
    assert resume.astimezone(BERLIN).hour == 4
    assert resume.astimezone(BERLIN).day == 25
    assert resume.tzinfo is UTC


def test_resume_at_hours() -> None:
    policy = OverridePolicy(kind="hours", hours=6)
    assert overrides.resume_at(policy, T0, BERLIN) == T0 + timedelta(hours=6)


@settings(max_examples=100, deadline=None)
@given(
    st.datetimes(
        min_value=datetime(2026, 1, 1),  # noqa: DTZ001 - hypothesis adds the zone
        max_value=datetime(2027, 1, 1),  # noqa: DTZ001
        timezones=st.just(UTC),
    ),
    st.integers(min_value=0, max_value=23),
)
def test_next_day_resume_is_strictly_later_and_within_48_hours(now: datetime, hour: int) -> None:
    policy = OverridePolicy(resume_time=datetime(2000, 1, 1, hour).time())  # noqa: DTZ001
    resume = overrides.resume_at(policy, now, BERLIN)
    assert now < resume <= now + timedelta(hours=48)
    local = resume.astimezone(BERLIN)
    # On the spring-forward day the wall time in the DST gap does not exist (02:xx in
    # Berlin); it resolves to the same instant one hour later on the clock.
    wall = datetime(local.year, local.month, local.day, hour, tzinfo=BERLIN)
    in_gap = wall.astimezone(UTC).astimezone(BERLIN).hour != hour
    assert local.hour == (hour + 1 if in_gap else hour)


def test_hold_resume_expire() -> None:
    rt = runtime()
    held = overrides.hold_until(rt, T0 + timedelta(minutes=10))
    assert held.is_held(T0)
    assert not held.is_held(T0 + timedelta(minutes=10))
    assert overrides.expire(held, T0 + timedelta(minutes=10)) == overrides.resume(held)
    assert overrides.expire(held, T0) is held
    forever = overrides.hold_until(rt, None)
    assert forever.is_held(T0 + timedelta(days=365))
    assert overrides.expire(forever, T0 + timedelta(days=365)) is forever
    assert overrides.resume(rt) is rt
    assert not overrides.resume(forever).override


def test_apply_verdict_without_override_is_identity() -> None:
    rt = runtime()
    assert overrides.apply_verdict(rt, overrides.NO_OVERRIDE, T0, OverridePolicy(), UTC) is rt


@pytest.mark.parametrize(
    "observed", [StepAction(step=2), SwitchAction(on=True), ModeAction(mode="x")]
)
def test_generic_override_reason(observed: object) -> None:
    rt = controlled(last_written=StepAction(step=0))
    v = overrides.judge(observed, rt, T0, settle_s=1)  # type: ignore[arg-type]
    assert v.override
    assert v.reason is not None
    assert v.reason.key in ("safety.override_detected", "safety.override_off")
