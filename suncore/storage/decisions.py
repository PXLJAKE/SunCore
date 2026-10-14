# SPDX-License-Identifier: Apache-2.0
"""Decision log repository; ``DecisionLog.append`` is a valid safety-gate sink."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from pydantic import ValidationError

from suncore.core.decisions import Decision, decision_from_json
from suncore.storage.db import Database

log = structlog.get_logger(__name__)

MAX_LIMIT: Final = 1000
LAYER_SAFETY: Final = "safety"
RETENTION_DAYS: Final = 30
RETENTION_MAX_ROWS: Final = 200_000


class DecisionLog:
    """Append-only log of safety decisions."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, decision: Decision) -> None:
        """Store one decision (matches ``suncore.safety.gate.DecisionSink``)."""
        reasons = json.dumps([r.model_dump(mode="json") for r in decision.reasons])
        await self._db.execute(
            "INSERT INTO decision (ts, asset_id, layer, kind, payload, reasons) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                decision.ts.isoformat(),
                decision.intent.asset_id,
                LAYER_SAFETY,
                decision.kind.value,
                decision.model_dump_json(),
                reasons,
            ),
        )

    async def latest(self, limit: int = 100) -> list[Decision]:
        """Newest first; ``limit`` is clamped to ``1..MAX_LIMIT``.

        Rows are deserialised tolerantly (``decision_from_json``): Phase 1 v1 payloads
        load, rows of a newer schema or with a corrupt payload are skipped and logged.
        """
        bounded = max(1, min(int(limit), MAX_LIMIT))
        rows = await self._db.fetchall(
            "SELECT id, payload FROM decision ORDER BY id DESC LIMIT ?", (bounded,)
        )
        decisions: list[Decision] = []
        for row in rows:
            try:
                decisions.append(decision_from_json(str(row[1])))
            except (ValueError, ValidationError) as exc:
                log.warning("decision_log.row_skipped", row_id=row[0], error=str(exc)[:200])
        return decisions

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) FROM decision")
        return int(str(row[0])) if row else 0

    async def prune(
        self,
        now: datetime,
        max_age_days: int = RETENTION_DAYS,
        max_rows: int = RETENTION_MAX_ROWS,
    ) -> int:
        """Retention: drop rows older than ``max_age_days`` and keep at most ``max_rows``.

        ``now`` is injected (no wall clock here). Returns the number of deleted rows. Age
        uses the indexed ``ts`` (ISO-8601 UTC text sorts chronologically), the row cap keeps
        the newest ids.
        """
        cutoff = (now.astimezone(UTC) - timedelta(days=max_age_days)).isoformat()
        async with self._db.transaction() as tx:
            by_age = await tx.execute("DELETE FROM decision WHERE ts < ?", (cutoff,))
            by_count = await tx.execute(
                "DELETE FROM decision WHERE id <= "
                "COALESCE((SELECT id FROM decision ORDER BY id DESC LIMIT 1 OFFSET ?), 0)",
                (max(0, max_rows),),
            )
        deleted = by_age + by_count
        if deleted:
            log.info("decision_log.pruned", deleted=deleted)
        return deleted
