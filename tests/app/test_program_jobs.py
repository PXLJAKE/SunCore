# SPDX-License-Identifier: Apache-2.0
"""PR 6-L: the program job book (arm, cancel, controller updates, persistence, notices)."""

from __future__ import annotations

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
