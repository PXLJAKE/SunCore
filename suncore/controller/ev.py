# SPDX-License-Identifier: Apache-2.0
"""EV charging in the controller (plan WP9, ADR-0017 decisions 1, 3, 4; PR 6-I).

A charger (``ChargerControl``) is driven through the same level allocator as every other
continuous load - surplus, smoothing, hysteresis, min run/pause, priority, grid caps, deficit
back-off - with three additions made here:

* **Levels** (:func:`charger_levels`): level 0 = paused (``ChargeAction(current_a=0)``), then one
  level per current from ``min_current_a`` (the 6 A floor) to ``max_current_a`` in steps of 1 A
  (snapped to the entity's ``step`` by the driver's quantiser) on the active phases. The car's
  draw at ``I`` A is ``I x voltage x phases``.
* **Mode** (:meth:`EvMachine.directive`), per tick from the runtime state (``core.flex``):

  - ``off`` -> paused (cap 0); a vehicle known to be unplugged -> paused (E5);
  - ``pv`` -> surplus only (the Phase 3 rule);
  - ``min_pv`` -> a **floor** at the minimum current (the user-demanded floor of ADR-0017
    decision 4), the rest from surplus;
  - ``now`` -> a floor at the maximum current;
  - ``target`` -> a floor at the highest level within the fresh plan band's
    ``grid_power_max_w`` (the planner's cheapest windows), the rest from surplus.

  A floor is a rise without the surplus guard and without hysteresis delay, mirrored by the
  gate's E3 (:func:`suncore.safety.flex.ev_allowance_w`). It is offered only while the home
  battery cannot discharge into the car (no battery, or SunCore holds a discharge lock - the
  battery controller asks for one meanwhile, :attr:`EvDirective.lock_wanted`; E6). The deficit
  back-off never goes below the floor; the import-limit shedding and the grid caps (island,
  §14a, cool-down) do (they are hard limits, E4).
* **Fail-safe:** stale or unknown site inputs -> no increase and at most the minimum current
  (a charging car falls back to 6 A instead of keeping a high current on unknown inputs; the
  watchdog's site trip then releases it to the wallbox's own settings).
* **Phase switching** (plan §0 decision 6, :class:`EvMachine`): only with ``phase_switching``;
  3 -> 1 when the surplus the charger could use stays below ``3 x min_current x voltage -
  hysteresis`` for 5 min, 1 -> 3 when it stays above that value + 500 W for 5 min; never in
  ``now``; at most ``max_phase_switches_per_hour``. The charger is paused (cap 0), and after
  ``phase_switch_pause_s`` of pause the phase intent ``ChargeAction(0, phases=new)`` is sent;
  charging resumes ``phase_switch_pause_s`` after the change (the gate's E2 checks the same).

Deterministic, time injected, ``core`` only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal

from suncore.controller import reasons as rs
from suncore.controller.levels import Level, LevelTable, Quantiser
from suncore.core.control import DeviceBand, DeviceState
from suncore.core.decisions import (
    BatteryAction,
    ChargeAction,
    Decision,
    DecisionKind,
    Intent,
    Origin,
    Reason,
)
from suncore.core.devices import ChargerControl, DeviceConfig
from suncore.core.flex import EvMode

CURRENT_STEP_A: Final = 1.0
"""Level grid of the charging current (the driver snaps to the entity's own step)."""
PHASE_DWELL_S: Final = 300.0
"""How long the surplus must stay beyond a phase threshold (plan §0 decision 6: 5 min)."""
PHASE_UP_MARGIN_W: Final = 500.0
"""1 -> 3 needs the threshold plus this much (plan §0 decision 6)."""
PHASE_RETRY_S: Final = 900.0
"""After a refused phase change no new attempt for this long (no oscillation on refusals)."""
EPS: Final = 1e-6
SOC_LOCK_TOLERANCE_PCT: Final = 0.5
PHASE_INTENT_PREFIX: Final = "evphase:"


def battery_holds_lock(held: BatteryAction | None, soc_pct: float | None) -> bool:
    """SunCore holds a battery state that stops discharging (mirrors
    ``safety.flex.holds_discharge_lock``; E6)."""
    if held is None:
        return False
    if held.max_discharge_w is not None and held.max_discharge_w <= 1.0:
        return True
    return (
        held.min_soc_pct is not None
        and soc_pct is not None
        and held.min_soc_pct >= soc_pct - SOC_LOCK_TOLERANCE_PCT
    )


def table_phases(device: DeviceConfig, state: DeviceState | None) -> Literal[1, 3] | None:
    """The phases the level table is built for: observed with a ``phases`` role (``None`` =
    unknown, not controllable), else the wired phases."""
    control = device.control
    assert isinstance(control, ChargerControl)
    if control.phases_role is None:
        return control.phases
    if state is None or state.phases is None:
        return None
    return state.phases


def charger_levels(
    device: DeviceConfig, state: DeviceState | None, quantiser: Quantiser
) -> LevelTable | None:
    """The level table of a charger (module doc); ``None`` = phases unknown."""
    control = device.control
    if not isinstance(control, ChargerControl):
        return None
    phases = table_phases(device, state)
    if phases is None:
        return None
    action_phases = phases if control.phases_role is not None else None
    per_amp = control.voltage_v * phases
    levels: list[Level] = [Level(0, 0.0, ChargeAction(current_a=0.0, phases=action_phases))]
    seen: set[float] = set()
    count = math.floor((control.max_current_a - control.min_current_a) / CURRENT_STEP_A + EPS)
    candidates = [control.min_current_a + k * CURRENT_STEP_A for k in range(count + 1)]
    candidates.append(control.max_current_a)
    for raw in candidates:
        current = quantiser.current_a(device.id, raw)
        if current <= 0 or current in seen:
            continue
        seen.add(current)
        levels.append(
            Level(0, current * per_amp, ChargeAction(current_a=current, phases=action_phases))
        )
    if len(levels) < 2:
        return None
    ordered = sorted(levels[1:], key=lambda lv: lv.power_w)
    table = (levels[0], *ordered)
    return LevelTable(
        tuple(Level(i, lv.power_w, lv.action) for i, lv in enumerate(table)), switchable=True
    )


def charger_observed_level(table: LevelTable, state: DeviceState | None) -> int | None:
    """Level 0 while not charging, else the level nearest the current entity."""
    if state is None or not state.known or state.on is None:
        return None
    if not state.on:
        return 0
    if state.value is None:
        return None
    best = 1 if table.top >= 1 else 0
    for level in table.levels[1:]:
        action = level.action
        assert isinstance(action, ChargeAction)
        best_action = table.level(best).action
        assert isinstance(best_action, ChargeAction)
        if abs(action.current_a - state.value) < abs(best_action.current_a - state.value):
            best = level.index
    return best


# --- the per-tick directive ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvTick:
    """What the EV logic looks at in one tick (built by the allocator)."""

    mode: EvMode
    band: DeviceBand | None
    """The device band of a **fresh** plan interval (``None`` = none)."""
    battery_locked: bool
    """No home battery, or SunCore holds a discharge lock (E6)."""
    site_trusted: bool
    """The site inputs are fresh and ``OK`` (else no increase, at most the minimum)."""
    available_w: float | None
    """Surplus the charger could use now (its committed draw plus the free surplus)."""


@dataclass(frozen=True, slots=True)
class EvDirective:
    """The EV's say on the allocation of one charger."""

    floor: int = 0
    floor_reason: Reason | None = None
    cap: int | None = None
    """``None`` = no EV cap (the table's top)."""
    cap_reason: Reason | None = None
    lock_wanted: bool = False
    """The charger may draw grid power (an allowance): the battery should hold a discharge
    lock (E6) - also while the floor waits for it."""


def _floor_level(table: LevelTable, power_w: float) -> int:
    """The highest level whose power fits ``power_w`` (0 if not even the minimum does)."""
    best = 0
    for level in table.levels:
        if level.power_w <= power_w + EPS:
            best = level.index
    return best


@dataclass
class EvMachine:
    """Per charger: phase switching and the mode directive (module doc)."""

    device: DeviceConfig
    below_since: datetime | None = None
    above_since: datetime | None = None
    switching_to: Literal[1, 3] | None = None
    switch_started: datetime | None = None
    last_switch: datetime | None = None
    switch_times: list[datetime] = field(default_factory=list)
    retry_after: datetime | None = None
    in_flight_id: str | None = None
    in_flight_since: datetime | None = None
    idle_since: datetime | None = None
    """First tick the charger was seen paused at level 0 (the pause before a phase change when
    SunCore did not pause it itself)."""
    seq: int = 0

    @property
    def control(self) -> ChargerControl:
        control = self.device.control
        assert isinstance(control, ChargerControl)
        return control

    # --- directive ----------------------------------------------------------------------

    def directive(  # noqa: PLR0911 - one return per rule
        self,
        now: datetime,
        table: LevelTable,
        state: DeviceState | None,
        level: int,
        tick: EvTick,
    ) -> EvDirective:
        """Floor and cap of this tick (module doc)."""
        control = self.control
        connected = None if state is None else state.connected
        if tick.mode is EvMode.OFF:
            self._clear_switch()
            return EvDirective(cap=0, cap_reason=rs.plain(rs.EV_MODE_OFF))
        if connected is False:
            self._clear_switch()
            return EvDirective(cap=0, cap_reason=rs.plain(rs.EV_NOT_CONNECTED))
        self._decide_phases(now, table, state, tick)
        if self.switching_to is not None:
            return EvDirective(cap=0, cap_reason=rs.ev_phase_switch(self.switching_to))
        if self.last_switch is not None:
            waited = (now - self.last_switch).total_seconds()
            if waited < control.phase_switch_pause_s:
                return EvDirective(
                    cap=0, cap_reason=rs.ev_phase_pause(control.phase_switch_pause_s - waited)
                )
        if not tick.site_trusted:
            # Fail-safe: no increase, a charging car falls back to the minimum current.
            return EvDirective(cap=min(level, 1), cap_reason=rs.plain(rs.EV_INPUTS_UNKNOWN))
        allowance, key = self._allowance(table, tick)
        if allowance <= 0:
            return EvDirective()
        floor = _floor_level(table, allowance)
        if floor <= 0:
            return EvDirective(lock_wanted=True)
        if not tick.battery_locked:
            return EvDirective(lock_wanted=True, floor_reason=rs.plain(rs.EV_BATTERY_WAIT))
        return EvDirective(
            floor=floor,
            floor_reason=rs.ev_floor(key, table.level(floor).power_w),
            lock_wanted=True,
        )

    def _allowance(self, table: LevelTable, tick: EvTick) -> tuple[float, str]:
        """The grid power the mode permits (mirrors ``safety.flex.ev_allowance_w``)."""
        if tick.mode is EvMode.NOW:
            return table.level(table.top).power_w, rs.EV_NOW
        if tick.mode is EvMode.MIN_PV:
            return table.level(min(1, table.top)).power_w, rs.EV_MIN_PV
        band = tick.band
        if tick.mode is EvMode.TARGET and band is not None and band.allowed:
            return max(0.0, band.grid_power_max_w or 0.0), rs.EV_TARGET_GRID
        return 0.0, rs.EV_NOW

    # --- phase switching ----------------------------------------------------------------

    def _clear_switch(self) -> None:
        self.switching_to = None
        self.switch_started = None
        self.below_since = None
        self.above_since = None

    def _decide_phases(  # noqa: PLR0911 - one return per rule
        self, now: datetime, table: LevelTable, state: DeviceState | None, tick: EvTick
    ) -> None:
        control = self.control
        if not control.phase_switching or state is None or state.phases is None:
            self._clear_switch()
            return
        if self.switching_to is not None:
            if state.phases == self.switching_to:
                self._clear_switch()  # done (or the user did it)
            return
        if tick.mode is EvMode.NOW or not tick.site_trusted or tick.available_w is None:
            self.below_since = self.above_since = None
            return
        if self.retry_after is not None and now < self.retry_after:
            return
        self.switch_times = [t for t in self.switch_times if now - t < timedelta(hours=1)]
        if len(self.switch_times) >= control.max_phase_switches_per_hour:
            return
        threshold = 3 * control.min_current_a * control.voltage_v
        hysteresis = self.device.hysteresis_w if self.device.hysteresis_w is not None else 100.0
        available = tick.available_w
        if state.phases == 3 and available < threshold - hysteresis:
            self.above_since = None
            self.below_since = self.below_since or now
            if (now - self.below_since).total_seconds() >= PHASE_DWELL_S:
                self._start_switch(now, 1)
            return
        if state.phases == 1 and available > threshold + PHASE_UP_MARGIN_W:
            self.below_since = None
            self.above_since = self.above_since or now
            if (now - self.above_since).total_seconds() >= PHASE_DWELL_S:
                self._start_switch(now, 3)
            return
        self.below_since = self.above_since = None
        del table

    def _start_switch(self, now: datetime, phases: Literal[1, 3]) -> None:
        self.switching_to = phases
        self.switch_started = now
        self.below_since = self.above_since = None

    def phase_intent(
        self,
        now: datetime,
        state: DeviceState | None,
        level: int,
        paused_since: datetime | None,
        *,
        machine_busy: bool,
    ) -> Intent | None:
        """The phase change, once the charger has been paused long enough (E2)."""
        idle = state is not None and state.on is False and level == 0
        self.idle_since = (self.idle_since or now) if idle else None
        target = self.switching_to
        if target is None or state is None or machine_busy:
            return None
        if self.in_flight_id is not None:
            timeout = 120.0
            if self.in_flight_since is not None and (
                (now - self.in_flight_since).total_seconds() > timeout
            ):
                self.in_flight_id = None  # the decision never came back
            return None
        since = paused_since if paused_since is not None else self.idle_since
        if not idle or since is None:
            return None
        if (now - since).total_seconds() < self.control.phase_switch_pause_s:
            return None
        self.seq += 1
        intent = Intent(
            id=f"{PHASE_INTENT_PREFIX}{self.device.id}:{int(now.timestamp())}:{self.seq}",
            ts=now,
            asset_id=self.device.id,
            action=ChargeAction(current_a=0.0, phases=target),
            reasons=(rs.ev_phase_switch(target),),
            origin=Origin.CONTROLLER,
        )
        self.in_flight_id = intent.id
        self.in_flight_since = now
        return intent

    def report(self, decision: Decision) -> bool:
        """The gate's decision on a phase intent; ``True`` if it was ours."""
        if decision.intent.id != self.in_flight_id:
            return False
        self.in_flight_id = None
        self.in_flight_since = None
        now = decision.ts
        if decision.kind in (DecisionKind.ACCEPTED, DecisionKind.SHADOW, DecisionKind.PROPOSED):
            self.switch_times.append(now)
            self.last_switch = now
            self.switching_to = None
            if decision.kind is not DecisionKind.ACCEPTED:
                # Nothing changes on the device: do not try again for a while.
                self.retry_after = now + timedelta(seconds=PHASE_RETRY_S)
                self.last_switch = None
        else:
            self.switching_to = None
            self.retry_after = now + timedelta(seconds=PHASE_RETRY_S)
        return True


__all__ = [
    "CURRENT_STEP_A",
    "PHASE_DWELL_S",
    "PHASE_INTENT_PREFIX",
    "EvDirective",
    "EvMachine",
    "EvTick",
    "battery_holds_lock",
    "charger_levels",
    "charger_observed_level",
    "table_phases",
]
