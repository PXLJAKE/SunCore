# SPDX-License-Identifier: Apache-2.0
"""Throttle for the refused-command log (security review 1.0, SEC-16).

Every broker user can publish commands (SEC-14), and every refused command used to produce a
log line, so a flood of refused or rate-limited messages could flood the app log. The
throttle keeps the first refusal per ``(entity, reason)`` in full and counts the rest; a
summary with the counts is due every :data:`SUMMARY_INTERVAL_S`.

Only the log changes. The refusal itself, its reason, the republished state and the refusal
hook are untouched. The emergency stop is never rate-limited or refused by policy, but an
invalid stop payload is a refusal like any other and is throttled the same way. The clock
is injected; nothing here sleeps or reads the wall clock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

SUMMARY_INTERVAL_S: Final = 60.0
MAX_KEYS: Final = 64
"""Distinct ``(entity, reason)`` pairs tracked per interval; beyond the cap they are counted
under :data:`OVERFLOW_ENTITY` (the reasons are a small fixed set)."""
OVERFLOW_ENTITY: Final = "*"
POOLED_REASONS: Final = frozenset({"mqtt.command.unknown_entity"})
"""Reasons whose entity comes straight from the topic: a sender can invent any number of
object ids. They are always counted under :data:`OVERFLOW_ENTITY`, so invented ids can never
fill the cap and hide the refusals of real entities."""


@dataclass(frozen=True, slots=True)
class RefusalCount:
    entity: str
    reason: str
    suppressed: int


class RefusalLogThrottle:
    """Decide which refusals are logged in full and when a count summary is due."""

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        interval_s: float = SUMMARY_INTERVAL_S,
        max_keys: int = MAX_KEYS,
    ) -> None:
        self._clock = clock
        self._interval_s = interval_s
        self._max_keys = max_keys
        self._window_start = clock()
        self._suppressed: dict[tuple[str, str], int] = {}
        """Pairs logged in full in this or the last busy interval -> refusals not logged."""

    def record(self, entity: str, reason: str) -> bool:
        """Count one refusal; ``True`` = log it in full (the first of its pair)."""
        key = (OVERFLOW_ENTITY if reason in POOLED_REASONS else entity, reason)
        if key not in self._suppressed and len(self._suppressed) >= self._max_keys:
            key = (OVERFLOW_ENTITY, reason)
        if key in self._suppressed:
            self._suppressed[key] += 1
            return False
        self._suppressed[key] = 0
        return True

    def summary(self) -> list[RefusalCount] | None:
        """The counts since the last summary once an interval has passed, else ``None``.

        A pair that was quiet for a whole interval is forgotten, so its next refusal is
        logged in full again; a pair that keeps being refused stays counted. An empty list
        means the interval passed with nothing suppressed (nothing to log)."""
        now = self._clock()
        if now - self._window_start < self._interval_s:
            return None
        self._window_start = now
        counts = [
            RefusalCount(entity, reason, n)
            for (entity, reason), n in sorted(self._suppressed.items())
            if n > 0
        ]
        self._suppressed = {(c.entity, c.reason): 0 for c in counts}
        return counts
