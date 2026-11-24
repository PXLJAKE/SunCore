# SPDX-License-Identifier: Apache-2.0
"""Program jobs at runtime (PR 6-L): the open jobs, arming, cancelling, persistence, notices.

:class:`ProgramJobBook` keeps the open jobs of every program device in memory (the controller
and the gate read them every tick, synchronously) and persists every change to ``flex_state``
(``suncore.db``) when a store is given. The controller's job updates are applied only when they
follow from the stored state (``core.flex.JOB_TRANSITIONS``): a job the user cancelled in the
meantime stays cancelled, a running job is never cancelled (P2).

:func:`program_notice` turns a controller :class:`~suncore.controller.programs.ProgramEvent`
into a notification (category ``program``; plan WP5 table: "start now" ignores quiet hours,
"done" and "missed" respect them).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Final

import structlog

from suncore.core.decisions import Reason
from suncore.core.devices import DeviceConfig
from suncore.core.flex import (
    JOB_TRANSITIONS,
    ArmedBy,
    JobState,
    JobTransitionError,
    ProgramJob,
    open_job,
    transition,
)
from suncore.notify.model import Category, NotifyEvent

if TYPE_CHECKING:
    from suncore.controller.programs import ProgramEvent
    from suncore.storage.flex import FlexStateStore

log = structlog.get_logger(__name__)

NOTICE_KEYS: Final[Mapping[str, str]] = {
    "start_now": "notify.program.start_now",
    "done": "notify.program.done",
    "missed": "notify.program.missed",
}
"""Program events that are announced; ``started`` is only logged."""


class ProgramJobError(ValueError):
    """Arming or cancelling is not possible (unknown device, open job, running job)."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class ProgramJobBook:
    """Open program jobs in memory, persisted to ``flex_state`` (see the module docstring)."""

    def __init__(self, store: FlexStateStore | None = None) -> None:
        self._store = store
        self._jobs: dict[str, ProgramJob] = {}
        self._dirty: dict[str, ProgramJob] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Read the open jobs (at start); a failed read keeps what is in memory."""
        if self._store is None:
            return
        try:
            jobs = await self._store.jobs(open_only=True)
        except Exception:
            log.exception("programs.load_failed")
            return
        self._jobs = {j.id: j for j in jobs}

    # --- reads (synchronous, every tick) ---------------------------------------------------

    def jobs_of(self, device_id: str) -> tuple[ProgramJob, ...]:
        return tuple(j for j in self._jobs.values() if j.device_id == device_id and not j.final)

    def open_jobs(self) -> dict[str, tuple[ProgramJob, ...]]:
        out: dict[str, list[ProgramJob]] = {}
        for job in self._jobs.values():
            if not job.final:
                out.setdefault(job.device_id, []).append(job)
        return {k: tuple(v) for k, v in out.items()}

    def all_open(self) -> list[ProgramJob]:
        return sorted((j for j in self._jobs.values() if not j.final), key=lambda j: j.deadline)

    def current(self, device_id: str) -> ProgramJob | None:
        return open_job(self.jobs_of(device_id))

    # --- user commands -----------------------------------------------------------------------

    async def arm(
        self,
        device: DeviceConfig,
        deadline: datetime,
        *,
        now: datetime,
        armed_by: ArmedBy = "user",
    ) -> ProgramJob:
        """Arm one job ("finished by ``deadline``"); one open job per device (6-H)."""
        if device.program is None:
            raise ProgramJobError("api.error.program_not_a_program")
        if self.jobs_of(device.id):
            raise ProgramJobError("api.error.program_job_open")
        job_id = f"{device.id[:40]}-{now:%Y%m%d%H%M%S}"
        suffix = 1
        while job_id in self._jobs:
            suffix += 1
            job_id = f"{device.id[:40]}-{now:%Y%m%d%H%M%S}-{suffix}"
        try:
            job = ProgramJob(
                id=job_id, device_id=device.id, deadline=deadline, armed_at=now, armed_by=armed_by
            )
        except ValueError as exc:
            raise ProgramJobError("api.error.program_deadline_past") from exc
        await self._put(job)
        return job

    async def cancel(self, device_id: str, *, now: datetime) -> ProgramJob:
        """Cancel the device's waiting job; a running job cannot be cancelled (P2)."""
        job = self.current(device_id)
        if job is None:
            raise ProgramJobError("api.error.program_no_job")
        try:
            cancelled = transition(job, JobState.CANCELLED, now)
        except JobTransitionError as exc:
            raise ProgramJobError("api.error.program_job_running") from exc
        await self._put(cancelled)
        return cancelled

    # --- controller updates ------------------------------------------------------------------

    def update_jobs(self, jobs: Sequence[ProgramJob]) -> bool:
        """Take the controller's job updates that follow from the stored state; ``True`` when
        something changed (the caller then schedules :meth:`flush`)."""
        changed = False
        for job in jobs:
            current = self._jobs.get(job.id)
            if current is None or current.final:
                continue
            if job.state is not current.state and job.state not in JOB_TRANSITIONS[current.state]:
                log.info("programs.update_dropped", job_id=job.id, state=job.state.value)
                continue
            self._jobs[job.id] = job
            self._dirty[job.id] = job
            changed = True
        return changed

    async def flush(self) -> None:
        """Persist the pending updates (a failure keeps them pending)."""
        async with self._lock:
            pending, self._dirty = self._dirty, {}
            if self._store is None:
                return
            for job in pending.values():
                try:
                    await self._store.put_job(job)
                except Exception:
                    log.exception("programs.persist_failed", job_id=job.id)
                    self._dirty.setdefault(job.id, job)

    async def _put(self, job: ProgramJob) -> None:
        async with self._lock:
            if self._store is not None:
                await self._store.put_job(job)
            self._jobs[job.id] = job
            self._dirty.pop(job.id, None)


def program_notice(
    event: ProgramEvent, name: Callable[[str], str], tz: tzinfo
) -> NotifyEvent | None:
    """The notification of a program event (``None`` = not announced)."""
    key = NOTICE_KEYS.get(event.kind)
    if key is None:
        return None
    when = event.job.deadline.astimezone(tz)
    return NotifyEvent(
        category=Category.PROGRAM,
        key=f"program:{event.job.id}:{event.kind}",
        reason=Reason(
            key=key, args={"device": name(event.device_id), "deadline": when.strftime("%H:%M")}
        ),
        ts=event.ts,
        quiet_hours=event.kind != "start_now",
    )


__all__ = [
    "NOTICE_KEYS",
    "ProgramJobBook",
    "ProgramJobError",
    "program_notice",
]
