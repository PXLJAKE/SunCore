# SPDX-License-Identifier: Apache-2.0
"""Per-device hysteresis state machine (plan WP7).

One :class:`DeviceMachine` per configured device remembers the level SunCore considers
applied, a pending level change with the time its condition has held, the intent in
flight, the retry cool-down after a rejection, the switch history for the protection
times and the climate base set-point. All transitions take ``now`` as an argument; the
machine never reads a clock.

States (:class:`Phase`):

* ``idle`` - not controllable right now (unknown state, held by the gate, no power
  figure, climate off without the opt-in); the machine re-synchronises from the observed
  state when the device becomes controllable again;
* ``steady`` - at ``level`` with no change pending;
* ``pending_up`` / ``pending_down`` - the surplus condition for another level holds and
  the hysteresis delay runs (a forced change - window, plan, deficit back-off - has no
  delay but passes through here as well);
* ``in_flight`` - an intent was submitted; nothing else is proposed until the decision
  arrives (:meth:`DeviceMachine.report`);
* ``retry`` - the gate rejected or the write failed; the next proposal waits.

Intents are therefore emitted only when the level changes, never every tick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, unique
from typing import Final

from suncore.controller import reasons as rs
from suncore.controller.levels import LevelTable
from suncore.core.control import ControlSettings
from suncore.core.decisions import (
    Action,
    Decision,
    DecisionKind,
    Reason,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig

SWITCH_WINDOW: Final = timedelta(hours=1)
WRITE_NO_CHANGE: Final = "safety.write_no_change"
PROPOSED_RETRY_FACTOR: Final = 1.0


@unique
class Phase(StrEnum):
    IDLE = "idle"
    STEADY = "steady"
    PENDING_UP = "pending_up"
    PENDING_DOWN = "pending_down"
    IN_FLIGHT = "in_flight"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class Transition:
    """A level change the allocator wants; ``forced`` skips the hysteresis delay."""

    level: int
    reason: Reason
    forced: bool = False
    deficit: bool = False
    need_w: float = 0.0
    """Added power of a rise (checked against the free surplus before submitting)."""


@dataclass
class DeviceMachine:
    """The controller's memory about one device."""

    device: DeviceConfig
    settings: ControlSettings
    level: int = 0
    phase: Phase = Phase.IDLE
    table: LevelTable | None = None
    synced: bool = False
    pending_level: int | None = None
    pending_since: datetime | None = None
    in_flight_level: int | None = None
    in_flight_id: str | None = None
    in_flight_since: datetime | None = None
    retry_until: datetime | None = None
    applied_kind: DecisionKind | None = None
    applied_ts: datetime | None = None
    switch_times: list[datetime] = field(default_factory=list)
    last_on_ts: datetime | None = None
    last_off_ts: datetime | None = None
    base_setpoint_c: float | None = None
    switched_on_by_us: bool = False
    reasons: tuple[Reason, ...] = ()
    since: datetime | None = None
    seq: int = 0

    # --- derived -------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """Whether SunCore allocates something to the device (level above the floor)."""
        return self.synced and self.level > 0 and self.table is not None

    @property
    def hysteresis_w(self) -> float:
        h = self.device.hysteresis_w
        return h if h is not None else self.settings.default_hysteresis_w

    @property
    def hysteresis_delay_s(self) -> float:
        d = self.device.hysteresis_delay_s
        return float(d) if d is not None else self.settings.default_hysteresis_delay_s

    @property
    def settle_s(self) -> float:
        s = self.device.settle_s
        return float(s) if s is not None else self.settings.default_settle_s

    @property
    def max_switches_per_hour(self) -> int:
        m = self.device.max_switches_per_hour
        return m if m is not None else self.settings.default_max_switches_per_hour

    def current_action(self) -> Action | None:
        if self.table is None:
            return None
        return self.table.level(self.level).action

    def switches_in_window(self, now: datetime) -> int:
        start = now - SWITCH_WINDOW
        self.switch_times = [t for t in self.switch_times if t > start]
        return sum(1 for t in self.switch_times if t <= now)

    # --- transitions -----------------------------------------------------------------

    def reset(self, now: datetime, reason: Reason) -> None:
        """Leave the device alone: forget the level, keep the switch history."""
        if self.phase is not Phase.IDLE or self.reasons != (reason,):
            self.since = now
        self.phase = Phase.IDLE
        self.synced = False
        self.level = 0
        self.table = None
        self.pending_level = None
        self.pending_since = None
        self.in_flight_level = None
        self.in_flight_id = None
        self.in_flight_since = None
        self.applied_kind = None
        self.base_setpoint_c = None
        self.switched_on_by_us = False
        self.reasons = (reason,)

    def sync(self, now: datetime, table: LevelTable, observed: int | None) -> None:
        """Adopt the observed level when the device becomes controllable."""
        self.table = table
        if not self.synced:
            self.level = observed if observed is not None else 0
            self.synced = True
            self.phase = Phase.STEADY
            self.since = now
            self.reasons = (rs.plain(rs.IDLE),)
            self.applied_kind = None

    def steady(self, now: datetime, reason: Reason) -> None:
        if self.pending_level is not None or self.phase is not Phase.STEADY:
            self.since = now
        self.pending_level = None
        self.pending_since = None
        if self.phase in (Phase.STEADY, Phase.PENDING_UP, Phase.PENDING_DOWN):
            self.phase = Phase.STEADY
        self.reasons = (reason,)

    def want(self, now: datetime, transition: Transition) -> float:
        """Register a wanted level; returns the remaining delay in seconds (0 = due)."""
        if self.pending_level != transition.level:
            self.pending_level = transition.level
            self.pending_since = now
            self.since = now
        if self.phase in (Phase.STEADY, Phase.PENDING_UP, Phase.PENDING_DOWN):
            self.phase = Phase.PENDING_UP if transition.level > self.level else Phase.PENDING_DOWN
        if transition.forced:
            return 0.0
        assert self.pending_since is not None
        elapsed = (now - self.pending_since).total_seconds()
        return max(0.0, self.hysteresis_delay_s - elapsed)

    def blocked_by_protection(self, now: datetime, target: int) -> Reason | None:
        """Min run / min pause / switches per hour for a move to ``target``."""
        if self.table is None or not self.table.switchable:
            return None
        before = self.table.level(self.level).on
        after = self.table.level(target).on
        if before == after:
            return None
        if after:
            if self.last_off_ts is not None and self.device.min_pause_s > 0:
                remaining = self.device.min_pause_s - (now - self.last_off_ts).total_seconds()
                if remaining > 0:
                    return rs.min_pause_wait(remaining)
        elif self.last_on_ts is not None and self.device.min_run_s > 0:
            remaining = self.device.min_run_s - (now - self.last_on_ts).total_seconds()
            if remaining > 0:
                return rs.min_run_wait(remaining)
        limit = self.max_switches_per_hour
        if self.switches_in_window(now) >= limit:
            return rs.switch_limit(limit)
        return None

    def not_ready(self, now: datetime) -> Reason | None:
        """Why no intent may be submitted right now (in flight, retry cool-down)."""
        if self.in_flight_id is not None:
            if (
                self.in_flight_since is not None
                and (now - self.in_flight_since).total_seconds() > self.in_flight_timeout_s
            ):
                # The decision never came back (a crashed submit task): give up on it.
                self.in_flight_id = None
                self.in_flight_level = None
                self.in_flight_since = None
                self.phase = Phase.RETRY
                self.retry_until = now + timedelta(seconds=self.hysteresis_delay_s)
            else:
                return rs.plain(rs.IN_FLIGHT)
        if self.retry_until is not None:
            if now < self.retry_until:
                return rs.plain(rs.RETRY_WAIT)
            self.retry_until = None
            if self.phase is Phase.RETRY:
                self.phase = Phase.STEADY
        return None

    @property
    def in_flight_timeout_s(self) -> float:
        return self.settle_s + self.settings.intent_max_age_s + 2 * self.settings.tick_s

    def submitted(self, now: datetime, intent_id: str, level: int) -> None:
        self.in_flight_id = intent_id
        self.in_flight_level = level
        self.in_flight_since = now
        self.phase = Phase.IN_FLIGHT
        self.since = now

    def next_intent_id(self, now: datetime) -> str:
        self.seq += 1
        return f"ctl:{self.device.id}:{int(now.timestamp())}:{self.seq}"

    def report(self, decision: Decision) -> bool:
        """Feed back the gate's decision for the intent in flight; ``True`` if it matched."""
        if decision.intent.id != self.in_flight_id or self.in_flight_level is None:
            return False
        now = decision.ts
        target = self.in_flight_level
        self.in_flight_id = None
        self.in_flight_level = None
        self.in_flight_since = None
        kind = decision.kind
        applied = kind in (DecisionKind.ACCEPTED, DecisionKind.SHADOW, DecisionKind.PROPOSED)
        if kind is DecisionKind.REJECTED and any(
            r.key == WRITE_NO_CHANGE for r in decision.reasons
        ):
            applied = True  # the device already is what we asked for
        if applied:
            self._apply_level(now, target, decision.intent.action)
            self.applied_kind = kind
            self.applied_ts = now
            self.phase = Phase.STEADY
            self.pending_level = None
            self.pending_since = None
            self.reasons = decision.intent.reasons or self.reasons
            if kind is DecisionKind.PROPOSED:
                self.retry_until = now + timedelta(
                    seconds=self.settings.proposal_ttl_s * PROPOSED_RETRY_FACTOR
                )
        else:
            self.phase = Phase.RETRY
            self.retry_until = now + timedelta(seconds=max(self.hysteresis_delay_s, self.settle_s))
            self.reasons = decision.reasons or self.reasons
        self.since = now
        return True

    def _apply_level(self, now: datetime, target: int, action: Action) -> None:
        if self.table is not None and self.table.switchable:
            before = self.table.level(self.level).on
            after = self.table.level(target).on
            if before != after:
                self.switch_times.append(now)
                if after:
                    self.last_on_ts = now
                else:
                    self.last_off_ts = now
        if isinstance(action, SwitchAction):
            self.switched_on_by_us = action.on
        self.level = target


__all__ = ["DeviceMachine", "Phase", "Transition"]
