# SPDX-License-Identifier: Apache-2.0
"""Learned parameters (Phase 4 WP3): bias tables, error distributions, load profiles, ...

One row per ``(namespace, key)`` holding a JSON object. The module that owns a namespace
defines the payload and migrates old payloads by ``schema_version``; this repository only
stores, lists and deletes them. A payload that does not parse is treated as missing (logged),
so a learned value can never stop the application - the owner then starts learning again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import structlog

from suncore.storage.db import Database

log = structlog.get_logger(__name__)

NS_FORECAST_BIAS: Final = "forecast_bias"
NS_NET_ERROR: Final = "net_error"
NS_LOAD_PROFILE: Final = "load_profile"
NS_BLEND_WEIGHTS: Final = "blend_weights"
NS_ML_STATE: Final = "ml_state"
NS_FORECAST_ACCURACY: Final = "forecast_accuracy"

KNOWN_NAMESPACES: Final[frozenset[str]] = frozenset(
    {
        NS_FORECAST_BIAS,
        NS_NET_ERROR,
        NS_LOAD_PROFILE,
        NS_BLEND_WEIGHTS,
        NS_ML_STATE,
        NS_FORECAST_ACCURACY,
    }
)
"""The namespaces Phase 4 uses; other well-formed names are accepted (later phases)."""

_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KEY_MAX: Final = 200


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LearnedParam:
    namespace: str
    key: str
    payload: dict[str, Any]
    schema_version: int
    updated_at: datetime


def _check(namespace: str, key: str | None = None) -> None:
    if not _NAME_RE.match(namespace):
        msg = f"invalid learned-parameter namespace {namespace!r}"
        raise ValueError(msg)
    if key is not None and not 0 < len(key) <= _KEY_MAX:
        msg = "learned-parameter key must be 1..200 characters"
        raise ValueError(msg)


class LearnedParamStore:
    """``learned_param`` table behind typed methods."""

    def __init__(self, db: Database, clock: Callable[[], datetime] = _now) -> None:
        self._db = db
        self._clock = clock

    async def put(
        self, namespace: str, key: str, payload: Mapping[str, Any], *, schema_version: int = 1
    ) -> None:
        """Insert or replace one parameter set."""
        _check(namespace, key)
        await self._db.execute(
            "INSERT INTO learned_param (namespace, key, payload, schema_version, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(namespace, key) DO UPDATE SET "
            "payload = excluded.payload, schema_version = excluded.schema_version, "
            "updated_at = excluded.updated_at",
            (
                namespace,
                key,
                json.dumps(payload, allow_nan=False, sort_keys=True),
                schema_version,
                self._clock().astimezone(UTC).isoformat(),
            ),
        )

    async def get(self, namespace: str, key: str) -> LearnedParam | None:
        _check(namespace, key)
        row = await self._db.fetchone(
            "SELECT namespace, key, payload, schema_version, updated_at FROM learned_param "
            "WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        return None if row is None else self._decode(row)

    async def in_namespace(self, namespace: str) -> list[LearnedParam]:
        """All readable entries of a namespace, ordered by key."""
        _check(namespace)
        rows = await self._db.fetchall(
            "SELECT namespace, key, payload, schema_version, updated_at FROM learned_param "
            "WHERE namespace = ? ORDER BY key",
            (namespace,),
        )
        decoded = (self._decode(r) for r in rows)
        return [p for p in decoded if p is not None]

    async def delete(self, namespace: str, key: str) -> bool:
        _check(namespace, key)
        deleted = await self._db.execute(
            "DELETE FROM learned_param WHERE namespace = ? AND key = ?", (namespace, key)
        )
        return deleted > 0

    async def delete_namespace(self, namespace: str) -> int:
        """Forget everything learned in a namespace (e.g. a reset button); returns the count."""
        _check(namespace)
        return await self._db.execute("DELETE FROM learned_param WHERE namespace = ?", (namespace,))

    @staticmethod
    def _decode(row: tuple[object, ...]) -> LearnedParam | None:
        try:
            data = json.loads(str(row[2]))
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            log.warning("learned_param.unreadable", namespace=row[0], key=row[1])
            return None
        return LearnedParam(
            namespace=str(row[0]),
            key=str(row[1]),
            payload=data,
            schema_version=int(str(row[3])),
            updated_at=datetime.fromisoformat(str(row[4])),
        )


__all__ = [
    "KNOWN_NAMESPACES",
    "NS_BLEND_WEIGHTS",
    "NS_FORECAST_ACCURACY",
    "NS_FORECAST_BIAS",
    "NS_LOAD_PROFILE",
    "NS_ML_STATE",
    "NS_NET_ERROR",
    "LearnedParam",
    "LearnedParamStore",
]
