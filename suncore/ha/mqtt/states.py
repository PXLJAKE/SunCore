# SPDX-License-Identifier: Apache-2.0
"""On-change state publishing with deadbands and a per-entity rate limit (ADR-0016 decision 8).

States are retained, QoS 0. A value is published when its text changed and, for numbers, when
it moved by more than the entity's deadband (absolute or relative, whichever is larger) since
the last *published* value; never more often than once per ``min_interval_s`` per entity. A
value held back by the rate limit stays pending: :attr:`StatePublisher.pending` tells the caller
to call :meth:`StatePublisher.sync` again. Nothing here blocks the control tick.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from suncore.ha.mqtt.discovery import Topics

PublishFn = Callable[[str, str, int, bool], Awaitable[bool]]
"""``(topic, payload, qos, retain) -> delivered``; ``False`` when not connected."""

MIN_INTERVAL_S = 5.0


@dataclass(frozen=True, slots=True)
class StateItem:
    object_id: str
    payload: str
    number: float | None = None
    deadband_abs: float = 0.0
    deadband_rel: float = 0.0


@dataclass(slots=True)
class _Last:
    payload: str
    number: float | None
    at: float


class StatePublisher:
    def __init__(
        self,
        topics: Topics,
        publish: PublishFn,
        *,
        clock: Callable[[], float] = time.monotonic,
        min_interval_s: float = MIN_INTERVAL_S,
    ) -> None:
        self._topics = topics
        self._publish = publish
        self._clock = clock
        self._min_interval_s = min_interval_s
        self._last: dict[str, _Last] = {}
        self.pending = False
        """Some value was held back by the rate limit; sync again after ``min_interval_s``."""

    def reset(self) -> None:
        """Forget what was published (after the configs were republished)."""
        self._last.clear()
        self.pending = False

    @staticmethod
    def _moved(item: StateItem, last: _Last) -> bool:
        if item.payload == last.payload:
            return False
        if item.number is None or last.number is None:
            return True
        threshold = max(item.deadband_abs, item.deadband_rel * abs(last.number))
        return abs(item.number - last.number) >= threshold if threshold > 0 else True

    async def sync(self, items: Iterable[StateItem]) -> int:
        """Publish what changed; returns the number of messages sent."""
        sent = 0
        self.pending = False
        now = self._clock()
        for item in items:
            last = self._last.get(item.object_id)
            if last is not None and not self._moved(item, last):
                continue
            if last is not None and now - last.at < self._min_interval_s:
                self.pending = True
                continue
            delivered = await self._publish(
                self._topics.state(item.object_id), item.payload, 0, True
            )
            if not delivered:
                self.pending = True
                break
            self._last[item.object_id] = _Last(item.payload, item.number, now)
            sent += 1
        return sent
