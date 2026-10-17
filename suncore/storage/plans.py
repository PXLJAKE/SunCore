# SPDX-License-Identifier: Apache-2.0
"""Plan snapshots: compressed JSON payloads of computed plans (Phase 4 WP3).

The planner (WP9) stores each plan it computes; the UI's plan history and the accuracy
bookkeeping read them back. The payload is opaque here - the plan model owns its shape and
passes its ``schema_version``. Retention keeps at most :data:`KEEP_LAST` snapshots and none
older than :data:`KEEP_DAYS` days.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog

from suncore.storage.db import Database

log = structlog.get_logger(__name__)

KEEP_LAST: Final = 200
KEEP_DAYS: Final = 14
MAX_LIMIT: Final = 500
COMPRESSION_LEVEL: Final = 6


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PlanSnapshotMeta:
    """Header of a stored snapshot (no payload)."""

    id: int
    created_at: datetime
    source: str
    schema_version: int


@dataclass(frozen=True, slots=True)
class PlanSnapshot:
    """A stored snapshot with its decoded payload."""

    id: int
    created_at: datetime
    source: str
    schema_version: int
    payload: dict[str, Any]


def encode_payload(payload: Mapping[str, Any]) -> bytes:
    """JSON (finite numbers only) compressed with zlib."""
    text = json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return zlib.compress(text.encode("utf-8"), COMPRESSION_LEVEL)


def decode_payload(blob: bytes) -> dict[str, Any]:
    """Inverse of :func:`encode_payload`; ``ValueError`` if the blob is not a JSON object."""
    try:
        data = json.loads(zlib.decompress(blob).decode("utf-8"))
    except (zlib.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"plan snapshot payload unreadable: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = "plan snapshot payload is not an object"
        raise ValueError(msg)
    return data


class PlanSnapshotStore:
    """``plan_snapshot`` table behind typed methods."""

    def __init__(self, db: Database, clock: Callable[[], datetime] = _now) -> None:
        self._db = db
        self._clock = clock

    async def save(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
        schema_version: int,
        created_at: datetime | None = None,
    ) -> int:
        """Store a snapshot and return its id (ids grow with time)."""
        when = (created_at or self._clock()).astimezone(UTC)
        blob = encode_payload(payload)
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO plan_snapshot (created_at, source, schema_version, payload) "
                "VALUES (?, ?, ?, ?)",
                (when.isoformat(), source, schema_version, blob),
            )
            row = await tx.fetchone("SELECT last_insert_rowid()")
        assert row is not None
        return int(str(row[0]))

    async def get(self, snapshot_id: int) -> PlanSnapshot | None:
        """The snapshot, or ``None`` if it does not exist or its payload is unreadable."""
        row = await self._db.fetchone(
            "SELECT id, created_at, source, schema_version, payload FROM plan_snapshot "
            "WHERE id = ?",
            (snapshot_id,),
        )
        return None if row is None else self._decode(row)

    async def latest(self) -> PlanSnapshot | None:
        """The newest readable snapshot."""
        rows = await self._db.fetchall(
            "SELECT id, created_at, source, schema_version, payload FROM plan_snapshot "
            "ORDER BY id DESC LIMIT 5"
        )
        for row in rows:
            snapshot = self._decode(row)
            if snapshot is not None:
                return snapshot
        return None

    async def headers(self, limit: int = 50) -> list[PlanSnapshotMeta]:
        """Headers, newest first; ``limit`` is clamped to ``1..MAX_LIMIT``."""
        bounded = max(1, min(int(limit), MAX_LIMIT))
        rows = await self._db.fetchall(
            "SELECT id, created_at, source, schema_version FROM plan_snapshot "
            "ORDER BY id DESC LIMIT ?",
            (bounded,),
        )
        return [
            PlanSnapshotMeta(
                id=int(str(r[0])),
                created_at=datetime.fromisoformat(str(r[1])),
                source=str(r[2]),
                schema_version=int(str(r[3])),
            )
            for r in rows
        ]

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) FROM plan_snapshot")
        return int(str(row[0])) if row else 0

    async def prune(
        self, now: datetime, keep_last: int = KEEP_LAST, keep_days: int = KEEP_DAYS
    ) -> int:
        """Drop snapshots older than ``keep_days`` and all but the newest ``keep_last``."""
        cutoff = (now.astimezone(UTC) - timedelta(days=keep_days)).isoformat()
        async with self._db.transaction() as tx:
            by_age = await tx.execute("DELETE FROM plan_snapshot WHERE created_at < ?", (cutoff,))
            by_count = await tx.execute(
                "DELETE FROM plan_snapshot WHERE id <= "
                "COALESCE((SELECT id FROM plan_snapshot ORDER BY id DESC LIMIT 1 OFFSET ?), 0)",
                (max(0, keep_last),),
            )
        deleted = by_age + by_count
        if deleted:
            log.info("plan_snapshot.pruned", deleted=deleted)
        return deleted

    @staticmethod
    def _decode(row: tuple[object, ...]) -> PlanSnapshot | None:
        try:
            payload = decode_payload(bytes(row[4]))  # type: ignore[call-overload]
        except ValueError as exc:
            log.warning("plan_snapshot.unreadable", snapshot_id=row[0], error=str(exc)[:200])
            return None
        return PlanSnapshot(
            id=int(str(row[0])),
            created_at=datetime.fromisoformat(str(row[1])),
            source=str(row[2]),
            schema_version=int(str(row[3])),
            payload=payload,
        )


__all__ = [
    "KEEP_DAYS",
    "KEEP_LAST",
    "PlanSnapshot",
    "PlanSnapshotMeta",
    "PlanSnapshotStore",
    "decode_payload",
    "encode_payload",
]
