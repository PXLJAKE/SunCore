# SPDX-License-Identifier: Apache-2.0
"""SQLite persistence of the safety runtime and of the learned power models (Phase 3 WP4).

:class:`SqliteRuntimeStore` implements :class:`suncore.safety.runtime.RuntimeStore`
(all methods ``async``). Loading is tolerant: a payload that does not parse is logged and
treated as *missing*. "Missing" is the conservative case by construction: the gate then
knows nothing about the last switch times, so it applies its unknown-state rules (no
switching on short notice, no baseline -> nothing to release). The gate, not this store,
decides what unknown means. The one exception is the emergency stop flag: an unreadable
value counts as *stopped* (fail safe).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from pydantic import ValidationError

from suncore.safety.runtime import DeviceRuntime
from suncore.storage.db import Database

log = structlog.get_logger(__name__)

KEY_EMERGENCY_STOP: Final = "emergency_stop"
KEY_MODE_IDENTITIES: Final = "mode_identities"
"""Identity record of the devices that hold a non-shadow mode (see ``ConfigStore``)."""
KEY_BATTERY_MODE_IDENTITY: Final = "battery_mode_identity"
"""Battery identity token under which a non-shadow battery mode was granted (ADR-0013)."""


def _now() -> datetime:
    return datetime.now(UTC)


class SqliteRuntimeStore:
    """``device_runtime`` and ``safety_state`` tables behind the gate's ``RuntimeStore``."""

    def __init__(self, db: Database, clock: Callable[[], datetime] = _now) -> None:
        self._db = db
        self._clock = clock

    @staticmethod
    def _parse(device_id: object, payload: object) -> DeviceRuntime | None:
        try:
            runtime = DeviceRuntime.model_validate_json(str(payload))
        except (ValidationError, ValueError) as exc:
            log.warning(
                "runtime_store.payload_unreadable", device_id=device_id, error=str(exc)[:200]
            )
            return None
        if runtime.device_id != device_id:
            log.warning("runtime_store.device_id_mismatch", device_id=device_id)
            return None
        return runtime

    async def load_runtime(self, device_id: str) -> DeviceRuntime | None:
        row = await self._db.fetchone(
            "SELECT payload FROM device_runtime WHERE device_id = ?", (device_id,)
        )
        return None if row is None else self._parse(device_id, row[0])

    async def save_runtime(self, runtime: DeviceRuntime) -> None:
        await self._db.execute(
            "INSERT INTO device_runtime (device_id, payload, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET payload = excluded.payload, "
            "updated_at = excluded.updated_at",
            (runtime.device_id, runtime.model_dump_json(), self._clock().isoformat()),
        )

    async def all_runtimes(self) -> Sequence[DeviceRuntime]:
        rows = await self._db.fetchall(
            "SELECT device_id, payload FROM device_runtime ORDER BY device_id"
        )
        parsed = (self._parse(r[0], r[1]) for r in rows)
        return tuple(r for r in parsed if r is not None)

    async def delete_runtime(self, device_id: str) -> None:
        await self._db.execute("DELETE FROM device_runtime WHERE device_id = ?", (device_id,))

    async def load_emergency_stop(self) -> bool:
        row = await self._db.fetchone(
            "SELECT value FROM safety_state WHERE key = ?", (KEY_EMERGENCY_STOP,)
        )
        if row is None:
            return False
        value = str(row[0])
        if value not in ("0", "1"):
            log.warning("runtime_store.emergency_stop_unreadable")
            return True  # fail safe
        return value == "1"

    async def save_emergency_stop(self, active: bool) -> None:
        await self._db.execute(
            "INSERT INTO safety_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (KEY_EMERGENCY_STOP, "1" if active else "0", self._clock().isoformat()),
        )


class PowerModelStateStore:
    """Learned power-model state per device (JSON object; the model owns the shape)."""

    def __init__(self, db: Database, clock: Callable[[], datetime] = _now) -> None:
        self._db = db
        self._clock = clock

    async def get(self, device_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT payload FROM power_model_state WHERE device_id = ?", (device_id,)
        )
        if row is None:
            return None
        try:
            data = json.loads(str(row[0]))
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            log.warning("power_model_state.unreadable", device_id=device_id)
            return None
        return data

    async def put(self, device_id: str, state: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO power_model_state (device_id, payload, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET payload = excluded.payload, "
            "updated_at = excluded.updated_at",
            (device_id, json.dumps(state, allow_nan=False), self._clock().isoformat()),
        )

    async def delete(self, device_id: str) -> None:
        await self._db.execute("DELETE FROM power_model_state WHERE device_id = ?", (device_id,))


__all__ = [
    "KEY_BATTERY_MODE_IDENTITY",
    "KEY_EMERGENCY_STOP",
    "KEY_MODE_IDENTITIES",
    "PowerModelStateStore",
    "SqliteRuntimeStore",
]
