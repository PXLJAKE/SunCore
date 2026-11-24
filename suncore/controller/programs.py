# SPDX-License-Identifier: Apache-2.0
"""Shiftable programs: the job state machine of the controller (plan WP12, PR 6-L).

Deterministic and pure (time injected, no I/O): :meth:`ProgramController.step` takes the open
jobs (runtime ``flex_state``), the devices' observed state and power, the plan and the site's
trust in its inputs, and returns the intents for the gate, the changed jobs (the composition
root persists them) and the events the notification layer announces. Decisions on its intents
come back through :meth:`ProgramController.report`.

Job states (``core.flex``): ``armed -> scheduled -> running -> done``; ``cancelled`` when the
deadline passed without a start (``missed``). Per device and tick:

* **Scheduled** from the first interval of a *fresh* plan whose band has ``start`` (6-H plans
  one job per device); a fresh plan without a start puts it back to ``armed``.
* **Start due** when the fresh band covering now has ``start`` (the planner's window) or the
  latest start that still meets the deadline has come (the deadline guarantee; also without a
  plan). Only with trusted inputs: a fresh measured power of the appliance, an ``OK`` grid
  reading, not blocked by the gate, not in island (fail-safe: no start).

  - ``plug``/``remote``: one ``SwitchAction(on)``; an accepted decision makes the job
    ``running``. A rejected or shadowed start is retried after :data:`RETRY_S`
    (:data:`RETRY_QUIET_S` after a shadow or proposal decision).
  - ``notify``: one "start now" event per job; the user starts the appliance.
* **Arming** (plug only): right after arming (:data:`~suncore.core.flex.PROGRAM_ARM_WINDOW_S`),
  while the start is not due, the plug is on and the appliance draws less than ``idle_w``, the
  plug is switched off so the appliance resumes when it is switched on again (P4; the gate
  judges it again). A plug that is on later (the user, a release, the crash recovery) stays on:
  the appliance then starts early rather than never.
* **Running** is detected by power as well: the appliance draws ``idle_w`` or more for
  :data:`MANUAL_START_S` (a manual start, also the notify case).
* **Done** once the run time has passed and the power stayed below ``idle_w`` for
  :data:`~suncore.core.flex.PROGRAM_DONE_IDLE_S` (unknown power never ends a run).
* **Missed**: the deadline passed without a start: ``cancelled``.

A running program is never switched off and never shed: the controller emits no switch-off for
it, and it has no surplus levels, so neither the deficit back-off nor the grid shedding of the
allocator touch it. After ``done``, ``missed`` or a cancellation by the user the device is
released (the plug goes back to its baseline, on - P3). The release is an intent in flight like
a start: it is sent again after :data:`RETRY_S` until the gate accepted it, held it or answered
that the device is no longer under control (review 6-L H1). An intent without a decision (a
transport error, :meth:`ProgramController.reset_pending`, or :data:`IN_FLIGHT_TIMEOUT_S`) never
blocks the release (review 6-L M1).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Final, Literal

from suncore.controller.reasons import (
    PROGRAM_ARM_OFF,
    PROGRAM_ARMED,
    PROGRAM_BLOCKED,
    PROGRAM_DEADLINE_START,
    PROGRAM_INPUTS_UNKNOWN,
    PROGRAM_ISLAND,
    PROGRAM_NO_JOB,
    PROGRAM_NO_POWER_SENSOR,
    PROGRAM_RELEASE,
    PROGRAM_RUNNING,
    PROGRAM_SCHEDULED,
    PROGRAM_START,
    PROGRAM_START_NOW,
)
from suncore.core.control import (
    DeviceState,
    Plan,
    PowerEstimate,
    battery_band_now,
    device_band_now,
)
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    Origin,
    Reason,
    ReleaseAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig
from suncore.core.flex import (
    PROGRAM_ARM_WINDOW_S,
    PROGRAM_DONE_IDLE_S,
    JobState,
    ProgramJob,
    latest_start,
    open_job,
    transition,
)
from suncore.core.signals import Quality

RETRY_S: Final = 60.0
"""A start or an arming switch-off the gate rejected is tried again after this long."""
RETRY_QUIET_S: Final = 900.0
"""... and after this long when it was shadowed or proposed (no decision-log flood)."""
MANUAL_START_S: Final = 30.0
"""The appliance drew ``idle_w`` or more this long: it runs (a manual or notified start)."""
INTENT_PREFIX: Final = "program:"
REASON_NOT_UNDER_CONTROL_KEY: Final = "safety.not_under_control"
"""The gate's answer to a release of a device it does not control (``safety.gate``; the
controller depends on ``core`` only, so the key is repeated here)."""
IN_FLIGHT_TIMEOUT_S: Final = 300.0
"""An intent without a decision after this long (a transport error) no longer blocks."""

EventKind = Literal["start_now", "started", "done", "missed"]


@dataclass(frozen=True, slots=True)
class ProgramEvent:
    """Something to tell the user (``suncore.notify``, category ``program``)."""

    kind: EventKind
    device_id: str
    job: ProgramJob
    ts: datetime
    power_known: bool = True
    """``missed``: the appliance's power was known throughout the job; ``False`` = it may have
    run unseen, so the job's outcome is unknown rather than missed (review 6-L L4)."""


@dataclass(frozen=True, slots=True)
class ProgramInputs:
    """What one tick of the program controller looks at."""

    jobs: Mapping[str, Sequence[ProgramJob]]
    """Open jobs per device (``flex_state``)."""
    states: Mapping[str, DeviceState]
    estimates: Mapping[str, PowerEstimate]
    blocked: Mapping[str, Reason]
    plan: Plan
    site_ok: bool
    """The site inputs are trusted (an ``OK`` grid reading)."""
    island: bool = False


@dataclass(frozen=True, slots=True)
class ProgramOutcome:
    intents: tuple[Intent, ...] = ()
    jobs: tuple[ProgramJob, ...] = ()
    """Jobs that changed this tick (to persist)."""
    events: tuple[ProgramEvent, ...] = ()
    reasons: Mapping[str, tuple[Reason, ...]] = field(default_factory=dict)
    """Why each program device is where it is (the "Why?" view)."""


@dataclass(slots=True)
class _Memory:
    job_id: str | None = None
    above_since: datetime | None = None
    idle_since: datetime | None = None
    in_flight: str | None = None
    in_flight_kind: str | None = None
    in_flight_ts: datetime | None = None
    retry_at: datetime | None = None
    started_at: datetime | None = None
    """An accepted start decision (the job becomes ``running`` at the next tick)."""
    notified: bool = False
    pending_release: str | None = None
    """The job id whose end still needs a release."""
    wrote_job: str | None = None
    """The job for which the gate accepted a write (only then is a release due)."""
    release_retry_at: datetime | None = None
    """A release that failed (or got no decision) is sent again from this time."""
    power_gap: bool = False
    """The appliance's power was unknown at some tick of the current job."""

    def reset(self, job_id: str | None) -> None:
        self.job_id = job_id
        self.above_since = None
        self.idle_since = None
        self.retry_at = None
        self.started_at = None
        self.notified = False
        self.power_gap = False


class ProgramController:
    """Job state machine per program device (see the module docstring)."""

    def __init__(self, devices: Iterable[DeviceConfig] = (), *, tz: tzinfo = UTC) -> None:
        self._tz = tz
        self._devices: dict[str, DeviceConfig] = {}
        self._memory: dict[str, _Memory] = {}
        self._seq = 0
        self.set_devices(devices)

    def set_devices(self, devices: Iterable[DeviceConfig]) -> None:
        self._devices = {d.id: d for d in devices if d.program is not None}
        self._memory = {k: v for k, v in self._memory.items() if k in self._devices}

    @property
    def device_ids(self) -> frozenset[str]:
        return frozenset(self._devices)

    # --- the tick --------------------------------------------------------------------------

    def step(self, now: datetime, inputs: ProgramInputs) -> ProgramOutcome:
        intents: list[Intent] = []
        changed: list[ProgramJob] = []
        events: list[ProgramEvent] = []
        reasons: dict[str, tuple[Reason, ...]] = {}
        for device_id, device in self._devices.items():
            memory = self._memory.setdefault(device_id, _Memory())
            job = open_job(list(inputs.jobs.get(device_id, ())))
            reasons[device_id] = self._device(
                now, device, memory, job, inputs, intents, changed, events
            )
        return ProgramOutcome(tuple(intents), tuple(changed), tuple(events), reasons)

    def _device(  # noqa: PLR0917 - one tick of one device
        self,
        now: datetime,
        device: DeviceConfig,
        memory: _Memory,
        job: ProgramJob | None,
        inputs: ProgramInputs,
        intents: list[Intent],
        changed: list[ProgramJob],
        events: list[ProgramEvent],
    ) -> tuple[Reason, ...]:
        spec = device.program
        assert spec is not None
        self._expire_in_flight(now, memory)
        if job is None or job.id != memory.job_id:
            if memory.job_id is not None and memory.job_id == memory.wrote_job:
                # The previous job ended (done, missed, cancelled by the user): release it.
                memory.pending_release = memory.job_id
            memory.reset(None if job is None else job.id)
        if (
            memory.pending_release is not None
            and memory.in_flight is None
            and device.id not in inputs.blocked
            and (memory.release_retry_at is None or now >= memory.release_retry_at)
        ):
            intent = self._intent(now, device.id, ReleaseAction(), PROGRAM_RELEASE)
            memory.in_flight, memory.in_flight_kind = intent.id, "release"
            memory.in_flight_ts = now
            intents.append(intent)
        if job is None:
            return (Reason(key=PROGRAM_NO_JOB),)
        power = _power(device, inputs.estimates.get(device.id))
        self._track_power(now, memory, power, spec.idle_w)
        if power is None:
            memory.power_gap = True
        if job.state is JobState.RUNNING:
            return self._running(now, device, memory, job, power, changed, events)
        # armed or scheduled
        if memory.started_at is not None or _sustained(memory.above_since, now, MANUAL_START_S):
            started = memory.started_at or memory.above_since or now
            running = transition(job, JobState.RUNNING, started)
            changed.append(running)
            events.append(ProgramEvent("started", device.id, running, now))
            memory.started_at = None
            return (Reason(key=PROGRAM_RUNNING),)
        if now >= job.deadline:
            missed = transition(job, JobState.CANCELLED, now)
            changed.append(missed)
            events.append(
                ProgramEvent("missed", device.id, missed, now, power_known=not memory.power_gap)
            )
            return (Reason(key=PROGRAM_NO_JOB),)
        job = self._schedule(now, device, job, inputs.plan, changed)
        return self._waiting(now, device, memory, job, power, inputs, intents, events, changed)

    def _waiting(  # noqa: PLR0911, PLR0917 - one return per verdict
        self,
        now: datetime,
        device: DeviceConfig,
        memory: _Memory,
        job: ProgramJob,
        power: float | None,
        inputs: ProgramInputs,
        intents: list[Intent],
        events: list[ProgramEvent],
        changed: list[ProgramJob],
    ) -> tuple[Reason, ...]:
        """An armed or scheduled job: notify, start, or power the appliance down (P4)."""
        spec = device.program
        assert spec is not None
        latest = latest_start(job, spec.runtime_min)
        band = device_band_now(inputs.plan, device.id, now)
        planned = band is not None and band.start
        due = planned or now >= latest
        waiting = self._waiting_reason(job, latest)
        if device.power_model.power_role is None:
            return (Reason(key=PROGRAM_NO_POWER_SENSOR), waiting)
        if power is None or not inputs.site_ok:
            return (Reason(key=PROGRAM_INPUTS_UNKNOWN), waiting)
        blocked = inputs.blocked.get(device.id)
        if blocked is not None:
            return (Reason(key=PROGRAM_BLOCKED), blocked, waiting)
        if inputs.island and due:
            return (Reason(key=PROGRAM_ISLAND), waiting)
        state = inputs.states.get(device.id)
        on = None if state is None or not state.known else state.on
        if spec.start_method == "notify":
            if job.notified_at is not None:
                memory.notified = True  # asked before a restart (review 6-L L2)
            if due and not memory.notified:
                memory.notified = True
                asked = job.model_copy(update={"notified_at": now})
                changed.append(asked)
                events.append(ProgramEvent("start_now", device.id, asked, now))
            return (Reason(key=PROGRAM_START_NOW),) if memory.notified else (waiting,)
        if memory.in_flight is not None or (memory.retry_at is not None and now < memory.retry_at):
            return (waiting,)
        if due and on is False:
            key = PROGRAM_START if planned else PROGRAM_DEADLINE_START
            intent = self._intent(now, device.id, SwitchAction(on=True), key, kind="start")
            memory.in_flight, memory.in_flight_kind = intent.id, "start"
            memory.in_flight_ts = now
            intents.append(intent)
            return intent.reasons
        if (
            spec.start_method == "plug"
            and on is True
            and power < spec.idle_w
            and not due
            and now - job.armed_at <= timedelta(seconds=PROGRAM_ARM_WINDOW_S)
        ):
            # P4: power the waiting appliance down so it resumes when the plug comes back.
            intent = self._intent(
                now, device.id, SwitchAction(on=False), PROGRAM_ARM_OFF, kind="arm_off"
            )
            memory.in_flight, memory.in_flight_kind = intent.id, "arm_off"
            memory.in_flight_ts = now
            intents.append(intent)
            return (*intent.reasons, waiting)
        return (waiting,)

    def _running(  # noqa: PLR0917
        self,
        now: datetime,
        device: DeviceConfig,
        memory: _Memory,
        job: ProgramJob,
        power: float | None,
        changed: list[ProgramJob],
        events: list[ProgramEvent],
    ) -> tuple[Reason, ...]:
        spec = device.program
        assert spec is not None
        started = job.started_at or now
        ran = now - started >= timedelta(minutes=spec.runtime_min)
        if ran and power is not None and _sustained(memory.idle_since, now, PROGRAM_DONE_IDLE_S):
            done = transition(job, JobState.DONE, now)
            changed.append(done)
            events.append(ProgramEvent("done", device.id, done, now))
            return (Reason(key=PROGRAM_NO_JOB),)
        return (Reason(key=PROGRAM_RUNNING),)

    def _schedule(
        self,
        now: datetime,
        device: DeviceConfig,
        job: ProgramJob,
        plan: Plan,
        changed: list[ProgramJob],
    ) -> ProgramJob:
        """Follow the fresh plan's start band (a stale or no plan keeps the job as it is)."""
        _, fresh = battery_band_now(plan, now)
        if not fresh:
            return job  # no plan, or a stale one: keep what the last fresh plan said
        start = next(
            (
                i.start
                for i in plan.intervals
                if i.end > now and (band := i.devices.get(device.id)) is not None and band.start
            ),
            None,
        )
        if start is None:
            if job.state is JobState.SCHEDULED:
                updated = transition(job, JobState.ARMED, now)
                changed.append(updated)
                return updated
            return job
        if job.state is JobState.SCHEDULED and job.planned_start == start:
            return job
        updated = transition(job, JobState.SCHEDULED, now, planned_start=start)
        changed.append(updated)
        return updated

    @staticmethod
    def _expire_in_flight(now: datetime, memory: _Memory) -> None:
        """M1: an intent without a decision after :data:`IN_FLIGHT_TIMEOUT_S` no longer blocks
        (a pending release is then sent again at once)."""
        if (
            memory.in_flight is not None
            and memory.in_flight_ts is not None
            and _sustained(memory.in_flight_ts, now, IN_FLIGHT_TIMEOUT_S)
        ):
            memory.in_flight = None
            memory.in_flight_kind = None

    def _waiting_reason(self, job: ProgramJob, latest: datetime) -> Reason:
        if job.state is JobState.SCHEDULED and job.planned_start is not None:
            return Reason(key=PROGRAM_SCHEDULED, args={"start": self._hhmm(job.planned_start)})
        return Reason(key=PROGRAM_ARMED, args={"latest": self._hhmm(latest)})

    def _hhmm(self, at: datetime) -> str:
        return at.astimezone(self._tz).strftime("%H:%M")

    @staticmethod
    def _track_power(now: datetime, memory: _Memory, power: float | None, idle_w: float) -> None:
        if power is None:
            memory.above_since = None
            memory.idle_since = None
            return
        if power >= idle_w:
            memory.idle_since = None
            if memory.above_since is None:
                memory.above_since = now
        else:
            memory.above_since = None
            if memory.idle_since is None:
                memory.idle_since = now

    def _intent(
        self,
        now: datetime,
        device_id: str,
        action: SwitchAction | ReleaseAction,
        key: str,
        *,
        kind: str = "release",
    ) -> Intent:
        self._seq += 1
        return Intent(
            id=f"{INTENT_PREFIX}{device_id}:{kind}:{self._seq}",
            ts=now,
            asset_id=device_id,
            action=action,
            reasons=(Reason(key=key),),
            origin=Origin.CONTROLLER,
        )

    # --- feedback --------------------------------------------------------------------------

    def reset_pending(self, intent: Intent) -> None:
        """The submission of one of this controller's intents raised (no decision will come):
        it no longer blocks; a start or a release is tried again after :data:`RETRY_S` (M1)."""
        memory = self._memory.get(intent.asset_id)
        if memory is None or memory.in_flight != intent.id:
            return
        kind = memory.in_flight_kind
        memory.in_flight = None
        memory.in_flight_kind = None
        again = intent.ts + timedelta(seconds=RETRY_S)
        if kind == "release":
            memory.release_retry_at = again
        else:
            memory.retry_at = again

    def report(self, decision: Decision) -> bool:
        """A decision on one of this controller's intents; ``True`` if it was ours."""
        intent = decision.intent
        if not intent.id.startswith(INTENT_PREFIX):
            return False
        memory = self._memory.get(intent.asset_id)
        if memory is None or memory.in_flight != intent.id:
            return True
        kind = memory.in_flight_kind
        memory.in_flight = None
        memory.in_flight_kind = None
        if kind == "release":
            self._released(memory, decision)
            return True
        if decision.kind is DecisionKind.ACCEPTED:
            memory.retry_at = None
            memory.wrote_job = memory.job_id
            if kind == "start":
                memory.started_at = decision.ts
            return True
        wait = (
            RETRY_QUIET_S
            if decision.kind in (DecisionKind.SHADOW, DecisionKind.PROPOSED, DecisionKind.HELD)
            else RETRY_S
        )
        memory.retry_at = decision.ts + timedelta(seconds=wait)
        return True

    @staticmethod
    def _released(memory: _Memory, decision: Decision) -> None:
        if not _release_done(decision):
            memory.release_retry_at = decision.ts + timedelta(seconds=RETRY_S)
            return
        if memory.wrote_job == memory.pending_release:
            memory.wrote_job = None
        memory.pending_release = None
        memory.release_retry_at = None


def _release_done(decision: Decision) -> bool:
    """H1: a release is settled once the gate restored (or staged) the baseline, or the device
    is not under SunCore's control any more; a failure or another rejection is retried."""
    if decision.kind in (DecisionKind.ACCEPTED, DecisionKind.HELD):
        return True
    return decision.kind is DecisionKind.REJECTED and any(
        r.key == REASON_NOT_UNDER_CONTROL_KEY for r in decision.reasons
    )


def _power(device: DeviceConfig, estimate: PowerEstimate | None) -> float | None:
    """A fresh measured power of the appliance (``None`` = unknown: no start, no done)."""
    if device.power_model.power_role is None or estimate is None:
        return None
    if estimate.quality is not Quality.OK:
        return None
    return estimate.value_w


def _sustained(since: datetime | None, now: datetime, seconds: float) -> bool:
    return since is not None and (now - since).total_seconds() >= seconds


__all__ = [
    "INTENT_PREFIX",
    "IN_FLIGHT_TIMEOUT_S",
    "MANUAL_START_S",
    "RETRY_QUIET_S",
    "RETRY_S",
    "ProgramController",
    "ProgramEvent",
    "ProgramInputs",
    "ProgramOutcome",
]
