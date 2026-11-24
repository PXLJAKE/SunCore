# SPDX-License-Identifier: Apache-2.0
"""Runtime state of flexible loads: EV modes and targets, program jobs (ADR-0017 decisions 3, 7).

This is *runtime* state, not configuration: it changes many times a day and is stored in the
``flex_state`` table of ``suncore.db`` (plan §0 decision 16), not in the config history. Pure
data and transition rules; imports nothing from sibling modules but the validation helper.

Program jobs follow ``armed -> scheduled -> running -> done``; ``armed`` and ``scheduled`` may
be ``cancelled``; ``scheduled`` may go back to ``armed`` (re-plan); a manual start is detected
from ``armed`` or ``scheduled`` straight to ``running``. ``done`` and ``cancelled`` are final.
**A running job is never cancelled by SunCore** (P2): the only way out of ``running`` is
``done``.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from enum import StrEnum, unique
from typing import Annotated, Final, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from suncore.core.validation import invalid

FLEX_ID_PATTERN: Final = r"^[a-z][a-z0-9_]{0,47}$"
"""Same shape as a device id (``core.devices.DEVICE_ID_PATTERN``)."""
JOB_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- EV -----------------------------------------------------------------------------------


@unique
class EvMode(StrEnum):
    """User-chosen EV charging mode (BRIEF §4.5)."""

    OFF = "off"
    NOW = "now"
    """Charge at the maximum current now; may import (user-demanded floor, ADR-0017 d. 4)."""
    MIN_PV = "min_pv"
    """At least the minimum current, the rest from surplus; the floor may import."""
    PV = "pv"
    """Surplus only (default)."""
    TARGET = "target"
    """Reach a SoC or an energy by a time; grid power only where the plan allows it."""


EV_MODE_DEFAULT: Final = EvMode.PV

EvccMode = Literal["off", "smart", "now"]

_EVCC_MODE: Final[dict[EvMode, EvccMode]] = {
    EvMode.OFF: "off",
    EvMode.NOW: "now",
    EvMode.MIN_PV: "smart",  # UNVERIFIED: evcc has no min+pv mode any more (ADR-0017 d. 3)
    EvMode.PV: "smart",
    EvMode.TARGET: "smart",
}


def evcc_mode(mode: EvMode) -> EvccMode:
    """The evcc loadpoint mode an EV mode maps to (ADR-0017 decision 3)."""
    return _EVCC_MODE[mode]


class EvTarget(_Frozen):
    """Be at ``soc_pct`` (vehicle SoC known) or have charged ``energy_wh`` by ``by``."""

    soc_pct: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    energy_wh: float | None = Field(default=None, gt=0, le=500_000, allow_inf_nan=False)
    by: AwareDatetime

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.soc_pct is None) == (self.energy_wh is None):
            raise invalid("ev_target_one", "ev target: set exactly one of soc_pct and energy_wh")
        return self


class EvState(_Frozen):
    """Runtime EV state of one charger device."""

    device_id: Annotated[str, StringConstraints(pattern=FLEX_ID_PATTERN)]
    mode: EvMode = EV_MODE_DEFAULT
    target: EvTarget | None = None
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _target(self) -> Self:
        if self.mode is EvMode.TARGET and self.target is None:
            raise invalid("ev_mode_target_missing", "ev state: mode `target` needs a target")
        return self


def ev_energy_need_wh(
    target_soc_pct: float, soc_pct: float, capacity_wh: float, efficiency: float
) -> float:
    """Energy drawn from the grid side to raise the SoC to the target (0 if already there).

    ``efficiency`` is the charging efficiency (0, 1]: the car stores ``efficiency`` of what the
    charger draws.
    """
    if not 0 < efficiency <= 1:
        msg = "efficiency must be in (0, 1]"
        raise ValueError(msg)
    missing = max(0.0, target_soc_pct - soc_pct) / 100.0 * capacity_wh
    return missing / efficiency


# --- programs -----------------------------------------------------------------------------


@unique
class JobState(StrEnum):
    ARMED = "armed"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"


FINAL_JOB_STATES: Final = frozenset({JobState.DONE, JobState.CANCELLED})

JOB_TRANSITIONS: Final[dict[JobState, frozenset[JobState]]] = {
    JobState.ARMED: frozenset({JobState.SCHEDULED, JobState.RUNNING, JobState.CANCELLED}),
    JobState.SCHEDULED: frozenset(
        {JobState.ARMED, JobState.RUNNING, JobState.CANCELLED, JobState.SCHEDULED}
    ),
    JobState.RUNNING: frozenset({JobState.DONE}),
    JobState.DONE: frozenset(),
    JobState.CANCELLED: frozenset(),
}
"""Allowed job transitions; ``running`` only ends in ``done`` (never cancelled, P2)."""

ArmedBy = Literal["user", "mqtt", "notification"]


class ProgramJob(_Frozen):
    """One armed run of a shiftable program ("finished by ``deadline``")."""

    id: Annotated[str, StringConstraints(pattern=JOB_ID_PATTERN)]
    device_id: Annotated[str, StringConstraints(pattern=FLEX_ID_PATTERN)]
    state: JobState = JobState.ARMED
    deadline: AwareDatetime
    armed_at: AwareDatetime
    armed_by: ArmedBy = "user"
    planned_start: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.deadline <= self.armed_at:
            raise invalid("job_deadline_past", "program job: the deadline must be after arming")
        if self.state is JobState.SCHEDULED and self.planned_start is None:
            raise invalid("job_scheduled_start", "program job: `scheduled` needs planned_start")
        if self.state in (JobState.RUNNING, JobState.DONE) and self.started_at is None:
            raise invalid("job_started_at", "program job: running/done needs started_at")
        if self.state is JobState.DONE and self.finished_at is None:
            raise invalid("job_finished_at", "program job: done needs finished_at")
        return self

    @property
    def final(self) -> bool:
        return self.state in FINAL_JOB_STATES

    @property
    def running(self) -> bool:
        return self.state is JobState.RUNNING


class JobTransitionError(ValueError):
    """A job transition that :data:`JOB_TRANSITIONS` does not allow."""


def transition(
    job: ProgramJob, to: JobState, at: datetime, *, planned_start: datetime | None = None
) -> ProgramJob:
    """The job after moving to ``to`` at ``at`` (pure; raises :class:`JobTransitionError`)."""
    if to not in JOB_TRANSITIONS[job.state]:
        msg = f"program job {job.id}: {job.state.value} -> {to.value} is not allowed"
        raise JobTransitionError(msg)
    update: dict[str, object] = {"state": to}
    if to is JobState.SCHEDULED:
        if planned_start is None:
            msg = f"program job {job.id}: scheduling needs planned_start"
            raise JobTransitionError(msg)
        update["planned_start"] = planned_start
    elif to is JobState.ARMED:
        update["planned_start"] = None
    elif to is JobState.RUNNING:
        update["started_at"] = at
    elif to is JobState.DONE:
        update["finished_at"] = at
    return ProgramJob.model_validate({**job.model_dump(), **update})


DEFAULT_FINISH_TIME: Final = time(18, 0)
DEFAULT_FINISH_FALLBACK: Final = timedelta(hours=12)


def proposed_deadline(now_local: datetime, runtime: timedelta) -> datetime:
    """The deadline the UI proposes (plan §0 decision 11; never applied automatically).

    The next 18:00 (local, ``now_local`` must be aware) that still leaves the run time, else
    now + 12 h (absolute hours). The candidate is 18:00 local wall time also on a DST day; the
    run time is compared in absolute time.
    """
    if now_local.tzinfo is None:
        msg = "now_local must be timezone-aware"
        raise ValueError(msg)
    for days in (0, 1):
        day = (now_local + timedelta(days=days)).date()
        candidate = datetime.combine(day, DEFAULT_FINISH_TIME, tzinfo=now_local.tzinfo)
        if candidate.astimezone(UTC) - now_local.astimezone(UTC) >= runtime:
            return candidate
    return (now_local.astimezone(UTC) + DEFAULT_FINISH_FALLBACK).astimezone(now_local.tzinfo)


__all__ = [
    "DEFAULT_FINISH_FALLBACK",
    "DEFAULT_FINISH_TIME",
    "EV_MODE_DEFAULT",
    "FINAL_JOB_STATES",
    "JOB_TRANSITIONS",
    "ArmedBy",
    "EvMode",
    "EvState",
    "EvTarget",
    "EvccMode",
    "JobState",
    "JobTransitionError",
    "ProgramJob",
    "ev_energy_need_wh",
    "evcc_mode",
    "proposed_deadline",
    "transition",
]
