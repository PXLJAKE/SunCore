# SPDX-License-Identifier: Apache-2.0
"""Dedupe, minimum interval, quiet hours and the daily push cap (plan WP5).

Pure and deterministic: ``now`` is passed in. The limiter only *remembers* a message when the
caller commits it (:meth:`NotifyLimiter.commit`), so a refused delivery does not use up the
interval or the cap.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, tzinfo
from typing import Final

from suncore.core.config import NotificationsConfig, QuietHours
from suncore.notify.model import POLICIES, Category, NotifyEvent, Verdict

MAX_KEYS: Final = 512
"""Bound of the remembered dedupe keys (oldest dropped first)."""


def _parse(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def in_quiet_hours(quiet: QuietHours, now: datetime, tz: tzinfo = UTC) -> bool:
    """Whether ``now`` (in ``tz``) lies in ``[start, end)``; a window may wrap midnight."""
    if not quiet.enabled:
        return False
    local = now.astimezone(tz).time()
    start, end = _parse(quiet.start), _parse(quiet.end)
    if start < end:
        return start <= local < end
    return local >= start or local < end


class NotifyLimiter:
    def __init__(self) -> None:
        self._last: dict[str, datetime] = {}
        self._day: date | None = None
        self._pushed = 0

    @property
    def pushed_today(self) -> int:
        return self._pushed

    def _roll(self, now: datetime, tz: tzinfo) -> None:
        today = now.astimezone(tz).date()
        if today != self._day:
            self._day = today
            self._pushed = 0

    def is_duplicate(self, event: NotifyEvent, now: datetime) -> bool:
        """True when this key was announced and its interval has not elapsed (or is once)."""
        last = self._last.get(event.key)
        if last is None:
            return False
        interval = POLICIES[event.category].min_interval_s
        return interval <= 0 or (now - last).total_seconds() < interval

    def judge_push(
        self, event: NotifyEvent, config: NotificationsConfig, now: datetime, tz: tzinfo = UTC
    ) -> Verdict:
        """The verdict for one push message (no state is changed)."""
        self._roll(now, tz)
        if not config.push_enabled:
            return Verdict.DISABLED
        if self.is_duplicate(event, now):
            return Verdict.DUPLICATE
        quiet = (
            POLICIES[event.category].quiet_hours if event.quiet_hours is None else event.quiet_hours
        )
        if quiet and in_quiet_hours(config.quiet_hours, now, tz):
            return Verdict.QUIET_HOURS
        if (
            event.category is not Category.SAFETY
            and not event.cap_exempt
            and self._pushed >= config.daily_push_cap
        ):
            # Safety events are never starved by the cap (proposals and errors can use it up),
            # nor are the events that must reach the user (``NotifyEvent.cap_exempt``).
            return Verdict.DAILY_CAP
        return Verdict.SEND

    def commit(self, event: NotifyEvent, now: datetime, tz: tzinfo = UTC, *, pushed: bool) -> None:
        """Remember a delivered message (``pushed``: it counts against the daily cap)."""
        self._roll(now, tz)
        self._last[event.key] = now
        if pushed:
            self._pushed += 1
        while len(self._last) > MAX_KEYS:
            del self._last[next(iter(self._last))]
