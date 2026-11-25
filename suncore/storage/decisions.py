# SPDX-License-Identifier: Apache-2.0
"""Decision log repository; ``DecisionLog.append`` is a valid safety-gate sink."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from pydantic import ValidationError

from suncore.core.decisions import Decision, DecisionKind, decision_from_json
from suncore.storage.db import Database, SqlParam

log = structlog.get_logger(__name__)

MAX_LIMIT: Final = 1000
LAYER_SAFETY: Final = "safety"
RETENTION_DAYS: Final = 30
RETENTION_MAX_ROWS: Final = 200_000


def _like(text: str) -> str:
    """``text`` as a LIKE operand (``ESCAPE '\\'``) that matches it literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True, slots=True)
class DecisionFilter:
    """Filters of :meth:`DecisionLog.query`; unset fields do not filter."""

    device_id: str | None = None
    kinds: frozenset[DecisionKind] = field(default_factory=frozenset)
    since: datetime | None = None
    """Inclusive."""
    until: datetime | None = None
    """Exclusive."""
    layer: str | None = None
    """The layer that produced the intent (``Origin`` value: planner, controller, user, safety)."""
    reason: str | None = None
    """A reason key of the decision or of its intent; a key that is a dotted prefix of a longer
    one (``safety`` matches ``safety.shadow_mode``) matches it, too."""
    text: str | None = None
    """Case-insensitive substring of the device id or of the stored decision (reason keys and
    arguments, action, mode)."""


_INTENT_REASONS: Final = "payload, '$.intent.reasons'"


def _reason_exists(source: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM json_each({source}) AS j "
        "WHERE json_extract(j.value, '$.key') = ? "
        "OR json_extract(j.value, '$.key') LIKE ? ESCAPE '\\')"
    )


def _where(f: DecisionFilter) -> tuple[str, list[SqlParam]]:
    clauses: list[str] = []
    params: list[SqlParam] = []
    if f.device_id is not None:
        clauses.append("asset_id = ?")
        params.append(f.device_id)
    if f.kinds:
        wanted = sorted(k.value for k in f.kinds)
        clauses.append(f"kind IN ({', '.join('?' for _ in wanted)})")
        params.extend(wanted)
    if f.since is not None:
        clauses.append("ts >= ?")
        params.append(f.since.astimezone(UTC).isoformat())
    if f.until is not None:
        clauses.append("ts < ?")
        params.append(f.until.astimezone(UTC).isoformat())
    if f.layer is not None:
        clauses.append("json_extract(payload, '$.intent.origin') = ?")
        params.append(f.layer)
    if f.reason is not None:
        pattern = f"{_like(f.reason)}.%"
        clauses.append(f"({_reason_exists('reasons')} OR {_reason_exists(_INTENT_REASONS)})")
        params.extend((f.reason, pattern, f.reason, pattern))
    if f.text:
        clauses.append("(asset_id LIKE ? ESCAPE '\\' OR payload LIKE ? ESCAPE '\\')")
        params.extend((f"%{_like(f.text)}%",) * 2)
    return (f"WHERE {' AND '.join(clauses)} " if clauses else ""), params


def _decode(row: tuple[object, ...]) -> Decision | None:
    try:
        return decision_from_json(str(row[1]))
    except (ValueError, ValidationError) as exc:
        log.warning("decision_log.row_skipped", row_id=row[0], error=str(exc)[:200])
        return None


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
                decision.ts.astimezone(UTC).isoformat(),  # the ts index sorts chronologically
                decision.intent.asset_id,
                LAYER_SAFETY,
                decision.kind.value,
                decision.model_dump_json(),
                reasons,
            ),
        )

    async def latest(
        self,
        limit: int = 100,
        *,
        device_id: str | None = None,
        kinds: Collection[DecisionKind] | None = None,
    ) -> list[Decision]:
        """Newest first; ``limit`` is clamped to ``1..MAX_LIMIT``.

        ``device_id`` (the decision's asset) and ``kinds`` filter in SQL (indexed by
        ``decision_asset_idx``); an empty ``kinds`` means no filter on the kind.

        Rows are deserialised tolerantly (``decision_from_json``): Phase 1 v1 payloads
        load, rows of a newer schema or with a corrupt payload are skipped and logged.
        """
        bounded = max(1, min(int(limit), MAX_LIMIT))
        clauses: list[str] = []
        params: list[str | int] = []
        if device_id is not None:
            clauses.append("asset_id = ?")
            params.append(device_id)
        if kinds:
            wanted = sorted({k.value for k in kinds})
            clauses.append(f"kind IN ({', '.join('?' for _ in wanted)})")
            params.extend(wanted)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = await self._db.fetchall(
            f"SELECT id, payload FROM decision {where}ORDER BY id DESC LIMIT ?",
            (*params, bounded),
        )
        decisions: list[Decision] = []
        for row in rows:
            try:
                decisions.append(decision_from_json(str(row[1])))
            except (ValueError, ValidationError) as exc:
                log.warning("decision_log.row_skipped", row_id=row[0], error=str(exc)[:200])
        return decisions

    async def query(
        self, f: DecisionFilter, *, limit: int = 100, offset: int = 0
    ) -> list[tuple[int, Decision]]:
        """Newest first with the row id (the key of ``get``); ``limit`` is clamped to
        ``1..MAX_LIMIT``. Rows that no longer parse are skipped (and logged)."""
        where, params = _where(f)
        rows = await self._db.fetchall(
            f"SELECT id, payload FROM decision {where}ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, max(1, min(int(limit), MAX_LIMIT)), max(0, int(offset))),
        )
        found: list[tuple[int, Decision]] = []
        for row in rows:
            decision = _decode(row)
            if decision is not None:
                found.append((int(str(row[0])), decision))
        return found

    async def count_matching(self, f: DecisionFilter) -> int:
        where, params = _where(f)
        row = await self._db.fetchone(f"SELECT COUNT(*) FROM decision {where}", params)
        return int(str(row[0])) if row else 0

    async def get(self, decision_id: int) -> Decision | None:
        """One decision by row id; ``None`` if it was pruned or does not parse."""
        row = await self._db.fetchone(
            "SELECT id, payload FROM decision WHERE id = ?", (decision_id,)
        )
        return None if row is None else _decode(row)

    async def neighbours(
        self, decision_id: int, asset_id: str, count: int
    ) -> tuple[list[tuple[int, Decision]], list[tuple[int, Decision]]]:
        """The ``count`` decisions of the same asset before and after ``decision_id``, each
        list oldest first."""
        bounded = max(0, min(int(count), 20))
        out: list[list[tuple[int, Decision]]] = []
        for op, order in (("<", "DESC"), (">", "ASC")):
            rows = await self._db.fetchall(
                f"SELECT id, payload FROM decision WHERE asset_id = ? AND id {op} ? "
                f"ORDER BY id {order} LIMIT ?",
                (asset_id, decision_id, bounded),
            )
            items = [(int(str(r[0])), d) for r in rows if (d := _decode(r)) is not None]
            out.append(sorted(items, key=lambda item: item[0]))
        return out[0], out[1]

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
