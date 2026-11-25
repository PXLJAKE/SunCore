# SPDX-License-Identifier: Apache-2.0
"""``flex_state`` (``suncore.db`` schema 6): EV modes/targets and program jobs (Phase 6, 6-B).

Runtime state, not configuration (plan §0 decision 16). Loading is tolerant: a row that does
not parse is logged and skipped - for an EV that means the default mode ``pv`` (surplus only,
the conservative mode), for a job that it is gone (nothing is started for it). Finished jobs
(``done``, ``cancelled``) are pruned after :data:`FINAL_JOB_KEEP`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from pydantic import ValidationError

from suncore.core.flex import EvState, ProgramJob
from suncore.storage.db import Database

log = structlog.get_logger(__name__)

FINAL_JOB_KEEP: Final = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(UTC)


def _ev_key(device_id: str) -> str:
    return f"ev:{device_id}"


def _job_key(job_id: str) -> str:
    return f"job:{job_id}"


class FlexStateStore:
    """Repository over ``flex_state``."""

    def __init__(self, db: Database, clock: Callable[[], datetime] = _now) -> None:
        self._db = db
        self._clock = clock

    # --- EV ----------------------------------------------------------------------------------

    async def put_ev(self, state: EvState) -> None:
        await self._upsert(_ev_key(state.device_id), "ev", state.device_id, False, state)

    async def get_ev(self, device_id: str) -> EvState | None:
        row = await self._db.fetchone(
            "SELECT payload FROM flex_state WHERE key = ?", (_ev_key(device_id),)
        )
        return None if row is None else self._parse_ev(device_id, row[0])

    async def all_ev(self) -> list[EvState]:
        rows = await self._db.fetchall(
            "SELECT device_id, payload FROM flex_state WHERE kind = 'ev' ORDER BY device_id"
        )
        return [s for d, p in rows if (s := self._parse_ev(str(d), p)) is not None]

    # --- jobs --------------------------------------------------------------------------------

    async def put_job(self, job: ProgramJob) -> None:
        await self._upsert(_job_key(job.id), "job", job.device_id, job.final, job)

    async def get_job(self, job_id: str) -> ProgramJob | None:
        row = await self._db.fetchone(
            "SELECT payload FROM flex_state WHERE key = ?", (_job_key(job_id),)
        )
        return None if row is None else self._parse_job(job_id, row[0])

    async def jobs(
        self, device_id: str | None = None, *, open_only: bool = True
    ) -> list[ProgramJob]:
        """Jobs (optionally of one device), oldest deadline first; open ones only by default."""
        sql = "SELECT key, payload FROM flex_state WHERE kind = 'job'"
        params: list[str | int] = []
        if device_id is not None:
            sql += " AND device_id = ?"
            params.append(device_id)
        if open_only:
            sql += " AND final = 0"
        rows = await self._db.fetchall(sql, params)
        jobs = [j for k, p in rows if (j := self._parse_job(str(k)[4:], p)) is not None]
        return sorted(jobs, key=lambda j: (j.deadline, j.id))

    async def delete_device(self, device_id: str) -> int:
        """Forget everything of a removed device."""
        return await self._db.execute("DELETE FROM flex_state WHERE device_id = ?", (device_id,))

    async def prune(self) -> int:
        """Delete finished jobs last updated before ``now - FINAL_JOB_KEEP``."""
        cut = (self._clock() - FINAL_JOB_KEEP).astimezone(UTC).isoformat()
        return await self._db.execute(
            "DELETE FROM flex_state WHERE kind = 'job' AND final = 1 AND updated_at < ?", (cut,)
        )

    # --- helpers -----------------------------------------------------------------------------

    async def _upsert(
        self,
        key: str,
        kind: str,
        device_id: str,
        final: bool,
        model: EvState | ProgramJob,
    ) -> None:
        await self._db.execute(
            "INSERT INTO flex_state (key, kind, device_id, final, payload, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "device_id = excluded.device_id, final = excluded.final, "
            "payload = excluded.payload, updated_at = excluded.updated_at",
            (
                key,
                kind,
                device_id,
                int(final),
                model.model_dump_json(),
                self._clock().astimezone(UTC).isoformat(),
            ),
        )

    @staticmethod
    def _parse_ev(device_id: str, payload: object) -> EvState | None:
        try:
            state = EvState.model_validate_json(str(payload))
        except (ValidationError, ValueError) as exc:
            log.warning("flex_state.ev_unreadable", device_id=device_id, error=str(exc)[:200])
            return None
        if state.device_id != device_id:
            log.warning("flex_state.ev_device_mismatch", device_id=device_id)
            return None
        return state

    @staticmethod
    def _parse_job(job_id: str, payload: object) -> ProgramJob | None:
        try:
            job = ProgramJob.model_validate_json(str(payload))
        except (ValidationError, ValueError) as exc:
            log.warning("flex_state.job_unreadable", job_id=job_id, error=str(exc)[:200])
            return None
        if job.id != job_id:
            log.warning("flex_state.job_id_mismatch", job_id=job_id)
            return None
        return job


__all__ = ["FINAL_JOB_KEEP", "FlexStateStore"]
