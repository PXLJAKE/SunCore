# SPDX-License-Identifier: Apache-2.0
"""Per-device write rate limiter with try-acquire semantics.

Intents are never queued: an intent that arrives while the device's minimum write
interval has not elapsed is dropped (the controller re-issues on the next tick), and
an intent older than the gate's ``intent_max_age_s`` is stale and dropped before any
other check. Time is injected; the limiter never reads the wall clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def is_stale_intent(intent_ts: datetime, now: datetime, max_age_s: float) -> bool:
    """``True`` if the intent was issued more than ``max_age_s`` before ``now``."""
    return now - intent_ts > timedelta(seconds=max_age_s)


class WriteRateLimiter:
    """At most one write per device every ``min_interval_s`` (BRIEF §5 write protection)."""

    def __init__(self, min_interval_s: float) -> None:
        if min_interval_s < 0:
            msg = "min_interval_s must not be negative"
            raise ValueError(msg)
        self._interval = timedelta(seconds=min_interval_s)
        self._last: dict[str, datetime] = {}

    @property
    def min_interval_s(self) -> float:
        return self._interval.total_seconds()

    def next_allowed(self, device_id: str) -> datetime | None:
        """Earliest instant of the next write; ``None`` if the device never wrote."""
        last = self._last.get(device_id)
        return None if last is None else last + self._interval

    def can_acquire(self, device_id: str, now: datetime) -> bool:
        allowed = self.next_allowed(device_id)
        return allowed is None or now >= allowed

    def try_acquire(self, device_id: str, now: datetime) -> bool:
        """Reserve a write slot at ``now``; ``False`` (and no reservation) if too early."""
        if not self.can_acquire(device_id, now):
            return False
        self._last[device_id] = now
        return True

    def release_slot(self, device_id: str, previous: datetime | None) -> None:
        """Undo a reservation (write not attempted); ``previous`` is the prior last write."""
        if previous is None:
            self._last.pop(device_id, None)
        else:
            self._last[device_id] = previous

    def last_write(self, device_id: str) -> datetime | None:
        return self._last.get(device_id)

    def prime(self, device_id: str, last_write: datetime | None) -> None:
        """Seed the limiter from persisted runtime (after a restart)."""
        if last_write is None:
            self._last.pop(device_id, None)
        else:
            self._last[device_id] = last_write


__all__ = ["WriteRateLimiter", "is_stale_intent"]
