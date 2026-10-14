# SPDX-License-Identifier: Apache-2.0
"""Per-device runtime state of the safety layer and the store that persists it.

:class:`DeviceRuntime` is what the invariants need between ticks and across restarts
(min run/pause, switches per hour, write-only-on-change, the takeover baseline for the
release path, the override hold). :class:`RuntimeStore` is the persistence protocol the
storage package implements in Phase 3 WP4 (``device_runtime`` and ``safety_state``
tables); :class:`MemoryRuntimeStore` is the in-memory implementation for tests and the
demo. ``safety`` never imports ``storage`` (boundary rule 6).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.core.decisions import Action

SWITCH_WINDOW = timedelta(hours=1)
"""Sliding window of the max-switches-per-hour invariant."""


class DeviceRuntime(BaseModel):
    """Everything the gate remembers about one device. Immutable; use ``model_copy``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    device_id: str = Field(min_length=1)
    last_on_ts: AwareDatetime | None = None
    """When SunCore last observed or caused a switch-on (min run time)."""
    last_off_ts: AwareDatetime | None = None
    """When SunCore last observed or caused a switch-off (min pause time)."""
    switch_times: tuple[AwareDatetime, ...] = ()
    """Switch instants of the last hour (both directions), oldest first."""
    last_written: Action | None = None
    """The last action SunCore wrote and confirmed; ``None`` = never written."""
    last_write_ts: AwareDatetime | None = None
    last_write_context_id: str | None = None
    """HA context id of the last write (positive evidence for "ours", never negative)."""
    baseline: Action | None = None
    """State at takeover; restored by ``release``. ``None`` = not under control."""
    baseline_ts: AwareDatetime | None = None
    base_setpoint_c: float | None = Field(default=None, allow_inf_nan=False)
    """Climate: the user's own set-point (updated when the user changes it)."""
    override_until: AwareDatetime | None = None
    """Manual override respected until this time (``None`` with ``override`` = indefinitely)."""
    override: bool = False

    @property
    def under_control(self) -> bool:
        return self.baseline is not None

    def switches_in_window(self, now: datetime) -> int:
        start = now - SWITCH_WINDOW
        return sum(1 for ts in self.switch_times if start < ts <= now)

    def pruned(self, now: datetime) -> DeviceRuntime:
        """Drop switch instants that left the sliding hour."""
        start = now - SWITCH_WINDOW
        kept = tuple(ts for ts in self.switch_times if ts > start)
        return (
            self
            if len(kept) == len(self.switch_times)
            else self.model_copy(update={"switch_times": kept})
        )

    def with_switch(self, now: datetime, *, on: bool) -> DeviceRuntime:
        """Record a switch at ``now`` (after pruning the window)."""
        pruned = self.pruned(now)
        update: dict[str, object] = {"switch_times": (*pruned.switch_times, now)}
        update["last_on_ts" if on else "last_off_ts"] = now
        return pruned.model_copy(update=update)

    def is_held(self, now: datetime) -> bool:
        if not self.override:
            return False
        return self.override_until is None or now < self.override_until


class RuntimeStore(Protocol):
    """Persistence of the safety runtime (implemented by ``storage`` in WP4)."""

    async def load_runtime(self, device_id: str) -> DeviceRuntime | None: ...

    async def save_runtime(self, runtime: DeviceRuntime) -> None: ...

    async def all_runtimes(self) -> Sequence[DeviceRuntime]: ...

    async def load_emergency_stop(self) -> bool: ...

    async def save_emergency_stop(self, active: bool) -> None: ...


class MemoryRuntimeStore:
    """In-memory :class:`RuntimeStore` (tests, demo). Nothing survives the process."""

    def __init__(self) -> None:
        self._runtimes: dict[str, DeviceRuntime] = {}
        self._emergency_stop = False

    async def load_runtime(self, device_id: str) -> DeviceRuntime | None:
        return self._runtimes.get(device_id)

    async def save_runtime(self, runtime: DeviceRuntime) -> None:
        self._runtimes[runtime.device_id] = runtime

    async def all_runtimes(self) -> Sequence[DeviceRuntime]:
        return tuple(self._runtimes.values())

    async def load_emergency_stop(self) -> bool:
        return self._emergency_stop

    async def save_emergency_stop(self, active: bool) -> None:
        self._emergency_stop = active


__all__ = ["SWITCH_WINDOW", "DeviceRuntime", "MemoryRuntimeStore", "RuntimeStore"]
