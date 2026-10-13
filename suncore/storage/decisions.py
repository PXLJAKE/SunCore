# SPDX-License-Identifier: Apache-2.0
"""Decision log repository; ``DecisionLog.append`` is a valid safety-gate sink."""

from __future__ import annotations

import json
from typing import Final

from suncore.core.decisions import Decision
from suncore.storage.db import Database

MAX_LIMIT: Final = 1000
LAYER_SAFETY: Final = "safety"


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
        """Newest first; ``limit`` is clamped to ``1..MAX_LIMIT``."""
        bounded = max(1, min(int(limit), MAX_LIMIT))
        rows = await self._db.fetchall(
            "SELECT payload FROM decision ORDER BY id DESC LIMIT ?", (bounded,)
        )
        return [Decision.model_validate_json(str(row[0])) for row in rows]

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) FROM decision")
        return int(str(row[0])) if row else 0
