# SPDX-License-Identifier: Apache-2.0
"""The safety gate: every intent passes here, nothing else touches hardware.

Phase 1 scope (``docs/plans/phase-1.md`` WP2): the global operating mode is
shadow and cannot be changed - there is no setter, no constructor
parameter and no code path in this module that calls an actuator. Every
intent becomes a ``shadow`` decision; while the emergency stop is active
every intent is ``rejected``. The automatic path (invariants, write,
read-back, watchdogs) is added in Phase 3 together with the invariant
functions and their property-based tests.

Time is injected through a clock callable; the gate never reads the wall
clock, so tests are deterministic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final

from suncore.core.decisions import Decision, DecisionKind, Intent, OperatingMode, Reason
from suncore.safety.actuator import Actuator

Clock = Callable[[], datetime]
"""Returns the current time as a timezone-aware ``datetime``."""

DecisionSink = Callable[[Decision], Awaitable[None]]
"""Receives every decision the gate makes (the decision log, the live feed)."""

REASON_SHADOW_MODE: Final = Reason(key="safety.shadow_mode")
REASON_EMERGENCY_STOP: Final = Reason(key="safety.emergency_stop")


class SafetyGate:
    """Single writer boundary between the control layers and the actuators.

    Holds the actuator references so that no other object can; in Phase 1 it
    never uses them. The global mode is a class constant, not state.
    """

    MODE: Final[OperatingMode] = OperatingMode.SHADOW

    def __init__(
        self,
        *,
        clock: Clock,
        sink: DecisionSink,
        actuators: Mapping[str, Actuator] | None = None,
    ) -> None:
        self._clock = clock
        self._sink = sink
        self._actuators: Mapping[str, Actuator] = MappingProxyType(dict(actuators or {}))
        self._emergency_stop = False

    @property
    def mode(self) -> OperatingMode:
        """The global operating mode - always shadow in Phase 1."""
        return self.MODE

    @property
    def emergency_stopped(self) -> bool:
        """Whether the global emergency stop ("back to normal") is active."""
        return self._emergency_stop

    @property
    def asset_ids(self) -> frozenset[str]:
        """Assets with a registered actuator (never the actuators themselves)."""
        return frozenset(self._actuators)

    def emergency_stop(self) -> None:
        """Activate the global emergency stop; idempotent, works from any state.

        From now on every intent is rejected until :meth:`release` is called.
        Phase 3 adds the hardware release (heartbeat stop, limits reset).
        """
        self._emergency_stop = True

    def release(self) -> None:
        """Clear the emergency stop; idempotent. The mode stays shadow."""
        self._emergency_stop = False

    async def submit(self, intent: Intent) -> Decision:
        """Judge one intent, emit the decision to the sink and return it.

        Phase 1 never writes: the result is ``rejected`` under emergency stop
        and ``shadow`` otherwise.
        """
        now = self._now()
        if self._emergency_stop:
            decision = Decision(
                intent=intent,
                kind=DecisionKind.REJECTED,
                ts=now,
                reasons=(REASON_EMERGENCY_STOP,),
                mode=self.MODE,
            )
        else:
            decision = Decision(
                intent=intent,
                kind=DecisionKind.SHADOW,
                ts=now,
                reasons=(REASON_SHADOW_MODE,),
                mode=self.MODE,
            )
        await self._sink(decision)
        return decision

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "SafetyGate clock must return a timezone-aware datetime"
            raise ValueError(msg)
        return now


__all__ = [
    "REASON_EMERGENCY_STOP",
    "REASON_SHADOW_MODE",
    "Clock",
    "DecisionSink",
    "SafetyGate",
]
