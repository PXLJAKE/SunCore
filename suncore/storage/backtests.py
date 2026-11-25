# SPDX-License-Identifier: Apache-2.0
"""``backtest_run``: the last results of what-if and backtest runs (Phase 5 WP3).

The engine is ``suncore.sim.backtest``, the job runner ``suncore.app.backtests`` (PR 5-J). The
request is JSON text, the result a zlib-compressed JSON blob (same encoding as plan snapshots).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Final, Literal

import structlog

from suncore.storage.db import Database
from suncore.storage.plans import decode_payload, encode_payload

log = structlog.get_logger(__name__)

BacktestStatus = Literal["running", "done", "failed", "cancelled"]
KEEP_RESULTS: Final = 5


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class BacktestRunMeta:
    id: int
    created_at: datetime
    finished_at: datetime | None
    status: BacktestStatus
    window_start: datetime
    window_end: datetime
    schema_version: int


@dataclass(frozen=True, slots=True)
class BacktestRun:
    meta: BacktestRunMeta
    request: dict[str, Any]
    result: dict[str, Any] | None


_COLUMNS: Final = (
    "id, created_at, finished_at, status, window_start, window_end, schema_version, request, result"
)


class BacktestRunStore:
    """Repository over ``backtest_run`` keeping the newest :attr:`KEEP` runs."""

    KEEP: ClassVar[int] = KEEP_RESULTS

    def __init__(self, db: Database, clock: Callable[[], datetime] = _now) -> None:
        self._db = db
        self._clock = clock

    async def start(
        self,
        request: Mapping[str, Any],
        *,
        window_start: datetime,
        window_end: datetime,
        schema_version: int = 1,
    ) -> int:
        """Record a run as ``running`` and return its id; older runs beyond the keep limit go."""
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO backtest_run (created_at, status, window_start, window_end, "
                "request, schema_version) VALUES (?, 'running', ?, ?, ?, ?)",
                (
                    self._clock().astimezone(UTC).isoformat(),
                    window_start.astimezone(UTC).isoformat(),
                    window_end.astimezone(UTC).isoformat(),
                    json.dumps(request, allow_nan=False, sort_keys=True),
                    schema_version,
                ),
            )
            row = await tx.fetchone("SELECT last_insert_rowid()")
            assert row is not None
            run_id = int(str(row[0]))
            await self._prune(tx.execute)
        return run_id

    async def finish(
        self,
        run_id: int,
        status: Literal["done", "failed", "cancelled"],
        result: Mapping[str, Any] | None = None,
    ) -> bool:
        """Close a run; ``False`` if it was pruned meanwhile."""
        blob = None if result is None else encode_payload(result)
        changed = await self._db.execute(
            "UPDATE backtest_run SET status = ?, finished_at = ?, result = ? WHERE id = ?",
            (status, self._clock().astimezone(UTC).isoformat(), blob, run_id),
        )
        return changed > 0

    async def get(self, run_id: int) -> BacktestRun | None:
        row = await self._db.fetchone(
            f"SELECT {_COLUMNS} FROM backtest_run WHERE id = ?",
            (run_id,),
        )
        return None if row is None else _decode(row)

    async def headers(self) -> list[BacktestRunMeta]:
        """The kept runs, newest first."""
        rows = await self._db.fetchall(
            "SELECT id, created_at, finished_at, status, window_start, window_end, "
            "schema_version FROM backtest_run ORDER BY id DESC"
        )
        return [_meta(r) for r in rows]

    async def abandon_running(self) -> int:
        """Mark runs left ``running`` by an earlier process as ``failed`` (at start-up)."""
        changed: int = await self._db.execute(
            "UPDATE backtest_run SET status = 'failed', finished_at = ? WHERE status = 'running'",
            (self._clock().astimezone(UTC).isoformat(),),
        )
        if changed:
            log.info("backtest_run.abandoned", runs=changed)
        return changed

    async def delete(self, run_id: int) -> bool:
        return await self._db.execute("DELETE FROM backtest_run WHERE id = ?", (run_id,)) > 0

    async def _prune(self, execute: Callable[..., Any]) -> int:
        deleted: int = await execute(
            "DELETE FROM backtest_run WHERE id <= "
            "COALESCE((SELECT id FROM backtest_run ORDER BY id DESC LIMIT 1 OFFSET ?), 0)",
            (self.KEEP,),
        )
        if deleted:
            log.info("backtest_run.pruned", deleted=deleted)
        return deleted


def _meta(row: tuple[object, ...]) -> BacktestRunMeta:
    status = str(row[3])
    assert status in ("running", "done", "failed", "cancelled")
    return BacktestRunMeta(
        id=int(str(row[0])),
        created_at=datetime.fromisoformat(str(row[1])),
        finished_at=None if row[2] is None else datetime.fromisoformat(str(row[2])),
        status=status,  # type: ignore[arg-type]
        window_start=datetime.fromisoformat(str(row[4])),
        window_end=datetime.fromisoformat(str(row[5])),
        schema_version=int(str(row[6])),
    )


def _decode(row: tuple[object, ...]) -> BacktestRun | None:
    try:
        request = json.loads(str(row[7]))
        result = None if row[8] is None else decode_payload(bytes(row[8]))  # type: ignore[call-overload]
    except ValueError as exc:
        log.warning("backtest_run.unreadable", run_id=row[0], error=str(exc)[:200])
        return None
    if not isinstance(request, dict):
        return None
    return BacktestRun(meta=_meta(row), request=request, result=result)


__all__ = ["KEEP_RESULTS", "BacktestRun", "BacktestRunMeta", "BacktestRunStore", "BacktestStatus"]
