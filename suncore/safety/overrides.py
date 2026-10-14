# SPDX-License-Identifier: Apache-2.0
"""Manual override detection and the hold policy (plan WP3, risk R3).

The research (``docs/research/ha-websocket-api.md``, "Context propagation and override
detection") fixes the rule: a state whose context id equals one of our recent write
contexts is ours (positive proof); a non-matching context proves nothing, because
push integrations such as ``lg_thinq`` echo our own writes with fresh id-only contexts.
Detection is therefore **value-based**: after the settle time, an observed state that
differs from what SunCore last wrote is a manual change. It is respected until the
device's ``override_policy`` says otherwise (next day at the resume time, or N hours);
a unit switched off by remote stays off, a changed temperature becomes the new base
set-point. "Boost"/"pause until"/"resume now" are explicit user overrides through the
gate's API. All functions are pure; the gate applies the verdicts to the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Final

from suncore.core.decisions import Action, Reason, SetpointAction, SwitchAction
from suncore.core.devices import OverridePolicy
from suncore.safety.invariants import action_matches
from suncore.safety.runtime import DeviceRuntime


@dataclass(frozen=True, slots=True)
class OverrideVerdict:
    """Result of judging one observation."""

    override: bool
    reason: Reason | None = None
    new_base_setpoint_c: float | None = None
    """The user's new set-point (a changed temperature becomes the base)."""
    late_confirm: bool = False
    """The observation confirms a write whose read-back had failed (slow cloud echo)."""


NO_OVERRIDE = OverrideVerdict(override=False)
LATE_CONFIRM = OverrideVerdict(override=False, late_confirm=True)
PENDING_MIN_WINDOW_S: Final = 120.0
"""A pending write is confirmable for ``max(settle_s * 6, 120 s)`` after it was sent."""


def pending_window_s(settle_s: float) -> float:
    return max(settle_s * 6.0, PENDING_MIN_WINDOW_S)


def pending_expired(runtime: DeviceRuntime, now: datetime, settle_s: float) -> bool:
    """Whether the pending write is too old to be confirmed by a late echo."""
    if runtime.pending_action is None or runtime.pending_ts is None:
        return False
    return now - runtime.pending_ts > timedelta(seconds=pending_window_s(settle_s))


def resume_at(policy: OverridePolicy, now: datetime, tz: tzinfo) -> datetime:
    """When SunCore takes over again after an override detected at ``now``.

    ``next_day``: the policy's local resume time on the next calendar day in ``tz``
    (so an override at 03:00 holds until 04:00 of the *following* day, never the
    same morning); ``hours``: ``now + hours``.
    """
    if policy.kind == "hours":
        assert policy.hours is not None
        return now + timedelta(hours=policy.hours)
    local = now.astimezone(tz)
    next_day = (local + timedelta(days=1)).replace(
        hour=policy.resume_time.hour,
        minute=policy.resume_time.minute,
        second=0,
        microsecond=0,
    )
    return next_day.astimezone(now.tzinfo)


def is_ours(context_id: str | None, runtime: DeviceRuntime) -> bool:
    """Positive evidence only: the state carries the context of our last write."""
    return (
        context_id is not None
        and runtime.last_write_context_id is not None
        and context_id == runtime.last_write_context_id
    )


def judge(  # noqa: PLR0911 - one return per rule
    observed: Action,
    runtime: DeviceRuntime,
    now: datetime,
    *,
    settle_s: float,
    context_id: str | None = None,
) -> OverrideVerdict:
    """Judge an observed device state against what SunCore last wrote.

    * not under control: never an override;
    * our context id: ours;
    * a value equal to a pending write (read-back had failed) inside its window: a late
      confirmation (``late_confirm``), never an override - the LG cloud echoes our own
      writes later than the settle time (research/device-integrations.md);
    * within the settle time after the write: pending, no verdict;
    * nothing written yet, or a value equal to the last written action: no override;
    * otherwise the value differs from what SunCore wrote: a manual override.
    """
    if not runtime.under_control:
        return NO_OVERRIDE
    if is_ours(context_id, runtime):
        return NO_OVERRIDE
    if (
        runtime.pending_action is not None
        and not pending_expired(runtime, now, settle_s)
        and action_matches(runtime.pending_action, observed)
    ):
        return LATE_CONFIRM
    if runtime.last_write_ts is not None and now - runtime.last_write_ts < timedelta(
        seconds=settle_s
    ):
        return NO_OVERRIDE
    if runtime.last_written is None or action_matches(runtime.last_written, observed):
        return NO_OVERRIDE
    match observed:
        case SetpointAction(setpoint_c=setpoint):
            return OverrideVerdict(
                override=True,
                reason=Reason(key="safety.override_setpoint", args={"setpoint_c": setpoint}),
                new_base_setpoint_c=setpoint,
            )
        case SwitchAction(on=False):
            return OverrideVerdict(override=True, reason=Reason(key="safety.override_off"))
        case _:
            return OverrideVerdict(override=True, reason=Reason(key="safety.override_detected"))


def confirm_pending(runtime: DeviceRuntime) -> DeviceRuntime:
    """The pending write is confirmed: it becomes the last written action."""
    pending = runtime.pending_action
    if pending is None:
        return runtime
    update: dict[str, object] = {
        "last_written": pending,
        "last_write_ts": runtime.pending_ts or runtime.last_write_ts,
        "pending_action": None,
        "pending_ts": None,
    }
    if isinstance(pending, SetpointAction):
        update["base_setpoint_c"] = pending.base_c
    return runtime.model_copy(update=update)


def clear_pending(runtime: DeviceRuntime) -> DeviceRuntime:
    if runtime.pending_action is None and runtime.pending_ts is None:
        return runtime
    return runtime.model_copy(update={"pending_action": None, "pending_ts": None})


def apply_verdict(
    runtime: DeviceRuntime,
    verdict: OverrideVerdict,
    now: datetime,
    policy: OverridePolicy,
    tz: tzinfo,
) -> DeviceRuntime:
    """Return the runtime with the hold (and the new base set-point) applied."""
    if not verdict.override:
        return runtime
    update: dict[str, object] = {
        "override": True,
        "override_until": resume_at(policy, now, tz),
    }
    if verdict.new_base_setpoint_c is not None:
        update["base_setpoint_c"] = verdict.new_base_setpoint_c
    return runtime.model_copy(update=update)


def hold_until(runtime: DeviceRuntime, until: datetime | None) -> DeviceRuntime:
    """Explicit user override ("boost N min" / "pause until"): hold until ``until``."""
    return runtime.model_copy(update={"override": True, "override_until": until})


def resume(runtime: DeviceRuntime) -> DeviceRuntime:
    """ "Resume now": clear the hold."""
    if not runtime.override and runtime.override_until is None:
        return runtime
    return runtime.model_copy(update={"override": False, "override_until": None})


def expire(runtime: DeviceRuntime, now: datetime) -> DeviceRuntime:
    """Clear a hold whose time has passed."""
    if runtime.override and runtime.override_until is not None and now >= runtime.override_until:
        return resume(runtime)
    return runtime


__all__ = [
    "LATE_CONFIRM",
    "NO_OVERRIDE",
    "PENDING_MIN_WINDOW_S",
    "OverrideVerdict",
    "apply_verdict",
    "clear_pending",
    "confirm_pending",
    "expire",
    "hold_until",
    "is_ours",
    "judge",
    "pending_expired",
    "pending_window_s",
    "resume",
    "resume_at",
]
