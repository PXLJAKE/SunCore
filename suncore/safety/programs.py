# SPDX-License-Identifier: Apache-2.0
"""Shiftable-program invariants P1-P6 (plan WP12, ADR-0017 decision 7; PR 6-L).

Pure functions over plain values; :mod:`suncore.safety.invariants` calls them for every action
on a job-driven program device (``DeviceConfig.program``) with the :class:`ProgramSafety` the
gate builds per intent. Imports ``core`` only.

* **P1** - a switch-on only for an armed (``armed``/``scheduled``) job of that device, and only
  with a fresh measured power reading of the appliance (stale or unknown inputs: no start):
  :func:`check_program`.
* **P2** - never switch a running program off: not while its job is ``running``, not while the
  appliance draws ``idle_w`` or more, not while its power is unknown and not within run time +
  :data:`~suncore.core.flex.PROGRAM_OFF_MARGIN_S` after SunCore switched it on
  (:func:`program_running`). A remote-start switch is never switched off at all. The G2 fuse
  waiver never applies to a running program (``safety.invariants._fuse_shed``, review 6-F H3)
  and the controller never sheds or backs it off (it has no surplus levels).
* **P3** - the plug's takeover baseline is **on**; a release (and the crash recovery) switches
  it on and never off (the gate's takeover, ``devices.drivers.program``).
* **P4** - arming switches the plug off only while an armed job exists, within
  :data:`~suncore.core.flex.PROGRAM_ARM_WINDOW_S` after it was armed, and while the appliance
  draws less than ``idle_w`` (P2 covers the rest). A plug that is on later (switched on by the
  user, a release or the crash recovery) stays on for this job.
* **P5** - G1/G3-G6 hold for the start (``check_grid_situation``, the import limit); a start
  that the deadline binds (a fresh plan band's ``start``, or the latest start that still meets
  the deadline) may import up to the program's peak power (ADR-0017 decision 4):
  :func:`program_allowance_w`, :func:`check_program_grid`.
* **P6** - effective mode at most assisted while the behaviour is not confirmed: the gate's
  ``modes_of`` (``core.devices.behaviour_mode_capped``).

A notify-only program (``start_method: notify``) is never written: every action but a release
is rejected (``safety.program_notify_only``). A ``ReleaseAction`` is never judged here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from suncore.core.decisions import Action, Reason, ReleaseAction, SwitchAction
from suncore.core.devices import DeviceConfig, program_peak_w
from suncore.core.flex import PROGRAM_ARM_WINDOW_S, PROGRAM_OFF_MARGIN_S, JobState, ProgramJob

POWER_TOLERANCE_W: Final = 1.0
ARMED_STATES: Final = frozenset({JobState.ARMED, JobState.SCHEDULED})

REASON_PROGRAM_NO_JOB: Final = Reason(key="safety.program_no_job")
REASON_PROGRAM_RUNNING: Final = Reason(key="safety.program_running")
REASON_PROGRAM_POWER_UNKNOWN: Final = Reason(key="safety.program_power_unknown")
REASON_PROGRAM_SWITCH_ONLY: Final = Reason(key="safety.program_switch_only")
REASON_PROGRAM_NOTIFY_ONLY: Final = Reason(key="safety.program_notify_only")
REASON_PROGRAM_REMOTE_OFF: Final = Reason(key="safety.program_remote_off")
REASON_PROGRAM_ARM_WINDOW: Final = Reason(key="safety.program_arm_window")
REASON_SURPLUS_UNKNOWN: Final = Reason(key="safety.surplus_unknown")
REASON_GRID_UNKNOWN: Final = Reason(key="safety.grid_unknown")


@dataclass(frozen=True, slots=True)
class ProgramSafety:
    """What the program invariants look at for one action (the gate builds it).

    The defaults are the most conservative situation: no job, power unknown, nothing bound.
    """

    job: ProgramJob | None = None
    """The device's open job (``core.flex.open_job``), ``None`` = none."""
    power_w: float | None = None
    """The appliance's power from a fresh measured reading, ``None`` = unknown or stale."""
    start_bound: bool = False
    """The deadline binds a start now: a fresh plan band's ``start`` or the latest start passed."""
    last_on: datetime | None = None
    """When SunCore last switched the device on (the runtime's ``last_on_ts``)."""


NO_PROGRAM_SAFETY: Final = ProgramSafety()


def is_armed(job: ProgramJob | None) -> bool:
    return job is not None and job.state in ARMED_STATES


def program_running(device: DeviceConfig, ps: ProgramSafety, now: datetime) -> bool | None:
    """P2: whether the appliance may be running (``None`` = unknown, treated as running).

    Running = its job is ``running``, or it draws ``idle_w`` or more, or SunCore switched it on
    less than run time + margin ago. Unknown = none of these and no fresh power reading.
    """
    spec = device.program
    if spec is None:
        return False
    if ps.job is not None and ps.job.state is JobState.RUNNING:
        return True
    if ps.last_on is not None:
        window = timedelta(minutes=spec.runtime_min, seconds=PROGRAM_OFF_MARGIN_S)
        if now - ps.last_on < window:
            return True
    if ps.power_w is None:
        return None
    return ps.power_w >= spec.idle_w


def check_program(  # noqa: PLR0911 - one return per rule
    action: Action, device: DeviceConfig, ps: ProgramSafety, now: datetime
) -> Reason | None:
    """P1, P2, P4 and the notify/remote rules for one action on a program device."""
    spec = device.program
    if spec is None or isinstance(action, ReleaseAction):
        return None
    if spec.start_method == "notify":
        return REASON_PROGRAM_NOTIFY_ONLY
    if not isinstance(action, SwitchAction):
        return REASON_PROGRAM_SWITCH_ONLY
    if action.on:
        if not is_armed(ps.job):
            return REASON_PROGRAM_NO_JOB  # P1
        if ps.power_w is None:
            return REASON_PROGRAM_POWER_UNKNOWN  # P1: stale or unknown inputs never start
        return None
    if spec.start_method == "remote":
        return REASON_PROGRAM_REMOTE_OFF
    running = program_running(device, ps, now)
    if running is True:
        return REASON_PROGRAM_RUNNING  # P2
    if running is None:
        return REASON_PROGRAM_POWER_UNKNOWN  # P2: unknown counts as running
    job = ps.job
    if job is None or not is_armed(job):
        return REASON_PROGRAM_NO_JOB  # P4
    if now - job.armed_at > timedelta(seconds=PROGRAM_ARM_WINDOW_S):
        return REASON_PROGRAM_ARM_WINDOW  # P4: only right after arming
    return None


def program_allowance_w(device: DeviceConfig, ps: ProgramSafety) -> float:
    """P5: grid power a deadline-bound start may import (its peak power), else 0."""
    if device.program is None or not ps.start_bound or not is_armed(ps.job):
        return 0.0
    return program_peak_w(device) or 0.0


def check_program_grid(
    *,
    after_w: float,
    current_lower_w: float,
    surplus_lower_w: float | None,
    claimed_w: float,
    grid_w: float | None,
    import_limit_w: float | None,
    allowance_w: float,
) -> Reason | None:
    """P5 + G1 for a start with an allowance: the import it causes stays within the allowance
    and the import limit always holds (with a known grid reading). Stale site inputs (unknown
    surplus) never start a program."""
    if surplus_lower_w is None:
        return REASON_SURPLUS_UNKNOWN
    available = current_lower_w + surplus_lower_w - max(0.0, claimed_w)
    ceiling = allowance_w + max(0.0, available)
    if after_w > ceiling + POWER_TOLERANCE_W:
        return Reason(
            key="safety.program_import_exception",
            args={"need_w": round(after_w), "allowed_w": round(ceiling)},
        )
    if import_limit_w is not None:
        if grid_w is None:
            return REASON_GRID_UNKNOWN
        delta = max(0.0, after_w - current_lower_w)
        if grid_w + max(0.0, claimed_w) + delta > import_limit_w:
            return Reason(
                key="safety.grid_import_limit",
                args={"limit_w": round(import_limit_w), "grid_w": round(grid_w)},
            )
    return None


__all__ = [
    "ARMED_STATES",
    "NO_PROGRAM_SAFETY",
    "REASON_PROGRAM_ARM_WINDOW",
    "REASON_PROGRAM_NOTIFY_ONLY",
    "REASON_PROGRAM_NO_JOB",
    "REASON_PROGRAM_POWER_UNKNOWN",
    "REASON_PROGRAM_REMOTE_OFF",
    "REASON_PROGRAM_RUNNING",
    "REASON_PROGRAM_SWITCH_ONLY",
    "ProgramSafety",
    "check_program",
    "check_program_grid",
    "is_armed",
    "program_allowance_w",
    "program_running",
]
