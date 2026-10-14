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
the battery discharges at or below its reserve, no rise with an unknown or not-``OK``
surplus, and a rise only when its added demand *as the gate counts it*
(``expected - estimate.lower``) fits the free surplus minus its uncertainty that the
rises before it in the same tick left (the gate judges every intent of a tick against
the same snapshot). A device whose intent is in flight keeps its level and reservation.

**Draws.** Every controllable device reports a :class:`DeviceDraw`: the real draw (from
its estimate; 0 when seen off) enters the smoothed available sample; the committed draw
is what the allocation and the free surplus count - at least the level's expected power
while a change is not yet visible (settle time, an estimate older than the change, an
intent in flight) and for a virtual level (shadow, assisted).

**No grid import caused by SunCore** (plan §6): the *attributable deficit* is what the
devices are counted to draw beyond what is available in the newest sample, capped at the
committed draw of the devices above their floor. The battery buffers it for
``deficit_delay_s`` (halved when an active device's estimate is poor) or until
``deficit_budget_wh`` is spent (the energy is kept until the deficit has been absent for
``deficit_delay_s``, so a flickering deficit still uses it up); at or below the battery
reserve, and above the configured import limit, there is no buffering. Then the
lowest-priority active device backs off one level (climate: set-point toward the base;
steps/continuous: the level that covers the shortfall; on/off: off only after
``min_run_s`` - otherwise the next device is tried). The next step waits for the backed-off
device's settle time plus a tick, and the delay restarts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
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
    hold: bool = False
    """Keep the level and reserve the draw (an intent is in flight)."""
    gate_lower_w: float | None = None
    """The current draw as the gate's grid invariant counts it (``power_delta_w``:
    the estimate's lower bound); ``None`` = ``current_lower_w``."""


@dataclass(frozen=True, slots=True)
class Wanted:
    device_id: str
    level: int
    reserved_w: float
    reason: Reason
    forced: bool = False
    """A cap (window, plan) drives the change: no hysteresis delay."""
    need_w: float = 0.0
    """Added power of a rise (what the gate's grid invariant will compare)."""


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
        if c.hold:
            remaining -= c.draw_w
            out.append(Wanted(c.device_id, level, c.draw_w, rs.plain(rs.IN_FLIGHT)))
            continue
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
        need = 0.0
        if wanted > level:
            power = table.level(wanted).power_w
            added = max(0.0, power - c.current_lower_w)
            reserved = c.draw_w + added
            gate_lower = c.current_lower_w if c.gate_lower_w is None else c.gate_lower_w
            need = max(added, power - gate_lower)
        elif wanted < level:
            reserved = max(
                0.0, c.draw_w - (table.level(level).power_w - table.level(wanted).power_w)
            )
        else:
            reserved = c.draw_w
        remaining -= reserved
        out.append(Wanted(c.device_id, wanted, reserved, reason, forced, need))
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
    """Expected power of the level SunCore considers applied."""
    committed_w: float
    """Draw the controller counts for the device this tick (see :class:`DeviceDraw`)."""
    pending_level: int | None
    base_setpoint_c: float | None
    reasons: tuple[Reason, ...]
    since: datetime | None


@dataclass(frozen=True, slots=True)
class DeviationEvent:
    """Reality left the controller's assumption (Phase 4 re-plans on these).

    ``kind``: ``state_mismatch`` (a confirmed write is no longer what the device shows) or
    ``deficit_backoff`` (controlled devices caused import or battery discharge).
    """

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
    import_limit_w: float | None = None


@dataclass(frozen=True, slots=True)
class DeviceDraw:
    """How much a controllable device draws, as the controller counts it.

    ``real_w`` is the measured/estimated draw (0 when the device is seen off; ``None``
    without a usable estimate) - it enters the smoothed available sample. ``committed_w``
    is what the allocation and the free surplus count: the real draw, except that a change
    not yet visible in the estimate (inside ``settle_s`` + one tick, or an estimate older
    than the change, or an intent in flight) and a virtual level (shadow, assisted) count
    at least the level's expected power. ``from_estimate`` tells whether ``committed_w``
    is the estimate (its lower bound is then what a rise adds to).
    """

    real_w: float | None
    committed_w: float
    from_estimate: bool


@dataclass
class DeficitTracker:
    """Duration and energy of the attributable deficit (plan §6).

    ``since`` is the start of the current uninterrupted deficit; ``wh`` the battery/grid
    energy the deficit drew, kept until the deficit has been absent for ``reset_after_s``
    (so a flickering deficit still exhausts the budget). ``hold_until`` spaces the
    back-off steps by the backed-off device's settle time.
    """

    since: datetime | None = None
    wh: float = 0.0
    last_ts: datetime | None = None
    last_w: float = 0.0
    clear_since: datetime | None = None
    hold_until: datetime | None = None

    def update(self, now: datetime, attributable_w: float, reset_after_s: float) -> None:
        if self.last_ts is not None and self.last_w > 0 and now > self.last_ts:
            self.wh += self.last_w * (now - self.last_ts).total_seconds() / 3600.0
        self.last_ts = now
        self.last_w = max(0.0, attributable_w)
        if attributable_w > 0:
            if self.since is None:
                self.since = now
            self.clear_since = None
            return
        self.since = None
        if self.clear_since is None:
            self.clear_since = now
        if (now - self.clear_since).total_seconds() >= reset_after_s:
            self.wh = 0.0

    def restart(self, now: datetime, hold_s: float) -> None:
        self.since = now
        self.hold_until = now + timedelta(seconds=hold_s)

    def holding(self, now: datetime) -> bool:
        return self.hold_until is not None and now < self.hold_until


@dataclass
class TickOutcome:
    intents: list[Intent] = field(default_factory=list)
    views: dict[str, DeviceView] = field(default_factory=dict)
    deviations: list[DeviationEvent] = field(default_factory=list)
    draws: dict[str, DeviceDraw] = field(default_factory=dict)
    """Per controllable (synced) device."""
    total_w: float | None = None
    free_w: float | None = None
    attributable_deficit_w: float = 0.0

    @property
    def controlled_draw(self) -> dict[str, float]:
        """Real draw per device with a usable estimate (the members of the available sample)."""
        return {k: d.real_w for k, d in self.draws.items() if d.real_w is not None}

    @property
    def committed_w(self) -> float:
        return sum(d.committed_w for d in self.draws.values())

    @property
    def allocated_draw_w(self) -> float:
        """Committed draw of the devices SunCore drives above their floor."""
        return sum(
            d.committed_w
            for k, d in self.draws.items()
            if (v := self.views.get(k)) is not None and v.level > 0
        )


@dataclass
class _Budget:
    """Free surplus (lower bound) still unclaimed by the rises submitted in this tick."""

    w: float


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


def _known(estimate: PowerEstimate | None) -> bool:
    return estimate is not None and estimate.quality in (Quality.OK, Quality.STALE)


class Allocator:
    """Owns the per-device machines and runs the allocation each tick.

    A tick is two calls (``Controller.step`` makes them): :meth:`prepare` (devices,
    draws - the surplus tracker needs the real draw before it can smooth) and :meth:`run`
    (deficit back-off, allocation, intents).
    """

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

    def prepare(self, now: datetime, inputs: AllocationInputs) -> TickOutcome:
        """Synchronise every machine and count the draws (first half of a tick)."""
        out = TickOutcome()
        for m in self._ordered():
            self._prepare(now, m, inputs, out)
            if m.synced:
                out.draws[m.device.id] = self._draw(now, m, inputs)
        return out

    def run(
        self, now: datetime, site: SiteSurplus, inputs: AllocationInputs, out: TickOutcome
    ) -> TickOutcome:
        """Back-off, allocation and intents (second half of a tick, after :meth:`prepare`)."""
        local = now.astimezone(self._tz)
        machines = self._ordered()
        synced = [m for m in machines if m.synced]
        committed = {k: d.committed_w for k, d in out.draws.items()}
        reserve = self._settings.reserve_w
        out.total_w = site.total_w(reserve)
        out.free_w = site.free_w(reserve, sum(committed.values()))

        backoff = self._deficit_backoff(now, site, synced, committed, inputs, out)

        wanted_by_id: dict[str, Wanted] = {}
        if out.total_w is not None:
            candidates = [self._candidate(m, inputs, out.draws, local) for m in synced]
            wanted_by_id = {
                w.device_id: w for w in allocate_levels(candidates, out.total_w, site.uncertainty_w)
            }

        budget = _Budget(
            0.0 if out.free_w is None else max(0.0, max(0.0, out.free_w) - site.uncertainty_w)
        )
        for m in machines:
            if m.synced:
                transition = self._transition(m, wanted_by_id.get(m.device.id), backoff)
                self._drive(now, m, transition, site, inputs, budget, out)
            if (
                site.target_soc_pct is not None
                and site.soc_pct is not None
                and m.reasons
                and m.reasons[0].key == rs.NO_SURPLUS
            ):
                m.reasons = (*m.reasons, rs.battery_target(site.soc_pct, site.target_soc_pct))
            out.views[m.device.id] = self._view(m, out.draws.get(m.device.id))
        return out

    def _ordered(self) -> list[DeviceMachine]:
        return [self._machines[i] for i in self._order]

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
        if (
            m.table is not None
            and m.synced
            and m.in_flight_id is None
            and len(table.levels) != len(m.table.levels)
        ):
            # The table changed shape (climate switched on/off, quantiser): start over.
            m.reset(now, rs.plain(rs.IDLE))
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
            comfort = device.comfort
            sp = state.setpoint_c
            if comfort is not None and sp is not None and not comfort.min_c <= sp <= comfort.max_c:
                return rs.base_outside_comfort(sp, comfort.min_c, comfort.max_c)
        return rs.plain(rs.POWER_UNKNOWN)

    def _draw(self, now: datetime, m: DeviceMachine, inputs: AllocationInputs) -> DeviceDraw:
        assert m.table is not None
        estimate = inputs.estimates.get(m.device.id)
        virtual = m.table.level(m.level).power_w
        seen_on = _is_on(inputs.states.get(m.device.id))
        real: float | None = None
        if estimate is not None and _known(estimate):
            real = estimate.value_w if seen_on else 0.0
        if real is None or estimate is None:
            committed = virtual
        elif m.applied_kind is DecisionKind.ACCEPTED and m.applied_ts is not None:
            since = (now - m.applied_ts).total_seconds()
            unsettled = since <= m.settle_s + self._settings.tick_s or estimate.age_s >= since
            committed = max(real, virtual) if unsettled else real
        elif m.applied_kind in (DecisionKind.SHADOW, DecisionKind.PROPOSED):
            committed = max(real, virtual)
        else:
            committed = real  # adopted from the observed state
        from_estimate = real is not None and seen_on and committed == real
        if m.in_flight_level is not None:
            flight = m.table.level(m.in_flight_level).power_w
            if flight > committed:
                committed, from_estimate = flight, False
        return DeviceDraw(real_w=real, committed_w=committed, from_estimate=from_estimate)

    def _candidate(
        self,
        m: DeviceMachine,
        inputs: AllocationInputs,
        draws: Mapping[str, DeviceDraw],
        local: datetime,
    ) -> Candidate:
        assert m.table is not None
        device = m.device
        estimate = inputs.estimates.get(device.id)
        draw = draws[device.id]
        own_unc = estimate.uncertainty_w if estimate is not None and _known(estimate) else 0.0
        if draw.from_estimate and estimate is not None:
            current_lower = estimate.lower_w
        else:
            current_lower = draw.committed_w
        cap, cap_reason = self._cap(m, inputs.plan.band_for(device.id, local), local)
        return Candidate(
            device_id=device.id,
            table=m.table,
            level=m.level,
            draw_w=draw.committed_w,
            current_lower_w=current_lower,
            own_uncertainty_w=own_unc,
            hysteresis_w=m.hysteresis_w,
            max_level=cap,
            cap_reason=cap_reason,
            hold=m.in_flight_id is not None,
            gate_lower_w=self._gate_lower_w(m, estimate, inputs.states.get(device.id)),
        )

    @staticmethod
    def _gate_lower_w(
        m: DeviceMachine, estimate: PowerEstimate | None, state: DeviceState | None
    ) -> float:
        """What ``safety.invariants.power_delta_w`` subtracts from a rise's expected power."""
        if estimate is not None and _known(estimate):
            return estimate.lower_w
        if isinstance(m.device.control, SetpointControl):
            return 0.0
        nominal = m.device.power_model.nominal_w
        return nominal if _is_on(state) and nominal is not None else 0.0

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
            and wanted.level == 1
        ):
            reason = rs.plain(rs.SWITCH_ON)
        return Transition(wanted.level, reason, forced=wanted.forced, need_w=wanted.need_w)

    def _drive(  # noqa: PLR0911, PLR0917 - one return per outcome
        self,
        now: datetime,
        m: DeviceMachine,
        transition: Transition | None,
        site: SiteSurplus,
        inputs: AllocationInputs,
        budget: _Budget,
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
        rising = transition.level > m.level
        if rising:
            guard = self._rise_guard(site, inputs)
            if guard is None and transition.need_w > budget.w:
                # The surplus is promised to a device that has not backed off yet (or to
                # an earlier rise in this tick): the gate would see an import.
                guard = rs.no_surplus(budget.w, transition.need_w)
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
        if rising:
            budget.w -= transition.need_w
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
        if not site.trusted:
            return rs.plain(rs.SURPLUS_UNKNOWN)
        if site.grid_w is not None and site.grid_w > 0:
            return rs.grid_importing(site.grid_w)
        if _at_reserve(site, inputs.reserve_soc_pct):
            assert site.soc_pct is not None
            assert inputs.reserve_soc_pct is not None
            return rs.battery_reserve(site.soc_pct, inputs.reserve_soc_pct)
        return None

    # --- deficit -----------------------------------------------------------------------------

    def _deficit_backoff(  # noqa: PLR0917
        self,
        now: datetime,
        site: SiteSurplus,
        synced: Sequence[DeviceMachine],
        committed: Mapping[str, float],
        inputs: AllocationInputs,
        out: TickOutcome,
    ) -> dict[str, Transition]:
        """One back-off step when controlled devices cause import or discharge (plan §6).

        The deficit is measured on the newest (unsmoothed) sample: what the devices are
        counted to draw beyond what is available right now. It is attributable up to the
        committed draw of the devices above their floor.
        """
        active = [m for m in synced if m.level > 0]
        active_draw = sum(committed.get(m.device.id, 0.0) for m in active)
        attributable = 0.0
        if site.latest_available_w is not None and active_draw > 0:
            deficit = max(0.0, sum(committed.values()) - site.latest_available_w)
            attributable = min(deficit, active_draw)
        out.attributable_deficit_w = attributable
        delay = self._settings.deficit_delay_s
        if any(self._poor_estimate(m, inputs) for m in active):
            delay *= POOR_DELAY_FACTOR
        self._deficit.update(now, attributable, self._settings.deficit_delay_s)
        if attributable <= 0 or self._deficit.since is None:
            return {}
        elapsed = (now - self._deficit.since).total_seconds()
        reserve = inputs.reserve_soc_pct
        limit = inputs.import_limit_w
        if limit is not None and site.grid_w is not None and site.grid_w > limit:
            reason = rs.import_limit(site.grid_w, limit)
        elif _at_reserve(site, reserve):
            reason = rs.battery_reserve(site.soc_pct or 0.0, reserve or 0.0)
        elif self._deficit.wh >= self._settings.deficit_budget_wh:
            reason = rs.deficit_budget(self._deficit.wh)
        elif elapsed >= delay:
            reason = rs.deficit_backoff(attributable, elapsed)
        else:
            return {}
        if self._deficit.holding(now):
            return {}  # the previous step has not shown its effect yet
        for m in reversed(active):  # lowest priority first
            assert m.table is not None
            if m.in_flight_id is not None:
                continue
            target = min(
                m.level - 1,
                _fall_level(m.table, m.level, committed.get(m.device.id, 0.0), attributable),
            )
            protection = m.blocked_by_protection(now, target)
            if protection is not None:
                m.reasons = (reason, protection)
                continue
            self._deficit.restart(now, m.settle_s + self._settings.tick_s)
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
    def _view(m: DeviceMachine, draw: DeviceDraw | None) -> DeviceView:
        top = m.table.top if m.table is not None else 0
        power = m.table.level(m.level).power_w if m.table is not None else 0.0
        return DeviceView(
            device_id=m.device.id,
            phase=m.phase,
            level=m.level,
            top=top,
            power_w=power,
            committed_w=draw.committed_w if draw is not None else 0.0,
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
    "DeviceDraw",
    "DeviceView",
    "TickOutcome",
    "Wanted",
    "allocate_levels",
    "window_reason",
]
