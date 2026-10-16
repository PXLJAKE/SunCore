# SPDX-License-Identifier: Apache-2.0
"""Demo mode time: an accelerated clock so the real controller runs against the simulated house.

The demo runs the production stack (``suncore.app.control.ControlStack``) on the
``FakeHAConnection``. The controller, the safety gate and the entity cache must share one time
base with the simulated house, which plays ``demo_speed`` times faster than the wall clock
(60 = one 60 s scenario step per real second). :class:`DemoClock` is that time base: ``now()``
is the scenario time, :meth:`DemoClock.sleep` waits ``seconds / speed`` real seconds, so a gate
settle time of 10 s, a controller tick of 5 s and a 300 s sensor cadence mean the same simulated
seconds everywhere. Nothing here touches ``/data``; the demo keeps its state in a throw-away
database (``suncore.app.main``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

DEMO_TICK_REAL_S: Final = 0.1
"""Target real-time period of a demo controller tick (a tick is cheap, but 12 per second at
speed 60 would be silly)."""
DEMO_MAX_TICK_S: Final = 60.0
"""``ControlSettings.tick_s`` upper bound."""


class DemoClock:
    """Scenario time that advances ``speed`` simulated seconds per real second."""

    def __init__(
        self,
        start: datetime,
        speed: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if start.tzinfo is None or start.utcoffset() is None:
            msg = "the demo clock needs a timezone-aware start"
            raise ValueError(msg)
        if speed <= 0:
            msg = "speed must be positive"
            raise ValueError(msg)
        self._start = start
        self._speed = speed
        self._monotonic = monotonic
        self._t0 = monotonic()

    @property
    def speed(self) -> float:
        return self._speed

    def __call__(self) -> datetime:
        return self._start + timedelta(seconds=(self._monotonic() - self._t0) * self._speed)

    async def sleep(self, seconds: float) -> None:
        """Wait ``seconds`` of *simulated* time."""
        await asyncio.sleep(max(0.0, seconds) / self._speed)


def demo_tick_s(controller_interval_s: float, speed: float) -> float:
    """The controller tick in simulated seconds: the configured one, but never faster than
    :data:`DEMO_TICK_REAL_S` of real time (so ``speed`` 60 ticks every 6 simulated seconds)."""
    return min(DEMO_MAX_TICK_S, max(controller_interval_s, DEMO_TICK_REAL_S * speed))


__all__ = ["DEMO_MAX_TICK_S", "DEMO_TICK_REAL_S", "DemoClock", "demo_tick_s"]
