# SPDX-License-Identifier: Apache-2.0
"""PR 6-L: the program job book (arm, cancel, controller updates, persistence, notices)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from suncore.app.programs import ProgramJobBook, ProgramJobError, program_notice
from suncore.controller.programs import ProgramEvent
from suncore.core.flex import JobState, ProgramJob, transition
from suncore.notify.model import Category
from suncore.storage import Database, FlexStateStore
from tests.safety.helpers import PLUG, switch_device

NOW = datetime(2026, 6, 20, 7, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=11)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[FlexStateStore]:
    db = await Database.open(tmp_path / "flex.db")
    yield FlexStateStore(db, clock=lambda: NOW)
    await db.close()


async def test_arm_one_open_job_per_device() -> None:
    book = ProgramJobBook()
    job = await book.arm(PLUG, DEADLINE, now=NOW)
    assert job.state is JobState.ARMED
    assert book.current("washer") == job
    assert book.open_jobs() == {"washer": (job,)}
    with pytest.raises(ProgramJobError) as err:
        await book.arm(PLUG, DEADLINE, now=NOW)
    assert err.value.key == "api.error.program_job_open"


async def test_arm_rejects_a_non_program_and_a_past_deadline() -> None:
    book = ProgramJobBook()
    with pytest.raises(ProgramJobError) as err:
        await book.arm(switch_device(), DEADLINE, now=NOW)
    assert err.value.key == "api.error.program_not_a_program"
    with pytest.raises(ProgramJobError) as err:
        await book.arm(PLUG, NOW - timedelta(minutes=1), now=NOW)
    assert err.value.key == "api.error.program_deadline_past"
    assert book.all_open() == []


async def test_a_waiting_job_can_be_cancelled_a_running_one_not() -> None:
    book = ProgramJobBook()
    with pytest.raises(ProgramJobError) as err:
        await book.cancel("washer", now=NOW)
    assert err.value.key == "api.error.program_no_job"
    job = await book.arm(PLUG, DEADLINE, now=NOW)
    assert book.update_jobs([transition(job, JobState.RUNNING, NOW + timedelta(hours=1))])
    with pytest.raises(ProgramJobError) as err:
        await book.cancel("washer", now=NOW + timedelta(hours=2))
    assert err.value.key == "api.error.program_job_running"
    assert book.current("washer") is not None


async def test_update_jobs_takes_only_valid_transitions_and_ignores_final_jobs() -> None:
    book = ProgramJobBook()
    job = await book.arm(PLUG, DEADLINE, now=NOW)
    later = NOW + timedelta(hours=1)
    running = transition(job, JobState.RUNNING, later)
    done = transition(running, JobState.DONE, later + timedelta(hours=2))
    assert not book.update_jobs([done])  # armed -> done skips running: dropped
    assert book.update_jobs([running])
    assert book.update_jobs([done])
    assert book.all_open() == []
    assert not book.update_jobs([running])  # a final job stays final
    unknown = job.model_copy(update={"id": "other-1"})
    assert not book.update_jobs([unknown])


async def test_jobs_survive_a_restart_through_the_store(store: FlexStateStore) -> None:
    book = ProgramJobBook(store)
    job = await book.arm(PLUG, DEADLINE, now=NOW)
    book.update_jobs([transition(job, JobState.RUNNING, NOW + timedelta(minutes=5))])
    await book.flush()
    again = ProgramJobBook(store)
    await again.load()
    current = again.current("washer")
    assert current is not None
    assert current.state is JobState.RUNNING


def test_notices_of_program_events() -> None:
    armed = ProgramJob(id="j1", device_id="washer", deadline=DEADLINE, armed_at=NOW)
    job = transition(transition(armed, JobState.RUNNING, NOW), JobState.DONE, NOW)
    cases: tuple[tuple[Literal["start_now", "done", "missed"], bool], ...] = (
        ("start_now", False),
        ("done", True),
        ("missed", True),
    )
    for kind, quiet in cases:
        notice = program_notice(ProgramEvent(kind, "washer", job, NOW), lambda _: "Washer", UTC)
        assert notice is not None
        assert notice.category is Category.PROGRAM
        assert notice.reason.key == f"notify.program.{kind}"
        assert notice.reason.args["device"] == "Washer"
        assert notice.reason.args["deadline"] == "18:00"
        assert notice.quiet_hours is quiet
        assert notice.key == f"program:j1:{kind}"
    assert program_notice(ProgramEvent("started", "washer", job, NOW), str, UTC) is None


def test_l1_l4_start_now_and_missed_pass_the_daily_cap_an_unseen_run_is_unknown() -> None:
    armed = ProgramJob(id="j1", device_id="washer", deadline=DEADLINE, armed_at=NOW)
    start = program_notice(ProgramEvent("start_now", "washer", armed, NOW), str, UTC)
    done_job = transition(transition(armed, JobState.RUNNING, NOW), JobState.DONE, NOW)
    done = program_notice(ProgramEvent("done", "washer", done_job, NOW), str, UTC)
    missed_job = transition(armed, JobState.CANCELLED, DEADLINE)
    missed = program_notice(ProgramEvent("missed", "washer", missed_job, DEADLINE), str, UTC)
    unknown = program_notice(
        ProgramEvent("missed", "washer", missed_job, DEADLINE, power_known=False), str, UTC
    )
    assert start is not None
    assert start.cap_exempt
    assert done is not None
    assert not done.cap_exempt
    assert missed is not None
    assert missed.cap_exempt
    assert missed.reason.key == "notify.program.missed"
    assert unknown is not None
    assert unknown.cap_exempt
    assert unknown.reason.key == "notify.program.unknown"


async def test_l2_the_notified_flag_is_kept_by_the_book_and_the_store(
    store: FlexStateStore,
) -> None:
    book = ProgramJobBook(store)
    job = await book.arm(PLUG, DEADLINE, now=NOW)
    assert book.update_jobs([job.model_copy(update={"notified_at": NOW})])
    await book.flush()
    again = ProgramJobBook(store)
    await again.load()
    current = again.current("washer")
    assert current is not None
    assert current.notified_at == NOW


class SlowStore:
    """A store whose writes take a moment (the window of the races)."""

    def __init__(self, fail: bool = False) -> None:
        self.jobs: dict[str, ProgramJob] = {}
        self.fail = fail

    async def put_job(self, job: ProgramJob) -> None:
        await asyncio.sleep(0.01)
        if self.fail:
            raise OSError("disk full")
        self.jobs[job.id] = job


async def test_concurrent_arms_make_one_job_not_two() -> None:
    book = ProgramJobBook(SlowStore())  # type: ignore[arg-type]
    results = await asyncio.gather(
        book.arm(PLUG, DEADLINE, now=NOW),
        book.arm(PLUG, DEADLINE, now=NOW + timedelta(seconds=1)),
        book.arm(PLUG, DEADLINE, now=NOW),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ProgramJob) for r in results) == 1
    errors = [r for r in results if isinstance(r, ProgramJobError)]
    assert [e.key for e in errors] == ["api.error.program_job_open"] * 2
    assert len(book.jobs_of("washer")) == 1


async def test_cancel_cannot_overwrite_a_job_the_controller_started() -> None:
    store = SlowStore()
    book = ProgramJobBook(store)  # type: ignore[arg-type]
    job = await book.arm(PLUG, DEADLINE, now=NOW)
    cancel = asyncio.create_task(book.cancel("washer", now=NOW + timedelta(minutes=1)))
    await asyncio.sleep(0)  # cancel has applied its state and waits for the store
    running = transition(job, JobState.RUNNING, NOW + timedelta(minutes=1))
    assert book.update_jobs([running]) is False  # a cancelled job is final: the update is dropped
    cancelled = await cancel
    assert cancelled.state is JobState.CANCELLED
    assert book._jobs[job.id].state is JobState.CANCELLED


async def test_cancel_of_a_job_that_is_already_running_is_refused() -> None:
    book = ProgramJobBook(SlowStore())  # type: ignore[arg-type]
    job = await book.arm(PLUG, DEADLINE, now=NOW)
    book.update_jobs([transition(job, JobState.RUNNING, NOW + timedelta(minutes=1))])
    with pytest.raises(ProgramJobError) as err:
        await book.cancel("washer", now=NOW + timedelta(minutes=2))
    assert err.value.key == "api.error.program_job_running"
    assert book.current("washer") is not None
    assert book.current("washer").state is JobState.RUNNING  # type: ignore[union-attr]


async def test_a_failing_store_gives_a_key_and_leaves_no_trace() -> None:
    book = ProgramJobBook(SlowStore(fail=True))  # type: ignore[arg-type]
    with pytest.raises(ProgramJobError) as err:
        await book.arm(PLUG, DEADLINE, now=NOW)
    assert err.value.key == "api.error.flex_store_failed"
    assert book.jobs_of("washer") == ()
    good = SlowStore()
    book = ProgramJobBook(good)  # type: ignore[arg-type]
    await book.arm(PLUG, DEADLINE, now=NOW)
    good.fail = True
    with pytest.raises(ProgramJobError) as cerr:
        await book.cancel("washer", now=NOW + timedelta(minutes=1))
    assert cerr.value.key == "api.error.flex_store_failed"
    assert book.current("washer") is not None  # restored: still armed


async def test_a_deadline_beyond_seven_days_is_refused() -> None:
    book = ProgramJobBook()
    with pytest.raises(ProgramJobError) as err:
        await book.arm(PLUG, NOW + timedelta(days=7, seconds=1), now=NOW)
    assert err.value.key == "api.error.program_deadline_far"
    await book.arm(PLUG, NOW + timedelta(days=7), now=NOW)
