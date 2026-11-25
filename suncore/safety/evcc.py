# SPDX-License-Identifier: Apache-2.0
"""evcc loadpoint invariants L1-L5 (plan WP10, ADR-0017 decisions 2 and 4; PR 6-J).

Pure functions over plain values; :mod:`suncore.safety.invariants` calls them for a
``LoadpointAction`` with the :class:`LoadpointSafety` (and the :class:`~suncore.safety.flex.
EvSafety` of the charger side) the gate builds per intent. Imports ``core`` only.

evcc regulates current and phases itself; SunCore sets mode, ``limitSoc`` and ``maxCurrent``. An
action is judged by what it can make the car draw: the **potential** power is ``maxCurrent x
voltage x phases`` for ``smart`` and ``now`` and 0 for ``off``.

* **L1** - values in range: ``maxCurrent`` inside ``[max(6 A, evcc's minCurrent), configured
  maximum]``, ``limitSoc`` in 1-100, no vehicle plan (clearing one over MQTT is ``UNVERIFIED``):
  :func:`check_values`.
* **L2** - ``now`` only in EV mode ``now`` (the user's floor) or inside a planned grid interval
  of mode ``target`` (:func:`loadpoint_allowance_w`), never for a vehicle known to be unplugged,
  and only while no home battery can discharge into the car (E6, in ``check_charge_grid``);
  no raise while the EV mode is ``off`` or evcc's own state is unknown: :func:`check_raise`.
* **L3** - the potential power of a raise keeps G1/G3/G4/G5/G6 (import and peak limit, §14a
  budget, island, unknown inputs, cool-down): ``invariants.check_grid`` /
  ``check_grid_situation`` judge it like any load increase (``expected_power_w`` knows it).
  ``smart`` follows the PV surplus by evcc's design, so it needs no surplus and no import room;
  a *raise* of ``maxCurrent`` or of the mode is still an increase for the grid situation.
* **L4** - write on change, write rate, read-back within ``settle_s``, three failures trip the
  device: the unchanged gate path (``check_change``, ``check_write_rate``, ``_confirm``).
* **L5** - evcc silent (no publication for 120 s), shadow, emergency stop, a site trip: the
  device state is unavailable, the watchdog trips the device and the gate releases it to the
  baseline (mode, ``maxCurrent``, ``limitSoc`` as read at takeover); the release is refused
  while evcc is silent and retried until evcc shows the baseline again.

A ``ReleaseAction`` is never judged here ("back to normal" works from any state).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from suncore.core.control import DeviceBand
from suncore.core.decisions import LoadpointAction, Reason
from suncore.core.devices import LoadpointControl
from suncore.core.flex import EvMode
from suncore.safety.flex import POWER_TOLERANCE_W, EvSafety, charge_power_w

CURRENT_TOLERANCE_A: Final = 0.05
IEC_MIN_CURRENT_A: Final = 6.0
"""IEC 61851 minimum charging current; evcc's own ``minCurrent`` raises the floor."""
_RANK: Final = {"off": 0, "smart": 1, "now": 2}

REASON_PLAN_UNVERIFIED: Final = Reason(key="safety.evcc_plan_unverified")
REASON_NOW_NOT_ALLOWED: Final = Reason(key="safety.evcc_now_not_allowed")
REASON_STATE_UNKNOWN: Final = Reason(key="safety.evcc_state_unknown")
REASON_LIMIT_RANGE: Final = Reason(key="safety.evcc_limit_range")


@dataclass(frozen=True, slots=True)
class LoadpointSafety:
    """What the loadpoint invariants look at besides :class:`EvSafety` (the gate builds it).

    The defaults are the most conservative situation: nothing observed, evcc's minimum unknown.
    """

    observed: LoadpointAction | None = None
    """evcc's state as read back (``None`` = unknown or evcc silent)."""
    min_current_a: float | None = None
    """evcc's own ``minCurrent`` (``None`` = not published)."""


NO_LOADPOINT_SAFETY: Final = LoadpointSafety()


def mode_rank(mode: str) -> int:
    return _RANK[mode]


def loadpoint_raises(action: LoadpointAction, observed: LoadpointAction | None) -> bool:
    """The action can make the car draw more than it may now: a higher mode or a higher
    ``maxCurrent`` in a mode that charges. Unknown state counts as a raise unless it is ``off``."""
    if observed is None:
        return action.mode != "off"
    if mode_rank(action.mode) > mode_rank(observed.mode):
        return True
    if action.mode == "off" or action.max_current_a is None:
        return False
    return observed.max_current_a is None or (
        action.max_current_a > observed.max_current_a + CURRENT_TOLERANCE_A
    )


def potential_power_w(
    action: LoadpointAction, control: LoadpointControl, observed: LoadpointAction | None
) -> float:
    """What the car may draw after the action: 0 for ``off``, else ``maxCurrent`` on the wired
    phases (the action's value, else evcc's current one, else the configured maximum)."""
    if action.mode == "off":
        return 0.0
    current = action.max_current_a
    if current is None and observed is not None:
        current = observed.max_current_a
    if current is None:
        current = control.max_current_a
    return charge_power_w(min(current, control.max_current_a), control.phases, control.voltage_v)


def loadpoint_allowance_w(
    mode: EvMode, control: LoadpointControl, *, connected: bool | None, band: DeviceBand | None
) -> float:
    """Grid power the bounded import exception permits (ADR-0017 decision 4), W.

    ``now``: the configured maximum current; ``target``: the fresh plan band's
    ``grid_power_max_w`` (only in an allowed band); everything else 0 - evcc has no floor mode
    (``min_pv`` is ``smart``, ``UNVERIFIED`` in plan §8), so ``min_pv`` gets no grid power.
    A vehicle known to be unplugged gets nothing.
    """
    if connected is False:
        return 0.0
    if mode is EvMode.NOW:
        return charge_power_w(control.max_current_a, control.phases, control.voltage_v)
    if mode is EvMode.TARGET and band is not None and band.allowed:
        return max(0.0, band.grid_power_max_w or 0.0)
    return 0.0


def check_values(
    action: LoadpointAction, control: LoadpointControl, lp: LoadpointSafety
) -> Reason | None:
    """L1: the plan is refused; ``maxCurrent`` and ``limitSoc`` stay in range."""
    if action.plan_soc_pct is not None or action.plan_time is not None:
        return REASON_PLAN_UNVERIFIED
    current = action.max_current_a
    if current is not None:
        low = max(IEC_MIN_CURRENT_A, lp.min_current_a or 0.0)
        high = control.max_current_a
        if not low - CURRENT_TOLERANCE_A <= current <= high + CURRENT_TOLERANCE_A:
            return Reason(
                key="safety.evcc_current_range",
                args={
                    "current_a": round(current, 1),
                    "min_a": round(low, 1),
                    "max_a": round(high, 1),
                },
            )
    limit = action.limit_soc_pct
    if limit is not None and not 1 <= round(limit) <= 100:
        return REASON_LIMIT_RANGE
    return None


def check_raise(action: LoadpointAction, ev: EvSafety, lp: LoadpointSafety) -> Reason | None:
    """L2: what a raise needs - the user's mode, a known evcc state, a vehicle that may charge -
    and what ``now`` needs: an allowance (the user's ``now`` or a planned grid interval)."""
    raises = loadpoint_raises(action, lp.observed)
    if action.mode == "now" and ev.allowance_w <= POWER_TOLERANCE_W:
        return REASON_NOW_NOT_ALLOWED
    if not raises:
        return None
    if ev.mode is EvMode.OFF:
        return Reason(key="safety.ev_mode_off")
    if lp.observed is None:
        return REASON_STATE_UNKNOWN
    if ev.connected is False:
        return Reason(key="safety.ev_not_connected")
    return None


__all__ = [
    "IEC_MIN_CURRENT_A",
    "NO_LOADPOINT_SAFETY",
    "REASON_LIMIT_RANGE",
    "REASON_NOW_NOT_ALLOWED",
    "REASON_PLAN_UNVERIFIED",
    "REASON_STATE_UNKNOWN",
    "LoadpointSafety",
    "check_raise",
    "check_values",
    "loadpoint_allowance_w",
    "loadpoint_raises",
    "mode_rank",
    "potential_power_w",
]
