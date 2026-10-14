# SPDX-License-Identifier: Apache-2.0
"""Priority allocation of the surplus with hysteresis and deficit back-off (plan WP7).

Every tick the :class:`Allocator` walks the devices in priority order (lower value first)
with ``remaining = total available`` (:mod:`suncore.controller.surplus`) and decides a
*wanted level* for each (:mod:`suncore.controller.levels`):

* **headroom** = ``remaining - draw`` is what the device may add on top of what it
  already draws. A device rises to the highest level whose added need plus
  ``hysteresis_w`` fits into ``headroom - uncertainty`` (the site spread plus the device's
  own estimate uncertainty: counter-only devices get a wider band, BRIEF §4.5). Ramped
  kinds (climate shifts) rise one level at a time;
* it falls when ``headroom < -hysteresis_w`` (the median, not the lower bound, so cloud
  flicker alone never switches a running device off) to the highest level that covers
  the shortfall - ramped kinds one level;
* both after ``hysteresis_delay_s`` of the condition holding (:class:`DeviceMachine`);
* what a device keeps or is about to get is subtracted from ``remaining`` before the
  next device, also while its delay runs, so a lower priority never takes the surplus a
  higher one is waiting for.

Constraints that cap the level without delay: time windows and quiet hours
(``DeviceConfig.windows``, local time) and the plan band (``allowed``, ``max_power_w``;
Phase 4 fills it). The rules the gate would enforce anyway are mirrored so the intent is
not even proposed: min run/pause and switches per hour, no rise while the grid imports or
the battery discharges at or below its reserve, no rise with an unknown surplus.

**No grid import caused by SunCore** (plan §6): the *attributable deficit* is the part
of grid import plus battery discharge that the controlled devices explain
(``min(import + discharge, sum of their draw)``). The battery buffers it for
``deficit_delay_s`` (halved when an active device's estimate is poor) or until
``deficit_budget_wh`` is spent; at or below the battery reserve there is no buffering.
Then the lowest-priority active device backs off one level (climate: set-point toward
the base; steps/continuous: the level that covers the shortfall; on/off: off only after
``min_run_s`` - otherwise the next device is tried). After a back-off the delay restarts;
the budget keeps counting until the deficit ends.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Final

from suncore.controller import reasons as rs
from suncore.controller.hysteresis import DeviceMachine, Phase, Transition
from suncore.controller.levels import (
    LevelTable,
    Quantiser,
    build_levels,
    observed_level,
)
from suncore.controller.surplus import SiteSurplus
from suncore.core.control import ControlSettings, DeviceBand, DeviceState, Plan, PowerEstimate
from suncore.core.decisions import Decision, DecisionKind, Intent, Origin, Reason, SetpointAction
from suncore.core.devices import DeviceConfig, SetpointControl, TimeWindow
from suncore.core.signals import Quality

POOR_ESTIMATE_REL: Final = 0.5
"""An active device whose uncertainty exceeds this share of its draw halves the deficit delay."""
POOR_DELAY_FACTOR: Final = 0.5
MISMATCH_GRACE_TICKS: Final = 2
HVAC_OFF: Final = "off"
SHIFTABLE_MODES: Final = frozenset({"heat", "cool"})


# --- windows -----------------------------------------------------------------------


def _inside(window: TimeWindow, local: datetime) -> bool:
    day = local.weekday()
    t = local.time().replace(tzinfo=None)
    if window.start < window.end:
        return day in window.days and window.start <= t < window.end
    # Wraps over midnight: the part before midnight belongs to the start day.
    previous = (day - 1) % 7
    return (day in window.days and t >= window.start) or (
        previous in window.days and t < window.end
    )


def window_reason(windows: Sequence[TimeWindow], local: datetime) -> Reason | None:
    """Why the device may not be allocated now (``None`` = allowed)."""
    allows = [w for w in windows if w.mode == "allow"]
    blocks = [w for w in windows if w.mode == "block"]
    if any(_inside(w, local) for w in blocks):
        return rs.plain(rs.QUIET_HOURS)
    if allows and not any(_inside(w, local) for w in allows):
        return rs.plain(rs.OUTSIDE_WINDOW)
    return None


# --- pure allocation (property-tested) -----------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """One device as the pure allocator sees it."""

    device_id: str
    table: LevelTable
    level: int
    draw_w: float
    """Current draw counted in the total (0 below the floor)."""
    current_lower_w: float
    """Lower bound of the draw the device is known to have (for the added need)."""
    own_uncertainty_w: float
    hysteresis_w: float
    max_level: int
    """Cap from windows/plan (``top`` when unconstrained)."""
    cap_reason: Reason | None = None


@dataclass(frozen=True, slots=True)
class Wanted:
    device_id: str
    level: int
    reserved_w: float
    reason: Reason
    forced: bool = False
    """A cap (window, plan) drives the change: no hysteresis delay."""


def allocate_levels(
    candidates: Iterable[Candidate], total_w: float, uncertainty_w: float
) -> tuple[Wanted, ...]:
    """Wanted level per device in the given (priority) order; pure, no state.

    Invariants (``tests/controller/test_allocation.py``): the sum of added needs never
    exceeds ``total - uncertainty`` minus what runs; a device rises only if every device
    before it got the level it could get.
    """
    remaining = total_w
    out: list[Wanted] = []
    for c in candidates:
        table = c.table
        level = min(c.level, table.top)
        cap = max(0, min(c.max_level, table.top))
        headroom = remaining - c.draw_w
        forced = False
        if level > cap:
            wanted = cap
            reason = c.cap_reason or rs.plain(rs.PLAN_BLOCKED)
            forced = True
        elif headroom < -c.hysteresis_w:
            wanted = _fall_level(table, level, c.draw_w, -headroom)
            reason = rs.surplus_gone(headroom)
        else:
            usable = headroom - uncertainty_w - c.own_uncertainty_w
            wanted = _rise_level(table, level, cap, c.current_lower_w, usable, c.hysteresis_w)
            if wanted > level:
                reason = rs.surplus_on(table.level(wanted).power_w - c.current_lower_w, headroom)
            elif level > 0:
                reason = rs.surplus_on(c.draw_w, headroom)
            elif cap == 0 and c.cap_reason is not None:
                reason = c.cap_reason
            else:
                need = table.need_w(level, level + 1) if table.top > level else 0.0
                reason = rs.no_surplus(usable, need)
        if wanted > level:
            reserved = c.draw_w + (table.level(wanted).power_w - c.current_lower_w)
        elif wanted < level:
            reserved = max(
                0.0, c.draw_w - (table.level(level).power_w - table.level(wanted).power_w)
            )
        else:
            reserved = c.draw_w
        remaining -= reserved
        out.append(Wanted(c.device_id, wanted, reserved, reason, forced))
    return tuple(out)


def _rise_level(  # noqa: PLR0917
    table: LevelTable, level: int, cap: int, current_lower_w: float, usable_w: float, hyst_w: float
) -> int:
    best = level
    top = min(cap, table.top)
    for index in range(level + 1, top + 1):
        need = table.level(index).power_w - current_lower_w
        if need + hyst_w <= usable_w:
            best = index
        if table.ramped:
            break
    return best


def _fall_level(table: LevelTable, level: int, draw_w: float, shortfall_w: float) -> int:
    if level <= 0:
        return 0
    if table.ramped:
        return level - 1
    target_power = draw_w - shortfall_w
    for index in range(level - 1, -1, -1):
        if table.level(index).power_w <= target_power:
            return index
    return 0


# --- stateful allocator ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceView:
    """What the API and the "Why?" view show per device."""

    device_id: str
    phase: Phase
    level: int
    top: int
    power_w: float
    pending_level: int | None
    base_setpoint_c: float | None
    reasons: tuple[Reason, ...]
    since: datetime | None


@dataclass(frozen=True, slots=True)
class DeviationEvent:
    """Reality left the controller's assumption (Phase 4 re-plans on these)."""

    kind: str
    device_id: str
    ts: datetime
    reason: Reason


@dataclass(frozen=True, slots=True)
class AllocationInputs:
    states: Mapping[str, DeviceState]
    estimates: Mapping[str, PowerEstimate]
    blocked: Mapping[str, Reason]
    plan: Plan
    reserve_soc_pct: float | None


@dataclass
class DeficitTracker:
    """Duration and energy of the attributable deficit (plan §6)."""

    since: datetime | None = None
    wh: float = 0.0
    last_ts: datetime | None = None
    last_w: float = 0.0

    def update(self, now: datetime, attributable_w: float) -> None:
        if attributable_w <= 0:
            self.since = None
            self.wh = 0.0
            self.last_ts = now
            self.last_w = 0.0
            return
        if self.since is None:
            self.since = now
        if self.last_ts is not None and self.last_w > 0:
            dt_h = max(0.0, (now - self.last_ts).total_seconds()) / 3600.0
            self.wh += self.last_w * dt_h
        self.last_ts = now
        self.last_w = attributable_w

    def restart(self, now: datetime) -> None:
        self.since = now


@dataclass
class TickOutcome:
    intents: list[Intent] = field(default_factory=list)
    views: dict[str, DeviceView] = field(default_factory=dict)
    deviations: list[DeviationEvent] = field(default_factory=list)
    allocated_draw_w: float = 0.0
    total_w: float | None = None
    attributable_deficit_w: float = 0.0


def _is_on(state: DeviceState | None) -> bool:
    if state is None or not state.known:
        return False
    if state.on is not None:
        return state.on
    if state.mode is not None:
        return state.mode != HVAC_OFF
    if state.step is not None:
        return state.step > 0
    return state.value is not None and state.value > 0


class Allocator:
    """Owns the per-device machines and runs the allocation each tick."""

    def __init__(self, settings: ControlSettings, quantiser: Quantiser, *, tz: tzinfo) -> None:
        self._settings = settings
        self._quantiser = quantiser
        self._tz = tz
        self._machines: dict[str, DeviceMachine] = {}
        self._order: list[str] = []
        self._deficit = DeficitTracker()

    # --- devices ---------------------------------------------------------------------

    def set_devices(self, devices: Iterable[DeviceConfig]) -> None:
        ordered = sorted(devices, key=lambda d: (d.priority, d.id))
        keep: dict[str, DeviceMachine] = {}
        for device in ordered:
            machine = self._machines.get(device.id)
            if machine is None or machine.device != device:
                fresh = DeviceMachine(device, self._settings)
                if machine is not None:
                    # Keep the protection history across a configuration edit.
                    fresh.switch_times = list(machine.switch_times)
                    fresh.last_on_ts = machine.last_on_ts
                    fresh.last_off_ts = machine.last_off_ts
                machine = fresh
            keep[device.id] = machine
        self._machines = keep
        self._order = [d.id for d in ordered]

    @property
    def machines(self) -> Mapping[str, DeviceMachine]:
        return self._machines

    def machine(self, device_id: str) -> DeviceMachine | None:
        return self._machines.get(device_id)

    @property
    def deficit(self) -> DeficitTracker:
        return self._deficit

    def report(self, decision: Decision) -> bool:
        machine = self._machines.get(decision.intent.asset_id)
        return machine is not None and machine.report(decision)

    # --- tick ----------------------------------------------------------------------------

    def run(self, now: datetime, site: SiteSurplus, inputs: AllocationInputs) -> TickOutcome:
        out = TickOutcome()
        local = now.astimezone(self._tz)
        machines = [self._machines[i] for i in self._order]

        # 1. Prepare every device: controllable or not, level table, sync.
        for m in machines:
            self._prepare(now, m, inputs, out)

        # 2. Total available = free + draw of what we allocated.
        active = [m for m in machines if m.active]
        draws = {m.device.id: self._draw_w(m, inputs) for m in active}
        out.allocated_draw_w = sum(draws.values())
        total = site.total_w(self._settings.reserve_w, out.allocated_draw_w)
        out.total_w = total

        # 3. Deficit back-off (plan §6).
        backoff = self._deficit_backoff(now, site, active, draws, inputs)

        # 4. Allocation by priority.
        wanted_by_id: dict[str, Wanted] = {}
        if total is not None:
            candidates = [self._candidate(m, inputs, draws, local) for m in machines if m.synced]
            wanted_by_id = {
                w.device_id: w for w in allocate_levels(candidates, total, site.uncertainty_w)
            }

        # 5. Transitions, delays, protection, intents.
        for m in machines:
            if m.synced:
                transition = self._transition(m, wanted_by_id.get(m.device.id), backoff)
                self._drive(now, m, transition, site, inputs, out)
            out.views[m.device.id] = self._view(m)
        return out

    # --- steps -----------------------------------------------------------------------------

    def _prepare(
        self, now: datetime, m: DeviceMachine, inputs: AllocationInputs, out: TickOutcome
    ) -> None:
        device = m.device
        blocked = inputs.blocked.get(device.id)
        if blocked is not None:
            m.reset(now, blocked)
            return
        state = inputs.states.get(device.id)
        if state is None or not state.known:
            m.reset(now, rs.plain(rs.STATE_UNKNOWN))
            return
        estimate = inputs.estimates.get(device.id)
        base = m.base_setpoint_c if m.synced and m.level > 0 else None
        table = build_levels(
            device,
            state,
            estimate,
            self._settings,
            self._quantiser,
            base_setpoint_c=base,
            switched_on_by_us=m.switched_on_by_us if m.synced else False,
        )
        if table is None:
            m.reset(now, self._uncontrollable_reason(device, state))
            return
        observed = observed_level(table, device, state)
        was_synced = m.synced
        m.sync(now, table, observed)
        m.base_setpoint_c = table.base_setpoint_c
        if (
            was_synced
            and observed is not None
            and observed != m.level
            and m.applied_kind is DecisionKind.ACCEPTED
            and m.applied_ts is not None
            and m.in_flight_id is None
            and (now - m.applied_ts).total_seconds()
            > m.settle_s + MISMATCH_GRACE_TICKS * self._settings.tick_s
        ):
            # Reality disagrees with a confirmed write: adopt it and tell the planner.
            out.deviations.append(
                DeviationEvent("state_mismatch", device.id, now, rs.plain(rs.STATE_UNKNOWN))
            )
            m.level = observed
            m.applied_kind = None
            m.pending_level = None
            m.pending_since = None

    @staticmethod
    def _uncontrollable_reason(device: DeviceConfig, state: DeviceState) -> Reason:
        if isinstance(device.control, SetpointControl):
            if not _is_on(state) and not device.allow_switch_on:
                return rs.plain(rs.CLIMATE_OFF)
            if _is_on(state) and state.mode is not None and state.mode not in SHIFTABLE_MODES:
                return rs.climate_mode_unsupported(state.mode)
        return rs.plain(rs.POWER_UNKNOWN)

    def _draw_w(self, m: DeviceMachine, inputs: AllocationInputs) -> float:
        """Draw counted in the total: the estimate when the device is seen on, else the level.

        A virtual level (shadow, assisted) counts its expected power so the "would have"
        allocation behaves like the real one; a confirmed write whose device is now off
        counts nothing (the mismatch rule re-synchronises it).
        """
        assert m.table is not None
        estimate = inputs.estimates.get(m.device.id)
        virtual = m.table.level(m.level).power_w
        if estimate is None or estimate.quality not in (Quality.OK, Quality.STALE):
            return virtual
        if _is_on(inputs.states.get(m.device.id)):
            return estimate.value_w
        return 0.0 if m.applied_kind is DecisionKind.ACCEPTED else virtual

    def _candidate(
        self,
        m: DeviceMachine,
        inputs: AllocationInputs,
        draws: Mapping[str, float],
        local: datetime,
    ) -> Candidate:
        assert m.table is not None
        device = m.device
        estimate = inputs.estimates.get(device.id)
        draw = draws.get(device.id, 0.0)
        known = estimate is not None and estimate.quality in (Quality.OK, Quality.STALE)
        own_unc = estimate.uncertainty_w if known and estimate is not None else 0.0
        if m.level > 0 and known and estimate is not None and draw == estimate.value_w:
            current_lower = estimate.lower_w
        elif (
            isinstance(device.control, SetpointControl)
            and m.level == 0
            and known
            and estimate is not None
            and _is_on(inputs.states.get(device.id))
        ):
            # A running unit at its base: a shift adds nominal minus what it draws now.
            current_lower = estimate.lower_w
        else:
            current_lower = m.table.level(m.level).power_w
        cap, cap_reason = self._cap(m, inputs.plan.band_for(device.id, local), local)
        return Candidate(
            device_id=device.id,
            table=m.table,
            level=m.level,
            draw_w=draw,
            current_lower_w=current_lower,
            own_uncertainty_w=own_unc,
            hysteresis_w=m.hysteresis_w,
            max_level=cap,
            cap_reason=cap_reason,
        )

    @staticmethod
    def _cap(m: DeviceMachine, band: DeviceBand, local: datetime) -> tuple[int, Reason | None]:
        assert m.table is not None
        window = window_reason(m.device.windows, local)
        if window is not None:
            return 0, window
        if not band.allowed:
            return 0, rs.plain(rs.PLAN_BLOCKED)
        if band.max_power_w is not None:
            cap = max(
                (lv.index for lv in m.table.levels if lv.power_w <= band.max_power_w), default=0
            )
            return cap, rs.plain(rs.PLAN_BLOCKED)
        return m.table.top, None

    @staticmethod
    def _transition(
        m: DeviceMachine, wanted: Wanted | None, backoff: Mapping[str, Transition]
    ) -> Transition | None:
        assert m.table is not None
        forced = backoff.get(m.device.id)
        if forced is not None:
            return forced
        if wanted is None:
            return None
        if wanted.level == m.level:
            return Transition(m.level, wanted.reason)
        reason = wanted.reason
        level = m.table.level(wanted.level)
        if isinstance(level.action, SetpointAction) and m.base_setpoint_c is not None:
            assert level.setpoint_c is not None
            reason = (
                rs.setpoint_base(level.setpoint_c)
                if wanted.level == 0
                else rs.setpoint_shift(level.setpoint_c - m.base_setpoint_c, level.setpoint_c)
            )
        elif (
            isinstance(m.device.control, SetpointControl)
            and wanted.level > m.level
            and level.setpoint_c is not None
            and wanted.level == 1
        ):
            reason = rs.plain(rs.SWITCH_ON)
        return Transition(wanted.level, reason, forced=wanted.forced)

    def _drive(  # noqa: PLR0911, PLR0917 - one return per outcome
        self,
        now: datetime,
        m: DeviceMachine,
        transition: Transition | None,
        site: SiteSurplus,
        inputs: AllocationInputs,
        out: TickOutcome,
    ) -> None:
        assert m.table is not None
        if transition is None:
            m.steady(now, rs.plain(rs.SURPLUS_UNKNOWN))
            m.not_ready(now)
            return
        if transition.level == m.level:
            m.steady(now, transition.reason)
            m.not_ready(now)  # expire a retry cool-down even without a wish
            return
        remaining = m.want(now, transition)
        if remaining > 0:
            m.reasons = (transition.reason, rs.waiting_delay(remaining))
            return
        blocked = m.blocked_by_protection(now, transition.level)
        if blocked is not None:
            m.reasons = (transition.reason, blocked)
            return
        if transition.level > m.level:
            guard = self._rise_guard(site, inputs)
            if guard is not None:
                m.reasons = (transition.reason, guard)
                return
        not_ready = m.not_ready(now)
        if not_ready is not None:
            m.reasons = (transition.reason, not_ready)
            return
        action = m.table.level(transition.level).action
        if action is None:  # pragma: no cover - every level has an action today
            m.steady(now, transition.reason)
            return
        intent = Intent(
            id=m.next_intent_id(now),
            ts=now,
            asset_id=m.device.id,
            action=action,
            reasons=(transition.reason,),
            origin=Origin.CONTROLLER,
        )
        m.submitted(now, intent.id, transition.level)
        m.reasons = (transition.reason,)
        out.intents.append(intent)
        if transition.deficit:
            out.deviations.append(
                DeviationEvent("deficit_backoff", m.device.id, now, transition.reason)
            )

    @staticmethod
    def _rise_guard(site: SiteSurplus, inputs: AllocationInputs) -> Reason | None:
        """The gate's grid and battery guards, mirrored so a rise is not proposed in vain."""
        if not site.known:
            return rs.plain(rs.SURPLUS_UNKNOWN)
        if site.grid_w is not None and site.grid_w > 0:
            return rs.grid_importing(site.grid_w)
        if _at_reserve(site, inputs.reserve_soc_pct):
            assert site.soc_pct is not None
            assert inputs.reserve_soc_pct is not None
            return rs.battery_reserve(site.soc_pct, inputs.reserve_soc_pct)
        return None

    # --- deficit -----------------------------------------------------------------------------

    def _deficit_backoff(
        self,
        now: datetime,
        site: SiteSurplus,
        active: Sequence[DeviceMachine],
        draws: Mapping[str, float],
        inputs: AllocationInputs,
    ) -> dict[str, Transition]:
        total_draw = sum(draws.values())
        deficit = max(0.0, -site.site_w) if site.site_w is not None else 0.0
        attributable = min(deficit, total_draw) if total_draw > 0 else 0.0
        self._deficit.update(now, attributable)
        if attributable <= 0 or self._deficit.since is None:
            return {}
        elapsed = (now - self._deficit.since).total_seconds()
        delay = self._settings.deficit_delay_s
        if any(self._poor_estimate(m, inputs) for m in active):
            delay *= POOR_DELAY_FACTOR
        reserve = inputs.reserve_soc_pct
        if _at_reserve(site, reserve):
            reason = rs.battery_reserve(site.soc_pct or 0.0, reserve or 0.0)
        elif self._deficit.wh >= self._settings.deficit_budget_wh:
            reason = rs.deficit_budget(self._deficit.wh)
        elif elapsed >= delay:
            reason = rs.deficit_backoff(attributable, elapsed)
        else:
            return {}
        for m in reversed(active):  # lowest priority first
            assert m.table is not None
            if m.in_flight_id is not None:
                continue
            target = min(
                m.level - 1,
                _fall_level(m.table, m.level, draws.get(m.device.id, 0.0), attributable),
            )
            if target < 0:
                continue
            protection = m.blocked_by_protection(now, target)
            if protection is not None:
                m.reasons = (reason, protection)
                continue
            self._deficit.restart(now)
            return {m.device.id: Transition(target, reason, forced=True, deficit=True)}
        return {}

    @staticmethod
    def _poor_estimate(m: DeviceMachine, inputs: AllocationInputs) -> bool:
        estimate = inputs.estimates.get(m.device.id)
        if estimate is None or estimate.quality is not Quality.OK:
            return True
        return estimate.uncertainty_w > POOR_ESTIMATE_REL * max(estimate.value_w, 1.0)

    # --- views -------------------------------------------------------------------------------

    @staticmethod
    def _view(m: DeviceMachine) -> DeviceView:
        top = m.table.top if m.table is not None else 0
        power = m.table.level(m.level).power_w if m.table is not None else 0.0
        return DeviceView(
            device_id=m.device.id,
            phase=m.phase,
            level=m.level,
            top=top,
            power_w=power,
            pending_level=m.pending_level,
            base_setpoint_c=m.base_setpoint_c,
            reasons=m.reasons,
            since=m.since,
        )


def _at_reserve(site: SiteSurplus, reserve_soc_pct: float | None) -> bool:
    """The battery discharges at or below its reserve: no buffering, no added load."""
    return (
        reserve_soc_pct is not None
        and site.soc_pct is not None
        and site.battery_w is not None
        and site.battery_w < 0
        and site.soc_pct <= reserve_soc_pct
    )


__all__ = [
    "AllocationInputs",
    "Allocator",
    "Candidate",
    "DeficitTracker",
    "DeviationEvent",
    "DeviceView",
    "TickOutcome",
    "Wanted",
    "allocate_levels",
    "window_reason",
]
