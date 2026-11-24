# SPDX-License-Identifier: Apache-2.0
"""evcc as actor in the controller (plan WP10, ADR-0017 decisions 2-4; PR 6-J).

An evcc loadpoint (``LoadpointControl``) is not driven by the level allocator: evcc regulates
the charging current and the phases itself. SunCore only says *what evcc may do* - the mode and
the maximum current - and :class:`EvccIntents` turns the runtime EV mode into that, once per
tick, as ``LoadpointAction`` intents on change (the gate judges every one, L1-L5):

* ``off`` -> evcc ``off`` (always, also on unknown inputs: lowering is the safe direction);
* ``pv`` -> ``smart``; ``min_pv`` -> ``smart`` too (evcc has no minimum-power mode any more, the
  floor is not enforced: ``UNVERIFIED`` plan §8, the safe direction - nothing is imported for the
  car); ``now`` and ``target`` in a planned grid interval -> ``now`` with a ``maxCurrent`` that
  fits the allowance, the import limit and the §14a budget (the user-demanded floor and the
  plan-permitted grid power of ADR-0017 decision 4), only while the home battery cannot
  discharge into the car (E6: the battery controller is asked for a lock meanwhile,
  :attr:`LoadpointView.lock_wanted`);
* the maximum current of ``smart`` is the configured wiring limit, lowered under an active §14a
  budget (below the 6 A floor: ``off``).

**Fail-safe:** unknown or stale site inputs, an unreadable or silent evcc, island, a running
cool-down, §14a unknown or a vehicle known to be unplugged -> **no raise**: the wanted state is
cut down to what evcc shows now (``now`` falls back to ``smart``). A device the gate holds or
has paused gets no intent. Intents on change only; a refused or shadowed one is repeated after
:data:`RETRY_S` at the earliest; ``maxCurrent`` is raised only by at least
:data:`RAISE_STEP_A` (against headroom noise) but lowered at once.

Deterministic, time injected, ``core`` only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from suncore.controller import reasons as rs
from suncore.controller.grid import GridHolds, GridInputs
from suncore.core.control import DeviceBand, DeviceState
from suncore.core.decisions import (
    Decision,
    Intent,
    LoadpointAction,
    Origin,
    Reason,
)
from suncore.core.devices import DeviceConfig, LoadpointControl
from suncore.core.flex import EvccMode, EvMode, EvTarget
from suncore.core.grid import GridState, Para14aStatus
from suncore.core.signals import Quality

INTENT_PREFIX: Final = "evcc:"
RETRY_S: Final = 60.0
"""A wanted state that is not on the device yet is sent again no sooner than this."""
RAISE_STEP_A: Final = 2.0
"""``maxCurrent`` is raised only when the wanted value is at least this much higher."""
IEC_MIN_CURRENT_A: Final = 6.0
CURRENT_TOLERANCE_A: Final = 0.05
EPS: Final = 1e-6


@dataclass(frozen=True, slots=True)
class LoadpointTick:
    """What the evcc logic looks at for one loadpoint in one tick (built by the controller)."""

    device: DeviceConfig
    state: DeviceState | None
    mode: EvMode
    target: EvTarget | None
    band: DeviceBand | None
    """The device band of a **fresh** plan interval (``None`` = none)."""
    battery_locked: bool
    """No home battery, or SunCore holds a discharge lock (E6)."""
    site_trusted: bool
    grid: GridInputs
    holds: GridHolds
    import_w: float | None
    """The measured grid import (``OK`` reading, W; ``None`` = unknown)."""
    blocked: Reason | None = None
    """The gate holds the device or it is paused (no intent)."""


@dataclass(frozen=True, slots=True)
class LoadpointView:
    """The decision of one tick for one loadpoint (the "Why?" view and the battery lock)."""

    wanted: LoadpointAction | None
    reasons: tuple[Reason, ...] = ()
    lock_wanted: bool = False
    """The loadpoint may draw grid power (an allowance): the battery should hold a discharge
    lock (E6) - also while ``now`` waits for it."""


@dataclass
class _Sent:
    action: LoadpointAction
    ts: datetime
    pending_id: str | None = None


@dataclass
class EvccIntents:
    """Per loadpoint: the wanted action and the intent bookkeeping (module doc)."""

    sent: dict[str, _Sent] = field(default_factory=dict)
    views: dict[str, LoadpointView] = field(default_factory=dict)
    seq: int = 0

    # --- the tick -----------------------------------------------------------------------

    def step(self, now: datetime, ticks: list[LoadpointTick]) -> list[Intent]:
        intents: list[Intent] = []
        live = {t.device.id for t in ticks}
        for gone in [d for d in self.sent if d not in live]:
            del self.sent[gone]
        self.views = {}
        for tick in ticks:
            view = self.wanted(tick)
            self.views[tick.device.id] = view
            intent = self._intent(now, tick, view)
            if intent is not None:
                intents.append(intent)
        return intents

    @property
    def lock_wanted(self) -> bool:
        return any(v.lock_wanted for v in self.views.values())

    # --- what is wanted -----------------------------------------------------------------

    def wanted(self, tick: LoadpointTick) -> LoadpointView:
        """The mode and ``maxCurrent`` evcc may have now (module doc)."""
        control = tick.device.control
        assert isinstance(control, LoadpointControl)
        state = tick.state
        if state is None or not state.known or state.mode is None:
            return LoadpointView(None)  # evcc unknown or silent: nothing to say
        if tick.mode is EvMode.OFF:
            return LoadpointView(
                LoadpointAction(mode="off"), (rs.plain(rs.EV_MODE_OFF),), lock_wanted=False
            )
        per_amp = control.voltage_v * control.phases
        reasons: list[Reason] = []
        lock_wanted = False
        mode: EvccMode = "smart"
        cap_a = control.max_current_a
        # §14a: an active budget caps the flagged loadpoint, below the 6 A floor it is off.
        budget = tick.grid.para14a
        if tick.device.para14a and budget.status is Para14aStatus.ACTIVE and budget.budget_w:
            para_a = math.floor(budget.budget_w / per_amp + EPS)
            if para_a < IEC_MIN_CURRENT_A:
                return LoadpointView(LoadpointAction(mode="off"), (rs.plain(rs.EVCC_PARA14A_OFF),))
            cap_a = min(cap_a, float(para_a))
        allowance_w, key = self._allowance(tick, control)
        if allowance_w > 0:
            now_a = min(cap_a, math.floor(allowance_w / per_amp + EPS))
            limit = tick.grid.import_limit_w
            if limit is not None and tick.import_w is not None:
                draw_w = 0.0 if state.value is None else state.value
                headroom_a = math.floor(max(0.0, limit - tick.import_w + draw_w) / per_amp + EPS)
                now_a = min(now_a, float(headroom_a))
            if now_a >= IEC_MIN_CURRENT_A:
                lock_wanted = True
                if tick.battery_locked:
                    mode, cap_a = "now", float(now_a)
                    reasons.append(rs.ev_floor(key, now_a * per_amp))
                else:
                    reasons.append(rs.plain(rs.EV_BATTERY_WAIT))
        if mode == "smart":
            reasons.append(
                rs.plain(rs.EVCC_MIN_PV if tick.mode is EvMode.MIN_PV else rs.EVCC_SMART)
            )
        if cap_a < control.max_current_a:
            reasons.append(rs.evcc_cap(cap_a))
        wanted = LoadpointAction(mode=mode, max_current_a=cap_a)
        return LoadpointView(
            self._no_raise_cut(tick, state, wanted, reasons), tuple(reasons), lock_wanted
        )

    def _allowance(self, tick: LoadpointTick, control: LoadpointControl) -> tuple[float, str]:
        """The grid power the mode permits (mirrors ``safety.evcc.loadpoint_allowance_w``)."""
        if tick.state is not None and tick.state.connected is False:
            return 0.0, rs.EV_NOW
        if tick.mode is EvMode.NOW:
            return control.max_current_a * control.voltage_v * control.phases, rs.EV_NOW
        band = tick.band
        if tick.mode is EvMode.TARGET and band is not None and band.allowed:
            if self._target_reached(tick):
                return 0.0, rs.EV_TARGET_GRID
            return max(0.0, band.grid_power_max_w or 0.0), rs.EV_TARGET_GRID
        return 0.0, rs.EV_NOW

    @staticmethod
    def _target_reached(tick: LoadpointTick) -> bool:
        target, state = tick.target, tick.state
        return (
            target is not None
            and target.soc_pct is not None
            and state is not None
            and state.soc_pct is not None
            and state.soc_pct >= target.soc_pct
        )

    def _no_raise_cut(
        self,
        tick: LoadpointTick,
        state: DeviceState,
        wanted: LoadpointAction,
        reasons: list[Reason],
    ) -> LoadpointAction:
        """Fail-safe: while anything is unknown or protective, never more than evcc shows."""
        grid, flagged = tick.grid, tick.device.para14a
        protect = (
            not tick.site_trusted
            or grid.grid_state in (GridState.ISLAND, GridState.UNKNOWN)
            or tick.holds.island_s > 0
            or (
                flagged
                and (grid.para14a.status is Para14aStatus.UNKNOWN or tick.holds.para14a_s > 0)
            )
            or state.connected is False
            or state.quality is not Quality.OK
        )
        if not protect:
            return self._debounced(state, wanted)
        reasons.append(rs.plain(rs.EVCC_NO_RAISE))
        mode = _lower(wanted.mode, state.mode)
        current = wanted.max_current_a
        if current is not None and state.max_current_a is not None:
            current = min(current, state.max_current_a)
        return LoadpointAction(mode=mode, max_current_a=current)

    @staticmethod
    def _debounced(state: DeviceState, wanted: LoadpointAction) -> LoadpointAction:
        """Raise ``maxCurrent`` only by a full step (headroom noise), lower it at once."""
        current, seen = wanted.max_current_a, state.max_current_a
        if current is None or seen is None or current <= seen:
            return wanted
        if current < seen + RAISE_STEP_A and wanted.mode == state.mode:
            return LoadpointAction(mode=wanted.mode, max_current_a=seen)
        return wanted

    # --- intents ------------------------------------------------------------------------

    def _intent(self, now: datetime, tick: LoadpointTick, view: LoadpointView) -> Intent | None:
        wanted, state = view.wanted, tick.state
        device_id = tick.device.id
        if wanted is None or state is None or tick.blocked is not None:
            return None
        if _shown(wanted, state):
            self.sent.pop(device_id, None)
            return None
        previous = self.sent.get(device_id)
        if previous is not None:
            if previous.pending_id is not None:
                return None  # the gate has not answered yet
            if previous.action == wanted and (now - previous.ts) < timedelta(seconds=RETRY_S):
                return None
        self.seq += 1
        intent = Intent(
            id=f"{INTENT_PREFIX}{device_id}:{int(now.timestamp())}:{self.seq}",
            ts=now,
            asset_id=device_id,
            action=wanted,
            reasons=view.reasons or (rs.plain(rs.EVCC_SMART),),
            origin=Origin.CONTROLLER,
        )
        self.sent[device_id] = _Sent(wanted, now, intent.id)
        return intent

    def report(self, decision: Decision) -> bool:
        """The gate's decision on one of our intents; ``True`` if it was ours."""
        if not decision.intent.id.startswith(INTENT_PREFIX):
            return False
        entry = self.sent.get(decision.intent.asset_id)
        if entry is None or entry.pending_id != decision.intent.id:
            return True
        entry.pending_id = None
        entry.ts = decision.ts
        return True

    def reset_pending(self) -> None:
        for entry in self.sent.values():
            entry.pending_id = None


def _shown(wanted: LoadpointAction, state: DeviceState) -> bool:
    """evcc already shows the wanted mode and ``maxCurrent`` (nothing to write)."""
    if state.mode != wanted.mode:
        return False
    if wanted.max_current_a is None:
        return True
    return (
        state.max_current_a is not None
        and abs(state.max_current_a - wanted.max_current_a) <= CURRENT_TOLERANCE_A
    )


_RANK: Final = {"off": 0, "smart": 1, "now": 2}


def _lower(wanted: EvccMode, observed: str | None) -> EvccMode:
    """The lower of two evcc modes (``off < smart < now``); an unknown observed one is ``off``."""
    seen = _RANK.get(observed or "off", 0)
    if _RANK[wanted] <= seen:
        return wanted
    return "now" if observed == "now" else ("smart" if observed == "smart" else "off")


__all__ = [
    "INTENT_PREFIX",
    "RAISE_STEP_A",
    "RETRY_S",
    "EvccIntents",
    "LoadpointTick",
    "LoadpointView",
]
